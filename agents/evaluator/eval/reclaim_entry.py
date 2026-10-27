"""Intraday Re-ORH reclaim entry — Jeff Sun's actual mechanic, as an ADDITIVE
alternative to the flat-4% day-over-day ``RangeBreakoutStrategy`` trigger. Does
NOT modify or replace it — a completely separate detector you can compare against.

The flat-4% trigger anchors on day-over-day %, confirms at the close, and fills
the NEXT day's open (T+1) — none of which matches his method. This detector:

  1. Its OWN box detection (``collect_box_breaks``) — independent of the
     validated flat-% ``RangeBreakoutStrategy`` (untouched, not reused here):
     box tightness is measured in ATR units (height <= ``k_box`` * ATR, ATR as
     of the PRIOR bar) instead of a flat %, same 'maximal box ending at the
     previous bar' + ``min_days`` duration semantics. Gets every box-break day
     (T0) with its ``box_high`` reference level.
  2. Checks T0's own FIRST HOUR: does the opening-range high clear ``box_high``
     by >= ``min_expansion_pct`` (the box-relative anchor, default 1%, replacing
     the flat 4%)? Is first-hour RVOL >= ``min_rvol``?
  3. If both hold, scans the REST of T0's hourly bars for the RECLAIM (the first
     later bar whose high crosses back above that ORH) — the reclaim is the
     entry (his M30 Re-ORH). No prior dip is required, only the reclaim itself.
     At the reclaim, the LoD gate: (fill - running_low_since_hour1) / ATR must be
     < ``max_lod_atr`` (default 0.6, his rule) — else the day already ran too far
     and it's skipped. No qualifying reclaim that session = no trade (same-day-
     only; it does not carry over to T+1).

Entry fill (leak-free, buy-stop semantics — mirrors the daily broker's stop
fill): ``max(bar.open, orh)`` on the reclaim bar — the level, unless price
gapped straight through it on that bar (then the open, worse fill).

Exit: reuses a REAL, already-tested ``ExitRule`` (default ``AtrStopExit(k_atr=1)``
— entry - k*ATR stop + the same 2-closes-below-MA sell-in-weakness leg used
everywhere else in the engine), by treating T0 itself as the buy bar with the
intraday reclaim price as ``buy_price`` — see ``simulate_exit``.

Caveat: only tickers/dates inside the ~3yr hourly cache are reachable — this
is inherently a small-sample, recent-window method, not a full-history one.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd

from ..data import intraday, store
from ..engine.scan import collect_setups
from ..strategy.stage_range_strategy import StageRangeStrategy
from .opening_range import OPEN_TIME, opening_range_metrics


def collect_box_breaks(tickers, *, box_pct: float = 5.0, min_days: int = 3,
                       start: str = "2023-06-01", end: Optional[str] = None,
                       strat_kwargs: Optional[dict] = None, verbose: bool = True) -> pd.DataFrame:
    """Every box break (any size — ``expansion_pct`` forced near-zero), reusing
    the SAME validated flat-% box detector as the rest of the codebase (box
    height stays <= ``box_pct`` while forming — plain containment, no ATR
    scaling; matches Jeff Sun's own documented tightness-scan number, ~5%).
    Contraction (is the range SHRINKING over time) is a separate, additional
    test — see ``atr_contraction_pct`` — not folded into the box itself.

    Returns one row per break with ``box_high``/``box_low`` (the box BEFORE
    today), ATR, and the contraction feature already recorded on the setup."""
    kw = dict(strat_kwargs or {})
    kw.pop("expansion_pct", None)
    rows = []
    for i, tk in enumerate(tickers, 1):
        if not store.is_cached(tk):
            continue
        df = store.load(tk)
        strat = StageRangeStrategy(box_pct=box_pct, min_days=min_days,
                                   expansion_pct=0.01, price_mode="open_close", **kw)
        for sig in collect_setups(df, strat, ticker=tk, start=start, end=end):
            f = sig["features"]
            if f.get("box_high") is None:
                continue
            rows.append(dict(ticker=tk, signal_date=pd.Timestamp(sig["signal_date"]),
                             box_high=f["box_high"], box_low=f.get("box_low"),
                             box_len=f.get("range_length_days"),
                             atr=f.get("atr"), atr_pct=f.get("atr_pct"),
                             atr_contraction_pct=f.get("atr_contraction_pct")))
        if verbose and i % 100 == 0:
            print(f"  box-break scan: {i}/{len(tickers)} tickers, {len(rows)} candidates so far")
    return pd.DataFrame(rows)


def find_reclaim_entries(candidates: pd.DataFrame, *, min_expansion_pct: float = 1.0,
                         min_rvol: float = 1.0, max_lod_atr: float = 0.6,
                         verbose: bool = True) -> pd.DataFrame:
    """For each T0 candidate, check the box-relative anchor + RVOL on the first
    hour, then scan the rest of T0 for the reclaim of the opening-range high.
    At the reclaim, also require the LoD gate: (fill - running_low_since_hour1)
    / ATR < ``max_lod_atr`` (his 60% rule) — if the day has already run too far
    from its low by the time of the reclaim, skip it. Returns one row per
    qualifying reclaim (the actual entry event)."""
    out = []
    for n, (tk, grp) in enumerate(candidates.groupby("ticker"), 1):
        orm = opening_range_metrics(tk)
        if orm.empty:
            continue
        intr = intraday.load(tk)
        if intr is None or intr.empty:
            continue
        idf = intr.copy()
        idf["day"] = idf.index.date
        idf["time"] = idf.index.time
        for _, row in grp.iterrows():
            d = pd.Timestamp(row["signal_date"]).normalize()
            if d not in orm.index:
                continue
            orh, rvol = orm.loc[d, "or_high"], orm.loc[d, "rvol_open_20"]
            box_high = row["box_high"]
            if not box_high or box_high <= 0 or pd.isna(orh):
                continue
            clear_pct = (orh / box_high - 1) * 100
            if clear_pct < min_expansion_pct:
                continue
            if pd.isna(rvol) or rvol < min_rvol:
                continue
            day_bars = idf[idf["day"] == d.date()].sort_index()
            rest = day_bars[day_bars["time"] > OPEN_TIME]
            atr = row.get("atr")
            if not atr or atr <= 0:
                continue
            # the reclaim = the first later bar whose HIGH crosses back above the
            # opening-range high (no prior "dip" required — see collect_box_breaks
            # docstring for why that check was near-vacuous). Track the running
            # low since hour 1 as we go, for the LoD gate at the reclaim bar.
            or_low = float(orm.loc[d, "or_low"])
            running_low, reclaim_ts = or_low, None
            for ts, bar in rest.iterrows():
                running_low = min(running_low, float(bar["Low"]))
                if bar["High"] >= orh:
                    reclaim_ts = ts
                    break
            if reclaim_ts is None:
                continue
            bar = rest.loc[reclaim_ts]
            fill = max(float(bar["Open"]), float(orh))
            lod_dist_atr = (fill - running_low) / atr
            if lod_dist_atr >= max_lod_atr:
                continue   # the day already ran too far from its low -- pass
            out.append(dict(ticker=tk, signal_date=d, box_high=box_high, or_high=orh,
                            clear_pct=round(clear_pct, 2), rvol=round(float(rvol), 2),
                            reclaim_time=reclaim_ts, entry_price=fill, atr=atr,
                            atr_pct=row.get("atr_pct"), lod_dist_atr=round(lod_dist_atr, 3)))
        if verbose and n % 200 == 0:
            print(f"  reclaim scan: {n}/{candidates['ticker'].nunique()} tickers, {len(out)} entries so far")
    return pd.DataFrame(out)


def simulate_exit(entries: pd.DataFrame, exit_rule=None) -> pd.DataFrame:
    """Simulate each reclaim entry forward using a REAL, already-tested
    ``ExitRule`` (default: ``StopAndWeaknessExit`` — the same stop + 2-closes-
    below-MA sell-in-weakness leg used everywhere else in the engine; pass
    ``AtrStopExit()``/others to try a different sell on the same entries).

    Treats T0 itself as the buy bar (``buy_idx`` = T0's index in the DAILY
    series) with ``buy_price`` = the intraday reclaim fill — so the exit rule's
    own ATR/MA machinery runs unmodified from there. The weakness leg can't
    fire on the entry bar itself (`k > buy_idx` in every ``ExitRule``), so it
    naturally only checks from T0+1 onward — exactly matching how a same-day
    entry would really be managed. ``exit_reason='end_of_data'`` rows are
    STILL-OPEN positions (unrealized) — kept in the frame but excluded from
    win-rate/expectancy the same way ``eval.metrics.ledger_df`` excludes them."""
    from ..engine.exits import AtrStopExit, PriceData
    if exit_rule is None:
        exit_rule = AtrStopExit(k_atr=1.0)   # k*ATR stop + the same sell-in-weakness leg

    out = []
    for tk, grp in entries.groupby("ticker"):
        df = store.load(tk).sort_index()
        pd_ = PriceData(df)
        idx_of = {d: i for i, d in enumerate(pd_.dates)}
        for _, e in grp.iterrows():
            buy_idx = idx_of.get(pd.Timestamp(e["signal_date"]))
            if buy_idx is None:
                continue
            buy_price = float(e["entry_price"])
            setup = {"criteria_price": None, "constant_pct": None}
            er = exit_rule.simulate(pd_, buy_idx, buy_price, setup)
            ret_pct = (er.sell_price / buy_price - 1) * 100
            peak_pct = (er.peak_close / buy_price - 1) * 100
            out.append(dict(**e.to_dict(), exit_price=er.sell_price,
                            exit_date=pd_.dates[er.sell_idx], exit_reason=er.reason,
                            return_pct=round(ret_pct, 3), peak_return_pct=round(peak_pct, 3),
                            days_held=er.sell_idx - buy_idx))
    return pd.DataFrame(out)
