"""Rebuild the pooled setup dataset(s) — durable, reproducible.

Runs ``run_scan`` over the cached US universe and pools every breakout setup
(features + outcomes) into ``runs/<name>.{parquet,csv}``. Compute is cache-only,
so run ``python -m agents.evaluator.data.ingest`` first if prices are stale.

Named builds (pick one as the argv, default = ``base``):
    base      setups_all          — weekly trend 20/5/5 (current default)
    wk1035    setups_all_wk1035   — weekly trend 10-EMA / 3-week slope / ±5 pivot
                                    (Kell's weekly 10-EMA; the A/B challenger).
Only the WEEKLY trend horizon differs between the two; the daily strategy, the
breakout setups, and every outcome are identical — so the two datasets are
row-aligned and directly comparable (see ``probabilities/trend_eval.py``).

Usage:
    .venv/bin/python -m agents.evaluator.rebuild_datasets            # base
    .venv/bin/python -m agents.evaluator.rebuild_datasets wk1035     # challenger
"""
from __future__ import annotations

import sys

from agents.evaluator.data import store
from agents.evaluator.probabilities import screen

BASE_CFG = dict(box_pct=5.0, price_mode="open_close", expansion_pct=4.0, min_days=3)

BUILDS = {
    "base": ("setups_all", dict(BASE_CFG)),
    "wk1035": ("setups_all_wk1035", dict(BASE_CFG, weekly_ext_ref_ema=10,
                                         weekly_trend_slope_window=3,
                                         weekly_trend_pivot_window=5)),
}


def main(which: str = "base") -> None:
    name, cfg = BUILDS[which]
    tickers = [t for t in store.cached_tickers() if t in set(store.us_universe())]
    print(f"building '{name}' over {len(tickers)} cached US tickers; cfg={cfg}")
    screen.build_dataset(tickers, strat_kwargs=cfg, name=name)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "base")
