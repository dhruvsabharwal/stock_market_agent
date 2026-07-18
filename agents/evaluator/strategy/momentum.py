"""Trailing returns — the stock's own momentum and the market's.

  * ``TrailingReturns`` — the stock's % gain over trailing windows (months and/or
    trading days), a read on trend strength and how *fresh* the uptrend is.
  * ``MarketReturns`` — the same windows for a benchmark (default ^GSPC, the S&P
    500), a read on whether the broad market was rising at the signal.

Both are point-in-time: only prices on/before the signal bar are used.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import pandas as pd

from ..engine.records import Bar


def _ret(cur: float, base: Optional[float]) -> Optional[float]:
    if base is None or base <= 0 or cur <= 0:
        return None
    return round((cur / base - 1) * 100, 2)


class TrailingReturns:
    """Stock trailing returns from the fed bars. ``months`` are date-based,
    ``days`` are trading-day-based (uses the price ``days`` bars ago)."""

    def __init__(self, *, months=(1, 3, 6, 12), days=()):
        self.months = list(months)
        self.days = list(days)
        span = max([int(max(self.months, default=0) * 22), max(self.days, default=0)]) + 40
        self._buf: deque = deque(maxlen=max(span, 5))   # (date, close)

    def update(self, bar: Bar) -> None:
        self._buf.append((bar.date, bar.close))

    def features(self, bar: Bar) -> dict:
        out = {f"ret_{m}m": None for m in self.months}
        out.update({f"ret_{d}d": None for d in self.days})
        if not self._buf:
            return out
        cur = bar.close
        for m in self.months:
            cutoff = bar.date - pd.DateOffset(months=m)
            base = next((c for d, c in reversed(self._buf) if d <= cutoff), None)
            out[f"ret_{m}m"] = _ret(cur, base)
        for d in self.days:
            out[f"ret_{d}d"] = _ret(cur, self._buf[-1 - d][1]) if len(self._buf) > d else None
        return out


class MarketReturns:
    """Benchmark trailing returns, loaded once (cache-on-first-use)."""

    def __init__(self, *, benchmark: str = "SPY", months=(1, 3, 6, 12), days=()):
        self.benchmark = benchmark
        self.months = list(months)
        self.days = list(days)
        self._closes: Optional[pd.Series] = None
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        try:
            from ..data import store
            self._closes = store.load(self.benchmark)["Close"]   # cache-only, no fetch
        except Exception:
            self._closes = None                                  # not ingested -> None features

    def features(self, bar: Bar) -> dict:
        if not self._loaded:
            self._load()
        out = {f"mkt_ret_{m}m": None for m in self.months}
        out.update({f"mkt_ret_{d}d": None for d in self.days})
        if self._closes is None:
            return out
        asof = self._closes[self._closes.index <= bar.date]   # point-in-time
        if asof.empty:
            return out
        cur = float(asof.iloc[-1])
        for m in self.months:
            base = asof[asof.index <= bar.date - pd.DateOffset(months=m)]
            out[f"mkt_ret_{m}m"] = _ret(cur, float(base.iloc[-1]) if not base.empty else None)
        for d in self.days:
            out[f"mkt_ret_{d}d"] = _ret(cur, float(asof.iloc[-1 - d]) if len(asof) > d else None)
        return out


class MarketRegime:
    """Index regime gauge (Kell: QQQ vs its 20 EMA = the cash/margin switch).

    Loads the index close once (cache-only) and emits, point-in-time as of the
    signal bar: trailing returns, whether the index is above its 20-EMA / 50-SMA
    (risk-on vs raise-cash), and its extension above the 20-EMA (regime strength).
    EMA/SMA are causal (backward-looking) so there is no lookahead."""

    def __init__(self, *, index: str = "QQQ", ema: int = 20, sma: int = 50,
                 months=(1, 3, 6, 12)):
        self.index = index
        self.ema_n = ema
        self.sma_n = sma
        self.months = list(months)
        self._close = None
        self._ema = None
        self._sma = None
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        try:
            from ..data import store
            self._close = store.load(self.index)["Close"]
            self._ema = self._close.ewm(span=self.ema_n, adjust=False).mean()
            self._sma = self._close.rolling(self.sma_n).mean()
        except Exception:
            self._close = None

    def features(self, bar: Bar) -> dict:
        if not self._loaded:
            self._load()
        px = self.index.lower()
        out = {f"{px}_ret_{m}m": None for m in self.months}
        out.update({f"{px}_above_{self.ema_n}ema": None,
                    f"{px}_above_{self.sma_n}sma": None,
                    f"{px}_ext_{self.ema_n}ema_pct": None})
        if self._close is None:
            return out
        asof = self._close[self._close.index <= bar.date]
        if asof.empty:
            return out
        cur = float(asof.iloc[-1])
        for m in self.months:
            base = asof[asof.index <= bar.date - pd.DateOffset(months=m)]
            out[f"{px}_ret_{m}m"] = _ret(cur, float(base.iloc[-1]) if not base.empty else None)
        d = asof.index[-1]
        e, s = float(self._ema.loc[d]), self._sma.loc[d]
        out[f"{px}_above_{self.ema_n}ema"] = cur > e
        out[f"{px}_ext_{self.ema_n}ema_pct"] = round((cur / e - 1) * 100, 2) if e else None
        out[f"{px}_above_{self.sma_n}sma"] = (cur > float(s)) if pd.notna(s) else None
        return out


class RelativeStrength:
    """Cross-sectional RS rank (O'Neil/Minervini "RS rating"): the stock's
    trailing return ranked vs the whole universe on the signal date, as a 0–100
    percentile. Precomputed by ``data.relative_strength.build_rs_cache`` and
    loaded cache-only (None if the RS cache isn't built)."""

    def __init__(self, *, months=(1, 3, 6, 12)):
        self.months = list(months)
        self._rs = None
        self._loaded = False

    def _load(self, ticker: str) -> None:
        self._loaded = True
        from ..data.relative_strength import RSHistory
        self._rs = RSHistory.load(ticker)   # cache-only

    def features(self, bar: Bar, ticker: str) -> dict:
        if not self._loaded:
            self._load(ticker)
        out = {f"rs_rank_{m}m": None for m in self.months}
        row = self._rs.rank_as_of(bar.date) if self._rs is not None else None
        if row:
            for m in self.months:
                v = row.get(f"rs_{m}m")
                out[f"rs_rank_{m}m"] = round(float(v), 1) if v is not None and v == v else None
        return out
