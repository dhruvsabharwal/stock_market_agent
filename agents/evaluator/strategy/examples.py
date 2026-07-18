"""Example / test-fixture strategies.

These are NOT real trading strategies — real ones (stage + range ports) are
written in a later phase. They exist to validate the engine end-to-end.
"""
from __future__ import annotations

from ..engine.records import Bar
from .base import Strategy, StrategyContext


class BuyAndHold(Strategy):
    """Buy on the first in-window bar, hold to the end. Sanity-checks fill math
    and the equity curve against a simple buy-and-hold return."""

    name = "buy_and_hold"

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if not ctx.in_position:
            ctx.buy(reason="initial", features={"strategy": self.name})


class StopAtFivePct(Strategy):
    """Buy and hold with a fixed 5% trailing-from-entry stop. Exercises the
    broker's intrabar stop handling."""

    name = "stop_5pct"

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if not ctx.in_position:
            ctx.buy(reason="initial", features={"strategy": self.name})
        elif ctx.entry_price is not None:
            ctx.set_stop(ctx.entry_price * 0.95)
