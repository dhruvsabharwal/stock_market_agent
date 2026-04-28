"""
Base Identification Module
==========================
Standalone module for finding ALL bases in a ticker's daily price history.

Uses daily OHLCV data exclusively.  No network calls — pass DataFrames in.

Key design principles (from Richard Moglen / TraderLion):
  - A base begins when a daily High is not overcome for at least 10 trading days.
  - Spike tolerance (Moglen "85-95 % rule"):  Brief spikes (< ~2 %) above the
    current base high that immediately reverse are treated as noise — they do NOT
    start a new base.  Instead they get absorbed into the existing base (the
    base_high is updated to include them).
  - A breakout = price **closes** meaningfully (> min_breakout_pct) above the
    base_high (the absolute high of the base, including any absorbed spikes).
  - The effective_pivot (95th percentile of daily Highs) is stored as a reference
    for where the bulk of price action sits, but breakout is judged vs base_high.
  - Minimum base length: 25 trading days (≈ 5 weeks).
  - Prior uptrend of >= 20 % feeding into the base is required for quality bases.
  - Bases are counted in stages; later stages carry higher failure risk.

Usage
-----
    import pandas as pd, yfinance as yf
    daily = yf.download("AAPL", period="2y", interval="1d")
    from base_identification import find_all_bases
    bases = find_all_bases(daily)
    for b in bases:
        print(b)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_scalar(val):
    """Safely convert numpy / pandas scalar to Python float."""
    if hasattr(val, "item"):
        return float(val.item())
    return float(val)


def _pct_change(a: float, b: float) -> Optional[float]:
    """Return (a / b - 1) * 100, or None on division issues."""
    try:
        if b is None or b == 0 or math.isnan(b):
            return None
        r = (a / b - 1) * 100
        return None if math.isnan(r) or math.isinf(r) else round(r, 2)
    except Exception:
        return None


def _safe_loc(daily: pd.DataFrame, date) -> int:
    """Get integer location for a date, handling slice results."""
    loc = daily.index.get_loc(date)
    if isinstance(loc, slice):
        return loc.start
    return loc


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ThreeWeeksTight:
    """A Weeks Tight pattern — N consecutive weekly closes within 1.5%.

    Classic 3WT requires 3 weeks; this generalises to any run ≥ 3 weeks.
    Longer runs (4WT, 5WT …) are more significant.
    """

    start_date: pd.Timestamp
    end_date: pd.Timestamp
    num_weeks: int                     # length of the tight run (≥ 3)
    closes: list                       # the N weekly close values
    spread_pct: float                  # (max - min) / max * 100

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class VolumeSignature:
    """A significant volume event (HVE, HV1, HVIPO) within or near a base."""

    date: pd.Timestamp
    signature_type: str                # "HVE", "HV1", "HVIPO"
    volume: float                      # the volume on that day
    gap_up_pct: Optional[float] = None  # gap up % from prior close (None if no gap)
    is_gap_up: bool = False            # True if opened > prior close by >= 1%

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class PocketPivot:
    """A 10-day pocket pivot — up-day volume > max down-day volume of prior 10 days."""

    date: pd.Timestamp
    volume: float
    max_down_volume_10d: float         # the threshold it exceeded
    close: float
    above_ema_21: bool = False         # was this pocket pivot above the 21 EMA?

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class PrimingPattern:
    """A short-term pattern that signals a range is ready to break out (Section 8)."""

    date: pd.Timestamp
    pattern_type: str                  # "inside_day", "upside_reversal",
                                       # "positive_expectation_breaker", "tight_setup_day"
    buy_point: float                   # actionable price level for entry
    near_pivot_level: Optional[float] = None   # nearest pivot level (if within 3%)
    near_pivot_type: Optional[str] = None      # "base", "consolidation", or "range"
    on_right_side: bool = False        # True if after base_low_date

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class StopLossHit:
    """Exit produced when the initial stop loss is triggered.

    The stop fires intraday on any bar whose Low ≤ stop_price.  Fill price is
    ``min(Open, stop_price)`` — if the stock gapped below the stop, the fill
    is the worse open; otherwise the fill is the stop price.
    """

    hit_date: pd.Timestamp
    sell_date: pd.Timestamp             # same calendar day as hit_date
    sell_price: float                   # fill (gap-adjusted)
    stop_price: float                   # the stop that was hit
    gap_down: bool = False              # True when Open ≤ stop_price

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class SellInWeakness:
    """Exit signal produced by the sell-in-weakness rule.

    Logic: scan forward after the trade is opened.  When the close is below
    the chosen MA (21 EMA or 50 SMA) on two consecutive days, emit a sell on
    the OPEN of day 3 (the day after the second weakness day), regardless of
    where that open sits relative to the MA.

    Rising-close exception (optional): if both weakness days close below the
    MA but day2_close > day1_close, the signal is vetoed.  Scanning resumes
    from day 2 (rolling window of 2), so day2+day3 can still trigger.
    """

    weakness_day1_date: pd.Timestamp
    weakness_day1_close: float
    weakness_day2_date: pd.Timestamp
    weakness_day2_close: float
    sell_date: pd.Timestamp
    sell_price: float                   # open of day 3 (day after 2nd weakness day)
    ma_type: str = "21ema"              # "21ema" or "50sma"
    allow_rising_close_exception: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class StopLoss:
    """Initial stop loss for a trade.

    Five modes, selected by ``stop_type``:
      - ``"range_low"``      — stop below Low of the expansion day
      - ``"expansion_open"`` — stop below Open of the expansion day  (default)
      - ``"21ema"``          — stop below 21 EMA on the expansion day
      - ``"50sma"``          — stop below 50 SMA on the expansion day
      - ``"constant_pct"``   — stop = buy_price × (1 − constant_pct)

    All ``*_pct`` fields are **fractions**, not percentages (0.02 = 2 %).

    For the level-based modes, the criteria stop is
    ``reference_price × (1 − buffer_pct)``.
    For ``"constant_pct"`` the criteria stop is ``buy_price × (1 − constant_pct)``.

    A hard loss cap ``max_loss_pct`` is then applied: the final ``stop_price``
    is the TIGHTER (higher price) of the criteria stop and
    ``buy_price × (1 − max_loss_pct)``.  ``capped_by_max_loss`` records
    whether the cap was binding.
    """

    stop_type: str                      # see docstring
    reference_price: float              # the level before any buffer
    stop_price: float                   # final stop (after buffer AND max-loss cap)
    buffer_pct: float = 0.02            # fraction below reference (ignored for constant_pct)
    constant_pct: float = 0.03          # fraction (only used when stop_type == "constant_pct")
    max_loss_pct: float = 0.04          # hard loss cap as a fraction of buy_price
    capped_by_max_loss: bool = False    # True if max-loss floor was the binding stop

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Buy:
    """A buy entry paired with its initial stop loss.

    A trade always has a buy point AND a stop loss — they travel together.
    """

    date: pd.Timestamp
    price: float
    stop_loss: StopLoss

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        if isinstance(self.stop_loss, StopLoss):
            d["stop_loss"] = self.stop_loss.to_dict()
        return d


@dataclass
class RangeExpansionTrade:
    """A paper trade built from an up RangeExpansion.

    Entry: OPEN of the day after the expansion day (carried in ``buy``).
    Stop : initial stop loss (carried in ``buy.stop_loss``).
    Exit : SellInWeakness rule (see that class).  If no signal fires inside
           the available data, ``sell`` stays None (trade still open).
    """

    buy: Buy
    # Whichever fires first: SellInWeakness or StopLossHit
    sell: Optional[object] = None
    sell_reason: Optional[str] = None   # "weakness" or "stop_loss"
    return_pct: Optional[float] = None  # (sell_price - buy_price) / buy_price * 100
    days_held: Optional[int] = None     # trading days between buy.date and sell_date
    ma_type: str = "21ema"              # echoes config used to build the trade
    allow_rising_close_exception: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        if isinstance(self.buy, Buy):
            d["buy"] = self.buy.to_dict()
        if isinstance(self.sell, (SellInWeakness, StopLossHit)):
            d["sell"] = self.sell.to_dict()
        return d


@dataclass
class RangeExpansion:
    """The breakout move that follows a Range — the first day price exits
    the box by at least the required expansion_pct."""

    date: pd.Timestamp
    direction: str                     # "up" or "down"
    close: float                       # closing price on the expansion day
    day_chg_pct: float = 0.0          # (close - prev_close) / prev_close * 100  ← primary threshold
    move_pct: float = 0.0             # % move of close from the box high/low (reference)
    day_high: float = 0.0              # High of the expansion day
    day_low: float = 0.0               # Low of the expansion day
    day_range_pct: float = 0.0         # (day_high - day_low) / day_low * 100
    closing_range_pct: float = 0.0     # where the close sits in the day's range
                                       # 100 = closed at the high, 0 = closed at the low
    strong_close: bool = False         # True if close is in top close_threshold_pct of day range

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class Range:
    """A price box: a period where price trades within a defined height %.

    Can be defined using High/Low or Open/Close columns (controlled by
    the `price_mode` parameter when detecting ranges).

    Usage (standalone, no base needed)::

        from base_identification import find_ranges
        ranges = find_ranges(daily, box_pct=3.0, min_days=3, expansion_pct=4.0)
    """

    start_date: pd.Timestamp
    end_date: pd.Timestamp
    high: float                        # top of the box
    low: float                         # bottom of the box
    height_pct: float                  # (high - low) / low * 100
    length_days: int                   # trading days inside the box
    price_mode: str = "high_low"       # "high_low" or "open_close"
    expansion: Optional[RangeExpansion] = None  # the breakout after the range
    trade: Optional[RangeExpansionTrade] = None  # built only when an up expansion exists
    priming_patterns: list = field(default_factory=list)  # list of PrimingPattern within the range
    # MA context at range end
    above_50dma: bool = False           # range end close > 50-day SMA
    above_21ema: bool = False           # range end close > 21-day EMA
    slope_200dma: Optional[float] = None  # (200d SMA now - 5d ago) / 5d ago * 100
    slope_50dma: Optional[float] = None   # (50d SMA now - 5d ago) / 5d ago * 100
    slope_21ema: Optional[float] = None   # (21d EMA now - 5d ago) / 5d ago * 100

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
            elif isinstance(v, list):
                d[k] = [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in v
                ]
        # Nested dataclasses: use their to_dict so timestamps inside serialize.
        if isinstance(self.expansion, RangeExpansion):
            d["expansion"] = self.expansion.to_dict()
        if isinstance(self.trade, RangeExpansionTrade):
            d["trade"] = self.trade.to_dict()
        return d


@dataclass
class PivotPoint:
    """A horizontal resistance level within a base (Section 7)."""

    level: float                       # the price level of the pivot
    pivot_type: str                    # "base", "consolidation", or "range"
    start_date: pd.Timestamp           # first date that defines this pivot
    end_date: pd.Timestamp             # last date that defines this pivot
    num_days: int = 1                  # trading days the pivot/range spans
    num_tests: int = 1                 # how many times price tested this level
    near_21ema: bool = False           # pivot overlaps with 21 EMA
    near_base_high: bool = False       # within 2% of the base_high
    confluence_with: list = field(default_factory=list)  # pivot_types this lines up with

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class TightArea:
    """A short-term consolidation (tight range) within a base."""

    start_date: pd.Timestamp
    end_date: pd.Timestamp
    length_days: int
    high: float                       # high of the range
    low: float                        # low of the range
    range_pct: float                  # (high - low) / high * 100
    avg_volume_ratio: float           # avg volume in tight area / 20d avg volume before it
    volume_declining: bool            # volume in tight area below 20d avg
    near_21ema: bool                  # tight area overlaps with 21 EMA
    near_50dma: bool                  # tight area overlaps with 50 DMA
    near_10dma: bool                  # tight area overlaps with 10 DMA

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
        return d


@dataclass
class Base:
    """One identified base / consolidation."""

    # Core geometry
    start_date: pd.Timestamp          # date of the high that starts the base
    end_date: pd.Timestamp            # breakout date, or last available date if still active
    base_high: float                   # absolute highest High in the base
    base_high_date: pd.Timestamp
    base_low: float                    # absolute lowest Low in the base
    base_low_date: pd.Timestamp
    depth_pct: float                   # (base_low - base_high) / base_high * 100  (negative)

    # Effective pivot (Moglen 85-95 % rule)
    effective_pivot: float             # ~95th-percentile of daily Highs in the base
    pivot_tolerance_pct: float         # how far the absolute high is above the effective pivot

    # Dimensions
    length_days: int                   # trading days from start to end
    length_weeks: float                # length_days / 5

    # Prior uptrend (Section 4.2)
    prior_uptrend_pct: Optional[float] = None   # % move from prior trough to base_high
    prior_trough: Optional[float] = None
    prior_trough_date: Optional[pd.Timestamp] = None
    prior_uptrend_sufficient: bool = False       # True if >= 20%

    # Base depth vs market drawdown (Section 4.1)
    market_drawdown_pct: Optional[float] = None  # market drawdown during same period
    depth_vs_market_ratio: Optional[float] = None  # abs(depth) / abs(market_drawdown)
    depth_vs_market_passes: bool = False           # True if ratio <= 2.5

    # Status
    active: bool = False               # True if base has not yet broken out
    breakout_confirmed: bool = False

    # Stage & pattern (Section 4.3)
    stage_number: Optional[int] = None
    early_stage: bool = False          # True if stage 1-3
    pattern: str = "Unclear"
    handle_detected: bool = False      # for Cup with Handle: was a handle found?

    # VCP specifics
    vcp_swings: list = field(default_factory=list)
    vcp_contracting: bool = False

    # Accumulation & volume signatures (Section 5)
    accum_weeks: int = 0               # weeks with accumulation (5.1)
    distrib_weeks: int = 0             # weeks with distribution (5.1)
    accum_distrib_ratio: Optional[float] = None  # accum / distrib (> 1 = bullish)
    accum_passes: bool = False         # accum_weeks > distrib_weeks
    three_weeks_tight: list = field(default_factory=list)  # list of ThreeWeeksTight (5.2)
    volume_signatures: list = field(default_factory=list)  # list of VolumeSignature (5.3)
    daily_accum_days: int = 0          # up days with above-avg volume, right side (5.4)
    daily_distrib_days: int = 0        # down days with above-avg volume, right side (5.4)
    daily_accum_ratio: Optional[float] = None
    daily_accum_passes: bool = False
    #pocket_pivots: list = field(default_factory=list)  # list of PocketPivot (5.5)

    # Pivot points within the base (Section 7)
    pivots: list = field(default_factory=list)  # list of PivotPoint
    has_pivot_confluence: bool = False  # multiple pivot types line up near same level

    # Priming patterns (Section 8)
    priming_patterns: list = field(default_factory=list)  # list of PrimingPattern
    primed: bool = False               # any priming pattern found near a pivot on right side

    # Ranges within the base
    ranges: list = field(default_factory=list)  # list of Range

    # Tight areas within the base (Section 6)
    tight_areas: list = field(default_factory=list)  # list of TightArea
    overall_tightening: bool = False   # is the base getting tighter over time?
    rmv_at_end: Optional[float] = None  # Relative Measured Volatility at base end

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, pd.Timestamp):
                d[k] = v.strftime("%Y-%m-%d")
            elif isinstance(v, list):
                d[k] = [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in v
                ]
        return d


# ── Step 1: Find significant highs ──────────────────────────────────────────

def _find_significant_highs(
    daily: pd.DataFrame,
    confirmation_days: int = 10,
) -> list[tuple[pd.Timestamp, float]]:
    """
    A "significant high" = a daily High that is not exceeded for at least
    `confirmation_days` trading days afterwards.

    Returns list of (date, high_value) sorted chronologically.
    """
    highs = daily["High"].values
    dates = daily.index
    n = len(highs)
    sig = []

    i = 0
    while i < n:
        h = highs[i]
        # Look forward: is this high not exceeded for `confirmation_days`?
        exceeded = False
        j = i + 1
        days_held = 0
        while j < n and days_held < confirmation_days:
            if highs[j] > h:
                exceeded = True
                break
            days_held += 1
            j += 1

        if not exceeded and days_held >= confirmation_days:
            sig.append((dates[i], _to_scalar(h)))
            # Skip ahead past the confirmation window to avoid duplicates
            # from the same local peak region
            i = j
        else:
            if exceeded:
                # Jump to the day that exceeded — it might be a sig high itself
                i = j
            else:
                # Reached end of data before confirmation_days elapsed.
                # This high hasn't been exceeded yet — could be the start of
                # an active base.
                sig.append((dates[i], _to_scalar(h)))
                break

    return sig


# ── Step 2: Build raw bases from significant highs ──────────────────────────

def _compute_effective_pivot(
    daily_slice: pd.DataFrame,
    percentile: float = 95.0,
) -> float:
    """
    Compute the level where ~95 % of daily Highs within the base sit.
    Stored as a reference metric (where the bulk of price action lives).
    Breakout is judged against base_high, not this value.
    """
    return _to_scalar(np.percentile(daily_slice["High"].values, percentile))


def _is_breakout(
    daily: pd.DataFrame,
    start_idx: int,
    base_high: float,
    min_breakout_pct: float = 2.0,
) -> Optional[tuple[pd.Timestamp, str]]:
    """
    Scan forward from `start_idx` looking for a confirmed breakout above
    `base_high` (the absolute high of the base).

    Breakout is confirmed when price **closes** more than `min_breakout_pct` %
    above the base_high.  A close that's only marginally above (< min_breakout_pct)
    is treated as noise — the base is still intact.

    Returns (breakout_date, "close_above_base_high") or None.
    """
    closes = daily["Close"].values
    dates = daily.index
    n = len(closes)
    threshold = base_high * (1 + min_breakout_pct / 100)

    for i in range(start_idx, n):
        c = _to_scalar(closes[i])
        if c > threshold:
            return (dates[i], "close_above_base_high")

    return None


def _build_raw_bases(
    daily: pd.DataFrame,
    sig_highs: list[tuple[pd.Timestamp, float]],
    min_length_days: int = 25,
    pivot_percentile: float = 95.0,
    spike_tolerance_pct: float = 2.0,
    min_breakout_pct: float = 2.0,
) -> list[Base]:
    """
    For each significant high, attempt to define a base.

    Spike tolerance (Moglen "85-95 %" insight): if a later significant high
    is within `spike_tolerance_pct` of the current base_high, it is noise —
    it gets absorbed into the same base (base_high is updated).  If it is
    meaningfully higher (> spike_tolerance_pct above base_high), this base
    has been broken and the new high starts a fresh base.

    Breakout: price must **close** more than `min_breakout_pct` above
    base_high (absolute high including absorbed spikes).
    """
    bases: list[Base] = []
    used_through_date: Optional[pd.Timestamp] = None  # tracks how far we've consumed

    for idx, (sig_date, sig_high) in enumerate(sig_highs):
        # Skip this significant high if it falls within an already-consumed range
        if used_through_date is not None and sig_date <= used_through_date:
            continue

        # --- Determine the base span ---
        start_loc = _safe_loc(daily, sig_date)

        remaining = daily.iloc[start_loc:]
        if len(remaining) < 5:
            continue

        # Track the running base high as we absorb spikes
        base_abs_high = sig_high
        base_abs_high_date = sig_date

        # Check if any later significant high should be absorbed into this base
        for future_sig_date, future_sig_high in sig_highs[idx + 1:]:
            # Is this future high within spike tolerance of the current base_high?
            pct_above_base_high = (future_sig_high / base_abs_high - 1) * 100
            if pct_above_base_high <= spike_tolerance_pct:
                # Noise — absorb into the same base, update base_high
                if future_sig_high > base_abs_high:
                    base_abs_high = future_sig_high
                    base_abs_high_date = future_sig_date
                continue
            else:
                # Meaningfully higher — this base was broken, new base starts
                break

        # Now scan for breakout: close must be > min_breakout_pct above base_abs_high
        bo_result = _is_breakout(daily, start_loc + 1, base_abs_high, min_breakout_pct)

        if bo_result is not None:
            bo_date, bo_reason = bo_result
            bo_loc = _safe_loc(daily, bo_date)
            # The breakout day is NOT part of the base — the base ends
            # the day before the breakout.  Including the breakout day
            # would inflate base_high and distort volatility patterns.
            end_loc = bo_loc - 1
            if end_loc < start_loc:
                end_loc = start_loc  # degenerate case safety
            end_date = daily.index[end_loc]
            is_active = False
            breakout_confirmed = True
        else:
            end_date = daily.index[-1]
            end_loc = len(daily) - 1
            is_active = True
            breakout_confirmed = False

        # Final base slice
        base_slice = daily.iloc[start_loc: end_loc + 1]
        length_days = len(base_slice)

        # Skip if too short
        if length_days < min_length_days and not is_active:
            # If it's active (still forming), keep it even if short —
            # it may grow.  But completed bases must meet minimum.
            # Consume through end_date so we don't re-process these days
            used_through_date = end_date
            continue

        # Recompute effective pivot on the final base slice
        eff_pivot = _compute_effective_pivot(base_slice, pivot_percentile)

        # Base low
        base_low_val = _to_scalar(base_slice["Low"].min())
        base_low_date = base_slice["Low"].idxmin()
        if isinstance(base_low_date, pd.Series):
            base_low_date = base_low_date.iloc[0]

        # Recalculate base_abs_high from the actual base slice
        base_abs_high = _to_scalar(base_slice["High"].max())
        base_abs_high_date = base_slice["High"].idxmax()
        if isinstance(base_abs_high_date, pd.Series):
            base_abs_high_date = base_abs_high_date.iloc[0]

        depth_pct = _pct_change(base_low_val, base_abs_high)
        pivot_tol = _pct_change(base_abs_high, eff_pivot)

        base = Base(
            start_date=sig_date,
            end_date=end_date,
            base_high=round(base_abs_high, 2),
            base_high_date=base_abs_high_date,
            base_low=round(base_low_val, 2),
            base_low_date=base_low_date,
            depth_pct=depth_pct,
            effective_pivot=round(eff_pivot, 2),
            pivot_tolerance_pct=round(pivot_tol, 2) if pivot_tol is not None else 0.0,
            length_days=length_days,
            length_weeks=round(length_days / 5, 1),
            active=is_active,
            breakout_confirmed=breakout_confirmed,
        )
        bases.append(base)
        used_through_date = end_date

    return bases


# ── Step 3: Prior uptrend measurement (Section 4.2) ─────────────────────────

def _measure_prior_uptrend(
    daily: pd.DataFrame,
    base: Base,
    lookback_days: int = 252,
    min_uptrend_pct: float = 20.0,
) -> None:
    """
    Look backwards from the base start to find the prior trough
    (most recent significant low before the run-up into the base).
    Mutates `base` in place.
    """
    start_loc = _safe_loc(daily, base.start_date)

    lb_start = max(0, start_loc - lookback_days)
    prior_slice = daily.iloc[lb_start: start_loc]

    if prior_slice.empty:
        return

    # The prior trough = the lowest Low in the lookback before the base
    trough_val = _to_scalar(prior_slice["Low"].min())
    trough_date = prior_slice["Low"].idxmin()
    if isinstance(trough_date, pd.Series):
        trough_date = trough_date.iloc[0]

    uptrend_pct = _pct_change(base.base_high, trough_val)

    base.prior_trough = round(trough_val, 2)
    base.prior_trough_date = trough_date
    base.prior_uptrend_pct = uptrend_pct
    base.prior_uptrend_sufficient = (
        uptrend_pct is not None and uptrend_pct >= min_uptrend_pct
    )


# ── Step 3b: Base depth vs market drawdown (Section 4.1) ────────────────────

def _measure_depth_vs_market(
    daily: pd.DataFrame,
    base: Base,
    market_daily: Optional[pd.DataFrame],
    max_ratio: float = 2.5,
) -> None:
    """
    Compare base depth to the general market (QQQ/SPY) drawdown during
    the same period.  Mutates `base` in place.

    Rule: base depth should be < max_ratio × market drawdown.
    """
    if market_daily is None or market_daily.empty or base.depth_pct is None:
        return

    # Find market data overlapping with the base period
    mask = (market_daily.index >= base.start_date) & (market_daily.index <= base.end_date)
    mkt_slice = market_daily.loc[mask]

    if mkt_slice.empty or len(mkt_slice) < 2:
        return

    # Market drawdown = (trough / peak - 1) * 100 during base period
    mkt_high = _to_scalar(mkt_slice["High"].max())
    mkt_low = _to_scalar(mkt_slice["Low"].min())
    mkt_drawdown = _pct_change(mkt_low, mkt_high)  # negative number

    if mkt_drawdown is None or mkt_drawdown == 0:
        return

    base.market_drawdown_pct = mkt_drawdown
    ratio = abs(base.depth_pct) / abs(mkt_drawdown)
    base.depth_vs_market_ratio = round(ratio, 2)
    base.depth_vs_market_passes = ratio <= max_ratio


# ── Step 4: Base stage counting (Section 4.3) ───────────────────────────────

def _count_stages(bases: list[Base]) -> None:
    """
    Assign stage numbers to bases.

    Rules:
      - First base = Stage 1.
      - Each subsequent base after a breakout = next stage.
      - If price undercuts the low of ANY prior base in the current run,
        reset the count back to 1.

    Mutates bases in place.
    """
    if not bases:
        return

    current_stage = 1
    # Track the lowest base_low in the current stage run
    # so we can detect resets
    run_low = bases[0].base_low

    for i, base in enumerate(bases):
        if i == 0:
            base.stage_number = current_stage
            base.early_stage = True
            run_low = base.base_low
            continue

        # Check for reset: did this base's low undercut the run low?
        if base.base_low < run_low:
            # Reset — this is a deeper correction than anything in the
            # current run, suggesting a new cycle.
            current_stage = 1
            run_low = base.base_low
        else:
            # Normal progression
            current_stage += 1
            run_low = min(run_low, base.base_low)

        base.stage_number = current_stage
        base.early_stage = current_stage <= 3


# ── Step 5: Pattern classification ───────────────────────────────────────────

def _detect_handle(
    daily: pd.DataFrame,
    base: Base,
    handle_max_depth_pct: float = 15.0,
    handle_max_length_days: int = 25,
    handle_min_length_days: int = 5,
) -> bool:
    """
    Detect a handle formation in the right portion of a cup-shaped base.

    A handle is a smaller, shallower consolidation that forms:
      - In the upper half of the base (after the right side has recovered)
      - Depth < handle_max_depth_pct (shallower than the cup)
      - Length: ~1-5 weeks (handle_min_length_days to handle_max_length_days)
      - Should drift slightly downward or sideways (not rally sharply)

    Returns True if a handle is detected.
    """
    start_loc = _safe_loc(daily, base.start_date)
    end_loc = _safe_loc(daily, base.end_date)
    base_slice = daily.iloc[start_loc: end_loc + 1]
    n = len(base_slice)

    if n < 30:  # need enough data for cup + handle
        return False

    # The handle should be in the last ~20-35% of the base
    handle_search_start = int(n * 0.65)
    right_portion = base_slice.iloc[handle_search_start:]

    if len(right_portion) < handle_min_length_days:
        return False

    # The right portion should be in the upper half of the base
    base_midpoint = (base.base_high + base.base_low) / 2
    right_lows = right_portion["Low"].values
    if _to_scalar(np.min(right_lows)) < base_midpoint:
        # Handle dips below the midpoint of the base — not a valid handle
        return False

    # Find the local high in the right portion (top of right side of cup)
    right_high = _to_scalar(right_portion["High"].max())
    right_high_loc = int(np.argmax(right_portion["High"].values))

    # The handle is the consolidation AFTER this right-side high
    handle_portion = right_portion.iloc[right_high_loc:]
    if len(handle_portion) < handle_min_length_days:
        return False
    if len(handle_portion) > handle_max_length_days:
        # Use only the last handle_max_length_days
        handle_portion = handle_portion.iloc[-handle_max_length_days:]

    handle_high = _to_scalar(handle_portion["High"].max())
    handle_low = _to_scalar(handle_portion["Low"].min())
    handle_depth = _pct_change(handle_low, handle_high)

    if handle_depth is None:
        return False

    # Handle must be shallower than the cup AND within max depth
    abs_handle_depth = abs(handle_depth)
    abs_cup_depth = abs(base.depth_pct) if base.depth_pct else 999

    return (
        abs_handle_depth <= handle_max_depth_pct
        and abs_handle_depth < abs_cup_depth
    )


def _classify_pattern(daily: pd.DataFrame, base: Base) -> None:
    """
    Classify the base pattern.  Mutates `base` in place.

    Patterns:
      - High Tight Flag (HTF): prior run 100%+ in <8 weeks, depth <25%, length 3-7 weeks
      - Flat Base: depth 5-15%, min 5 weeks
      - Cup with Handle: U-shape + handle in upper right portion, depth 15-40%, min 7 weeks
      - Cup without Handle: U-shape, depth 15-40%, min 7 weeks (no handle detected)
      - VCP: successive contractions in price swings
      - Double Bottom: W-shape, second low at or slightly below first
      - Unclear: doesn't match known patterns
    """
    depth = base.depth_pct
    weeks = base.length_weeks

    if depth is None:
        return

    abs_depth = abs(depth)

    # ── High Tight Flag ──
    # Check prior run: 100%+ in under 8 weeks (40 trading days)
    if base.prior_uptrend_pct is not None and base.prior_uptrend_pct >= 100:
        start_loc = _safe_loc(daily, base.start_date)
        if base.prior_trough_date is not None:
            trough_loc = _safe_loc(daily, base.prior_trough_date)
            run_length_days = start_loc - trough_loc
            if run_length_days <= 40 and abs_depth <= 25 and 3 <= weeks <= 7:
                base.pattern = "High Tight Flag"
                return

    # ── Flat Base ──
    if abs_depth <= 15 and weeks >= 5:
        base.pattern = "Flat Base"
        return

    # ── Get the base slice for shape analysis ──
    start_loc = _safe_loc(daily, base.start_date)
    end_loc = _safe_loc(daily, base.end_date)
    base_slice = daily.iloc[start_loc: end_loc + 1]
    n = len(base_slice)

    if n < 10:
        return

    # ── Double Bottom ──
    # Split into halves, find lows in each half
    mid = n // 2
    first_half_low = _to_scalar(base_slice["Low"].iloc[:mid].min())
    second_half_low = _to_scalar(base_slice["Low"].iloc[mid:].min())
    # Between the two lows there should be a rally (middle high)
    quarter = max(1, n // 4)
    mid_start = max(0, mid - quarter)
    mid_end = min(n, mid + quarter)
    middle_high = _to_scalar(base_slice["High"].iloc[mid_start:mid_end].max())

    middle_rally_pct = _pct_change(middle_high, min(first_half_low, second_half_low))
    lows_close = abs(_pct_change(second_half_low, first_half_low) or 999) < 5

    if (lows_close
            and middle_rally_pct is not None
            and middle_rally_pct >= 5
            and 15 <= abs_depth <= 50
            and weeks >= 5):
        base.pattern = "Double Bottom"
        return

    # ── VCP ──
    # Split into thirds and measure swing ranges
    if n >= 15:
        third = n // 3
        segments = [
            base_slice.iloc[:third],
            base_slice.iloc[third: 2 * third],
            base_slice.iloc[2 * third:],
        ]
        swings = []
        for seg in segments:
            seg_high = _to_scalar(seg["High"].max())
            seg_low = _to_scalar(seg["Low"].min())
            swing = abs(_pct_change(seg_low, seg_high) or 0)
            swings.append(round(swing, 1))

        base.vcp_swings = swings
        base.vcp_contracting = (
            len(swings) == 3
            and swings[0] > 0
            and swings[2] < swings[1] < swings[0]
        )

        if base.vcp_contracting and 10 <= abs_depth <= 50 and weeks >= 5:
            base.pattern = "VCP"
            return

    # ── Cup with Handle / Cup without Handle ──
    # U-shape heuristic: the base low should be roughly in the middle portion
    if base.base_low_date is not None and n >= 20:
        low_loc = _safe_loc(daily, base.base_low_date)
        relative_low_pos = (low_loc - start_loc) / n
        # Low in the middle 60% of the base → U-shape
        if 0.2 <= relative_low_pos <= 0.8 and 15 <= abs_depth <= 40 and weeks >= 7:
            has_handle = _detect_handle(daily, base)
            base.handle_detected = has_handle
            base.pattern = "Cup with Handle" if has_handle else "Cup without Handle"
            return

    base.pattern = "Unclear"


# ── Step 6: Tight areas & ranges (Section 6) ────────────────────────────────

def _compute_moving_averages(daily: pd.DataFrame) -> pd.DataFrame:
    """Compute key daily moving averages used for tight area proximity checks."""
    df = daily.copy()
    df["EMA_10"] = df["Close"].ewm(span=10, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["SMA_50"] = df["Close"].rolling(50).mean()
    return df


def _compute_rmv(daily: pd.DataFrame, period: int = 14, lookback: int = 100) -> pd.Series:
    """
    Relative Measured Volatility (Moglen).

    RMV = current ATR / median ATR over a longer lookback.
    When RMV is low (approaching 0 relative to history), price is contracting.

    Returns a Series of RMV values (0 = max contraction, 100 = max expansion
    relative to the lookback window).
    """
    high = daily["High"]
    low = daily["Low"]
    close = daily["Close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    # Normalize: rank within the lookback window, scale to 0-100
    rmv = atr.rolling(lookback).apply(
        lambda x: (pd.Series(x).rank().iloc[-1] - 1) / (len(x) - 1) * 100
        if len(x) > 1 else 50,
        raw=False,
    )
    return rmv


def _detect_tight_areas(
    daily_with_ma: pd.DataFrame,
    start_loc: int,
    end_loc: int,
    min_days: int = 2,
    max_days: int = 7,
    max_range_pct: float = 5.0,
) -> list[TightArea]:
    """
    Detect tight areas (short-term consolidations) within a base.

    A tight area is a window of `min_days` to `max_days` where:
      - The range (max High - min Low) / max High * 100 <= max_range_pct
      - Ideally accompanied by declining volume

    Also checks proximity to key moving averages (10 DMA, 21 EMA, 50 DMA).
    """
    base_slice = daily_with_ma.iloc[start_loc: end_loc + 1]
    n = len(base_slice)

    if n < min_days:
        return []

    # Pre-compute 20-day average volume for volume ratio
    vol_20d = daily_with_ma["Volume"].rolling(20).mean()

    tight_areas: list[TightArea] = []

    # Slide windows of varying lengths
    for window_len in range(min_days, min(max_days + 1, n + 1)):
        i = 0
        while i <= n - window_len:
            window = base_slice.iloc[i: i + window_len]
            w_high = _to_scalar(window["High"].max())
            w_low = _to_scalar(window["Low"].min())

            if w_high == 0:
                i += 1
                continue

            range_pct = (w_high - w_low) / w_high * 100

            if range_pct <= max_range_pct:
                # Check volume
                abs_start = start_loc + i
                avg_vol_before = _to_scalar(vol_20d.iloc[abs_start]) if abs_start >= 20 else None
                avg_vol_in_window = _to_scalar(window["Volume"].mean())

                if avg_vol_before and avg_vol_before > 0:
                    vol_ratio = round(avg_vol_in_window / avg_vol_before, 2)
                    vol_declining = vol_ratio < 1.0
                else:
                    vol_ratio = 1.0
                    vol_declining = False

                # Check MA proximity: does the tight area's range overlap with the MA?
                def _near_ma(ma_col: str) -> bool:
                    if ma_col not in window.columns:
                        return False
                    ma_vals = window[ma_col].dropna()
                    if ma_vals.empty:
                        return False
                    ma_mid = _to_scalar(ma_vals.mean())
                    return w_low <= ma_mid <= w_high

                ta = TightArea(
                    start_date=window.index[0],
                    end_date=window.index[-1],
                    length_days=window_len,
                    high=round(w_high, 2),
                    low=round(w_low, 2),
                    range_pct=round(range_pct, 2),
                    avg_volume_ratio=vol_ratio,
                    volume_declining=vol_declining,
                    near_21ema=_near_ma("EMA_21"),
                    near_50dma=_near_ma("SMA_50"),
                    near_10dma=_near_ma("EMA_10"),
                )
                if vol_declining:
                    tight_areas.append(ta)
                # Skip ahead to avoid overlapping windows of the same length
                i += window_len
            else:
                i += 1

    # Deduplicate: if a shorter tight area is fully contained within a
    # longer one, keep the longer one (it's more significant).
    if len(tight_areas) > 1:
        tight_areas.sort(key=lambda t: (t.start_date, -t.length_days))
        deduped = []
        for ta in tight_areas:
            # Check if this TA is fully contained within the last kept TA
            if deduped and ta.start_date >= deduped[-1].start_date and ta.end_date <= deduped[-1].end_date:
                continue
            deduped.append(ta)
        tight_areas = deduped

    return tight_areas


def _assess_overall_tightening(
    daily: pd.DataFrame,
    start_loc: int,
    end_loc: int,
) -> bool:
    """
    Is the base getting tighter over time?  Compare the ATR in the
    first half vs the second half.  If second half ATR < first half ATR,
    the base is tightening (constructive).
    """
    base_slice = daily.iloc[start_loc: end_loc + 1]
    n = len(base_slice)
    if n < 10:
        return False

    mid = n // 2
    first_half = base_slice.iloc[:mid]
    second_half = base_slice.iloc[mid:]

    def _atr(df):
        h, l, c = df["High"], df["Low"], df["Close"]
        prev_c = c.shift(1)
        tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
        return tr.mean()

    atr1 = _to_scalar(_atr(first_half))
    atr2 = _to_scalar(_atr(second_half))

    return atr2 < atr1


def _enrich_tight_areas(
    daily: pd.DataFrame,
    base: Base,
    daily_with_ma: pd.DataFrame,
    rmv_series: pd.Series,
    min_days: int = 2,
    max_days: int = 7,
    max_range_pct: float = 5.0,
) -> None:
    """
    Find tight areas within a base and assess overall tightening.
    Mutates `base` in place.
    """
    start_loc = _safe_loc(daily, base.start_date)
    end_loc = _safe_loc(daily, base.end_date)

    base.tight_areas = _detect_tight_areas(
        daily_with_ma, start_loc, end_loc, min_days, max_days, max_range_pct,
    )

    base.overall_tightening = _assess_overall_tightening(daily, start_loc, end_loc)

    # RMV at the end of the base
    if end_loc < len(rmv_series) and not pd.isna(rmv_series.iloc[end_loc]):
        base.rmv_at_end = round(_to_scalar(rmv_series.iloc[end_loc]), 1)


# ── Step 5b: Accumulation & volume signatures (Section 5) ────────────────────

def _resample_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly bars (Mon-Fri week ending Friday)."""
    weekly = daily.resample("W-FRI").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna(subset=["Open"])
    return weekly


