"""Build a portfolio trade book CSV from the pooled setup ledger.

Runs the same pipeline used in the evaluator session: apply the candidate
filters, replay them through ``simulate_portfolio`` (Jeff Sun equity-risk
sizing, see ``portfolio_sim.py``), and write the taken trades out as a CSV
that ``tradebook_viewer.html`` can load directly.

Edit the CONFIG block below and re-run — nothing here needs to change to
try a different filter set, ranking column, or sizing parameter.

Usage:
    .venv/bin/python -m agents.evaluator.eval.build_trade_book
"""
from __future__ import annotations

import pandas as pd

from .portfolio_sim import simulate_portfolio, summarise_portfolio

# ── CONFIG — edit these and re-run ──────────────────────────────────────
SETUPS_PATH = "agents/evaluator/runs/setups_intraday_clear2_d4.parquet"
OUT_PATH = "agents/evaluator/runs/portfolio_trade_book_composite.csv"

EXCLUDE_TICKERS = [
    "FFAI", "EDBL", "ELPW", "SGLY", "AEMD", "IESC", "ALUR", "NRDY",
    "BANL", "CIIT", "IPW", "MIDD", "LBRDA", "LBRDK", "B",
]

# ── hard gates (candidate must pass ALL of these to even be considered) ──
MIN_DOLLAR_VOL_20D = 500_000
MIN_ENTRY_PRICE = 5.0
REQUIRE_WEEKLY_NOT_DOWNTREND = True
REQUIRE_DAILY_NOT_DOWNTREND = False
REQUIRE_NOT_STUCK_RESISTANCE_6MO = False
REQUIRE_ABOVE_20EMA = True        # close_ext_20ema_pct > 0 — must be above it; ranking below then
                                   # prefers LESS extension among those already-above candidates
MIN_VARS_6M = None                # e.g. 0.0 to also hard-gate on positive VARS; None = soft-rank only

# ── composite ranking score — used to prioritise candidates competing for a
# slot on the same day (see simulate_portfolio's rank_by). Each column is
# converted to a 0-100 percentile rank (missing values get a neutral 50),
# sign-flipped where "lower is better", then combined as a weighted average.
# Bigger |weight| = more say in the ranking; the tight-days column is half
# weight since it's a "good to have", not a "should have" per the brief.
COMPOSITE_WEIGHTS = {
    "vars_6m": 1.0,                  # higher = more preferred (relative strength)
    "close_ext_20ema_pct": -1.0,     # lower = more preferred (less extended from 20-EMA)
    "tight_range_days_2pct": 0.5,    # higher = more preferred (nice-to-have, half weight)
}

INITIAL_EQUITY = 10_000.0
RISK_PCT_PER_TRADE = 0.20         # % of equity per trade (Jeff Sun: 0.15-0.25)
MAX_POSITIONS = 8                 # concurrent slots (Jeff Sun: 5-15, ~8-12 typical)
MAX_POSITION_PCT = 25.0           # per-name cap, % of equity (Jeff Sun: 15-30)
RANK_BY = "composite_score"       # column used to prioritise same-day candidates
RANK_ASCENDING = False
# ─────────────────────────────────────────────────────────────────────────


def compute_composite_score(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted blend of 0-100 percentile ranks, one per feature in ``weights``.

    A negative weight means "lower raw value is preferred" — the column is
    rank-inverted before weighting. Missing values get a neutral rank (50) so
    one absent feature doesn't tank (or inflate) a candidate's whole score.
    """
    score = pd.Series(0.0, index=df.index)
    total_abs_weight = sum(abs(w) for w in weights.values())
    for col, w in weights.items():
        pct_rank = df[col].rank(pct=True) * 100.0
        if w < 0:
            pct_rank = 100.0 - pct_rank
        pct_rank = pct_rank.fillna(50.0)
        score = score + pct_rank * abs(w)
    return score / total_abs_weight


def load_candidates() -> pd.DataFrame:
    df = pd.read_parquet(SETUPS_PATH)
    df = df[~df["ticker"].isin(EXCLUDE_TICKERS)].copy()
    if REQUIRE_WEEKLY_NOT_DOWNTREND:
        df = df[df["weekly_trend_state"] != "downtrend"]
    if REQUIRE_DAILY_NOT_DOWNTREND:
        df = df[df["trend_state"] != "downtrend"]
    if REQUIRE_NOT_STUCK_RESISTANCE_6MO:
        df = df[df["stuck_under_resistance_6_mo"] == False]  # noqa: E712 — explicit False, not just falsy/NaN
    if REQUIRE_ABOVE_20EMA:
        df = df[df["close_ext_20ema_pct"] > 0]
    df = df[(df["avg_dollar_vol_20d"] > MIN_DOLLAR_VOL_20D) & (df["entry_price"] > MIN_ENTRY_PRICE)]
    if MIN_VARS_6M is not None:
        df = df[df["vars_6m"] > MIN_VARS_6M]
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["composite_score"] = compute_composite_score(df, COMPOSITE_WEIGHTS)
    return df


def build_trade_book(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    result = simulate_portfolio(
        df,
        initial_equity=INITIAL_EQUITY,
        risk_pct_per_trade=RISK_PCT_PER_TRADE,
        max_positions=MAX_POSITIONS,
        max_position_pct=MAX_POSITION_PCT,
        rank_by=RANK_BY,
        rank_ascending=RANK_ASCENDING,
    )
    trades = result.trades.copy()
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])

    feat = df[["ticker", "entry_date", "entry_price", "exit_price", "exit_reason", "stop_dist_pct"]].copy()
    book = trades.merge(feat, on=["ticker", "entry_date"], how="left")
    book["days_held"] = (book["exit_date"] - book["entry_date"]).dt.days
    book = book.sort_values("entry_date").reset_index(drop=True)

    return book, summarise_portfolio(result)


if __name__ == "__main__":
    candidates = load_candidates()
    book, summary = build_trade_book(candidates)
    book.to_csv(OUT_PATH, index=False)

    print(f"candidates after filters: {len(candidates)}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nwrote {len(book)} trades -> {OUT_PATH}")
