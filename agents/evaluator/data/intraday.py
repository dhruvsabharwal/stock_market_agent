"""Intraday (hourly) price cache — the opening-range data layer.

yfinance serves ~60-minute bars for only the last ~2.9 years (a hard retention
wall; older intraday history simply doesn't exist there). That's too shallow for
the 2015-2026 daily backtest, but it IS a real ~3-year sample for computing and
evaluating **opening-range** metrics (first-hour ORH/ORL, RVOL-at-open, gap).

Seven bars per US session: 09:30, 10:30, …, 15:30 ET. The **09:30 bar is the
first hour** (09:30-10:30) — the opening range we can resolve at hourly
granularity (a true 30-min OR needs 5m/15m, which yfinance keeps only ~85 days).

Because the source only ever returns the trailing window, ``save`` MERGES each
fetch into the cache (union by timestamp, keep last) — so running ``ingest``
periodically **extends coverage forward** past the 3-year wall over time.

Stored one parquet/ticker under ``intraday_cache/`` with a tz-aware (US/Eastern)
DatetimeIndex and Open/High/Low/Close/Volume columns. ``load`` is cache-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

INTRADAY_CACHE_DIR = Path(__file__).resolve().parent / "intraday_cache"
INTERVAL = "60m"
FETCH_PERIOD = "730d"          # yfinance max for 60m (returns ~2.9 yr)
EASTERN = "America/New_York"


def cache_path(ticker: str) -> Path:
    return INTRADAY_CACHE_DIR / f"{ticker.replace('/', '_')}.parquet"


def is_cached(ticker: str) -> bool:
    return cache_path(ticker).exists()


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance hourly -> tz-aware (US/Eastern) OHLCV, sorted, deduped."""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep]
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df.index = idx.tz_convert(EASTERN)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def fetch(ticker: str) -> pd.DataFrame:
    """Download the full available hourly window (~2.9 yr). No caching."""
    import yfinance as yf
    raw = yf.download(ticker, period=FETCH_PERIOD, interval=INTERVAL,
                      auto_adjust=False, progress=False)
    return normalise(raw)


def save(ticker: str, df: pd.DataFrame) -> None:
    """Merge ``df`` into the cache (union by timestamp, keep last) so coverage
    accumulates forward across runs, then write."""
    INTRADAY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if is_cached(ticker):
        old = pd.read_parquet(cache_path(ticker))
        df = pd.concat([old, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(cache_path(ticker))


def load(ticker: str) -> Optional[pd.DataFrame]:
    """Cache-only read (None if not ingested)."""
    p = cache_path(ticker)
    return pd.read_parquet(p) if p.exists() else None


def get(ticker: str, *, refresh: bool = False) -> pd.DataFrame:
    """Cache-aware: fetch+merge on first use (or refresh), else read cache."""
    if refresh or not is_cached(ticker):
        df = fetch(ticker)
        if df.empty:
            raise ValueError(f"yfinance returned no hourly data for {ticker!r}.")
        save(ticker, df)
    return load(ticker)


def ingest(tickers, *, verbose: bool = True, max_consecutive_fails: int = 25) -> int:
    """Fetch+merge hourly bars for ``tickers`` (resume-safe circuit-breaker)."""
    INTRADAY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    written, fails = 0, 0
    for n, t in enumerate(tickers, 1):
        try:
            df = fetch(t)
            if df.empty:
                fails += 1
            else:
                save(t, df)
                written += 1
                fails = 0
        except Exception as e:
            fails += 1
            if verbose:
                print(f"  {t}: {type(e).__name__}")
        if fails >= max_consecutive_fails:
            print(f"  circuit-breaker: {fails} consecutive fails — stopping.")
            break
        if verbose and n % 25 == 0:
            print(f"  {n}/{len(tickers)} (wrote {written}) ...")
    if verbose:
        print(f"intraday: wrote {written} tickers -> {INTRADAY_CACHE_DIR}")
    return written


def cached_tickers() -> list:
    if not INTRADAY_CACHE_DIR.exists():
        return []
    return sorted(p.stem for p in INTRADAY_CACHE_DIR.glob("*.parquet"))
