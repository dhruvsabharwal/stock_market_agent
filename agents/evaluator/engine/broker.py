"""A simple long-only single-position broker for one ticker.

Fill model (realistic, leak-free):
  * A market order queued by the strategy on bar T fills at the OPEN of T+1.
  * A protective stop attached to an open position is monitored intrabar: if a
    bar's low pierces the stop it fills at the stop price (or the open, if the
    bar gapped below the stop).

Decisions are made on closed bars; fills happen on the next bar — so the broker
never uses information the strategy could not have had.

One position at a time per ticker (matches the range-expansion trade model).
Position sizing is "all-in" notional by default; the eval layer can normalise
returns per-trade, so absolute cash size is not important for the metrics we
care about first.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .records import Bar, Fill, Order, TradeRecord
from ..logging_util import logger


class Broker:
    def __init__(self, ticker: str, *, cash: float = 10_000.0):
        self.ticker = ticker
        self.cash = cash
        self.start_cash = cash
        # Open position state
        self._qty: float = 0.0
        self._entry_price: Optional[float] = None
        self._entry_date: Optional[pd.Timestamp] = None
        self._entry_features: dict = {}
        self._stop: Optional[float] = None
        self._peak_close: Optional[float] = None   # for peak_return / MAE tracking
        self._trough_low: Optional[float] = None
        self._days_held: int = 0
        self._days_to_peak: int = 0
        # Queued for next open
        self._pending: Optional[Order] = None
        # Outputs
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[tuple[pd.Timestamp, float]] = []

    # ── strategy-facing API (called from the StrategyContext) ─────────────────
    @property
    def in_position(self) -> bool:
        return self._qty > 0

    @property
    def position_qty(self) -> float:
        return self._qty

    @property
    def entry_price(self) -> Optional[float]:
        return self._entry_price

    def submit(self, order: Order) -> None:
        """Queue an order to fill at the next bar's open."""
        self._pending = order

    def set_stop(self, price: Optional[float]) -> None:
        """Set/adjust the protective stop on the open position."""
        self._stop = price

    # ── engine-facing per-bar processing ─────────────────────────────────────
    def on_bar(self, bar: Bar) -> None:
        """Process fills/stops for ``bar`` BEFORE the strategy sees it.

        Order of events within a bar:
          1. Fill any order queued on the previous bar at this bar's open.
          2. If still in a position, check the protective stop against the
             bar's low (intrabar).
          3. Update peak/trough excursions and mark equity at the close.
        """
        # 1. fills at the open
        if self._pending is not None:
            self._execute(self._pending, bar, price=bar.open)
            self._pending = None

        # 2. intrabar stop
        if self.in_position and self._stop is not None and bar.low <= self._stop:
            fill_price = min(self._stop, bar.open)  # gap-down fills at the open
            self._close(bar, fill_price, reason="stop_loss")

        # 3. excursion tracking + mark-to-market
        if self.in_position:
            self._days_held += 1
            if self._peak_close is None or bar.close > self._peak_close:
                self._peak_close = bar.close
                self._days_to_peak = self._days_held
            if self._trough_low is None or bar.low < self._trough_low:
                self._trough_low = bar.low

        self.equity_curve.append((bar.date, self._mark(bar.close)))

    def force_close(self, bar: Bar) -> None:
        """Close any open position at the final bar's close (end of data)."""
        if self.in_position:
            self._close(bar, bar.close, reason="end_of_data")

    # ── internals ─────────────────────────────────────────────────────────────
    def _mark(self, price: float) -> float:
        return self.cash + self._qty * price

    @staticmethod
    def _finalize_stop(order: Order, *, fill: float) -> Optional[float]:
        """Resolve the protective stop at fill time (see Order docstring)."""
        if order.stop_price is not None:
            return order.stop_price
        criteria = order.stop_criteria_price
        if criteria is None and order.stop_constant_pct is not None:
            criteria = fill * (1 - order.stop_constant_pct)
        cap = fill * (1 - order.stop_max_loss_pct) if order.stop_max_loss_pct is not None else None
        # A stop must be below the fill; drop any candidate >= fill (gap-up-fade
        # breakouts) and fall back to the max-loss stop.
        levels = [x for x in (criteria, cap) if x is not None and x < fill]
        return max(levels) if levels else None  # tighter (higher) stop wins

    def _execute(self, order: Order, bar: Bar, price: float) -> None:
        if order.side == "buy" and not self.in_position:
            qty = self.cash / price if price > 0 else 0.0
            if qty <= 0:
                return
            self.cash -= qty * price
            self._qty = qty
            self._entry_price = price
            self._entry_date = bar.date
            self._entry_features = dict(order.features)
            self._stop = self._finalize_stop(order, fill=price)
            logger.info(
                "%-6s BUY  %s @ %.4f  stop=%s  (%s)",
                self.ticker, bar.date.date(), price,
                f"{self._stop:.4f}" if self._stop is not None else "none",
                order.reason or "signal",
            )
            self._peak_close = bar.close
            self._trough_low = bar.low
            self._days_held = 0
            self._days_to_peak = 0
        elif order.side == "sell" and self.in_position:
            self._close(bar, price, reason=order.reason or "signal")

    def _close(self, bar: Bar, price: float, reason: str) -> None:
        entry = self._entry_price or price
        self.cash += self._qty * price
        ret = (price / entry - 1.0) * 100 if entry else 0.0
        peak_ret = (self._peak_close / entry - 1.0) * 100 if entry and self._peak_close else None
        mae = (self._trough_low / entry - 1.0) * 100 if entry and self._trough_low else None
        self.trades.append(
            TradeRecord(
                ticker=self.ticker,
                entry_date=self._entry_date,
                entry_price=entry,
                exit_date=bar.date,
                exit_price=price,
                qty=self._qty,
                exit_reason=reason,
                return_pct=round(ret, 2),
                days_held=self._days_held,
                peak_return_pct=round(peak_ret, 2) if peak_ret is not None else None,
                days_to_peak=self._days_to_peak,
                mae_pct=round(mae, 2) if mae is not None else None,
                features=self._entry_features,
            )
        )
        logger.info(
            "%-6s SELL %s @ %.4f  ret=%+.2f%%  held=%dd  (%s)",
            self.ticker, bar.date.date(), price, ret,
            self._days_held, reason,
        )
        # reset position state
        self._qty = 0.0
        self._entry_price = None
        self._entry_date = None
        self._entry_features = {}
        self._stop = None
        self._peak_close = None
        self._trough_low = None
        self._days_held = 0
        self._days_to_peak = 0
