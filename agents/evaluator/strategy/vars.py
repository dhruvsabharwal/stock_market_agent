"""VARS — Volatility-Adjusted Relative Strength (Jeff Sun).

Raw RS (``stock_ret − bench_ret``) is unfair across volatility regimes: *"a 10%
move in a 20%-ADR name should not count as a relative-strength day if SPY is up
2% on a 1% ADR."* VARS fixes that by expressing each side's trailing return in
**its own daily-range units**, then differencing:

    vars_{m}m  =  stock_ret_{m}m / stock_atr_pct   −   bench_ret_{m}m / bench_atr_pct

His example: 10/20 = 0.5 vs 2/1 = 2.0  →  vars = −1.5  (the stock is *weaker*
despite the bigger raw %). We normalise by **ATR%** (gap-inclusive) per the user's
choice. Emitted per horizon:

  * ``vars_spy_{m}m`` — vs SPY, for EVERY name (the universal baseline).
  * ``vars_{m}m``     — vs the name's resolved group benchmark (semis→SMH, miner→
    BTC-USD, …); equals the SPY value when the name is unmapped (benchmark→SPY).
  * ``rs_benchmark``  — which symbol the group VARS used (so analysis can slice).

Point-in-time: the stock's ATR% comes from the signal-bar ``Volatility`` provider;
each benchmark's returns + ATR% are read from its cached prices sliced to ≤ the
signal bar. No lookahead. Benchmarks with no cached prices yield None (not an error).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..engine.records import Bar
from . import benchmark_map


def _ratio(ret: Optional[float], vol: Optional[float]) -> Optional[float]:
    """return / volatility, guarding None and non-positive volatility."""
    if ret is None or vol is None or vol <= 0:
        return None
    return ret / vol


class _BenchStats:
    """Point-in-time trailing returns + ATR% for one benchmark symbol (cache-only)."""

    def __init__(self, symbol: str, *, months, atr_window: int = 14):
        self.symbol = symbol
        self.months = list(months)
        self.atr_window = atr_window
        self._close: Optional[pd.Series] = None
        self._atr_pct: Optional[pd.Series] = None
        self._load()

    def _load(self) -> None:
        try:
            from ..data import store
            df = store.load(self.symbol)          # cache-only
        except Exception:
            return
        h, l, c = df["High"].astype(float), df["Low"].astype(float), df["Close"].astype(float)
        pc = c.shift(1)
        tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
        atr = tr.rolling(self.atr_window).mean()
        self._close = c
        self._atr_pct = (atr / c * 100.0)

    def as_of(self, date) -> dict:
        """{ret_{m}m: %, atr_pct: %} using only bars on/before ``date``."""
        out = {f"ret_{m}m": None for m in self.months}
        out["atr_pct"] = None
        if self._close is None:
            return out
        asof = self._close[self._close.index <= date]
        if asof.empty:
            return out
        cur = float(asof.iloc[-1])
        for m in self.months:
            base = asof[asof.index <= date - pd.DateOffset(months=m)]
            if not base.empty and float(base.iloc[-1]) > 0:
                out[f"ret_{m}m"] = round((cur / float(base.iloc[-1]) - 1) * 100, 2)
        ap = self._atr_pct[self._atr_pct.index <= date].dropna()
        if not ap.empty:
            out["atr_pct"] = float(ap.iloc[-1])
        return out


class VolAdjRelativeStrength:
    """VARS vs SPY (all names) + vs the ticker's resolved group benchmark.

    Benchmarks are resolved once per ticker (on first use) from the cached
    sector/industry via ``benchmark_map``. SPY stats are shared; the group
    benchmark reuses the SPY loader when it resolves to SPY (unmapped names)."""

    def __init__(self, *, months=(1, 3, 6, 12), atr_window: int = 14):
        self.months = list(months)
        self.atr_window = atr_window
        self._spy: Optional[_BenchStats] = None
        self._bench: Optional[_BenchStats] = None
        self._bench_symbol: Optional[str] = None
        self._loaded = False

    def _load(self, ticker: str) -> None:
        self._loaded = True
        self._spy = _BenchStats("SPY", months=self.months, atr_window=self.atr_window)
        sym = benchmark_map.resolve_cached(ticker)
        self._bench_symbol = sym
        self._bench = self._spy if sym == "SPY" else _BenchStats(
            sym, months=self.months, atr_window=self.atr_window)

    def features(self, bar: Bar, stock_ret: dict, stock_atr_pct: Optional[float],
                 ticker: str) -> dict:
        if not self._loaded:
            self._load(ticker)
        out = {f"vars_spy_{m}m": None for m in self.months}
        out.update({f"vars_{m}m": None for m in self.months})
        out["rs_benchmark"] = self._bench_symbol

        spy = self._spy.as_of(bar.date) if self._spy else {}
        bench = self._bench.as_of(bar.date) if self._bench else {}
        for m in self.months:
            stock_side = _ratio(stock_ret.get(f"ret_{m}m"), stock_atr_pct)
            if stock_side is None:
                continue
            spy_side = _ratio(spy.get(f"ret_{m}m"), spy.get("atr_pct"))
            bench_side = _ratio(bench.get(f"ret_{m}m"), bench.get("atr_pct"))
            if spy_side is not None:
                out[f"vars_spy_{m}m"] = round(stock_side - spy_side, 3)
            if bench_side is not None:
                out[f"vars_{m}m"] = round(stock_side - bench_side, 3)
        return out
