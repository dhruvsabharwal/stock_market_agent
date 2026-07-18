"""Run a strategy across the universe over a window and write an eval bundle.

Evaluation window defaults to 2015-2026 (the holdout). For each ticker the
engine produces trades; the combined ledger + per-ticker equity are summarised
and written to ``eval/results/<strategy>_<window>/``:
    trades.csv     — the full ledger (one row per trade)
    summary.json   — headline metrics + by-stage breakdown
    equity.csv     — concatenated per-ticker equity curves

Strategies are supplied via a factory registry (real strategies land later;
the example fixtures are registered so the pipeline is runnable today).

Usage:
    .venv/bin/python -m agents.evaluator.eval.run_eval buy_and_hold \
        --start 2015-01-01 --end 2026-01-01 --tickers AAPL,MSFT,NVDA
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..data import store
from ..engine.runner import run_backtest
from ..strategy.examples import BuyAndHold, StopAtFivePct
from ..strategy.range_strategy import RangeBreakoutStrategy
from ..strategy.stage_range_strategy import StageRangeStrategy
from . import metrics

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Strategy registry: name -> factory.
REGISTRY = {
    BuyAndHold.name: BuyAndHold,
    StopAtFivePct.name: StopAtFivePct,
    RangeBreakoutStrategy.name: RangeBreakoutStrategy,
    StageRangeStrategy.name: StageRangeStrategy,
}


def run(
    strategy_name: str,
    *,
    tickers: list[str],
    start: str,
    end: str,
    params: dict | None = None,
) -> dict:
    if strategy_name not in REGISTRY:
        raise KeyError(
            f"Unknown strategy {strategy_name!r}. Registered: {sorted(REGISTRY)}"
        )
    factory = REGISTRY[strategy_name]

    all_trades = []
    equity_frames = []
    ran, missing = 0, 0
    for t in tickers:
        if not store.is_cached(t):
            missing += 1
            continue
        df = store.load(t)
        strat = factory(**(params or {}))
        res = run_backtest(df, strat, ticker=t, start=start, end=end)
        all_trades.extend(res.trades)
        if len(res.equity_curve):
            equity_frames.append(res.equity_curve.rename(t))
        ran += 1

    summary = metrics.summarise(all_trades)
    summary.update({
        "strategy": strategy_name, "start": start, "end": end,
        "tickers_run": ran, "tickers_missing": missing,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
    by_stage = metrics.by_feature(all_trades, "stage")

    # Write bundle
    tag = f"{strategy_name}_{start[:4]}_{end[:4]}"
    out = RESULTS_DIR / tag
    out.mkdir(parents=True, exist_ok=True)
    metrics.ledger_df(all_trades).to_csv(out / "trades.csv", index=False)
    if not by_stage.empty:
        summary["by_stage"] = json.loads(by_stage.reset_index().to_json(orient="records"))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    if equity_frames:
        pd.concat(equity_frames, axis=1).to_csv(out / "equity.csv")

    print(json.dumps(summary, indent=2))
    print(f"\nWrote bundle to {out}")
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Run a strategy eval over a window.")
    p.add_argument("strategy", help=f"one of {sorted(REGISTRY)}")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default="2026-01-01")
    p.add_argument(
        "--tickers",
        default="",
        help="comma-separated; default = all cached US tickers",
    )
    args = p.parse_args()
    tickers = (
        [t.strip() for t in args.tickers.split(",") if t.strip()]
        or store.cached_tickers()
    )
    run(args.strategy, tickers=tickers, start=args.start, end=args.end)


if __name__ == "__main__":
    main()