def _count_weekly_accumulation(
    weekly: pd.DataFrame,
    base: Base,
) -> None:
    """
    O'Neil weekly accumulation technique (Section 5.1).

    For each week within the base:
      - WCR = (Close - Low) / (High - Low) * 100
      - Accumulation: Close > Open AND WCR >= 40% AND Volume > 10w avg
      - Distribution: Close < Open AND WCR < 40% AND Volume > 10w avg
      - Neutral: volume below average (doesn't count)

    Mutates `base` in place.
    """
    mask = (weekly.index >= base.start_date) & (weekly.index <= base.end_date)
    base_weekly = weekly.loc[mask]

    if base_weekly.empty:
        return

    vol_10w = weekly["Volume"].rolling(10).mean()
    accum = 0
    distrib = 0

    for idx in base_weekly.index:
        row = base_weekly.loc[idx]
        o, h, l, c, v = (
            _to_scalar(row["Open"]),
            _to_scalar(row["High"]),
            _to_scalar(row["Low"]),
            _to_scalar(row["Close"]),
            _to_scalar(row["Volume"]),
        )

        rng = h - l
        if rng <= 0:
            continue

        wcr = (c - l) / rng * 100

        # Get 10-week average volume at this week
        avg_loc = weekly.index.get_loc(idx)
        if isinstance(avg_loc, slice):
            avg_loc = avg_loc.start
        if avg_loc < len(vol_10w) and not pd.isna(vol_10w.iloc[avg_loc]):
            avg_vol = _to_scalar(vol_10w.iloc[avg_loc])
        else:
            continue  # can't determine if volume is above average

        if v <= avg_vol:
            continue  # neutral week — below-average volume

        if c > o and wcr >= 40:
            accum += 1
        elif c < o and wcr < 40:
            distrib += 1

    base.accum_weeks = accum
    base.distrib_weeks = distrib
    base.accum_distrib_ratio = (
        round(accum / distrib, 2) if distrib > 0 else
        (float(accum) if accum > 0 else None)
    )
    base.accum_passes = accum > distrib


