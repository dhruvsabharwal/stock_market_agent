"""Per-ticker sector + industry cache (from yfinance ``.info``).

The benchmark for volatility-adjusted RS (VARS) depends on *which group* a name
belongs to — Jeff Sun benchmarks a semi against a semis ETF, not SPY. yfinance
exposes each ticker's ``sector`` / ``industry`` strings, which the benchmark
resolver (``strategy/benchmark_map.py``) maps to a passive ETF.

Sector/industry is (near-)static structural metadata, NOT derived from prices, so
using a ticker's current sector to pick its benchmark is not a lookahead leak —
the benchmark's *price* series is still read point-in-time by the VARS provider.

Cache = one parquet ``sector_cache/sectors.parquet`` (index=ticker, cols
sector/industry). ``ingest`` is the only network step (resume-safe); ``load``/``get``
are cache-only.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pandas as pd

SECTOR_CACHE_DIR = Path(__file__).resolve().parent / "sector_cache"
SECTOR_PARQUET = SECTOR_CACHE_DIR / "sectors.parquet"

_memo: Optional[dict] = None


def _read_df() -> pd.DataFrame:
    if SECTOR_PARQUET.exists():
        return pd.read_parquet(SECTOR_PARQUET)
    return pd.DataFrame(columns=["sector", "industry"])


def load_all(*, refresh: bool = False) -> dict:
    """{ticker: (sector, industry)} from cache (memoised). Empty if not built."""
    global _memo
    if _memo is None or refresh:
        df = _read_df()
        _memo = {t: (row.get("sector"), row.get("industry"))
                 for t, row in df.iterrows()}
    return _memo


def get(ticker: str) -> tuple:
    """(sector, industry) for one ticker, or (None, None) if uncached."""
    return load_all().get(ticker, (None, None))


def ingest(tickers, *, sleep: float = 0.0, verbose: bool = True,
           max_consecutive_fails: int = 40) -> int:
    """Fetch sector/industry for ``tickers`` via yfinance, resume-safe.

    Skips tickers already cached; appends new rows; saves periodically so a crash
    or throttle keeps progress. A run of ``max_consecutive_fails`` empty/error
    responses trips a circuit-breaker (yfinance throttling) and stops. Returns the
    number of tickers newly written."""
    import yfinance as yf

    SECTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df = _read_df()
    have = set(df.index)
    todo = [t for t in tickers if t not in have]
    if verbose:
        print(f"sectors: {len(have)} cached, fetching {len(todo)} ...")

    rows = {t: (df.loc[t, "sector"], df.loc[t, "industry"]) for t in have}
    written, fails = 0, 0

    def _save():
        out = pd.DataFrame(
            [{"ticker": t, "sector": s, "industry": i} for t, (s, i) in rows.items()]
        ).set_index("ticker")
        out.to_parquet(SECTOR_PARQUET)

    for n, t in enumerate(todo, 1):
        try:
            info = yf.Ticker(t).info
            sec, ind = info.get("sector"), info.get("industry")
            rows[t] = (sec, ind)
            written += 1
            fails = 0 if (sec or ind) else fails + 1
        except Exception as e:
            fails += 1
            if verbose:
                print(f"  {t}: {type(e).__name__}")
        if fails >= max_consecutive_fails:
            print(f"  circuit-breaker: {fails} consecutive fails — stopping (throttled?).")
            break
        if n % 50 == 0:
            _save()
            if verbose:
                print(f"  {n}/{len(todo)} (wrote {written}) ...")
        if sleep:
            time.sleep(sleep)

    _save()
    global _memo
    _memo = None                      # invalidate so load_all re-reads
    if verbose:
        print(f"sectors: wrote {written} new -> {SECTOR_PARQUET}")
    return written


def main() -> None:
    from . import store
    tickers = [t for t in store.cached_tickers() if t in set(store.us_universe())]
    ingest(tickers)


if __name__ == "__main__":
    main()
