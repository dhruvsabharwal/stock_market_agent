"""Cross-sectional Relative-Strength rank cache (O'Neil / Minervini "RS rating").

Every other feature is computed per-ticker on-bar. RS rank is different: it is
**cross-sectional** — a stock's trailing return ranked against the *whole
universe* on the same date — so it can't be computed inside a single-ticker
strategy. Instead we precompute it here (a batch pass over all cached prices)
and cache one series per ticker, which the strategy then loads cache-only like
prices/EPS/revenue.

No lookahead: the rank at date D uses only returns through D (a trailing window)
ranked across all tickers' returns through D — everything known at D's close.

Stored one parquet per ticker under ``data/rs_cache/`` with a tz-naive Date index
and columns ``rs_{m}m`` (percentile 0–100, higher = stronger) for each horizon.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from . import store

RS_CACHE_DIR = Path(__file__).resolve().parent / "rs_cache"
RS_MONTHS = (1, 3, 6, 12)
_TRADING_DAYS_PER_MONTH = 21


def rs_cache_path(ticker: str) -> Path:
    return RS_CACHE_DIR / f"{ticker.replace('/', '_')}.parquet"


def build_rs_cache(months=RS_MONTHS, *, min_names: int = 20, verbose: bool = True) -> int:
    """Rank every cached US ticker's trailing return cross-sectionally per date.

    For each horizon, build a date×ticker return matrix, rank each row (date)
    into a 0–100 percentile across all names with data that day, then write each
    ticker's rank series to ``rs_cache/{ticker}.parquet``. Returns #tickers written.
    ``min_names`` drops dates with too few names to rank meaningfully."""
    tickers = [t for t in store.cached_tickers() if t in set(store.us_universe())]
    if verbose:
        print(f"loading Close for {len(tickers)} tickers ...")
    closes = {}
    for t in tickers:
        try:
            closes[t] = store.load(t)["Close"]
        except Exception:
            continue
    px = pd.DataFrame(closes).sort_index()
    px = px[~px.index.duplicated(keep="last")]
    if verbose:
        print(f"price matrix: {px.shape[0]} dates × {px.shape[1]} tickers; ranking ...")

    rank_by_h = {}
    for m in months:
        ret = px.pct_change(m * _TRADING_DAYS_PER_MONTH, fill_method=None)
        # need enough names on a date for a meaningful cross-sectional rank
        ret = ret.where(ret.notna().sum(axis=1).ge(min_names).reindex(ret.index), other=pd.NA)
        rank_by_h[m] = ret.rank(axis=1, pct=True) * 100.0   # percentile per date

    RS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for t in px.columns:
        cols = {}
        for m in months:
            s = rank_by_h[m][t].dropna()
            if not s.empty:
                cols[f"rs_{m}m"] = s
        if not cols:
            continue
        out = pd.DataFrame(cols).sort_index()
        out.index.name = "Date"
        out.to_parquet(rs_cache_path(t))
        written += 1
        if verbose and written % 200 == 0:
            print(f"  wrote {written} ...")
    if verbose:
        print(f"done — wrote RS cache for {written} tickers to {RS_CACHE_DIR}")
    return written


class RSHistory:
    """Point-in-time cross-sectional RS-rank lookups for one ticker (cache-only)."""

    def __init__(self, df: Optional[pd.DataFrame]):
        self.df = df if df is not None and not df.empty else None

    @classmethod
    def load(cls, ticker: str) -> "RSHistory":
        p = rs_cache_path(ticker)
        return cls(pd.read_parquet(p) if p.exists() else None)

    def rank_as_of(self, date) -> Optional[dict]:
        """RS-rank row as of ``date`` (the last date on/before it). None if the
        ticker has no RS history yet. Point-in-time: ranks are dated by the day
        their trailing return was known."""
        if self.df is None:
            return None
        ts = pd.Timestamp(date).normalize()
        known = self.df[self.df.index <= ts]
        if known.empty:
            return None
        return known.iloc[-1].to_dict()


def main() -> None:
    build_rs_cache()


if __name__ == "__main__":
    main()
