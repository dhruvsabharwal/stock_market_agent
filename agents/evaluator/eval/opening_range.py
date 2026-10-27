"""Opening-range metrics from cached hourly bars (Jeff Sun's intraday layer).

For each session we resolve the **first hour** (the 09:30-10:30 ET bar) as the
opening range, and derive the metrics his entry trigger rests on: the OR high/low,
where the first hour closed inside it, the **gap**, and **RVOL-at-open** (first-hour
volume vs its own trailing average). Plus the rest-of-day read used to judge the
trigger: did price **reclaim the OR high** after the first hour (his "Re-ORH"), and
how deep was the low afterwards.

Leak discipline — every row is tagged by which moment it is known:
  * DECISION-TIME (known at 10:30, usable as a pre-entry filter): ``or_high/low``,
    ``or_range_pct``, ``or_close_pos``, ``or_vol``, ``gap_pct``, ``rvol_open_{20,50}``.
  * REST-OF-DAY (known only later; a trigger/outcome, never a pre-entry feature):
    ``reorh``, ``reorh_time``, ``lod_after_or_pct``, ``day_high/low/close``,
    ``close_above_orh``.

The RVOL baseline and the gap's prior close both use strictly earlier sessions, so
nothing here looks ahead. Returns one row per session date (tz-naive DatetimeIndex).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd

from ..data import intraday

OPEN_TIME = dt.time(9, 30)
SECOND_TIME = dt.time(10, 30)     # start of the 2nd hourly bar = the 10:30 fill price

# Columns attached to setups (ratio/pct/bool only — absolute prices/volumes are
# excluded, matching the evaluator's cross-ticker feature convention).
_DECISION_TIME = ["or_range_pct", "or_close_pos", "gap_pct", "rvol_open_20", "rvol_open_50", "lod_dist_pct"]
_REST_OF_DAY = ["reorh", "close_above_orh", "lod_after_or_pct"]
_ATTACH_COLS = _DECISION_TIME + _REST_OF_DAY


def opening_range_metrics(ticker: str, *, rvol_windows=(20, 50)) -> pd.DataFrame:
    """Per-session opening-range metrics for ``ticker`` (cache-only). Empty frame
    if no hourly data is cached."""
    intr = intraday.load(ticker)
    if intr is None or intr.empty:
        return pd.DataFrame()

    df = intr.copy()
    df["day"] = df.index.date
    df["time"] = df.index.time

    # ── first-hour (opening-range) bar per session ───────────────────────────
    first = df.sort_index().groupby("day").first()
    first_time = df.sort_index().groupby("day")["time"].first()
    # only trust sessions whose first bar actually starts at 09:30 (regular open)
    valid = first_time == OPEN_TIME

    out = pd.DataFrame(index=pd.to_datetime(first.index))
    out.index.name = "date"
    out["or_open"] = first["Open"].values
    out["or_high"] = first["High"].values
    out["or_low"] = first["Low"].values
    out["or_close"] = first["Close"].values
    out["or_vol"] = first["Volume"].values
    span = (out["or_high"] - out["or_low"])
    out["or_range_pct"] = (span / out["or_low"] * 100).round(3)
    out["or_close_pos"] = ((out["or_close"] - out["or_low"]) / span * 100).where(span > 0).round(1)
    # Jeff Sun's LoD-tightness: how far the first-hour low sits below the open, as %
    # (divide by atr_pct downstream for his "LoD < 60% ATR" gate).
    out["lod_dist_pct"] = ((out["or_open"] - out["or_low"]) / out["or_open"] * 100).round(3)

    # ── gap vs the prior session's close (from the intraday data itself, so both
    #    sides are raw/unadjusted and there's no daily-cache-freshness dependency)
    session_close = df.sort_index().groupby("day")["Close"].last()
    prev_close = pd.Series(session_close.values, index=pd.to_datetime(session_close.index)).shift(1)
    out["gap_pct"] = ((out["or_open"] / prev_close.reindex(out.index) - 1) * 100).round(3)

    # ── RVOL-at-open: first-hour volume vs its trailing mean (prior sessions) ─
    for w in rvol_windows:
        base = out["or_vol"].rolling(w).mean().shift(1)       # prior w sessions only
        out[f"rvol_open_{w}"] = (out["or_vol"] / base).round(3)

    # ── rest-of-day: Re-ORH reclaim + low after the opening range ────────────
    rest = df[df["time"] > OPEN_TIME]
    after_high = rest.groupby("day")["High"].max()
    after_low = rest.groupby("day")["Low"].min()
    day_high = df.groupby("day")["High"].max()
    day_low = df.groupby("day")["Low"].min()
    day_close = df.sort_index().groupby("day")["Close"].last()

    def _first_break_time(g):
        hit = g.index[g["High"] > first.loc[g.name, "High"]]
        return hit.min().time() if len(hit) else None
    reorh_time = rest.groupby("day").apply(_first_break_time, include_groups=False)

    idx_day = out.index.date
    # the 10:30 price (open of the 2nd hourly bar) — the intraday fill price for a
    # post-first-hour entry (kept for the future Re-ORH buy/sell; NOT a feature).
    second_open = df[df["time"] == SECOND_TIME].sort_index().groupby("day")["Open"].first()
    out["or_next_open"] = second_open.reindex(idx_day).values
    # how far up the day is vs the PRIOR close by 10:30 — tells us whether the
    # breakout (a >=expansion_pct move) was already reached in the first hour:
    pc = prev_close.reindex(out.index)
    out["move_high_pct"] = ((out["or_high"] / pc - 1) * 100).round(3)      # max up by 10:30
    out["move_1030_pct"] = ((out["or_next_open"] / pc - 1) * 100).round(3)  # still up at 10:30
    out["reorh"] = pd.Series(after_high.reindex(idx_day).values > out["or_high"].values, index=out.index)
    out["reorh_time"] = [str(reorh_time.get(d)) if reorh_time.get(d) is not None else None for d in idx_day]
    out["lod_after_or_pct"] = ((pd.Series(after_low.reindex(idx_day).values, index=out.index) / out["or_open"] - 1) * 100).round(3)
    out["day_high"] = day_high.reindex(idx_day).values
    out["day_low"] = day_low.reindex(idx_day).values
    out["day_close"] = day_close.reindex(idx_day).values
    out["close_above_orh"] = out["day_close"] > out["or_high"]

    out = out[valid.values]                                   # drop non-09:30-open sessions
    return out


# Columns useful on the BREAKOUT day (T0 / signal_date): the first-hour character
# of the breakout itself + how much of the >=expansion move was reached by 10:30.
_T0_COLS = ["or_range_pct", "gap_pct", "rvol_open_20", "lod_dist_pct",
            "move_high_pct", "move_1030_pct", "reorh"]


def attach_opening_range(df: pd.DataFrame, *, entry_col: str = "entry_date",
                         ticker_col: str = "ticker", prefix: str = "",
                         cols=None, flag: str = "has_opening_range") -> pd.DataFrame:
    """Left-join opening-range metrics onto a setups DataFrame by (ticker, ``entry_col``).
    Cache-only; adds NaN where a ticker/date has no hourly coverage.

    Default (``entry_col='entry_date'``, no prefix) joins the ENTRY-day (T+1) first
    hour — a post-first-hour entry filter, NOT an on-bar signal-bar feature (at the
    signal bar the entry-day open hasn't happened; using it there would be lookahead).

    Pass ``entry_col='signal_date', prefix='t0_', cols=_T0_COLS`` to instead join the
    BREAKOUT-day (T0) first hour — used to see whether the breakout was already
    reached by 10:30 (``move_high_pct``/``move_1030_pct`` vs the >=expansion threshold).

    Leak semantics: DECISION-TIME cols (``or_range_pct``/``gap_pct``/``rvol_open_*``/
    ``lod_dist_pct``/``move_*``) are known at 10:30; REST-OF-DAY (``reorh``/
    ``close_above_orh``/``lod_after_or_pct``) are same-day triggers, never a pre-entry filter."""
    cols = list(cols) if cols is not None else _ATTACH_COLS
    out = df.copy()
    pcols = [prefix + c for c in cols]
    if entry_col not in out.columns or ticker_col not in out.columns:
        for c in pcols:
            out[c] = np.nan
        out[flag] = False
        return out

    ent = pd.to_datetime(out[entry_col], errors="coerce").dt.normalize()
    mats = []
    for tk, idx in out.groupby(ticker_col).groups.items():
        m = opening_range_metrics(tk)
        if m.empty:
            continue
        matched = m[cols + ["or_high"]].reindex(ent.loc[idx].values)
        matched.index = idx                                   # align back to the setup rows
        mats.append(matched)

    if mats:
        allm = pd.concat(mats).reindex(out.index)             # whole-column assign preserves dtype
        for c, pc in zip(cols, pcols):
            out[pc] = allm[c].values
        out[flag] = allm["or_high"].notna().values
    else:
        for c in pcols:
            out[c] = np.nan
        out[flag] = False
    return out
