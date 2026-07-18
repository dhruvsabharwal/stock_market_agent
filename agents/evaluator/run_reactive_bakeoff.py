"""Exit bake-off: stop+weakness (current live sell) vs swing_trail_8 (prior
winner) vs the new ReactiveMomentumExit, over the full cached universe.

Collects setups once per ticker (StageRangeStrategy, same cfg as the featured
dataset), applies all three exit rules to the same setups, pools, and splits
by ``signal_date`` at 2015-01-01 (pre = train, 2015-2026 = holdout) — matching
the methodology in HANDOFF.md Sec 5.2.

Writes to ``agents/evaluator/runs/``:
    reactive_bakeoff_results.csv   — summary table (one row per exit x split)
    trades_<exit_name>.parquet     — full pooled ledger per exit rule

Usage:
    .venv/bin/python -m agents.evaluator.run_reactive_bakeoff
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from .data import store
from .engine.exits import ReactiveMomentumExit, StopAndWeaknessExit, SwingLowTrailExit
from .engine.scan import apply_exit, collect_setups
from .strategy.stage_range_strategy import StageRangeStrategy

RUNS_DIR = Path(__file__).resolve().parent / "runs"
CFG = dict(box_pct=5.0, price_mode="open_close", expansion_pct=4.0, min_days=3)
SPLIT_DATE = "2015-01-01"

RULES = {
    "stop+weakness": StopAndWeaknessExit(max_loss_pct=0.04),
    "swing_trail_8": SwingLowTrailExit(swing_window=8, trail_buffer_pct=1.0),
    "reactive_momentum": ReactiveMomentumExit(),
}


def summarise(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        return {}
    ret = df["return_pct"].astype(float)
    peak = df["peak_return_pct"].astype(float)
    return {
        "n": n,
        "mean_ret_pct": round(ret.mean(), 2),
        "median_ret_pct": round(ret.median(), 2),
        "win_gt0_pct": round((ret > 0).mean() * 100, 1),
        "reward_gt8_pct": round((ret > 8).mean() * 100, 1),
        "capture_pct": round(ret.mean() / peak.mean() * 100, 0) if peak.mean() else None,
        "avg_days": round(df["days_held"].astype(float).mean(), 0),
    }


def main() -> None:
    tickers = [t for t in store.cached_tickers() if t in set(store.us_universe())]
    print(f"universe: {len(tickers)} tickers")

    pooled = {name: [] for name in RULES}
    t0 = time.time()
    ran = 0
    for tk in tickers:
        df = store.load(tk)
        if len(df) < 100:
            continue
        su = collect_setups(df, StageRangeStrategy(**CFG), ticker=tk)
        if not su:
            continue
        for name, rule in RULES.items():
            trades = apply_exit(su, df, rule, ticker=tk)
            for t in trades:
                if not t.is_open:
                    d = t.to_dict()
                    d["ticker"] = tk
                    pooled[name].append(d)
        ran += 1
        if ran % 100 == 0:
            print(f"  {ran}/{len(tickers)} tickers, {time.time()-t0:.0f}s elapsed")

    print(f"done {ran} tickers in {time.time()-t0:.0f}s")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, records in pooled.items():
        df = pd.DataFrame(records)
        df.to_parquet(RUNS_DIR / f"trades_{name.replace('+', '_')}.parquet")
        df["signal_date"] = pd.to_datetime(df["signal_date"])
        pre = df[df["signal_date"] < SPLIT_DATE]
        post = df[df["signal_date"] >= SPLIT_DATE]
        for window, sub in (("pre2015", pre), ("holdout", post)):
            s = summarise(sub)
            if s:
                rows.append({"exit": name, "window": window, **s})

    out = pd.DataFrame(rows)
    out.to_csv(RUNS_DIR / "reactive_bakeoff_results.csv", index=False)
    print(f"\nwrote {RUNS_DIR / 'reactive_bakeoff_results.csv'}")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
