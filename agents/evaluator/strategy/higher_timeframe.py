"""Higher-timeframe (weekly / monthly) extension from the EMAs — Kell's gauge.

Oliver Kell repeatedly reads "how extended is price from the **Weekly 10 EMA**"
(his preferred "needs to base" gauge) and the monthly 10 EMA. This provider
maintains those EMAs *incrementally from the daily bar stream* — no separate
weekly/monthly cache, no lookahead.

Mechanic: a weekly EMA is the EMA of weekly closes. We keep the EMA "committed"
through the last COMPLETED week; the *live* value at any daily bar is one more EMA
step using the current in-progress week's close-so-far (= the latest daily close) —
exactly what a live weekly chart shows mid-week. On a week/month rollover the just-
finished period's final close is committed. Point-in-time by construction: only
closes up to the current daily bar are ever used.
"""
from __future__ import annotations

from ..engine.records import Bar


class HigherTimeframeExtension:
    def __init__(self, weekly_emas=(10, 20), monthly_emas=(10,)):
        self.weekly_emas = tuple(weekly_emas)
        self.monthly_emas = tuple(monthly_emas)
        # weekly state
        self._w_key = None            # current ISO (year, week)
        self._w_last_close = None     # last daily close seen within the current week
        self._w_ema = {p: None for p in self.weekly_emas}   # committed through last COMPLETED week
        self._w_count = 0             # completed weeks
        # monthly state
        self._m_key = None
        self._m_last_close = None
        self._m_ema = {p: None for p in self.monthly_emas}
        self._m_count = 0
        self._cur_close = None

    def update(self, bar: Bar) -> None:
        self._cur_close = bar.close
        wk = tuple(bar.date.isocalendar()[:2])     # (iso_year, iso_week)
        mk = (bar.date.year, bar.date.month)
        # weekly rollover — commit the week that just ended (its final close)
        if self._w_key is not None and wk != self._w_key:
            for p in self.weekly_emas:
                self._w_ema[p] = self._step(self._w_ema[p], self._w_last_close, p)
            self._w_count += 1
        self._w_key, self._w_last_close = wk, bar.close
        # monthly rollover
        if self._m_key is not None and mk != self._m_key:
            for p in self.monthly_emas:
                self._m_ema[p] = self._step(self._m_ema[p], self._m_last_close, p)
            self._m_count += 1
        self._m_key, self._m_last_close = mk, bar.close

    @staticmethod
    def _step(ema, close, span):
        a = 2.0 / (span + 1)
        return close if ema is None else a * close + (1 - a) * ema

    def _live(self, committed, count, span):
        """Live EMA including the in-progress period's close-so-far. Needs >= span
        completed periods so the EMA is seasoned (else None)."""
        if committed is None or count < span:
            return None
        return self._step(committed, self._cur_close, span)

    def features(self, bar: Bar) -> dict:
        out = {}
        c = self._cur_close
        for p in self.weekly_emas:
            live = self._live(self._w_ema[p], self._w_count, p)
            out[f"close_ext_{p}wema_pct"] = round((c / live - 1) * 100, 2) if live else None
            out[f"above_{p}wema"] = (c > live) if live else None
        for p in self.monthly_emas:
            live = self._live(self._m_ema[p], self._m_count, p)
            out[f"close_ext_{p}mema_pct"] = round((c / live - 1) * 100, 2) if live else None
            out[f"above_{p}mema"] = (c > live) if live else None
        return out
