"""Strategy base class + the context handed to a strategy each bar.

A strategy is *stateful* and *event-driven*: the engine calls ``on_bar`` once
per bar, strictly forward in time. The strategy may accumulate its own history
(the base class maintains ``self.history`` for convenience) and keep any rolling
state it likes — but it is only ever given bars up to the current one, so it
**cannot** look ahead no matter how it is written.

The one rule: a strategy uses only what it is fed through the context (and its
own accumulated state). It does no I/O of its own — no re-downloading data.

How a strategy computes its features internally is its own business:
  * incrementally (update O(1) per bar), or
  * by recomputing a batch function over ``self.history`` (its own past buffer).
Both are leak-free because ``self.history`` only ever holds bars <= T.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from ..engine.records import Bar, Order


class StrategyContext:
    """The strategy's only handle to the world. Wraps the broker so a strategy
    can act without touching engine internals or any future data."""

    def __init__(self, broker, ticker: str):
        self._broker = broker
        self.ticker = ticker
        self.bar: Optional[Bar] = None  # set by the runner each bar
        #: False during warm-up — strategy state still updates but orders are
        #: ignored, so warm-up bars can prime rolling state without trading.
        self.enabled: bool = True

    # ── position queries ──────────────────────────────────────────────────────
    @property
    def in_position(self) -> bool:
        return self._broker.in_position

    @property
    def entry_price(self) -> Optional[float]:
        return self._broker.entry_price

    # ── actions (fill at next bar's open) ─────────────────────────────────────
    def buy(self, *, reason: str = "", features: Optional[dict] = None,
            stop_price: Optional[float] = None,
            stop_criteria_price: Optional[float] = None,
            stop_constant_pct: Optional[float] = None,
            stop_max_loss_pct: Optional[float] = None) -> None:
        if not self.enabled:
            return
        self._broker.submit(Order(
            side="buy", reason=reason, features=features or {},
            stop_price=stop_price, stop_criteria_price=stop_criteria_price,
            stop_constant_pct=stop_constant_pct, stop_max_loss_pct=stop_max_loss_pct,
        ))

    def sell(self, *, reason: str = "") -> None:
        if not self.enabled:
            return
        self._broker.submit(Order(side="sell", reason=reason))

    def set_stop(self, price: Optional[float]) -> None:
        if not self.enabled:
            return
        self._broker.set_stop(price)


class Strategy(ABC):
    """Subclass and implement ``on_bar``. Register a factory with the eval layer
    when ready (strategies are written in a later phase)."""

    #: Human-readable name used in result folders / config naming.
    name: str = "base"
    #: Bars of history to feed before the simulation window starts (warm-up).
    warmup_bars: int = 0

    def __init__(self, **params):
        self.params = params
        self.history: list[Bar] = []   # accumulated past bars (<= current T)
        #: scan mode — record every setup as an independent signal instead of
        #: taking a single live position (see engine.scan.run_scan).
        self._scan: bool = False
        self.scan_signals: list = []

    # ── engine hook ────────────────────────────────────────────────────────--
    def _ingest(self, bar: Bar) -> None:
        """Called by the runner before ``on_bar`` to grow the past buffer."""
        self.history.append(bar)

    @abstractmethod
    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        """React to a newly-closed ``bar``. Queue orders via ``ctx``.

        Anything decided here uses only ``bar`` and prior state — the next bar
        does not exist yet.
        """
        raise NotImplementedError

    # ── convenience: the strategy's own past as a DataFrame ──────────────────
    def history_df(self) -> pd.DataFrame:
        """Past bars (<= current T) as an OHLCV frame, for strategies that want
        to recompute a batch function over their own buffer. Leak-free: this
        only ever contains bars already delivered."""
        if not self.history:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        idx = [b.date for b in self.history]
        return pd.DataFrame(
            {
                "Open": [b.open for b in self.history],
                "High": [b.high for b in self.history],
                "Low": [b.low for b in self.history],
                "Close": [b.close for b in self.history],
                "Volume": [b.volume for b in self.history],
            },
            index=pd.DatetimeIndex(idx),
        )
