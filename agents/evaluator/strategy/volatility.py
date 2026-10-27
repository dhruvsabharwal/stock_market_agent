"""Per-stock volatility — the risk unit behind Jeff Sun's R mechanism.

His whole method is built on the fact that **a flat % stop is the wrong unit**:
a 4% stop is ~2 ATR of room on a calm 2%-ATR name but only ~0.5 ATR (pure noise)
on a 8%-ATR mover. So risk, stops and extension should be measured in the
*stock's own* volatility, not a fixed percentage. This provider records that
unit, point-in-time as of the signal bar:

  * ``adr_pct``  — Average Daily Range %, the trader-standard gauge Jeff Sun uses
    for sizing / instrument selection: ``100 * (mean_N(High/Low) - 1)`` over
    ``adr_window`` bars (a range-only measure; ignores overnight gaps).
  * ``atr_pct``  — ATR% = ATR(``atr_window``) / close * 100, the gap-INCLUSIVE
    measure (Wilder true range). Jeff Sun measures extension-from-MA in ATR "because
    it includes gaps." ``atr`` is the same in dollars (for exact-dollar R downstream).
  * ``ext_50sma_atr`` / ``ext_50sma_adr`` — how far price sits above the 50-SMA in
    ATR / ADR multiples. Jeff Sun's focus-list gate is ``< 4x`` (room to run) and his
    scale-out zone is ``8-10x`` — real per-stock numbers, not a flat %.

All causal: every value uses only bars on/before the current bar, so it is known
at the signal date and there is no lookahead (entry fills at the next open).

ADR% note: ``100 * (mean(High/Low) - 1)`` is the Qullamaggie / TradingView "ADR%"
Jeff Sun references. ATR uses a simple rolling mean of true range (matching
``engine/exits.PriceData.atr``), not Wilder's smoothing — consistent across the codebase.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from ..engine.records import Bar


def _rnd(x: Optional[float], n: int = 3) -> Optional[float]:
    return round(float(x), n) if x is not None else None


class Volatility:
    def __init__(self, *, adr_window: int = 20, atr_window: int = 14,
                 ext_sma: int = 50, contraction_lookback: int = 40,
                 contraction_box_cap: int = 260):
        self.adr_window = adr_window
        self.atr_window = atr_window
        self.ext_sma = ext_sma
        # contraction = the 20-day ADR% now vs the same window `lookback` bars ago
        # (Jeff Sun reads the *trend* of the ADR% line, not a fixed 30/30 split).
        self.contraction_lookback = contraction_lookback
        self.contraction_box_cap = contraction_box_cap        # max bars for box-relative contraction
        # keep a couple extra bars: ATR needs the prior close for true range.
        span = max(adr_window, atr_window, ext_sma,
                   contraction_lookback + adr_window, contraction_box_cap) + 2
        self._h: deque = deque(maxlen=span)
        self._l: deque = deque(maxlen=span)
        self._c: deque = deque(maxlen=span)

    def update(self, bar: Bar) -> None:
        self._h.append(float(bar.high))
        self._l.append(float(bar.low))
        self._c.append(float(bar.close))

    # ── the two volatility gauges ────────────────────────────────────────────
    def _adr_pct(self) -> Optional[float]:
        """100 * (mean(High/Low) - 1) over the last adr_window bars."""
        n = self.adr_window
        if len(self._h) < n:
            return None
        ratios = [self._h[-i] / self._l[-i]
                  for i in range(1, n + 1) if self._l[-i] > 0]
        if len(ratios) < n:
            return None
        return (sum(ratios) / n - 1.0) * 100.0

    def _atr(self) -> Optional[float]:
        """Simple rolling mean of true range over atr_window bars (dollars)."""
        n = self.atr_window
        if len(self._c) < n + 1:
            return None
        trs = []
        for i in range(1, n + 1):
            h, l, pc = self._h[-i], self._l[-i], self._c[-i - 1]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs) / n

    def _sma(self, n: int) -> Optional[float]:
        if len(self._c) < n:
            return None
        return sum(list(self._c)[-n:]) / n

    # ── volatility CONTRACTION (is the daily range compressing?) ─────────────
    # A per-bar volatility series (ADR-style and ATR-style), indexed from the most
    # recent bar (offset 0) backwards, so we can average any window ending `offset`
    # bars ago and compare "now" vs "then" — a proxy for Jeff Sun's ADR%-line trend.
    def _win_mean_adr(self, offset: int, w: int) -> Optional[float]:
        """Mean per-bar range % ((High/Low - 1)*100) over ``w`` bars ending
        ``offset`` bars ago (offset 0 = the most recent ``w`` bars)."""
        if len(self._h) < offset + w:
            return None
        vals = [(self._h[-(offset + i)] / self._l[-(offset + i)] - 1.0) * 100.0
                for i in range(1, w + 1) if self._l[-(offset + i)] > 0]
        return sum(vals) / len(vals) if len(vals) == w else None

    def _win_mean_tr(self, offset: int, w: int) -> Optional[float]:
        """Mean per-bar true-range % (TR/close*100) over ``w`` bars ending
        ``offset`` bars ago (TR needs each bar's prior close)."""
        if len(self._c) < offset + w + 1:
            return None
        vals = []
        for i in range(1, w + 1):
            j = offset + i
            h, l, c, pc = self._h[-j], self._l[-j], self._c[-j], self._c[-j - 1]
            if c > 0:
                vals.append(max(h - l, abs(h - pc), abs(l - pc)) / c * 100.0)
        return sum(vals) / len(vals) if len(vals) == w else None

    @staticmethod
    def _ratio_pct(now: Optional[float], then: Optional[float]) -> Optional[float]:
        """(now / then - 1) * 100. Negative = contracting (now is quieter)."""
        if now is None or then is None or then <= 0:
            return None
        return (now / then - 1.0) * 100.0

    def _contraction_now_vs_ago(self) -> tuple:
        """ADR%/ATR% now vs the same window ``contraction_lookback`` bars ago."""
        lb, aw, tw = self.contraction_lookback, self.adr_window, self.atr_window
        adr = self._ratio_pct(self._win_mean_adr(0, aw), self._win_mean_adr(lb, aw))
        atr = self._ratio_pct(self._win_mean_tr(0, tw), self._win_mean_tr(lb, tw))
        return adr, atr

    def _contraction_box(self, box_len: int) -> tuple:
        """ADR%/ATR% over the LAST half of the base vs the FIRST half (the base's
        own compression). Uses the detected box length, capped to the buffer."""
        box_len = min(box_len, self.contraction_box_cap, len(self._c) - 1)
        half = box_len // 2
        if half < 3:
            return None, None
        adr = self._ratio_pct(self._win_mean_adr(0, half), self._win_mean_adr(half, half))
        atr = self._ratio_pct(self._win_mean_tr(0, half), self._win_mean_tr(half, half))
        return adr, atr

    def features(self, bar: Bar, box_len: Optional[int] = None) -> dict:
        out = {
            "adr_pct": None, "atr_pct": None, "atr": None,
            f"ext_{self.ext_sma}sma_atr": None,
            f"ext_{self.ext_sma}sma_adr": None,
            # contraction: negative = daily range is compressing
            "adr_contraction_pct": None, "atr_contraction_pct": None,
            "adr_contraction_box_pct": None, "atr_contraction_box_pct": None,
            "vol_contracting": None,
        }
        close = float(bar.close)
        if close <= 0:
            return out

        adr = self._adr_pct()
        atr = self._atr()
        out["adr_pct"] = _rnd(adr)
        out["atr"] = _rnd(atr, 4)
        out["atr_pct"] = _rnd(atr / close * 100.0) if atr is not None else None

        sma = self._sma(self.ext_sma)
        if sma is not None:
            dist = close - sma                       # dollars above the 50-SMA
            if atr is not None and atr > 0:
                out[f"ext_{self.ext_sma}sma_atr"] = _rnd(dist / atr, 2)
            if adr is not None and adr > 0:
                adr_dollar = adr / 100.0 * close     # ADR in dollars
                out[f"ext_{self.ext_sma}sma_adr"] = _rnd(dist / adr_dollar, 2)

        # contraction — now vs `lookback` bars ago (the ADR%-line trend)
        adr_c, atr_c = self._contraction_now_vs_ago()
        out["adr_contraction_pct"] = _rnd(adr_c, 2)
        out["atr_contraction_pct"] = _rnd(atr_c, 2)
        if atr_c is not None:
            out["vol_contracting"] = atr_c < 0       # any compression vs `lookback` ago
        # contraction — the base's own first-half vs last-half (when a box exists)
        if box_len is not None and box_len >= 6:
            adr_b, atr_b = self._contraction_box(box_len)
            out["adr_contraction_box_pct"] = _rnd(adr_b, 2)
            out["atr_contraction_box_pct"] = _rnd(atr_b, 2)
        return out
