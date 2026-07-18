"""Weekly-timeframe Kell cycle — the higher-timeframe trend context.

Kell is fractal/multi-timeframe: a daily entry is taken *within* a weekly trend.
This runs the **same** `KellCycle` uptrend state machine on **weekly bars**,
aggregated incrementally from the daily stream (no separate cache). At a daily
signal bar it reports the weekly trend state **as of the last COMPLETED week**
(point-in-time — the in-progress week is not fed until it closes, so there is no
lookahead into the rest of the current week).

Emitted (all prefixed ``weekly_``): trend state (`in_uptrend`, `legs`,
`from_reversal`, `bars_since_start` in *weeks*, exhaustion count in the run) and
weeks-since the last weekly wedge pop / drop.
"""
from __future__ import annotations

from ..engine.records import Bar
from .kell import KellCycle


class WeeklyKellContext:
    def __init__(self, **kell_kwargs):
        self._kell = KellCycle(**kell_kwargs)
        self._wk_key = None
        self._o = self._h = self._l = self._c = None
        self._v = 0.0
        self._last_date = None
        self._completed_close = None

    def update(self, bar: Bar) -> None:
        wk = tuple(bar.date.isocalendar()[:2])          # (iso_year, iso_week)
        if self._wk_key is not None and wk != self._wk_key:
            # the week just ended → feed its aggregated weekly bar to the cycle
            self._kell.update(Bar(date=self._last_date, open=self._o, high=self._h,
                                  low=self._l, close=self._c, volume=self._v))
            self._completed_close = self._c             # last COMPLETED weekly close
            self._o = None
        if self._o is None:                              # start a new week
            self._o, self._h, self._l, self._v = bar.open, bar.high, bar.low, 0.0
        self._h = max(self._h, bar.high)
        self._l = min(self._l, bar.low)
        self._c = bar.close
        self._v += bar.volume
        self._wk_key = wk
        self._last_date = bar.date

    def features(self, bar: Bar) -> dict:
        snap = self._kell.uptrend_snapshot()             # as of the last COMPLETED week
        # 3-state weekly trend vs the last COMPLETED weekly close (leak-free)
        wstate, wslope = self._kell.trend_state(self._completed_close) \
            if self._completed_close is not None else (None, None)
        return {
            "weekly_trend_state": wstate,
            "weekly_prev_trend_state": self._kell.prev_trend_state(),
            "weekly_trend_slope_pct": wslope,
            "weekly_in_uptrend": snap["in_uptrend"],
            "weekly_uptrend_legs": snap["legs"],
            "weekly_uptrend_from_reversal": snap["from_reversal"],
            "weekly_uptrend_weeks": snap["bars_since_start"],
            "weekly_exh_since_uptrend": snap["exh_since_uptrend"],
            "weekly_exh_since_downtrend": snap["exh_since_downtrend"],
            "weekly_exh_since_base": snap["exh_since_base"],
            "weekly_wedge_pop_weeks_since": snap["wedge_pop_bars_since"],
            "weekly_wedge_drop_weeks_since": snap["wedge_drop_bars_since"],
        }
