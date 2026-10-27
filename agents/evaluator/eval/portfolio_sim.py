"""Portfolio-level replay of the setup ledger — Jeff Sun equity-risk sizing.

Each row in the setup ledger (``runs/*.parquet``) is a trade simulated in
isolation, as if it were the only position ever taken. This module replays a
POOL of candidate signals as one portfolio with finite capital and a finite
number of concurrent slots, so the capital-constraint question ("if I'm fully
invested, what do I miss?") has an actual answer instead of an assumption.

Sizing follows Jeff Sun's rule (see ``artifacts/jeff-sun-methodology_2.html``,
``artifacts/Jeff_Sun_Process_Report.html``): position size is NOT a flat % of
equity — it scales inversely with the trade's own stop distance, so every
position risks roughly the same DOLLAR amount regardless of how tight or wide
its stop is:

    position_$ = equity * (risk_pct_per_trade / 100) / (stop_dist_pct / 100)

capped at ``max_position_pct`` of equity (his 15-30% ceiling) and at whatever
cash is actually free.

Simplification: with only entry/exit price + realised return per trade (no
daily price path), a position's value is held at cost basis until its exit
date, then the realised P&L lands in cash. So "equity" only moves at trade
exits, not continuously — good enough for sizing/capacity decisions, not a
substitute for a full mark-to-market equity curve.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

REQUIRED_COLS = ["ticker", "entry_date", "exit_date", "return_pct", "stop_dist_pct"]


@dataclass
class _OpenPosition:
    ticker: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    cost_basis: float
    return_pct: float
    candidate_idx: int


@dataclass
class PortfolioResult:
    trades: pd.DataFrame        # taken trades, one row each, with $ sizing/pnl
    missed: pd.DataFrame        # candidates skipped, with the reason
    events: pd.DataFrame        # equity/cash/open-slots after every entry+exit event
    initial_equity: float
    final_equity: float


def simulate_portfolio(
    signals: pd.DataFrame,
    *,
    initial_equity: float = 10_000.0,
    risk_pct_per_trade: float = 0.20,     # % of equity per trade (Jeff Sun: 0.15-0.25)
    max_positions: int = 8,               # concurrent slots (Jeff Sun: 5-15, ~8-12 typical)
    max_position_pct: float = 25.0,       # per-name cap, % of equity (Jeff Sun: 15-30)
    min_ticket_dollars: float = 25.0,     # skip a fill this small — not worth the friction
    rank_by: Optional[str] = None,        # column to prioritise same-day candidates by
    rank_ascending: bool = False,
) -> PortfolioResult:
    """Replay ``signals`` as one capital- and slot-constrained portfolio.

    ``signals`` needs (at minimum) ``ticker``, ``entry_date``, ``exit_date``,
    ``return_pct``, ``stop_dist_pct`` — plus whatever column ``rank_by`` names,
    if given, to break ties when more candidates show up on a day than there
    are open slots or free cash (defaults to the order rows already arrive in
    — pass ``rank_by`` to prioritise, e.g., by relative strength).
    """
    missing = [c for c in REQUIRED_COLS if c not in signals.columns]
    if missing:
        raise KeyError(f"signals missing required columns: {missing}")

    df = signals.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.dropna(subset=["entry_date", "exit_date", "return_pct", "stop_dist_pct"])
    df = df[df["stop_dist_pct"] > 0]
    df = df.sort_values("entry_date").reset_index(drop=True)

    realised_pnl = 0.0
    open_positions: list[_OpenPosition] = []
    exit_heap: list[tuple[pd.Timestamp, int]] = []   # (exit_date, seq) -> index into open_positions by id
    open_by_seq: dict[int, _OpenPosition] = {}
    open_tickers: set[str] = set()
    seq = 0

    trades_rows = []
    missed_rows = []
    events_rows = []

    def equity() -> float:
        return initial_equity + realised_pnl

    def cash() -> float:
        committed = sum(p.cost_basis for p in open_by_seq.values())
        return equity() - committed

    def close_position(seq_id: int, as_of: pd.Timestamp) -> None:
        nonlocal realised_pnl
        p = open_by_seq.pop(seq_id)
        open_tickers.discard(p.ticker)
        pnl = p.cost_basis * (p.return_pct / 100.0)
        realised_pnl += pnl
        trades_rows.append({
            "ticker": p.ticker, "entry_date": p.entry_date, "exit_date": p.exit_date,
            "dollar_size": round(p.cost_basis, 2), "return_pct": p.return_pct,
            "pnl_dollars": round(pnl, 2), "equity_after": round(equity(), 2),
        })
        events_rows.append({
            "date": as_of, "event": "exit", "ticker": p.ticker,
            "equity": round(equity(), 2), "cash": round(cash(), 2),
            "n_open": len(open_by_seq),
        })

    def close_due(as_of: pd.Timestamp) -> None:
        while exit_heap and exit_heap[0][0] <= as_of:
            _, sid = heapq.heappop(exit_heap)
            if sid in open_by_seq:      # guard against stale heap entries
                close_position(sid, as_of)

    for entry_date, day_df in df.groupby("entry_date", sort=True):
        close_due(entry_date)           # free capital/slots from trades exiting on/before today

        candidates = day_df[~day_df["ticker"].isin(open_tickers)]
        if rank_by is not None and rank_by in candidates.columns:
            candidates = candidates.sort_values(rank_by, ascending=rank_ascending, na_position="last")

        for _, row in candidates.iterrows():
            if len(open_by_seq) >= max_positions:
                missed_rows.append({**row.to_dict(), "reason": "no_slot"})
                continue

            eq = equity()
            target = eq * (risk_pct_per_trade / 100.0) / (row["stop_dist_pct"] / 100.0)
            target = min(target, eq * (max_position_pct / 100.0))
            actual = min(target, cash())

            if actual < min_ticket_dollars:
                missed_rows.append({**row.to_dict(), "reason": "insufficient_cash"})
                continue

            seq += 1
            pos = _OpenPosition(
                ticker=row["ticker"], entry_date=entry_date, exit_date=row["exit_date"],
                cost_basis=actual, return_pct=row["return_pct"], candidate_idx=seq,
            )
            open_by_seq[seq] = pos
            open_tickers.add(row["ticker"])
            heapq.heappush(exit_heap, (row["exit_date"], seq))
            events_rows.append({
                "date": entry_date, "event": "entry", "ticker": row["ticker"],
                "equity": round(equity(), 2), "cash": round(cash(), 2),
                "n_open": len(open_by_seq),
            })

    # flush anything still open past the last entry date
    while exit_heap:
        exit_date, sid = heapq.heappop(exit_heap)
        if sid in open_by_seq:
            close_position(sid, exit_date)

    trades = pd.DataFrame(trades_rows).sort_values("exit_date").reset_index(drop=True) if trades_rows else pd.DataFrame(columns=["ticker","entry_date","exit_date","dollar_size","return_pct","pnl_dollars","equity_after"])
    missed = pd.DataFrame(missed_rows) if missed_rows else pd.DataFrame(columns=list(df.columns) + ["reason"])
    events = pd.DataFrame(events_rows).sort_values("date").reset_index(drop=True) if events_rows else pd.DataFrame(columns=["date","event","ticker","equity","cash","n_open"])

    return PortfolioResult(
        trades=trades, missed=missed, events=events,
        initial_equity=initial_equity, final_equity=equity(),
    )


def summarise_portfolio(result: PortfolioResult) -> dict:
    """Headline numbers for a ``PortfolioResult``."""
    t = result.trades
    m = result.missed
    total_signals = len(t) + len(m)
    return {
        "initial_equity": result.initial_equity,
        "final_equity": round(result.final_equity, 2),
        "total_return_pct": round((result.final_equity / result.initial_equity - 1) * 100, 2),
        "trades_taken": len(t),
        "trades_missed": len(m),
        "pct_missed": round(100 * len(m) / total_signals, 1) if total_signals else 0.0,
        "missed_no_slot": int((m["reason"] == "no_slot").sum()) if len(m) else 0,
        "missed_no_cash": int((m["reason"] == "insufficient_cash").sum()) if len(m) else 0,
        "win_rate_%": round(100 * (t["return_pct"] > 0).mean(), 2) if len(t) else None,
        "avg_position_$": round(t["dollar_size"].mean(), 2) if len(t) else None,
        "max_position_$": round(t["dollar_size"].max(), 2) if len(t) else None,
    }