def _find_three_weeks_tight(
    weekly: pd.DataFrame,
    base: Base,
    max_spread_pct: float = 1.5,
    min_weeks: int = 3,
) -> None:
    """
    Weeks Tight pattern (Section 5.2).
    Find maximal contiguous runs of weekly closes within `max_spread_pct`.

    A run of N weeks (N ≥ min_weeks) is recorded once.  Each trading week
    belongs to at most one pattern — the algorithm greedily extends, then
    starts fresh after the run breaks.

    Mutates `base` in place.
    """
    mask = (weekly.index >= base.start_date) & (weekly.index <= base.end_date)
    base_weekly = weekly.loc[mask]

    if len(base_weekly) < min_weeks:
        return

    closes = base_weekly["Close"].values
    dates = base_weekly.index
    patterns: list[ThreeWeeksTight] = []

    i = 0
    while i <= len(closes) - min_weeks:
        # Start a new candidate run from week i
        run_closes = [_to_scalar(closes[i])]
        j = i + 1

        while j < len(closes):
            candidate = _to_scalar(closes[j])
            test_closes = run_closes + [candidate]
            mx = max(test_closes)
            mn = min(test_closes)
            if mx <= 0:
                break
            spread = (mx - mn) / mx * 100
            if spread > max_spread_pct:
                break
            run_closes.append(candidate)
            j += 1

        num_weeks = len(run_closes)
        if num_weeks >= min_weeks:
            mx = max(run_closes)
            mn = min(run_closes)
            spread = (mx - mn) / mx * 100
            patterns.append(ThreeWeeksTight(
                start_date=dates[i],
                end_date=dates[i + num_weeks - 1],
                num_weeks=num_weeks,
                closes=[round(c, 2) for c in run_closes],
                spread_pct=round(spread, 2),
            ))
            # Jump past this run — no overlap
            i = i + num_weeks
        else:
            i += 1

    base.three_weeks_tight = patterns


