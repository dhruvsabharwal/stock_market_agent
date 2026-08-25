"""Weekly Kell trend regime for the major market indices — 'situational awareness'.

Runs the weekly 3-state trend machine (``WeeklyKellContext``, the wk1035 config:
weekly 10-EMA / 3-week slope / ±5 pivot) on an index's daily bars and reports its
``weekly_trend_state`` (uptrend / basing / downtrend) point-in-time (as of the last
COMPLETED week). Read the broad-market regime across the four indices a momentum
trader watches — S&P 500 (SPY), Russell 2000 (IWM), S&P SmallCap 600 (IJR), S&P
MidCap 400 (IJH). Compute is cache-only; fetch the ETFs first via ``store.get``.

    from agents.evaluator.eval import regime
    regime.snapshot()                       # current weekly state of all four
    df = regime.weekly_states('IWM')        # full per-day weekly_trend_state series
"""
from __future__ import annotations

import pandas as pd

from ..data import store
from ..engine.records import Bar
from ..strategy.weekly_kell import WeeklyKellContext

INDICES = {"S&P 500": "SPY", "Russell 2000": "IWM",
           "S&P SmallCap 600": "IJR", "S&P MidCap 400": "IJH"}


def weekly_states(symbol, *, ext_ref_ema=10, slope_window=3, pivot_window=5):
    """Per-day ``weekly_trend_state`` (+ slope) for a symbol, point-in-time (as of
    the last completed week). DataFrame indexed by date."""
    px = store.load(symbol)
    wk = WeeklyKellContext(ema_periods=(10, 20), ext_ref_ema=ext_ref_ema,
                           trend_slope_window=slope_window, trend_pivot_window=pivot_window)
    rows = []
    for ts, r in px.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        bar = Bar(date=d, open=r["Open"], high=r["High"], low=r["Low"],
                  close=r["Close"], volume=r["Volume"])
        wk.update(bar)
        f = wk.features(bar)
        rows.append((ts, r["Close"], f["weekly_trend_state"], f["weekly_trend_slope_pct"]))
    return pd.DataFrame(rows, columns=["date", "close", "weekly_trend_state",
                                       "weekly_slope_pct"]).set_index("date")


def _since(state_series) -> object:
    """Date the current (last) weekly state run began."""
    st = state_series.values
    i = len(st) - 1
    while i > 0 and st[i - 1] == st[-1]:
        i -= 1
    return state_series.index[i]


def snapshot(symbols=None, asof=None):
    """Print the current weekly trend state (+ slope + how long) of each index."""
    m = symbols if symbols is not None else INDICES
    if isinstance(m, (list, tuple)):
        m = {s: s for s in m}
    print(f"{'index':17} {'sym':4} {'weekly state':10} {'slope%':>7}  since")
    print("-" * 56)
    out = {}
    for name, sym in m.items():
        s = weekly_states(sym)
        if asof:
            s = s[s.index <= pd.Timestamp(asof)]
        cur = s.iloc[-1]
        since = _since(s["weekly_trend_state"]).date()
        slope = cur["weekly_slope_pct"]
        print(f"{name:17} {sym:4} {str(cur['weekly_trend_state']):10} "
              f"{(f'{slope:+.1f}' if slope is not None else '—'):>7}  since {since}")
        out[name] = cur["weekly_trend_state"]
    return out
