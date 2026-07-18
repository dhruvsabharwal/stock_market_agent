"""Scan mode — evaluate EVERY valid setup independently (overlaps allowed).

Two phases, so the sell can be swapped without recomputing the 139 features:
  1. ``collect_setups`` — run the strategy, record every breakout with its
     feature snapshot + entry/stop hints. Exit-independent.
  2. ``apply_exit`` — simulate each setup forward under a given ``ExitRule``.

``run_scan`` chains both with a default exit that reproduces the live engine's
sell (initial stop + sell-in-weakness). Pass ``exit_rule=`` to try a different
sell on the same setups. Each trade buys ``notional`` (default $1000); overlaps
are allowed; no price rounding.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .broker import Broker
from .exits import ExitRule, PriceData, StopAndWeaknessExit
from .records import TradeRecord
from ..strategy.base import StrategyContext


def collect_setups(df, strategy, *, ticker, start=None, end=None) -> list[dict]:
    """Run the strategy in scan mode and return every recorded setup
    (signal_date, features, stop hints). No exit is applied here."""
    df = df.sort_index()
    sim_start = pd.Timestamp(start) if start is not None else None
    if sim_start is not None and strategy.warmup_bars > 0:
        pre = df[df.index < sim_start]
        warm = pre.iloc[-strategy.warmup_bars:] if len(pre) else pre
        feed_df = pd.concat([warm, df[df.index >= sim_start]])
        feed_df = feed_df[~feed_df.index.duplicated(keep="first")]
    else:
        feed_df = df
    if end is not None:
        feed_df = feed_df[feed_df.index <= pd.Timestamp(end)]

    from .feed import BarFeed
    strategy._scan = True
    strategy.scan_signals = []
    ctx = StrategyContext(Broker(ticker), ticker)   # no fills in scan mode
    for bar in BarFeed(feed_df, ticker=ticker):
        strategy._ingest(bar)
        ctx.bar = bar
        ctx.enabled = sim_start is None or bar.date >= sim_start
        strategy.on_bar(bar, ctx)
    return list(strategy.scan_signals)


def apply_exit(setups, df, exit_rule: ExitRule, *, ticker, notional: float = 1000.0) -> list[TradeRecord]:
    """Simulate each setup forward under ``exit_rule`` → one TradeRecord each."""
    pd_ = PriceData(df.sort_index())
    idx_of = {d: i for i, d in enumerate(pd_.dates)}
    trades = []
    for sig in setups:
        si = idx_of.get(pd.Timestamp(sig["signal_date"]))
        if si is None:
            continue
        buy_idx = si + 1                     # buy at the open after the signal
        if buy_idx >= len(pd_):
            continue
        buy_price = float(pd_.open[buy_idx])
        if buy_price <= 0:
            continue
        er = exit_rule.simulate(pd_, buy_idx, buy_price, sig)
        trades.append(TradeRecord(
            ticker=ticker, entry_date=pd_.dates[buy_idx], entry_price=buy_price,
            exit_date=pd_.dates[er.sell_idx], exit_price=er.sell_price,
            qty=notional / buy_price, exit_reason=er.reason,
            return_pct=round((er.sell_price / buy_price - 1) * 100, 2),
            days_held=er.sell_idx - buy_idx,
            peak_return_pct=round((er.peak_close / buy_price - 1) * 100, 2),
            days_to_peak=er.days_to_peak,
            mae_pct=round((er.trough_low / buy_price - 1) * 100, 2),
            mae_before_peak_pct=round((er.trough_before_peak / buy_price - 1) * 100, 2),
            features=sig["features"],
        ))
    return trades


def run_scan(df, strategy, *, ticker, start=None, end=None, notional: float = 1000.0,
             exit_rule: Optional[ExitRule] = None) -> list[TradeRecord]:
    """Collect every setup and simulate each under ``exit_rule`` (default =
    the live engine's stop + sell-in-weakness, built from the strategy's params)."""
    if exit_rule is None:
        exit_rule = StopAndWeaknessExit(
            ma_type=getattr(strategy, "trade_ma_type", "21ema"),
            allow_rising_close_exception=getattr(strategy, "allow_rising_close_exception", True),
            max_loss_pct=getattr(strategy, "max_loss_pct", 0.04),
        )
    setups = collect_setups(df, strategy, ticker=ticker, start=start, end=end)
    return apply_exit(setups, df, exit_rule, ticker=ticker, notional=notional)
