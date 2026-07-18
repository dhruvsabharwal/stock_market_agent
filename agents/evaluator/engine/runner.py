"""The backtest runner — drives the feed → strategy → broker loop for one ticker.

Per-bar sequence (the no-lookahead ordering):
  1. ``broker.on_bar(bar)`` — fill orders queued last bar at this open, check
     stops, mark equity. (Uses only this bar + prior state.)
  2. ``strategy._ingest(bar)`` then ``strategy.on_bar(bar, ctx)`` — the strategy
     observes the just-closed bar and may queue orders for the *next* open.

So a decision on bar T fills on T+1, and the strategy never sees T+1 while
deciding T. At the end, any open position is closed at the last bar's close.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .broker import Broker
from .feed import BarFeed
from .records import TradeRecord
from ..strategy.base import Strategy, StrategyContext


@dataclass
class BacktestResult:
    ticker: str
    trades: list[TradeRecord]
    equity_curve: pd.Series          # indexed by date
    start_cash: float

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1]) if len(self.equity_curve) else self.start_cash


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    *,
    ticker: str,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
    cash: float = 10_000.0,
) -> BacktestResult:
    """Run ``strategy`` over ``df`` (single-ticker OHLCV) within [start, end].

    Warm-up: if ``strategy.warmup_bars`` > 0, bars before ``start`` are fed to
    the strategy (so rolling state is primed) but trading is suppressed until
    the simulation window begins.
    """
    df = df.sort_index()
    sim_start = pd.Timestamp(start) if start is not None else None

    # Build the warm-up + simulation feed. We feed everything from
    # (start - warmup) onward so the strategy can prime its state, but the
    # broker only acts from sim_start.
    if sim_start is not None and strategy.warmup_bars > 0:
        pre = df[df.index < sim_start]
        warm = pre.iloc[-strategy.warmup_bars:] if len(pre) else pre
        feed_df = pd.concat([warm, df[df.index >= sim_start]])
        feed_df = feed_df[~feed_df.index.duplicated(keep="first")]
    else:
        feed_df = df
    feed = BarFeed(feed_df, ticker=ticker, end=end)

    broker = Broker(ticker, cash=cash)
    ctx = StrategyContext(broker, ticker)

    last_bar = None
    for bar in feed:
        in_window = sim_start is None or bar.date >= sim_start
        # The broker only acts inside the simulation window. During warm-up the
        # strategy still observes bars (priming its state) but ctx.enabled is
        # False, so any orders it tries to place are ignored.
        if in_window:
            broker.on_bar(bar)
        strategy._ingest(bar)
        ctx.bar = bar
        ctx.enabled = in_window
        strategy.on_bar(bar, ctx)
        last_bar = bar

    if last_bar is not None:
        broker.force_close(last_bar)

    eq = pd.Series(
        {d: v for d, v in broker.equity_curve}, name="equity"
    ).sort_index()
    return BacktestResult(
        ticker=ticker, trades=broker.trades, equity_curve=eq, start_cash=cash
    )
