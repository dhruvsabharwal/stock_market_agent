"""Inspect a single ticker: on-bar strategy vs the batch lab oracle, side by side.

Shows, per entry date, the on-bar trade and the batch trade, and flags any
difference in exit reason / return / days-held. Use ``--verbose`` to also stream
the engine's BUY / SELL / SIGNAL events as they happen.

Usage (from repo root):
    .venv/bin/python -m agents.evaluator.inspect AAPL
    .venv/bin/python -m agents.evaluator.inspect AAPL --start 2015-01-01 --end 2026-01-01
    .venv/bin/python -m agents.evaluator.inspect AAPL --verbose
"""
from __future__ import annotations

import argparse

import pandas as pd

from .data import store
from .engine.runner import run_backtest
from .logging_util import set_verbose
from .strategy.range_strategy import RangeBreakoutStrategy

DEFAULTS = dict(
    box_pct=3.0, min_days=3, expansion_pct=4.0, max_expansion_days=1,
    close_threshold_pct=10.0, price_mode="high_low", trade_ma_type="21ema",
    allow_rising_close_exception=True, stop_type="expansion_open",
    stop_buffer_pct=0.02, stop_constant_pct=0.03, max_loss_pct=0.04,
)


def _batch(df, start, end):
    import sys
    sys.path.insert(0, "agents/base_breakout_strategy")
    from base_identification import find_ranges

    kw = dict(DEFAULTS)
    if start:
        kw["start_date"] = pd.Timestamp(start)
    if end:
        kw["end_date"] = pd.Timestamp(end)
    out = {}
    for r in find_ranges(df, **kw):
        t = r.trade
        if t and t.sell is not None:
            out[pd.Timestamp(t.buy.date)] = t
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="On-bar vs batch trade inspector.")
    p.add_argument("ticker")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--verbose", action="store_true", help="stream engine events")
    args = p.parse_args()

    if args.verbose:
        set_verbose(True)

    df = store.load(args.ticker)
    print(f"\n=== {args.ticker}  on-bar engine run "
          f"({args.start or 'start'} .. {args.end or 'end'}) ===")
    res = run_backtest(df, RangeBreakoutStrategy(**DEFAULTS),
                       ticker=args.ticker, start=args.start, end=args.end)
    onbar = {t.entry_date: t for t in res.trades}

    batch = _batch(df, args.start, args.end)

    # Union of entry dates, sorted.
    dates = sorted(set(onbar) | set(batch))
    hdr = f"\n{'entry':<12} {'on-bar (reason/ret/held)':<30} {'batch (reason/ret/held)':<30} diff"
    print(hdr)
    print("-" * len(hdr))
    diffs = 0
    for d in dates:
        ob, bt = onbar.get(d), batch.get(d)
        ob_s = (f"{ob.exit_reason}/{ob.return_pct:+.2f}%/{ob.days_held}d"
                if ob else "—")
        bt_s = (f"{bt.sell_reason}/{(bt.return_pct or 0):+.2f}%/{bt.days_held}d"
                if bt else "—")
        flag = ""
        if ob and bt:
            if (ob.exit_reason != bt.sell_reason
                    or abs((ob.return_pct or 0) - (bt.return_pct or 0)) > 0.15
                    or ob.days_held != bt.days_held):
                flag = "  <-- DIFF"
                if bt.buy.price < 1.0:
                    flag += " (batch penny-rounding)"
                diffs += 1
        elif ob and not bt:
            flag = "  <-- on-bar only"
        elif bt and not ob:
            flag = "  <-- batch only (position occupied / open)"
        print(f"{str(d.date()):<12} {ob_s:<30} {bt_s:<30}{flag}")

    print(f"\non-bar trades: {len(onbar)}   batch trades: {len(batch)}   "
          f"diffs: {diffs}")


if __name__ == "__main__":
    main()