def _find_volume_signatures(
    daily: pd.DataFrame,
    base: Base,
    gap_up_min_pct: float = 1.0,
) -> None:
    """
    Volume signatures (Section 5.3): HVE, HV1, HVIPO.

    For each day within the base:
      - HVE: volume is the highest ever recorded (in all available data)
      - HV1: volume is the highest in 52 weeks (252 trading days)
      - HVIPO: volume is the highest since the first trading day

    Also flags whether the day was a gap-up (Open > prior Close by >= gap_up_min_pct).

    Note: we detect the volume part objectively.  Earnings correlation
    (whether the gap was earnings-driven) is not available from OHLCV alone
    and can be enriched externally.

    Mutates `base` in place.
    """
    start_loc = _safe_loc(daily, base.start_date)
    end_loc = _safe_loc(daily, base.end_date)

    signatures: list[VolumeSignature] = []

    for i in range(start_loc, end_loc + 1):
        vol = _to_scalar(daily["Volume"].iloc[i])
        date = daily.index[i]

        # All-time max volume (HVE)
        all_time_max = _to_scalar(daily["Volume"].iloc[:i].max()) if i > 0 else 0
        # 52-week max volume (HV1)
        lookback_start = max(0, i - 252)
        year_max = _to_scalar(daily["Volume"].iloc[lookback_start:i].max()) if i > 0 else 0
        # IPO volume (first day)
        ipo_vol = _to_scalar(daily["Volume"].iloc[0])

        sig_type = None
        if vol > all_time_max and all_time_max > 0:
            sig_type = "HVE"
        elif vol > year_max and year_max > 0:
            sig_type = "HV1"
        elif vol > ipo_vol and ipo_vol > 0 and i > 0:
            # Only flag HVIPO if it's actually notable (not just any day above IPO vol)
            # HVIPO means highest since IPO — so vol must exceed ALL prior volume
            if vol > all_time_max:
                sig_type = "HVIPO"

        if sig_type is None:
            continue

        # Check for gap up
        gap_up_pct = None
        is_gap = False
        if i > 0:
            prev_close = _to_scalar(daily["Close"].iloc[i - 1])
            today_open = _to_scalar(daily["Open"].iloc[i])
            if prev_close > 0:
                gap_up_pct = round((today_open / prev_close - 1) * 100, 2)
                is_gap = gap_up_pct >= gap_up_min_pct

        signatures.append(VolumeSignature(
            date=date,
            signature_type=sig_type,
            volume=round(vol, 0),
            gap_up_pct=gap_up_pct,
            is_gap_up=is_gap,
        ))

    base.volume_signatures = signatures


