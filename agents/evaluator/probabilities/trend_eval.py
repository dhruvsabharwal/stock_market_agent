"""Reusable cohort evaluation for the pooled setup dataset.

Slice the setups by any grouping + any filter, ALWAYS holdout-split at
2015-01-01, and report the three win/quality columns we care about:

  sell_win  = % realized return_pct > 0   (the LIVE exit — did the trade close green)
  mom_win   = % peak_return_pct  > 4%      (did MOMENTUM show up, exit-independent)
  reward8   = % peak_return_pct  > 8%      (bigger-move reward, exit-independent)

Built for iterating entry filters toward a higher win rate: keep stacking
conditions in `query=` and watch sell_win / mom_win move (and the n shrink).

Usage
-----
    from agents.evaluator.probabilities import trend_eval as te
    df = te.load()                                   # wk1035 + stage buckets + investable
    # the main stage table across the 4 investable weekly x daily regimes:
    te.cohort(df, by=['weekly_trend_state','trend_state','dstage'])
    # stack MORE filters to push the win rate up:
    te.cohort(df, by='dstage',
              query="weekly_trend_state=='uptrend' and trend_state=='basing'"
                    " and overhead_highest_pct_6m < 5 and above_50sma")
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

RUNS = Path(__file__).resolve().parent.parent / "runs"
SPLIT = pd.Timestamp("2015-01-01")
INVEST = ["basing", "uptrend"]           # the regimes we'd actually invest in


def _stage(x):
    if pd.isna(x):
        return None
    x = int(x)
    return "0" if x == 0 else "1" if x == 1 else "2" if x == 2 else "3+"


def load(name: str = "setups_all_wk1035") -> pd.DataFrame:
    """Load a pooled dataset and add helper columns: `dstage`/`wstage` (0/1/2/3+
    exhaustion buckets), `period` (train/holdout), `investable` (weekly & daily
    both in basing/uptrend)."""
    df = pd.read_parquet(RUNS / f"{name}.parquet")
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df["dstage"] = df["exh_since_downtrend"].map(_stage)          # daily exhaustion stage
    df["wstage"] = df["weekly_exh_since_downtrend"].map(_stage)   # weekly exhaustion stage
    df["period"] = df["signal_date"].map(lambda d: "train" if d < SPLIT else "holdout")
    df["investable"] = (df["weekly_trend_state"].isin(INVEST)
                        & df["trend_state"].isin(INVEST))
    return df


def _row(s: pd.DataFrame) -> dict:
    return dict(
        n=len(s),
        sell_win=round(100 * (s.return_pct > 0).mean(), 1),
        mom_win=round(100 * (s.peak_return_pct > 4).mean(), 1),
        reward8=round(100 * (s.peak_return_pct > 8).mean(), 1),
        mean=round(s.return_pct.mean(), 2),
        peak=round(s.peak_return_pct.mean(), 2),
    )


def cohort(df: pd.DataFrame, by, *, query: str | None = None, investable: bool = True,
           min_n: int = 150, sort: str = "ho_sell_win", show: bool = True) -> pd.DataFrame:
    """Group `df` by `by` (str or list), holdout-split, and report sell_win /
    mom_win / reward8 (+ mean/peak) for TRAIN and HOLDOUT side by side.

    `query`      — a pandas .query() string to stack arbitrary extra filters.
    `investable` — restrict to weekly & daily both in basing/uptrend (default).
    `min_n`      — drop groups with fewer than this many rows in EITHER period.
    `sort`       — column to sort by (default holdout sell_win, descending).
    """
    d = df[df.investable] if investable else df
    if query:
        d = d.query(query)
    by = [by] if isinstance(by, str) else list(by)

    recs = []
    for keys, g in d.groupby(by, dropna=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        tr, ho = _row(g[g.period == "train"]), _row(g[g.period == "holdout"])
        if tr["n"] < min_n or ho["n"] < min_n:
            continue
        rec = dict(zip(by, keys))
        for per, m in (("tr", tr), ("ho", ho)):
            for k, v in m.items():
                rec[f"{per}_{k}"] = v
        recs.append(rec)

    out = pd.DataFrame(recs)
    if len(out):
        out = out.sort_values(sort, ascending=False).reset_index(drop=True)
    if show:
        base = d[d.period == "holdout"]
        cols = by + ["tr_n", "tr_sell_win", "tr_mom_win", "tr_reward8",
                     "ho_n", "ho_sell_win", "ho_mom_win", "ho_reward8"]
        with pd.option_context("display.width", 240, "display.max_rows", 200):
            print(f"[filter: {query or 'investable' if investable else 'ALL'}]  "
                  f"holdout base: sell_win={100*(base.return_pct>0).mean():.1f}% "
                  f"mom_win={100*(base.peak_return_pct>4).mean():.1f}% "
                  f"reward8={100*(base.peak_return_pct>8).mean():.1f}%  (n={len(base)})")
            print(out[cols].to_string(index=False) if len(out) else "  (no groups meet min_n)")
    return out
