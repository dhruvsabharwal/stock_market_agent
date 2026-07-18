"""Local price-data store.

Daily OHLCV is cached one row per (ticker, date) as parquet under
``data/cache/<TICKER>.parquet``. Reading is offline and reproducible — the
whole point of the evaluator is that a run does not change because yfinance
revised its data.

Columns are normalised to: Open, High, Low, Close, Volume (and Adj Close if
present), with a tz-naive DatetimeIndex named ``Date``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / "cache"
TICKERS_FILE = Path(__file__).resolve().parents[3] / "all_tickers.txt"

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def cache_path(ticker: str) -> Path:
    safe = ticker.replace("/", "_")
    return CACHE_DIR / f"{safe}.parquet"


def is_cached(ticker: str) -> bool:
    return cache_path(ticker).exists()


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a yfinance frame to the canonical OHLCV shape."""
    df = df.copy()
    # Flatten MultiIndex columns (yfinance: ("Close","AAPL") -> "Close").
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    keep = [c for c in (_OHLCV + ["Adj Close"]) if c in df.columns]
    df = df[keep].dropna(how="all")
    return df.sort_index()


def save(ticker: str, df: pd.DataFrame) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(ticker)
    normalise(df).to_parquet(path)
    return path


def load(
    ticker: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Load cached OHLCV for ``ticker``, optionally sliced to [start, end].

    Raises if the ticker isn't cached — use :func:`get` for cache-on-first-use.
    """
    path = cache_path(ticker)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached data for {ticker!r} at {path}. "
            f"Use store.get({ticker!r}) to fetch+cache it, or run data/ingest.py."
        )
    df = pd.read_parquet(path)
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def fetch(ticker: str) -> Optional[pd.DataFrame]:
    """Download full daily history from yfinance, normalised. No caching."""
    import yfinance as yf  # lazy: only needed when actually fetching

    raw = yf.download(ticker, period="max", interval="1d",
                      auto_adjust=False, progress=False)
    if raw is None or raw.empty:
        return None
    return normalise(raw)


def get(
    ticker: str,
    *,
    refresh: bool = False,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Cache-aware load: return cached data, fetching + caching on first use.

    * not cached            -> download, save to cache, return
    * cached & refresh=False -> read straight from cache (offline, reproducible)
    * cached & refresh=True  -> re-download max history and overwrite the cache

    This is the function to call from a notebook so a new ticker is added to the
    cache automatically the first time you run it.
    """
    if refresh or not is_cached(ticker):
        df = fetch(ticker)
        if df is None:
            raise ValueError(f"yfinance returned no data for {ticker!r}.")
        save(ticker, df)
    return load(ticker, start=start, end=end)


def us_universe() -> list[str]:
    """US tickers: all_tickers.txt (non-.NS) plus optional universe_extra.txt
    (exchange-listing additions), deduped, order preserved."""
    names: list[str] = []
    if TICKERS_FILE.exists():
        names += [t.strip() for t in TICKERS_FILE.read_text().splitlines()
                  if t.strip() and not t.strip().endswith(".NS")]
    extra = Path(__file__).resolve().parent / "universe_extra.txt"
    if extra.exists():
        names += [t.strip() for t in extra.read_text().splitlines() if t.strip()]
    seen, out = set(), []
    for t in names:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def cached_tickers() -> list[str]:
    return sorted(p.stem for p in CACHE_DIR.glob("*.parquet"))


def load_many(
    tickers: Iterable[str], *, start: Optional[str] = None, end: Optional[str] = None
):
    """Yield (ticker, df) for each cached ticker, skipping missing ones."""
    for t in tickers:
        if is_cached(t):
            yield t, load(t, start=start, end=end)