def _count_daily_accumulation(
    daily: pd.DataFrame,
    base: Base,
) -> None:
    """
    Daily accumulation on the right side of the base (Section 5.4).

    The "right side" starts at the base_low_date (after the trough, price
    is recovering).

    For each day on the right side:
      - Accumulation: Close > Open AND Volume > 50-day avg volume
      - Distribution: Close < Open AND Volume > 50-day avg volume

    Mutates `base` in place.
    """
    if base.base_low_date is None:
        return

    # Right side = from base_low_date to base end
    low_loc = _safe_loc(daily, base.base_low_date)
    end_loc = _safe_loc(daily, base.end_date)

    if low_loc >= end_loc:
        return

    vol_50d = daily["Volume"].rolling(50).mean()
    accum = 0
    distrib = 0

    for i in range(low_loc, end_loc + 1):
        c = _to_scalar(daily["Close"].iloc[i])
        o = _to_scalar(daily["Open"].iloc[i])
        v = _to_scalar(daily["Volume"].iloc[i])

        if i < len(vol_50d) and not pd.isna(vol_50d.iloc[i]):
            avg = _to_scalar(vol_50d.iloc[i])
        else:
            continue

        if v <= avg:
            continue  # below average — not significant

        if c > o:
            accum += 1
        elif c < o:
            distrib += 1

    base.daily_accum_days = accum
    base.daily_distrib_days = distrib
    base.daily_accum_ratio = (
        round(accum / distrib, 2) if distrib > 0 else
        (float(accum) if accum > 0 else None)
    )
    base.daily_accum_passes = accum > distrib


def _find_pocket_pivots(
    daily: pd.DataFrame,
    daily_with_ma: pd.DataFrame,
    base: Base,
    lookback: int = 10,
) -> None:
    """
    10-day pocket pivot (Section 5.5).

    An up-day where volume exceeds the maximum down-day volume of
    the prior `lookback` trading days.

    Mutates `base` in place.
    """
    start_loc = _safe_loc(daily, base.start_date)
    end_loc = _safe_loc(daily, base.end_date)

    pocket_pivots: list[PocketPivot] = []

    for i in range(start_loc + lookback, end_loc + 1):
        c = _to_scalar(daily["Close"].iloc[i])
        o = _to_scalar(daily["Open"].iloc[i])
        v = _to_scalar(daily["Volume"].iloc[i])

        # Must be an up day
        if c <= o:
            continue

        # Find max down-day volume in the prior `lookback` days
        max_down_vol = 0
        for j in range(i - lookback, i):
            cj = _to_scalar(daily["Close"].iloc[j])
            oj = _to_scalar(daily["Open"].iloc[j])
            vj = _to_scalar(daily["Volume"].iloc[j])
            if cj < oj:  # down day
                max_down_vol = max(max_down_vol, vj)

        if max_down_vol > 0 and v > max_down_vol:
            above_ema = False
            if "EMA_21" in daily_with_ma.columns and i < len(daily_with_ma):
                ema_val = daily_with_ma["EMA_21"].iloc[i]
                if not pd.isna(ema_val):
                    above_ema = c > _to_scalar(ema_val)

            pocket_pivots.append(PocketPivot(
                date=daily.index[i],
                volume=round(v, 0),
                max_down_volume_10d=round(max_down_vol, 0),
                close=round(c, 2),
                above_ema_21=above_ema,
            ))

    base.pocket_pivots = pocket_pivots


def _enrich_accumulation(
    daily: pd.DataFrame,
    daily_with_ma: pd.DataFrame,
    weekly: pd.DataFrame,
    base: Base,
) -> None:
    """
    Run all accumulation & volume signature analysis for one base.
    Mutates `base` in place.
    """
    _count_weekly_accumulation(weekly, base)
    _find_three_weeks_tight(weekly, base)
    _find_volume_signatures(daily, base)
    _count_daily_accumulation(daily, base)
    #_find_pocket_pivots(daily, daily_with_ma, base)


# ── Ranges ────────────────────────────────────────────────────────────────────

def build_stop_loss(
    expansion_date: pd.Timestamp,
    daily: pd.DataFrame,
    buy_price: float,
    stop_type: str = "expansion_open",
    buffer_pct: float = 0.02,
    constant_pct: float = 0.03,
    max_loss_pct: float = 0.04,
) -> Optional[StopLoss]:
    """Compute the initial stop loss for a trade.

    All ``*_pct`` inputs are fractions (0.02 = 2 %).

    Criteria stop:
      - ``range_low`` / ``expansion_open`` / ``21ema`` / ``50sma``:
        ``reference × (1 − buffer_pct)``
      - ``constant_pct``: ``buy_price × (1 − constant_pct)``

    Max-loss cap:
      Final stop = max(criteria_stop, buy_price × (1 − max_loss_pct)).
      (The TIGHTER stop wins — smaller loss.)  ``capped_by_max_loss`` is set
      True when the max-loss floor was the binding level.

    Returns None only when the chosen reference level is unavailable AND the
    caller wants a hard failure; typically the caller uses ``constant_pct``
    as a fallback so this function still returns something.
    """
    if expansion_date not in daily.index:
        return None

    exp_loc = daily.index.get_loc(expansion_date)
    if isinstance(exp_loc, slice):
        exp_loc = exp_loc.start

    reference: Optional[float] = None

    if stop_type == "range_low":
        reference = _to_scalar(daily["Low"].iloc[exp_loc])
    elif stop_type == "expansion_open":
        reference = _to_scalar(daily["Open"].iloc[exp_loc])
    elif stop_type == "21ema":
        ema = daily["Close"].ewm(span=21, adjust=False).mean()
        try:
            reference = _to_scalar(ema.iloc[exp_loc])
        except Exception:
            reference = None
    elif stop_type == "50sma":
        sma = daily["Close"].rolling(50).mean()
        try:
            reference = _to_scalar(sma.iloc[exp_loc])
        except Exception:
            reference = None
    elif stop_type == "constant_pct":
        reference = buy_price
    else:
        raise ValueError(
            f"stop_type must be one of 'range_low', 'expansion_open', '21ema', "
            f"'50sma', 'constant_pct'; got {stop_type!r}"
        )

    if reference is None or math.isnan(reference) or reference <= 0:
        return None

    if stop_type == "constant_pct":
        criteria_stop = reference * (1 - constant_pct)
    else:
        criteria_stop = reference * (1 - buffer_pct)

    max_loss_stop = buy_price * (1 - max_loss_pct)

    # Tighter stop = higher price = smaller loss
    if max_loss_stop > criteria_stop:
        final_stop = max_loss_stop
        capped = True
    else:
        final_stop = criteria_stop
        capped = False

    return StopLoss(
        stop_type=stop_type,
        reference_price=round(reference, 2),
        stop_price=round(final_stop, 2),
        buffer_pct=buffer_pct,
        constant_pct=constant_pct,
        max_loss_pct=max_loss_pct,
        capped_by_max_loss=capped,
    )


def build_range_expansion_trade(
    expansion_date: pd.Timestamp,
    daily: pd.DataFrame,
    ma_type: str = "21ema",
    allow_rising_close_exception: bool = True,
    stop_type: str = "expansion_open",
    stop_buffer_pct: float = 0.02,
    stop_constant_pct: float = 0.03,
    max_loss_pct: float = 0.04,
) -> Optional[RangeExpansionTrade]:
    """Build a paper trade from an up RangeExpansion.

    Entry: OPEN of the trading day immediately after ``expansion_date``.

    Exit: whichever of these fires FIRST wins —
      (a) SellInWeakness: 2 consecutive closes below the chosen MA → sell at
          OPEN of the next day (with optional rising-close-veto, rolling by 1).
      (b) StopLossHit: intraday Low ≤ stop_price → sell same day at
          ``min(Open, stop_price)``.

    Per-day ordering inside the scan: execute any sell queued from the prior
    close (the SellInWeakness exit) at today's open BEFORE checking the stop
    intraday.

    All ``*_pct`` inputs are fractions (0.02 = 2 %).
    """
    if ma_type == "50sma":
        ma_series = daily["Close"].rolling(50).mean()
    elif ma_type == "21ema":
        ma_series = daily["Close"].ewm(span=21, adjust=False).mean()
    else:
        raise ValueError(f"ma_type must be '21ema' or '50sma', got {ma_type!r}")

    if expansion_date not in daily.index:
        return None

    exp_loc = daily.index.get_loc(expansion_date)
    if isinstance(exp_loc, slice):
        exp_loc = exp_loc.start

    buy_loc = exp_loc + 1
    n = len(daily)
    if buy_loc >= n:
        return None  # no day to buy

    buy_date = daily.index[buy_loc]
    buy_price = _to_scalar(daily["Open"].iloc[buy_loc])

    stop_loss = build_stop_loss(
        expansion_date=expansion_date,
        daily=daily,
        buy_price=buy_price,
        stop_type=stop_type,
        buffer_pct=stop_buffer_pct,
        constant_pct=stop_constant_pct,
        max_loss_pct=max_loss_pct,
    )
    # Fallback: a trade must always have a stop — use constant_pct if the
    # requested level couldn't be computed (e.g. 50 SMA at start of data).
    if stop_loss is None:
        const_stop = buy_price * (1 - stop_constant_pct)
        max_loss_stop = buy_price * (1 - max_loss_pct)
        capped = max_loss_stop > const_stop
        final = max(const_stop, max_loss_stop)
        stop_loss = StopLoss(
            stop_type="constant_pct",
            reference_price=round(buy_price, 2),
            stop_price=round(final, 2),
            buffer_pct=stop_buffer_pct,
            constant_pct=stop_constant_pct,
            max_loss_pct=max_loss_pct,
            capped_by_max_loss=capped,
        )

    buy = Buy(
        date=buy_date,
        price=round(buy_price, 2),
        stop_loss=stop_loss,
    )

    trade = RangeExpansionTrade(
        buy=buy,
        sell=None,
        sell_reason=None,
        return_pct=None,
        days_held=None,
        ma_type=ma_type,
        allow_rising_close_exception=allow_rising_close_exception,
    )

    stop_price = stop_loss.stop_price
    closes = daily["Close"].values
    opens = daily["Open"].values
    lows = daily["Low"].values

    def _below_ma(loc: int) -> Optional[bool]:
        try:
            c = _to_scalar(closes[loc])
            m = _to_scalar(ma_series.iloc[loc])
            if math.isnan(m) or math.isnan(c):
                return None
            return c < m
        except Exception:
            return None

    def _finalize(sell_loc: int, sell_price: float, sell_obj, reason: str) -> None:
        trade.sell = sell_obj
        trade.sell_reason = reason
        if buy_price > 0:
            trade.return_pct = round((sell_price / buy_price - 1) * 100, 2)
        trade.days_held = sell_loc - buy_loc

    # Pending weakness sell (queued from a prior close, executes at next open)
    pending_weakness: Optional[dict] = None

    # Start intraday scanning on the buy day itself — a gap-down below stop on
    # entry should stop us out immediately (after the fill at open).
    k = buy_loc
    while k < n:
        # 1. Execute any weakness sell queued from yesterday's close, at today's open.
        if pending_weakness is not None:
            sell_price = _to_scalar(opens[k])
            sell_obj = SellInWeakness(
                weakness_day1_date=pending_weakness["d1_date"],
                weakness_day1_close=pending_weakness["d1_close"],
                weakness_day2_date=pending_weakness["d2_date"],
                weakness_day2_close=pending_weakness["d2_close"],
                sell_date=daily.index[k],
                sell_price=round(sell_price, 2),
                ma_type=ma_type,
                allow_rising_close_exception=allow_rising_close_exception,
            )
            _finalize(k, sell_price, sell_obj, "weakness")
            return trade

        # 2. Intraday stop check (skip on buy day only if buy above stop and
        #    low above stop — standard check handles both).
        day_low = _to_scalar(lows[k])
        day_open = _to_scalar(opens[k])
        if day_low <= stop_price:
            gap_down = day_open <= stop_price
            fill = min(day_open, stop_price)
            sell_obj = StopLossHit(
                hit_date=daily.index[k],
                sell_date=daily.index[k],
                sell_price=round(fill, 2),
                stop_price=round(stop_price, 2),
                gap_down=gap_down,
            )
            _finalize(k, fill, sell_obj, "stop_loss")
            return trade

        # 3. At close: update 2-day weakness state.  If today and yesterday
        #    both closed below MA, queue a sell for tomorrow's open (subject
        #    to rising-close exception).
        if k > buy_loc:
            b_prev = _below_ma(k - 1)
            b_today = _below_ma(k)
            if b_prev and b_today:
                c_prev = _to_scalar(closes[k - 1])
                c_today = _to_scalar(closes[k])
                # Rising-close veto: keep scanning (rolling 2-day window
                # means today can still pair with tomorrow).
                if not (allow_rising_close_exception and c_today > c_prev):
                    pending_weakness = {
                        "d1_date": daily.index[k - 1],
                        "d1_close": round(c_prev, 2),
                        "d2_date": daily.index[k],
                        "d2_close": round(c_today, 2),
                    }

        k += 1

    return trade


