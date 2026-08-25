"""Weekly Kell trend regime of the major market indices, as per-setup features.

Runs the SAME weekly 3-state trend cycle used per stock (``WeeklyKellContext``,
same config the strategy uses for the stock's weekly trend) on each broad-market
index, and at a signal bar reports each index's ``weekly_trend_state``
**point-in-time** (as of the last COMPLETED week on or before the bar). This lets
you filter/evaluate setups by broad-market regime — Bonde's "situational
awareness".

Indices: S&P 500 = SPY, Russell 2000 = IWM, S&P SmallCap 600 = IJR,
S&P MidCap 400 = IJH. Each index's per-date weekly-state series is computed ONCE
per (symbol, config) and cached at module scope (so building the whole universe
doesn't recompute it 1,000+ times). Compute is cache-only — ingest the ETFs first.

Emitted per index ``tag`` (spy/iwm/ijr/ijh): ``{tag}_weekly_state`` (uptrend /
basing / downtrend / None) and ``{tag}_risk_on`` (True when uptrend or basing —
the sticky, non-flip-flopping regime read; downtrend = risk-off).
"""
from __future__ import annotations

import pandas as pd

from ..data import store
from ..engine.records import Bar
from .weekly_kell import WeeklyKellContext

DEFAULT_INDICES = {"spy": "SPY", "iwm": "IWM", "ijr": "IJR", "ijh": "IJH"}

_CACHE: dict = {}   # (symbol, ext_ref_ema, slope, pivot) -> DataFrame[date -> state]


def index_weekly_states(symbol, *, ext_ref_ema, slope_window, pivot_window):
    """Cached per-date DataFrame (columns: state, slope) of the weekly trend for an
    index, point-in-time. Empty DataFrame if the symbol isn't cached."""
    key = (symbol, ext_ref_ema, slope_window, pivot_window)
    if key in _CACHE:
        return _CACHE[key]
    try:
        px = store.load(symbol)
    except Exception:
        px = None
    if px is None or not len(px):
        _CACHE[key] = pd.DataFrame(columns=["state", "slope"])
        return _CACHE[key]
    wk = WeeklyKellContext(ema_periods=(10, 20), ext_ref_ema=ext_ref_ema,
                           trend_slope_window=slope_window, trend_pivot_window=pivot_window)
    idx, states, slopes = [], [], []
    for ts, r in px.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        wk.update(Bar(date=d, open=r["Open"], high=r["High"], low=r["Low"],
                      close=r["Close"], volume=r["Volume"]))
        f = wk.features(None)                       # weekly features ignore the bar arg
        idx.append(pd.Timestamp(d)); states.append(f["weekly_trend_state"])
        slopes.append(f["weekly_trend_slope_pct"])
    df = pd.DataFrame({"state": states, "slope": slopes}, index=pd.DatetimeIndex(idx))
    _CACHE[key] = df
    return df


class IndexRegime:
    """Provider: weekly trend state of the broad indices as-of each signal bar."""

    def __init__(self, indices=None, *, ext_ref_ema: int = 20,
                 slope_window: int = 5, pivot_window: int = 5):
        self.indices = indices if indices is not None else DEFAULT_INDICES
        self._cfg = dict(ext_ref_ema=ext_ref_ema, slope_window=slope_window,
                         pivot_window=pivot_window)
        self._series: dict = {}
        self._loaded = False

    def _load(self) -> None:
        for tag, sym in self.indices.items():
            self._series[tag] = index_weekly_states(sym, **self._cfg)
        self._loaded = True

    def update(self, bar: Bar) -> None:
        """Stateless (series are precomputed); kept for provider-shape symmetry."""

    def features(self, bar: Bar) -> dict:
        if not self._loaded:
            self._load()
        bd = pd.Timestamp(bar.date)
        out: dict = {}
        for tag, df in self._series.items():
            state = None
            if len(df):
                i = df.index.searchsorted(bd, side="right") - 1   # last row on/before bd
                if i >= 0:
                    state = df["state"].iloc[i]
            out[f"{tag}_weekly_state"] = state
            out[f"{tag}_risk_on"] = (state in ("uptrend", "basing")) if state is not None else None
        return out
