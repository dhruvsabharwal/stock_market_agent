"""Phase-0 engine validation.

Run from repo root:
    .venv/bin/python -m agents.evaluator.tests.test_engine_smoke
"""
from __future__ import annotations

import pandas as pd

from ..data import store
from ..engine.runner import run_backtest
from ..eval import metrics
from ..strategy.examples import BuyAndHold, StopAtFivePct

TICKER = "AAPL"
START, END = "2015-01-01", "2026-01-01"


def _check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL"), "-", msg)
    assert cond, msg


def main() -> None:
    df = store.load(TICKER)

    # ── buy-and-hold: engine equity must match raw open-to-close return ──────
    res = run_backtest(df, BuyAndHold(), ticker=TICKER, start=START, end=END)
    win = df[(df.index >= pd.Timestamp(START)) & (df.index <= pd.Timestamp(END))]
    # Entry fills at the open of the bar AFTER the first in-window bar.
    entry_open = float(win["Open"].iloc[1])
    final_close = float(win["Close"].iloc[-1])
    expected_ret = (final_close / entry_open - 1) * 100

    _check(len(res.trades) == 1, f"buy_and_hold makes exactly 1 trade (got {len(res.trades)})")
    t = res.trades[0]
    _check(abs(t.entry_price - entry_open) < 1e-6,
           f"entry at next-bar open ({t.entry_price:.4f} vs {entry_open:.4f})")
    _check(t.exit_reason == "end_of_data", f"exits at end_of_data (got {t.exit_reason})")
    _check(abs(t.return_pct - expected_ret) < 0.05,
           f"return matches buy&hold ({t.return_pct:.2f}% vs {expected_ret:.2f}%)")
    _check(t.peak_return_pct >= t.return_pct,
           f"peak_return >= realised ({t.peak_return_pct} >= {t.return_pct})")
    _check(res.equity_curve.index.is_monotonic_increasing, "equity curve is forward in time")

    # ── stop strategy: the broker must enforce the 5% stop ──────────────────-
    res2 = run_backtest(df, StopAtFivePct(), ticker=TICKER, start=START, end=END)
    stops = [tr for tr in res2.trades if tr.exit_reason == "stop_loss"]
    _check(len(res2.trades) >= 1, f"stop_5pct makes trades (got {len(res2.trades)})")
    for tr in stops:
        _check(tr.return_pct <= 0.5,
               f"stopped trade loss is bounded (~-5%, got {tr.return_pct}%)")

    # ── metrics layer sanity ─────────────────────────────────────────────────
    s = metrics.summarise(res2.trades, equity_curve=res2.equity_curve)
    _check(s["n_trades"] == len(res2.trades), "metrics count matches ledger")
    print("\nstop_5pct summary:")
    for k, v in s.items():
        print(f"  {k}: {v}")

    print("\nALL ENGINE SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