def find_ranges(
    daily: pd.DataFrame,
    box_pct: float = 3.0,
    min_days: int = 3,
    expansion_pct: float = 4.0,
    max_expansion_days: int = 1,
    close_threshold_pct: float = 10.0,
    price_mode: str = "high_low",
    trade_ma_type: str = "21ema",
    allow_rising_close_exception: bool = True,
    stop_type: str = "expansion_open",
    stop_buffer_pct: float = 0.02,
    stop_constant_pct: float = 0.03,
    max_loss_pct: float = 0.04,
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
) -> list[Range]:
    """
    Find all price ranges (boxes) in daily data.

    A range is a contiguous stretch of ≥ min_days trading days where all
    price action fits within a box of height ≤ box_pct %.

    The box is defined by the running max and min of the chosen price columns:
      - ``"high_low"`` → uses daily High and Low
      - ``"open_close"`` → uses daily Open and Close

    After each range ends, the function looks for a RangeExpansion: the first
    subsequent day where the close moves ≥ expansion_pct % beyond the box
    high (up breakout) or below the box low (down breakdown).

    Each RangeExpansion also records the **closing range** of the expansion
    day — i.e. where the close sits within that day's High-Low range:
      - 100 % = closed at the day's high
      - 0 %   = closed at the day's low
    ``strong_close`` is True when the close is in the top
    ``close_threshold_pct`` of the day's range (for up) or bottom (for down).

    This function is standalone — it does not require a Base.  When called
    within the base pipeline, pass start_date / end_date to limit the scan.

    Parameters
    ----------
    daily : pd.DataFrame
        OHLCV data with DatetimeIndex.
    box_pct : float
        Maximum height of the box as a % of the low.  e.g. 3.0 means
        (high - low) / low * 100 ≤ 3.0.
    min_days : int
        Minimum number of trading days for a valid range.
    expansion_pct : float
        Required % move beyond the box for RangeExpansion.  e.g. 4.0 means
        close must be ≥ 4 % above box high (or ≤ 4 % below box low).
    max_expansion_days : int
        The expansion must occur within this many trading days after the
        range ends.  Default 1 = the very next day only.
    close_threshold_pct : float
        For ``strong_close``: the close must be in the top (up) or bottom
        (down) this % of the expansion day's range.  Default 10 means the
        close must be in the top 10 % of the day's High-Low range.
    price_mode : str
        ``"high_low"`` or ``"open_close"`` — which columns define the box.
    start_date, end_date : optional
        Limit the scan to a date window.

    Returns
    -------
    list[Range]
        Ranges sorted by start_date, each with at most one RangeExpansion.
        Overlapping ranges are allowed when they are NOT strict date-subsets
        of each other — e.g. Mar 1→Mar 9 and Mar 2→Mar 13 can both be
        returned because neither contains the other.  Any range whose
        (start, end) is strictly contained in another surviving range is
        dropped.
    """
    if price_mode == "open_close":
        col_upper, col_lower = "Close", "Open"
    else:
        col_upper, col_lower = "High", "Low"

    # Compute MAs on full daily BEFORE slicing so lookback periods are correct
    sma_50  = daily["Close"].rolling(50).mean()
    sma_200 = daily["Close"].rolling(200).mean()
    ema_21  = daily["Close"].ewm(span=21, adjust=False).mean()

    # Slope lookback: (ma_now - ma_N_days_ago) / ma_N_days_ago * 100
    _slope_lb = 5

    # Slice to requested window
    df = daily
    if start_date is not None:
        df = df.loc[df.index >= start_date]
    if end_date is not None:
        df = df.loc[df.index <= end_date]

    if len(df) < min_days:
        return []

    # For open_close mode, upper = max(Open, Close), lower = min(Open, Close)
    if price_mode == "open_close":
        upper_vals = np.maximum(df["Open"].values, df["Close"].values)
        lower_vals = np.minimum(df["Open"].values, df["Close"].values)
    else:
        upper_vals = df["High"].values
        lower_vals = df["Low"].values

    dates = df.index
    closes = df["Close"].values
    day_highs = df["High"].values
    day_lows = df["Low"].values
    n = len(df)

    # ── Pass 1: collect all candidate (i, j, box_high, box_low) intervals.
    # Slide by 1 (overlap allowed) so we discover ranges like Mar 2→Mar 13
    # that a later-starting envelope reveals.
    candidates: list[tuple[int, int, float, float]] = []  # (i, j, box_high, box_low)
    for i_cand in range(0, n - min_days + 1):
        bh = _to_scalar(upper_vals[i_cand])
        bl = _to_scalar(lower_vals[i_cand])
        if bl <= 0:
            continue

        j_cand = i_cand + 1
        while j_cand < n:
            du = _to_scalar(upper_vals[j_cand])
            dl = _to_scalar(lower_vals[j_cand])
            new_high = max(bh, du)
            new_low = min(bl, dl)
            if new_low <= 0:
                break
            height = (new_high - new_low) / new_low * 100
            if height > box_pct:
                break
            bh = new_high
            bl = new_low
            j_cand += 1

        if j_cand - i_cand >= min_days:
            candidates.append((i_cand, j_cand, bh, bl))

    # ── Pass 2: drop strict date-subsets.
    # B is a strict subset of A iff A.i ≤ B.i, B.j ≤ A.j, and (A.i < B.i or B.j < A.j).
    # Equivalently: remove any candidate fully contained in a longer one.
    candidates.sort(key=lambda t: (t[0], -(t[1] - t[0])))  # by start asc, length desc
    kept: list[tuple[int, int, float, float]] = []
    for c in candidates:
        ci, cj, _, _ = c
        dominated = False
        for k_i, k_j, _, _ in kept:
            if k_i <= ci and cj <= k_j and (k_i < ci or cj < k_j):
                dominated = True
                break
        if dominated:
            continue
        # Also remove any previously-kept that is a strict subset of this one.
        kept = [
            (ki, kj, kh, kl) for (ki, kj, kh, kl) in kept
            if not (ci <= ki and kj <= cj and (ci < ki or kj < cj))
        ]
        kept.append(c)
    kept.sort(key=lambda t: (t[0], t[1]))

    # ── Pass 3: build Range objects for surviving candidates.
    ranges: list[Range] = []
    for i, j, box_high, box_low in kept:
        length = j - i
        height_pct = (box_high - box_low) / box_low * 100 if box_low > 0 else 0

        # Look for RangeExpansion after the range ends.
        # Primary threshold: day-over-day % change (close vs prev close).
        # move_pct is kept as a reference (close vs box boundary).
        expansion = None

        exp_end = min(j + max_expansion_days, n)
        for k in range(j, exp_end):
            c = _to_scalar(closes[k])
            prev_c = _to_scalar(closes[k - 1])  # always valid: k >= j >= min_days >= 1
            d_high = _to_scalar(day_highs[k])
            d_low = _to_scalar(day_lows[k])
            day_range = d_high - d_low

            # Day-over-day % change (primary threshold)
            day_chg = (c / prev_c - 1) * 100 if prev_c > 0 else 0.0

            # Day's range as a % of the low
            drp = (day_range / d_low * 100) if d_low > 0 else 0.0

            # Closing range: where the close sits in the day's H-L range
            # 100 = closed at high, 0 = closed at low
            if day_range > 0:
                closing_range = (c - d_low) / day_range * 100
            else:
                closing_range = 50.0  # flat day

            if day_chg >= expansion_pct:
                expansion = RangeExpansion(
                    date=dates[k],
                    direction="up",
                    close=round(c, 2),
                    day_chg_pct=round(day_chg, 2),
                    move_pct=round((c / box_high - 1) * 100, 2),
                    day_high=round(d_high, 2),
                    day_low=round(d_low, 2),
                    day_range_pct=round(drp, 2),
                    closing_range_pct=round(closing_range, 1),
                    strong_close=closing_range >= (100 - close_threshold_pct),
                )
                break
            elif day_chg <= -expansion_pct:
                expansion = RangeExpansion(
                    date=dates[k],
                    direction="down",
                    close=round(c, 2),
                    day_chg_pct=round(day_chg, 2),
                    move_pct=round((c / box_low - 1) * 100, 2),
                    day_high=round(d_high, 2),
                    day_low=round(d_low, 2),
                    day_range_pct=round(drp, 2),
                    closing_range_pct=round(closing_range, 1),
                    strong_close=closing_range <= close_threshold_pct,
                )
                break

        # Detect priming patterns within the range
        # Map local slice indices to absolute indices in the full daily DataFrame
        abs_start = daily.index.get_loc(dates[i])
        abs_end = daily.index.get_loc(dates[j - 1])
        if isinstance(abs_start, slice):
            abs_start = abs_start.start
        if isinstance(abs_end, slice):
            abs_end = abs_end.start
        
        #priming = _scan_priming_patterns(daily, abs_start, abs_end)

        # MA context at range end date
        end_date_ts = dates[j - 1]

        def _ma_val(series: pd.Series) -> Optional[float]:
            if end_date_ts not in series.index:
                return None
            v = series.loc[end_date_ts]
            try:
                fv = _to_scalar(v)
                return fv if not math.isnan(fv) else None
            except Exception:
                return None

        def _ma_slope(series: pd.Series) -> Optional[float]:
            """% change of MA over _slope_lb trading days ending at end_date_ts."""
            idx = series.index.get_loc(end_date_ts) if end_date_ts in series.index else None
            if idx is None or isinstance(idx, slice):
                return None
            if idx < _slope_lb:
                return None
            now = _to_scalar(series.iloc[idx])
            ago = _to_scalar(series.iloc[idx - _slope_lb])
            if ago and ago > 0 and not math.isnan(now) and not math.isnan(ago):
                return round((now / ago - 1) * 100, 3)
            return None

        end_close = _to_scalar(closes[j - 1])
        sma50_val  = _ma_val(sma_50)
        ema21_val  = _ma_val(ema_21)

        # Build a paper trade only when there's an up expansion.
        trade = None
        if expansion is not None and expansion.direction == "up":
            trade = build_range_expansion_trade(
                expansion_date=expansion.date,
                daily=daily,
                ma_type=trade_ma_type,
                allow_rising_close_exception=allow_rising_close_exception,
                stop_type=stop_type,
                stop_buffer_pct=stop_buffer_pct,
                stop_constant_pct=stop_constant_pct,
                max_loss_pct=max_loss_pct,
            )

        ranges.append(Range(
            start_date=dates[i],
            end_date=end_date_ts,
            high=round(box_high, 2),
            low=round(box_low, 2),
            height_pct=round(height_pct, 2),
            length_days=length,
            price_mode=price_mode,
            expansion=expansion,
            trade=trade,
            priming_patterns='Disabled Manually',#priming,
            above_50dma=bool(sma50_val and end_close > sma50_val),
            above_21ema=bool(ema21_val and end_close > ema21_val),
            slope_200dma=_ma_slope(sma_200),
            slope_50dma=_ma_slope(sma_50),
            slope_21ema=_ma_slope(ema_21),
        ))

    return ranges


