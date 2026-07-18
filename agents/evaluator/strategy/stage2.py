"""Minervini-style Stage 2 trend template — incremental, no lookahead.

Fed one daily bar at a time (``update``); ``criteria`` returns the six checks as
of the latest bar, ``passes`` is their AND. Every check uses only bars already
delivered:

  1. price > 150-day SMA and > 200-day SMA
  2. 150-day SMA > 200-day SMA
  3. 200-day SMA turned up   (SMA200 today > SMA200 `slope_lookback` bars ago)
  4. higher highs and higher lows   (last two *confirmed* swing highs ascending
     AND last two confirmed swing lows ascending; a swing pivot is only confirmed
     once `pivot_window` later bars exist — so it is never forward-looking)
  5. up weeks carry more volume than down weeks   (mean up-week volume >
     mean down-week volume over the last `weeks_lookback` completed weeks)
  6. more up weeks than down weeks over the same window

Criteria that cannot yet be evaluated (not enough history) return False, so the
template only "passes" once a genuine confirmed uptrend exists.

Definitions for 3/4/5/6 are deliberately simple and tunable via the constructor.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from ..engine.records import Bar


@dataclass
class _Week:
    close: float
    volume: float


class Stage2TrendTemplate:
    def __init__(
        self,
        *,
        sma_fast: int = 150,
        sma_slow: int = 200,
        slope_lookback: int = 21,      # ~1 month: SMA200 "turned up" horizon
        min_slope_pct: float = 0.0,    # min % rise over the lookback to count as "rising"
        pivot_window: int = 10,        # bars each side to confirm a swing pivot
        weeks_lookback: int = 13,      # ~1 quarter for the volume-week checks
    ):
        self.sma_fast_n = sma_fast
        self.sma_slow_n = sma_slow
        self.slope_lookback = slope_lookback
        self.min_slope_pct = min_slope_pct
        self.pivot_window = pivot_window
        self.weeks_lookback = weeks_lookback

        self._fast: deque[float] = deque(maxlen=sma_fast)
        self._slow: deque[float] = deque(maxlen=sma_slow)
        self._slow_hist: deque[float] = deque(maxlen=slope_lookback + 1)

        # confirmed swing pivots (prices, oldest..newest)
        self._hi_buf: deque[tuple] = deque(maxlen=2 * pivot_window + 1)  # (high,)
        self._lo_buf: deque[tuple] = deque(maxlen=2 * pivot_window + 1)
        self._swing_highs: deque[float] = deque(maxlen=4)
        self._swing_lows: deque[float] = deque(maxlen=4)

        # weekly aggregation
        self._cur_week_key: Optional[tuple] = None
        self._cur_week: Optional[_Week] = None
        self._weeks: deque[_Week] = deque(maxlen=weeks_lookback + 1)

        self._last_close: Optional[float] = None

    # ── feed one bar ──────────────────────────────────────────────────────────
    def update(self, bar: Bar) -> None:
        self._last_close = bar.close
        self._fast.append(bar.close)
        self._slow.append(bar.close)
        if len(self._slow) == self.sma_slow_n:
            self._slow_hist.append(self._sma_slow())
        self._update_pivots(bar)
        self._update_week(bar)

    # ── the six criteria ───────────────────────────────────────────────────--
    def criteria(self) -> dict:
        c1 = self._c1_above_mas()
        c2 = self._c2_fast_above_slow()
        c3 = self._c3_slow_turned_up()
        c4 = self._c4_higher_highs_lows()
        c5 = self._c5_up_week_volume()
        c6 = self._c6_more_up_weeks()
        return {
            "above_150_200": c1,
            "sma150_above_200": c2,
            "sma200_rising": c3,
            "higher_highs_lows": c4,
            "up_week_volume": c5,
            "more_up_weeks": c6,
        }

    def passes(self) -> bool:
        return all(self.criteria().values())

    # ── helpers ────────────────────────────────────────────────────────────--
    def _sma_fast(self) -> Optional[float]:
        return sum(self._fast) / self.sma_fast_n if len(self._fast) == self.sma_fast_n else None

    def _sma_slow(self) -> Optional[float]:
        return sum(self._slow) / self.sma_slow_n if len(self._slow) == self.sma_slow_n else None

    def _c1_above_mas(self) -> bool:
        f, s = self._sma_fast(), self._sma_slow()
        return f is not None and s is not None and self._last_close > f and self._last_close > s

    def _c2_fast_above_slow(self) -> bool:
        f, s = self._sma_fast(), self._sma_slow()
        return f is not None and s is not None and f > s

    def _c3_slow_turned_up(self) -> bool:
        # SMA200 must be rising by at least ``min_slope_pct`` over the lookback —
        # not merely a hair above one bar a month ago (a flat/rolled-over SMA that
        # ticks up +0.01 shouldn't count). min_slope_pct=0.0 = the old ">0" test.
        if len(self._slow_hist) < self.slope_lookback + 1:
            return False
        return self._slow_hist[-1] > self._slow_hist[0] * (1 + self.min_slope_pct / 100.0)

    def _c4_higher_highs_lows(self) -> bool:
        if len(self._swing_highs) < 2 or len(self._swing_lows) < 2:
            return False
        hh = self._swing_highs[-1] > self._swing_highs[-2]
        hl = self._swing_lows[-1] > self._swing_lows[-2]
        return hh and hl

    def _completed_weeks(self) -> list:
        # the deque holds up to weeks_lookback+1; treat all but possibly the
        # current (still-open) week as completed — the open week is never pushed
        # here (see _update_week), so all entries are completed.
        return list(self._weeks)[-self.weeks_lookback:]

    def _week_changes(self):
        """Yield (prev_close, week) pairs for completed weeks with a predecessor."""
        wk = self._completed_weeks()
        for i in range(1, len(wk)):
            yield wk[i - 1].close, wk[i]

    def _c5_up_week_volume(self) -> bool:
        up_vol, down_vol = [], []
        for prev_close, w in self._week_changes():
            (up_vol if w.close > prev_close else down_vol).append(w.volume)
        if not up_vol or not down_vol:
            return False
        return (sum(up_vol) / len(up_vol)) > (sum(down_vol) / len(down_vol))

    def _c6_more_up_weeks(self) -> bool:
        up = down = 0
        for prev_close, w in self._week_changes():
            if w.close > prev_close:
                up += 1
            elif w.close < prev_close:
                down += 1
        return (up + down) > 0 and up > down

    def _update_pivots(self, bar: Bar) -> None:
        self._hi_buf.append(bar.high)
        self._lo_buf.append(bar.low)
        w = self.pivot_window
        if len(self._hi_buf) == 2 * w + 1:
            center_h = self._hi_buf[w]
            if center_h == max(self._hi_buf) and center_h > self._hi_buf[w - 1]:
                self._swing_highs.append(center_h)
            center_l = self._lo_buf[w]
            if center_l == min(self._lo_buf) and center_l < self._lo_buf[w - 1]:
                self._swing_lows.append(center_l)

    def _update_week(self, bar: Bar) -> None:
        iso = bar.date.isocalendar()
        key = (iso[0], iso[1])
        if self._cur_week_key is None:
            self._cur_week_key = key
            self._cur_week = _Week(close=bar.close, volume=bar.volume)
        elif key != self._cur_week_key:
            # previous week completed -> push it, start a new one
            self._weeks.append(self._cur_week)
            self._cur_week_key = key
            self._cur_week = _Week(close=bar.close, volume=bar.volume)
        else:
            self._cur_week.close = bar.close          # last close of the week
            self._cur_week.volume += bar.volume       # weekly volume
