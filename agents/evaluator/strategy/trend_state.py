"""Trend-state / base-quality gate — is this a CONSOLIDATION within an uptrend,
or a DECLINE / falling knife?

The single biggest quality distinction in Kell/Minervini: a breakout from a base
that formed *above a rising longer MA, near the highs* (a healthy pause in an
uptrend) is a real setup; a "breakout" while price is below falling MAs, deep off
the highs, is a dead-cat bounce off a decline. This provider records the features
that separate the two, incrementally and leak-free.

Emits at the signal bar:
  above_50sma / above_200sma            — price vs the intermediate/long MA
  sma50_slope_pct / sma200_slope_pct    — % change of the MA over `slope_lookback`
                                          (rising = uptrend; falling = decline)
  sma50_rising / sma200_rising          — slope > `min_slope_pct`
  pct_from_252d_high                    — how far below the 52-week high (0 = at highs)
  bars_since_252d_high
  base_in_uptrend                       — THE gate: above a rising 50 SMA AND within
                                          `max_pullback_pct` of the 52-week high
"""
from __future__ import annotations

from collections import deque

from ..engine.records import Bar


class TrendState:
    def __init__(self, *, sma_periods=(50, 200), slope_lookback: int = 21,
                 min_slope_pct: float = 0.0, high_lookback: int = 252,
                 max_pullback_pct: float = 25.0, pivot_window: int = 5):
        self.sma_periods = tuple(sma_periods)
        self.slope_lookback = slope_lookback
        self.min_slope_pct = min_slope_pct
        self.high_lookback = high_lookback
        self.max_pullback_pct = max_pullback_pct
        self.pivot_window = pivot_window
        self._win = {p: deque(maxlen=p) for p in self.sma_periods}
        # rolling history of each SMA value (for the slope) + the high window
        self._sma_hist = {p: deque(maxlen=slope_lookback + 1) for p in self.sma_periods}
        self._high_win: deque[float] = deque(maxlen=high_lookback)
        # swing-pivot structure (higher highs / lower highs in the run)
        self._hi_buf: deque[float] = deque(maxlen=2 * pivot_window + 1)
        self._lo_buf: deque[float] = deque(maxlen=2 * pivot_window + 1)
        self._swing_highs: deque[float] = deque(maxlen=6)
        self._swing_lows: deque[float] = deque(maxlen=6)
        self._close = None

    def update(self, bar: Bar) -> None:
        self._close = bar.close
        self._high_win.append(bar.high)
        for p in self.sma_periods:
            self._win[p].append(bar.close)
            if len(self._win[p]) == p:
                self._sma_hist[p].append(sum(self._win[p]) / p)
        # confirmed swing pivots (center of the window is the extreme), causal
        self._hi_buf.append(bar.high); self._lo_buf.append(bar.low)
        w = self.pivot_window
        if len(self._hi_buf) == 2 * w + 1:
            if self._hi_buf[w] == max(self._hi_buf):
                self._swing_highs.append(self._hi_buf[w])
            if self._lo_buf[w] == min(self._lo_buf):
                self._swing_lows.append(self._lo_buf[w])

    def _sma(self, p):
        return self._sma_hist[p][-1] if self._sma_hist[p] else None

    def _slope_pct(self, p):
        h = self._sma_hist[p]
        if len(h) < self.slope_lookback + 1 or h[0] <= 0:
            return None
        return round((h[-1] / h[0] - 1) * 100, 2)

    def features(self, bar: Bar) -> dict:
        c = self._close
        out = {}
        for p in self.sma_periods:
            sma, slope = self._sma(p), self._slope_pct(p)
            out[f"above_{p}sma_ts"] = (c > sma) if sma else None
            out[f"sma{p}_slope_pct"] = slope
            out[f"sma{p}_rising"] = (slope > self.min_slope_pct) if slope is not None else None
        # distance below the 52-week high (0 = breaking out at new highs)
        hi = max(self._high_win) if self._high_win else None
        out["pct_from_252d_high"] = round((c / hi - 1) * 100, 2) if hi and hi > 0 else None
        out["bars_since_252d_high"] = (
            (len(self._high_win) - 1 - _argmax(self._high_win)) if self._high_win else None)
        # NOTE: the old Minervini `base_in_uptrend` (rising-50-SMA + 25%-from-high)
        # is RETIRED — superseded by the Kell 3-state `trend_state` / `weekly_trend_state`
        # (EMA-slope ±band + swing-high), which is the real base/uptrend/downtrend read.

        # ── swing structure: higher highs / lower highs in the run (Kell's
        #    "wedging" warning = new highs that sell back = lower highs) ────────
        sh, sl = list(self._swing_highs), list(self._swing_lows)
        out["making_higher_high"] = (sh[-1] > sh[-2]) if len(sh) >= 2 else None
        out["making_higher_low"] = (sl[-1] > sl[-2]) if len(sl) >= 2 else None
        # net up-steps among the last 4 swing highs / lows (structure trend)
        recent_h = sh[-4:]
        out["higher_highs_count"] = (
            sum(1 for a, b in zip(recent_h, recent_h[1:]) if b > a) if len(recent_h) >= 2 else None)
        out["lower_highs_count"] = (
            sum(1 for a, b in zip(recent_h, recent_h[1:]) if b < a) if len(recent_h) >= 2 else None)
        # clean structural gate: uptrend structure = higher high AND higher low
        if len(sh) >= 2 and len(sl) >= 2:
            out["uptrend_structure"] = bool(sh[-1] > sh[-2] and sl[-1] > sl[-2])
        else:
            out["uptrend_structure"] = None
        return out


def _argmax(dq) -> int:
    best_i, best_v = 0, None
    for i, v in enumerate(dq):
        if best_v is None or v > best_v:
            best_i, best_v = i, v
    return best_i