def _enrich_ranges(
    daily: pd.DataFrame,
    base: Base,
    box_pct: float = 3.0,
    min_days: int = 3,
    expansion_pct: float = 4.0,
    price_mode: str = "high_low",
) -> None:
    """Find ranges within a base and attach them. Mutates `base` in place."""
    base.ranges = find_ranges(
        daily,
        box_pct=box_pct,
        min_days=min_days,
        expansion_pct=expansion_pct,
        price_mode=price_mode,
        start_date=base.start_date,
        end_date=base.end_date,
    )


# ── Step 7: Pivot points (Section 7) ─────────────────────────────────────────

def _find_consolidation_pivots(
    daily: pd.DataFrame,
    start_loc: int,
    end_loc: int,
    base_high: float,
    exception_pct: float = 1.0,
    min_span_days: int = 10,
    dedup_level_pct: float = 3.0,
) -> list[PivotPoint]:
    """
    Find consolidation pivots: resistance highs not breached for an extended
    period — like a mini-base within the base.

    A consolidation pivot is a ceiling: price can drift far below, but never
    closes above the pivot level (except by noise spikes ≤ exception_pct).

    Algorithm:
      1. Find local peaks (candidate resistance levels).
      2. For each peak, scan forward/backward counting *consecutive* days
         where no daily High exceeds the peak by more than exception_pct.
      3. If total span ≥ min_span_days, it qualifies.
      4. Deduplicate: if two pivots are within dedup_level_pct of each other
         AND one's time span is a subset of (or mostly overlaps) the other,
         keep only the longer one.
      5. Skip any pivot whose level is within exception_pct of base_high
         (that's just the base pivot, already tracked separately).
    """
    base_slice = daily.iloc[start_loc: end_loc + 1]
    n = len(base_slice)
    if n < min_span_days:
        return []

    highs = base_slice["High"].values
    dates = base_slice.index

    # Find local peaks
    peaks: list[tuple[int, float]] = []
    for i in range(1, n - 1):
        if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]:
            peaks.append((i, _to_scalar(highs[i])))

    if n >= 2:
        if highs[0] >= highs[1]:
            peaks.insert(0, (0, _to_scalar(highs[0])))
        if highs[-1] >= highs[-2]:
            peaks.append((n - 1, _to_scalar(highs[-1])))

    if not peaks:
        return []

    raw: list[dict] = []

    for pk_idx, pk_val in peaks:
        if pk_val <= 0:
            continue

        # Skip peaks that are essentially the base_high (redundant with base pivot)
        if base_high > 0 and abs(pk_val / base_high - 1) * 100 <= exception_pct:
            continue

        ceiling = pk_val * (1 + exception_pct / 100)

        # Scan forward — must be consecutive
        right = pk_idx
        for j in range(pk_idx + 1, n):
            if _to_scalar(highs[j]) <= ceiling:
                right = j
            else:
                break

        # Scan backward — must be consecutive
        left = pk_idx
        for j in range(pk_idx - 1, -1, -1):
            if _to_scalar(highs[j]) <= ceiling:
                left = j
            else:
                break

        span = right - left + 1
        if span < min_span_days:
            continue

        # Count tests: days where high came within 2% of the pivot level
        near_level = pk_val * 0.98
        tests = sum(
            1 for k in range(left, right + 1)
            if _to_scalar(highs[k]) >= near_level
        )

        raw.append({
            "level": round(pk_val, 2),
            "left": left,
            "right": right,
            "num_days": span,
            "start_date": dates[left],
            "end_date": dates[right],
            "num_tests": tests,
        })

    if not raw:
        return []

    # Deduplicate: longest first; drop if time-span is a subset of a kept
    # pivot at a similar level (within dedup_level_pct).
    raw.sort(key=lambda r: -r["num_days"])
    kept: list[dict] = []

    for rng in raw:
        redundant = False
        for existing in kept:
            level_diff = abs(rng["level"] / existing["level"] - 1) * 100 if existing["level"] > 0 else 999
            if level_diff > dedup_level_pct:
                continue
            # Time subset?
            if rng["left"] >= existing["left"] and rng["right"] <= existing["right"]:
                redundant = True
                break
            # Significant overlap (>50% of shorter span)?
            overlap_start = max(rng["left"], existing["left"])
            overlap_end = min(rng["right"], existing["right"])
            if overlap_end >= overlap_start:
                overlap_days = overlap_end - overlap_start + 1
                shorter_span = min(rng["num_days"], existing["num_days"])
                if overlap_days / shorter_span >= 0.5:
                    redundant = True
                    break
        if not redundant:
            kept.append(rng)

    pivots: list[PivotPoint] = []
    for rng in kept:
        pivots.append(PivotPoint(
            level=rng["level"],
            pivot_type="consolidation",
            start_date=rng["start_date"],
            end_date=rng["end_date"],
            num_days=rng["num_days"],
            num_tests=rng["num_tests"],
        ))

    return pivots


def _find_range_pivots(
    daily: pd.DataFrame,
    start_loc: int,
    end_loc: int,
    min_days: int = 3,
    threshold_pct: float = 5.0,
    exception_pct: float = 0.0,
) -> list[PivotPoint]:
    """
    Find range pivots: a key resistance high that is not breached for
    several days, with price trading in a tight band near that level.

    Rules (simple):
      - The pivot level is a local high (resistance).
      - Every day in the range, the daily High must satisfy:
            pivot * (1 - t1/100) ≤ High ≤ pivot * (1 + e1/100)
        i.e. within [95%, 101%] of the pivot (with defaults).
      - Scan forward and backward from the peak for the maximal
        *contiguous* run of days satisfying this band.
      - num_days = length of that run.
      - Dedup: if one range's time span is fully inside another at a
        similar level, drop the shorter one. No merging (which would
        corrupt date boundaries).

    Parameters
    ----------
    threshold_pct : float
        t1 — max % below the pivot that daily highs can be (default 5%).
    exception_pct : float
        e1 — max % above the pivot that a spike can go (default 1%).
    """
    base_slice = daily.iloc[start_loc: end_loc + 1]
    n = len(base_slice)

    if n < min_days:
        return []

    highs = base_slice["High"].values
    dates = base_slice.index

    # Find local peaks
    peaks: list[tuple[int, float]] = []
    for i in range(1, n - 1):
        if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]:
            peaks.append((i, _to_scalar(highs[i])))

    if n >= 2:
        if highs[0] >= highs[1]:
            peaks.insert(0, (0, _to_scalar(highs[0])))
        if highs[-1] >= highs[-2]:
            peaks.append((n - 1, _to_scalar(highs[-1])))

    if not peaks:
        return []

    raw: list[dict] = []

    for pk_idx, pk_val in peaks:
        if pk_val <= 0:
            continue

        floor = pk_val * (1 - threshold_pct / 100)
        ceiling = pk_val * (1 + exception_pct / 100)

        # Scan forward — strictly contiguous
        right = pk_idx
        for j in range(pk_idx + 1, n):
            h = _to_scalar(highs[j])
            if floor <= h <= ceiling:
                right = j
            else:
                break

        # Scan backward — strictly contiguous
        left = pk_idx
        for j in range(pk_idx - 1, -1, -1):
            h = _to_scalar(highs[j])
            if floor <= h <= ceiling:
                left = j
            else:
                break

        span = right - left + 1
        if span < min_days:
            continue

        # Count tests: days where high came within 1% of the pivot level
        near_level = pk_val * 0.99
        tests = sum(
            1 for k in range(left, right + 1)
            if _to_scalar(highs[k]) >= near_level
        )

        raw.append({
            "level": round(pk_val, 2),
            "left": left,
            "right": right,
            "num_days": span,
            "start_date": dates[left],
            "end_date": dates[right],
            "num_tests": tests,
        })

    if not raw:
        return []

    # Deduplicate: longest first; drop strict subsets at similar levels.
    # NO merging — that would extend dates beyond the validated band.
    raw.sort(key=lambda r: -r["num_days"])
    kept: list[dict] = []

    for rng in raw:
        redundant = False
        for existing in kept:
            level_diff = abs(rng["level"] / existing["level"] - 1) * 100 if existing["level"] > 0 else 999
            if level_diff > threshold_pct:
                continue
            # Strict time subset → drop
            if rng["left"] >= existing["left"] and rng["right"] <= existing["right"]:
                redundant = True
                break
        if not redundant:
            kept.append(rng)

    pivots: list[PivotPoint] = []
    for rng in kept:
        pivots.append(PivotPoint(
            level=rng["level"],
            pivot_type="range",
            start_date=rng["start_date"],
            end_date=rng["end_date"],
            num_days=rng["num_days"],
            num_tests=rng["num_tests"],
        ))

    return pivots


def _enrich_pivots(
    daily_with_ma: pd.DataFrame,
    base: Base,
    confluence_pct: float = 1.5,
) -> None:
    """
    Build the full pivot hierarchy for a base and detect confluence.
    Mutates `base` in place.

    Pivot hierarchy (Moglen):
      1. Base pivot — the base_high itself (one per base)
      2. Consolidation pivots — clusters of highs lasting 5-10+ days
      3. Range pivots — highs of tight areas (already detected)

    Confluence: when pivots of different types are within `confluence_pct`
    of each other, flag them.  "Range breakout triggers consolidation
    pivot triggers base pivot → expect a fast move."
    """
    start_loc = _safe_loc(daily_with_ma, base.start_date)
    end_loc = _safe_loc(daily_with_ma, base.end_date)

    all_pivots: list[PivotPoint] = []

    # 1. Base pivot (always one)
    base_pivot = PivotPoint(
        level=base.base_high,
        pivot_type="base",
        start_date=base.base_high_date,
        end_date=base.base_high_date,
        num_tests=1,
        near_base_high=True,
    )
    all_pivots.append(base_pivot)

    # 2. Consolidation pivots
    consol_pivots = _find_consolidation_pivots(daily_with_ma, start_loc, end_loc, base_high=base.base_high)
    all_pivots.extend(consol_pivots)

    # 3. Range pivots (resistance levels with 85% containment)
    range_pivots = _find_range_pivots(daily_with_ma, start_loc, end_loc)
    all_pivots.extend(range_pivots)

    # Enrich: near_21ema and near_base_high flags
    for pv in all_pivots:
        if pv.pivot_type == "base":
            continue  # already flagged

        # Near base high?
        if base.base_high > 0:
            pct_from_base_high = abs(pv.level / base.base_high - 1) * 100
            pv.near_base_high = pct_from_base_high <= 2.0

        # Near 21 EMA? Check if the 21 EMA at the pivot's end_date is close to the level
        if "EMA_21" in daily_with_ma.columns:
            pv_loc = _safe_loc(daily_with_ma, pv.end_date)
            if pv_loc < len(daily_with_ma):
                ema_val = daily_with_ma["EMA_21"].iloc[pv_loc]
                if not pd.isna(ema_val):
                    ema_val = _to_scalar(ema_val)
                    pct_from_ema = abs(pv.level / ema_val - 1) * 100
                    pv.near_21ema = pct_from_ema <= 2.0

    # Detect confluence: do pivots of different types line up?
    pivot_types_present = set(pv.pivot_type for pv in all_pivots)
    has_confluence = False

    for i, pv_a in enumerate(all_pivots):
        for pv_b in all_pivots[i + 1:]:
            if pv_a.pivot_type == pv_b.pivot_type:
                continue  # only care about cross-type confluence
            if pv_a.level > 0:
                pct_diff = abs(pv_a.level / pv_b.level - 1) * 100
                if pct_diff <= confluence_pct:
                    if pv_b.pivot_type not in pv_a.confluence_with:
                        pv_a.confluence_with.append(pv_b.pivot_type)
                    if pv_a.pivot_type not in pv_b.confluence_with:
                        pv_b.confluence_with.append(pv_a.pivot_type)
                    has_confluence = True

    base.pivots = all_pivots
    base.has_pivot_confluence = has_confluence


