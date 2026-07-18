"""Canonical data records shared across the evaluator.

These dataclasses are the *contract* between the engine, the eval layer, and
the probabilities layer. A single backtest run produces a list of
``TradeRecord``s (the ledger); eval summarises it over 2015-2026 and
probabilities aggregates it over 1990-2015.

Design rule (no lookahead): a ``Bar`` only ever carries data known at its own
close. The engine never hands a strategy anything dated after the current bar.
Outcome fields on ``TradeRecord`` (``return_pct``, ``peak_return_pct``,
``mae_pct``) are computed by the engine *after* the trade closes and are LABELS
only — they must never be used as probability lookup keys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ── Market data ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. All fields are known at ``date``'s close."""

    date: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_row(cls, date: pd.Timestamp, row) -> "Bar":
        return cls(
            date=pd.Timestamp(date),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row.get("Volume", 0.0) or 0.0),
        )


# ── Orders & fills ──────────────────────────────────────────────────────────--
@dataclass
class Order:
    """A market order queued by a strategy on bar T, filled at T+1 open.

    ``features`` is an arbitrary snapshot of the setup context the strategy
    knew at entry (stage, range height, ETF stages, ...). It is carried onto
    the resulting ``TradeRecord`` and becomes the conditioning data for the
    probabilities DB. Only past-known values belong here.
    """

    side: str                       # "buy" or "sell"
    reason: str = ""                # free-text, e.g. "range_expansion", "stop_loss"
    features: dict = field(default_factory=dict)
    # Optional protective stop, set on a buy order, monitored intrabar.
    #   * ``stop_price``: a fixed level known at order time.
    #   * stop *spec*: when the final level depends on the fill price (e.g. a
    #     max-loss cap), the broker finalizes it AT FILL so the stop is active
    #     on the entry bar itself. Mirrors base_identification.build_stop_loss:
    #         criteria = stop_criteria_price OR fill*(1 - stop_constant_pct)
    #         final    = max(criteria, fill*(1 - stop_max_loss_pct))   # tighter wins
    stop_price: Optional[float] = None
    stop_criteria_price: Optional[float] = None
    stop_constant_pct: Optional[float] = None
    stop_max_loss_pct: Optional[float] = None

    def has_stop_spec(self) -> bool:
        return any(v is not None for v in (
            self.stop_price, self.stop_criteria_price,
            self.stop_constant_pct, self.stop_max_loss_pct,
        ))


@dataclass
class Fill:
    date: pd.Timestamp
    side: str
    price: float
    qty: float
    reason: str = ""


# ── The ledger row ──────────────────────────────────────────────────────────--
@dataclass
class TradeRecord:
    """One completed round-trip trade. The canonical ledger row.

    Mirrors the columns of the existing ``results/trades_*.csv`` so eval and
    probabilities feel familiar. ``features`` holds the entry-time setup snapshot
    (past-only). Everything else with ``_pct`` / ``days_`` is an outcome label.
    """

    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    qty: float = 0.0
    exit_reason: str = ""           # "stop_loss", "weakness", "stage3", "end_of_data", ...
    # ── outcome labels (engine-computed; never a probability key) ────────────
    return_pct: Optional[float] = None
    days_held: Optional[int] = None
    peak_return_pct: Optional[float] = None   # best unrealised gain during the hold
    days_to_peak: Optional[int] = None
    mae_pct: Optional[float] = None           # worst unrealised loss (max adverse excursion)
    mae_before_peak_pct: Optional[float] = None  # worst dip BEFORE the peak (heat before reward)
    # ── entry-time setup snapshot (the conditioning features) ────────────────
    features: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.exit_date is None

    def to_dict(self) -> dict:
        d = {
            "ticker": self.ticker,
            "entry_date": _fmt(self.entry_date),
            "entry_price": self.entry_price,
            "exit_date": _fmt(self.exit_date),
            "exit_price": self.exit_price,
            "qty": self.qty,
            "exit_reason": self.exit_reason,
            "return_pct": self.return_pct,
            "days_held": self.days_held,
            "peak_return_pct": self.peak_return_pct,
            "days_to_peak": self.days_to_peak,
            "mae_pct": self.mae_pct,
            "mae_before_peak_pct": self.mae_before_peak_pct,
        }
        # Flatten the entry-feature snapshot into top-level columns.
        for k, v in self.features.items():
            d[k] = v
        return d


def _fmt(ts) -> Optional[str]:
    if ts is None:
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")
