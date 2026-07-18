"""Validate the incremental Stage-2 template against a batch pandas recompute.

The MA-based criteria (1-3) are exactly recomputable with pandas, so we feed the
template bar by bar and assert its per-bar verdicts match the batch truth at
every sampled date. This is the same "incremental == batch (where comparable)"
discipline as the range equivalence test, and it guards against MA/slope drift.

Criteria 4-6 (confirmed pivots, completed-week volume) are leak-free by
construction — a pivot is only emitted once `pivot_window` later bars exist, and
only completed weeks enter the volume window — so they are not re-derived here.

Run: .venv/bin/python -m agents.evaluator.tests.test_stage2
"""
from __future__ import annotations

from ..data import store
from ..engine.records import Bar
from ..strategy.stage2 import Stage2TrendTemplate

TICKER = "AAPL"


def main() -> None:
    df = store.get(TICKER)
    close = df["Close"]
    sma150 = close.rolling(150).mean()
    sma200 = close.rolling(200).mean()
    sma200_lag = sma200.shift(21)

    tt = Stage2TrendTemplate(sma_fast=150, sma_slow=200, slope_lookback=21)
    checked = fails = 0
    for i, (d, row) in enumerate(df.iterrows()):
        tt.update(Bar.from_row(d, row))
        if i < 230:                      # let the slow SMA + slope window fill
            continue
        c = tt.criteria()
        f, s, slag = sma150.loc[d], sma200.loc[d], sma200_lag.loc[d]
        truth = {
            "above_150_200": bool(row["Close"] > f and row["Close"] > s),
            "sma150_above_200": bool(f > s),
            "sma200_rising": bool(s > slag),
        }
        checked += 1
        for k, want in truth.items():
            if c[k] != want:
                fails += 1
                if fails <= 10:
                    print(f"  {d.date()} {k}: incr={c[k]} batch={want}")

    print(f"checked {checked} bars; MA-criteria mismatches: {fails}")
    if fails:
        raise SystemExit("STAGE-2 VALIDATION FAILED")
    print("STAGE-2 VALIDATION PASSED — incremental criteria 1-3 match the pandas recompute.")


if __name__ == "__main__":
    main()