# ── Step 8: Priming patterns (Section 8) ─────────────────────────────────────

def _scan_priming_patterns(
    daily: pd.DataFrame,
    start_loc: int,
    end_loc: int,
    right_side_start: Optional[int] = None,
    range_atr_ratio: float = 0.5,
    body_max_pct: float = 1.0,
) -> list[PrimingPattern]:
    """
    Core scanner for the four Moglen priming patterns between start_loc and
    end_loc (inclusive) in ``daily``.

    Returns a list of PrimingPattern (without pivot tagging — caller does that).

    The four patterns:
      1. Inside Day
      2. Upside Reversal
      3. Positive Expectation Breaker (PEB)
      4. Tight Setup Day
    """
    if right_side_start is None:
        right_side_start = start_loc

    # Pre-compute 20-day median range for tight setup day detection
    daily_range = (daily["High"] - daily["Low"]) / daily["High"] * 100
    median_range_20d = daily_range.rolling(20).median()

    patterns: list[PrimingPattern] = []

    for i in range(max(start_loc + 1, 1), end_loc + 1):
        h_today = _to_scalar(daily["High"].iloc[i])
        l_today = _to_scalar(daily["Low"].iloc[i])
        o_today = _to_scalar(daily["Open"].iloc[i])
        c_today = _to_scalar(daily["Close"].iloc[i])
        h_yest = _to_scalar(daily["High"].iloc[i - 1])
        l_yest = _to_scalar(daily["Low"].iloc[i - 1])
        o_yest = _to_scalar(daily["Open"].iloc[i - 1])
        c_yest = _to_scalar(daily["Close"].iloc[i - 1])
        date = daily.index[i]
        on_right = i >= right_side_start

        # --- 1. Inside Day ---
        if h_today <= h_yest and l_today >= l_yest:
            patterns.append(PrimingPattern(
                date=date,
                pattern_type="inside_day",
                buy_point=round(h_today, 2),
                on_right_side=on_right,
            ))

        # --- 2. Upside Reversal ---
        if l_today < l_yest:
            today_range = h_today - l_today
            if today_range > 0:
                close_position = (c_today - l_today) / today_range
                if close_position >= 0.5 or c_today > c_yest:
                    patterns.append(PrimingPattern(
                        date=date,
                        pattern_type="upside_reversal",
                        buy_point=round(h_today, 2),
                        on_right_side=on_right,
                    ))

        # --- 3. Positive Expectation Breaker ---
        if c_yest < o_yest:
            if o_today > c_yest:
                if c_today > o_today:
                    patterns.append(PrimingPattern(
                        date=date,
                        pattern_type="positive_expectation_breaker",
                        buy_point=round(h_today, 2),
                        on_right_side=on_right,
                    ))

        # --- 4. Tight Setup Day ---
        if h_today > 0 and i < len(median_range_20d):
            med_raw = median_range_20d.iloc[i]
            med_val = _to_scalar(med_raw) if not (hasattr(med_raw, '__len__') or pd.isna(med_raw)) else None
            if med_val is not None and not math.isnan(med_val):
                today_range_pct = (h_today - l_today) / h_today * 100
                body_pct = abs(o_today - c_today) / c_today * 100 if c_today > 0 else 999

                if today_range_pct < med_val * range_atr_ratio and body_pct < body_max_pct:
                    patterns.append(PrimingPattern(
                        date=date,
                        pattern_type="tight_setup_day",
                        buy_point=round(h_today, 2),
                        on_right_side=on_right,
                    ))

    return patterns


def _detect_priming_patterns(
    daily: pd.DataFrame,
    daily_with_ma: pd.DataFrame,
    base: Base,
    range_atr_ratio: float = 0.5,
    body_max_pct: float = 1.0,
    near_pivot_pct: float = 3.0,
) -> None:
    """
    Detect priming patterns within a base and tag them with nearest pivots.
    Mutates `base` in place.
    """
    start_loc = _safe_loc(daily, base.start_date)
    end_loc = _safe_loc(daily, base.end_date)

    right_side_start = start_loc
    if base.base_low_date is not None:
        right_side_start = _safe_loc(daily, base.base_low_date)

    patterns = _scan_priming_patterns(
        daily, start_loc, end_loc,
        right_side_start=right_side_start,
        range_atr_ratio=range_atr_ratio,
        body_max_pct=body_max_pct,
    )

    # Tag each pattern with the nearest pivot
    if base.pivots:
        for pat in patterns:
            best_dist = float("inf")
            for pv in base.pivots:
                if pv.level > 0 and pat.buy_point > 0:
                    dist = abs(pat.buy_point / pv.level - 1) * 100
                    if dist < best_dist and dist <= near_pivot_pct:
                        best_dist = dist
                        pat.near_pivot_level = pv.level
                        pat.near_pivot_type = pv.pivot_type

    base.priming_patterns = patterns
    base.primed = any(
        p.on_right_side and p.near_pivot_level is not None
        for p in patterns
    )


# ── Public API ───────────────────────────────────────────────────────────────

def find_all_bases(
    daily: pd.DataFrame,
    market_daily: Optional[pd.DataFrame] = None,
    weekly: Optional[pd.DataFrame] = None,
    confirmation_days: int = 10,
    min_length_days: int = 25,
    pivot_percentile: float = 95.0,
    spike_tolerance_pct: float = 2.0,
    min_breakout_pct: float = 2.0,
    prior_uptrend_lookback_days: int = 252,
    min_uptrend_pct: float = 20.0,
    max_depth_vs_market_ratio: float = 2.5,
    tight_area_max_range_pct: float = 3.0,
) -> list[Base]:
    """
    Find all bases in a ticker's daily OHLCV history.

    Parameters
    ----------
    daily : pd.DataFrame
        Daily OHLCV with DatetimeIndex.  Must have columns:
        Open, High, Low, Close, Volume.
    market_daily : pd.DataFrame, optional
        Daily OHLCV for the market index (QQQ or SPY).  Used to compute
        base depth vs market drawdown (Section 4.1).  If None, that
        comparison is skipped.
    weekly : pd.DataFrame, optional
        Weekly OHLCV with DatetimeIndex.  Used for weekly accumulation
        count and 3WT detection (Sections 5.1, 5.2).  If None, weekly
        data is resampled from daily automatically.
    confirmation_days : int
        A significant high must hold for this many days.  Default 10
        (Moglen: "10-15 days").
    min_length_days : int
        Minimum trading days for a valid completed base.  Default 25 (~ 5 weeks).
    pivot_percentile : float
        Percentile of daily Highs used for the effective pivot.
        Default 95.0 (Moglen: "85-95 % of price action").
    spike_tolerance_pct : float
        A spike above the base_high that is within this % is treated
        as noise — it gets absorbed into the current base.  Default 2.0.
    min_breakout_pct : float
        A breakout is only confirmed when a daily close is more than this %
        above the base_high.  Closes marginally above are noise.  Default 2.0.
    prior_uptrend_lookback_days : int
        How far back to look for the prior trough.  Default 252 (1 year).
    min_uptrend_pct : float
        Minimum prior uptrend % for `prior_uptrend_sufficient`.  Default 20.0.
    max_depth_vs_market_ratio : float
        Maximum ratio of base depth to market drawdown.  Default 2.5.
    tight_area_max_range_pct : float
        Maximum range % for a tight area.  Default 5.0.

    Returns
    -------
    list[Base]
        Chronologically ordered list of all identified bases.
    """
    if daily is None or daily.empty or len(daily) < min_length_days:
        return []

    # Ensure we have a clean DatetimeIndex
    daily = daily.copy()
    if not isinstance(daily.index, pd.DatetimeIndex):
        daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()

    # Handle multi-level columns from yfinance (e.g. ('Close', 'AAPL'))
    if isinstance(daily.columns, pd.MultiIndex):
        daily.columns = daily.columns.get_level_values(0)

    # Clean market_daily if provided
    if market_daily is not None:
        market_daily = market_daily.copy()
        if not isinstance(market_daily.index, pd.DatetimeIndex):
            market_daily.index = pd.to_datetime(market_daily.index)
        market_daily = market_daily.sort_index()
        if isinstance(market_daily.columns, pd.MultiIndex):
            market_daily.columns = market_daily.columns.get_level_values(0)

    # Prepare weekly data for accumulation analysis (Section 5)
    if weekly is not None:
        weekly = weekly.copy()
        if not isinstance(weekly.index, pd.DatetimeIndex):
            weekly.index = pd.to_datetime(weekly.index)
        weekly = weekly.sort_index()
        if isinstance(weekly.columns, pd.MultiIndex):
            weekly.columns = weekly.columns.get_level_values(0)
    else:
        weekly = _resample_to_weekly(daily)

    # Step 1: Find significant highs
    sig_highs = _find_significant_highs(daily, confirmation_days)

    if not sig_highs:
        return []

    # Step 2: Build raw bases (with merging and effective pivot logic)
    bases = _build_raw_bases(
        daily, sig_highs, min_length_days, pivot_percentile,
        spike_tolerance_pct, min_breakout_pct,
    )

    # Step 3: Measure prior uptrend for each base (Section 4.2)
    for base in bases:
        _measure_prior_uptrend(daily, base, prior_uptrend_lookback_days, min_uptrend_pct)

    # Step 3b: Base depth vs market drawdown (Section 4.1)
    for base in bases:
        _measure_depth_vs_market(daily, base, market_daily, max_depth_vs_market_ratio)

    # Step 4: Count stages (Section 4.3)
    _count_stages(bases)

    # Step 5: Classify patterns (including Cup with Handle detection)
    for base in bases:
        _classify_pattern(daily, base)

    # Step 6: Tight areas & RMV (Section 6)
    daily_with_ma = _compute_moving_averages(daily)
    rmv_series = _compute_rmv(daily)
    for base in bases:
        _enrich_tight_areas(
            daily, base, daily_with_ma, rmv_series,
            max_range_pct=tight_area_max_range_pct,
        )

    # Step 5b: Accumulation & volume signatures (Section 5)
    for base in bases:
        _enrich_accumulation(daily, daily_with_ma, weekly, base)

    # Ranges within each base
    for base in bases:
        _enrich_ranges(daily, base)

    # Step 7: Pivot points (Section 7) — must run after tight areas
    for base in bases:
        _enrich_pivots(daily_with_ma, base)

    # Step 8: Priming patterns (Section 8) — must run after pivots
    for base in bases:
        _detect_priming_patterns(daily, daily_with_ma, base)

    return bases


def find_all_bases_summary(daily: pd.DataFrame, **kwargs) -> list[dict]:
    """Convenience wrapper that returns list of dicts (JSON-friendly)."""
    return [b.to_dict() for b in find_all_bases(daily, **kwargs)]
