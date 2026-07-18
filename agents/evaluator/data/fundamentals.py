"""Point-in-time fundamentals cache (EPS via yfinance; revenue via FMP).

yfinance's ``get_earnings_dates(limit=1000)`` returns ~25 years of quarterly
**Reported EPS keyed by the earnings announcement (report) date** — exactly the
anchor a no-lookahead backtest needs (EPS for a quarter is only knowable once
it's announced, not at fiscal period-end).

Stored one parquet per ticker under ``data/fundamentals_cache/`` with columns:
    report_date (index, tz-naive) | eps | revenue
``revenue`` in that EPS frame is always NaN (yfinance has no deep, report-dated
revenue). Revenue lives in its own ``data/revenue_cache/`` from FMP, keyed by the
SEC **filing date** (``fillingDate``) — the point-in-time anchor for revenue,
mirroring the EPS report-date anchor. yfinance's quarterly income statement only
exposes ~5 recent quarters as a current snapshot (no history), so it can't be
used for a backtest — FMP is the deep, report-dated source.

Growth lookups (``EpsHistory``/``RevenueHistory.growth_as_of``) only ever use
reports dated on or before the query date, so the recorded feature is
point-in-time correct.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent / "fundamentals_cache"
REVENUE_CACHE_DIR = Path(__file__).resolve().parent / "revenue_cache"

# Revenue source: SEC EDGAR XBRL company-facts (free, no key, deep history, dated
# by the real SEC filing date = the point-in-time anchor). yfinance/FMP-free only
# expose ~5 recent quarters, so neither works for a backtest.
SEC_UA = "financial-analysis-research dhruvsabharwal495@gmail.com"  # SEC requires a UA
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json"
# Revenue XBRL tags in priority order (ASC 606 tag first, older tags after). The
# first tag reporting a given quarter-end wins.
SEC_REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
_CIK_MAP_PATH = REVENUE_CACHE_DIR / "_ticker_cik.json"
_cik_map: Optional[dict] = None


def cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.replace('/', '_')}.parquet"


def revenue_cache_path(ticker: str) -> Path:
    return REVENUE_CACHE_DIR / f"{ticker.replace('/', '_')}.parquet"


def _load_cik_map() -> dict:
    """Ticker→zero-padded-CIK map from SEC (cached to disk after first fetch)."""
    global _cik_map
    if _cik_map is not None:
        return _cik_map
    import json
    if _CIK_MAP_PATH.exists():
        _cik_map = json.loads(_CIK_MAP_PATH.read_text())
        return _cik_map
    import requests
    raw = requests.get(SEC_TICKERS_URL, headers={"User-Agent": SEC_UA}, timeout=30).json()
    _cik_map = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
    REVENUE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CIK_MAP_PATH.write_text(json.dumps(_cik_map))
    return _cik_map


def fetch_eps(ticker: str) -> Optional[pd.DataFrame]:
    """Download report-dated quarterly EPS from yfinance. No caching."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    try:
        ed = t.get_earnings_dates(limit=1000)
    except Exception:
        return None
    if ed is None or ed.empty or "Reported EPS" not in ed.columns:
        return None
    df = ed[["Reported EPS"]].dropna().copy()
    if df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "report_date"
    df = df.rename(columns={"Reported EPS": "eps"}).sort_index()
    df["revenue"] = pd.NA          # reserved for FMP
    # collapse any duplicate report dates (keep last)
    return df[~df.index.duplicated(keep="last")]


def load_eps(ticker: str) -> Optional[pd.DataFrame]:
    """Cache-ONLY read (no network). Returns None if the ticker isn't ingested."""
    path = cache_path(ticker)
    return pd.read_parquet(path) if path.exists() else None


def get_eps(ticker: str, *, refresh: bool = False) -> Optional[pd.DataFrame]:
    """Fetch-aware EPS history; downloads + caches on first use. Used by the
    ingest step — NOT during compute (compute uses ``load_eps``)."""
    path = cache_path(ticker)
    if refresh or not path.exists():
        df = fetch_eps(ticker)
        if df is None:
            return None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    return pd.read_parquet(path)


# ── Revenue (SEC EDGAR XBRL, filing-dated) ───────────────────────────────────
def _sec_concept(cik: str, tag: str, pause: float) -> Optional[pd.DataFrame]:
    """One us-gaap concept's USD facts from 10-Q/10-K, as start/end/filed/val."""
    import time

    import requests
    r = requests.get(SEC_CONCEPT_URL.format(cik=cik, tag=tag),
                     headers={"User-Agent": SEC_UA}, timeout=30)
    time.sleep(pause)
    if r.status_code != 200:
        return None
    units = r.json().get("units", {}).get("USD", [])
    if not units:
        return None
    d = pd.DataFrame(units)
    d = d[d["form"].isin(["10-Q", "10-K"])].copy()
    if d.empty:
        return None
    d["start"] = pd.to_datetime(d["start"]); d["end"] = pd.to_datetime(d["end"])
    d["filed"] = pd.to_datetime(d["filed"]); d["val"] = pd.to_numeric(d["val"], errors="coerce")
    d["days"] = (d["end"] - d["start"]).dt.days
    return d.dropna(subset=["val"])


def fetch_revenue(ticker: str, *, pause: float = 0.12) -> Optional[pd.DataFrame]:
    """Deep quarterly revenue from SEC EDGAR, keyed by SEC filing date.

    Merges the revenue XBRL tags (priority order), keeps single-quarter durations
    (~80–100 days), and **reconstructs the missing fiscal Q4** (10-Ks report the
    full year, not a standalone Q4) as annual − sum(Q1..Q3), anchored to the 10-K
    filing date. Returns a frame indexed by ``report_date`` (= filing date) with a
    ``revenue`` column, newest last. ``None`` if the symbol has no usable data."""
    cik = _load_cik_map().get(ticker.upper())
    if cik is None:
        return None
    frames = []
    for pri, tag in enumerate(SEC_REVENUE_TAGS):
        d = _sec_concept(cik, tag, pause)
        if d is not None:
            d["pri"] = pri
            frames.append(d[["start", "end", "filed", "val", "pri"]])
    if not frames:
        return None
    allf = pd.concat(frames)

    allf["days"] = (allf["end"] - allf["start"]).dt.days

    def dedup(df):  # first-priority tag wins for a given quarter-end
        return df.sort_values(["end", "pri"]).drop_duplicates("end", keep="first")

    q = dedup(allf[allf["days"].between(80, 100)])
    ann = dedup(allf[allf["days"].between(350, 380)])
    # reconstruct fiscal Q4 = FY − (the 3 quarters inside that fiscal year)
    recon = []
    for _, fy in ann.iterrows():
        qin = q[(q["end"] > fy["start"]) & (q["end"] <= fy["end"])]
        if len(qin) == 3:
            q4 = fy["val"] - qin["val"].sum()
            if q4 > 0:
                recon.append({"end": fy["end"], "filed": fy["filed"], "val": q4})
    full = pd.concat([q[["end", "filed", "val"]], pd.DataFrame(recon)], ignore_index=True)
    full = full.drop_duplicates("end", keep="first").sort_values("filed")
    out = full[["filed", "val"]].rename(columns={"filed": "report_date", "val": "revenue"})
    out["report_date"] = out["report_date"].dt.tz_localize(None).dt.normalize()
    out = out.set_index("report_date").sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out if not out.empty else None


def load_revenue(ticker: str) -> Optional[pd.DataFrame]:
    """Cache-ONLY read (no network). None if the ticker's revenue isn't ingested."""
    path = revenue_cache_path(ticker)
    return pd.read_parquet(path) if path.exists() else None


def get_revenue(ticker: str, *, refresh: bool = False) -> Optional[pd.DataFrame]:
    """Fetch-aware revenue history; downloads + caches on first use. Ingest-only
    (compute uses ``load_revenue``)."""
    path = revenue_cache_path(ticker)
    if refresh or not path.exists():
        df = fetch_revenue(ticker)
        if df is None:
            return None
        REVENUE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    return pd.read_parquet(path)


@dataclass
class EpsPoint:
    report_date: pd.Timestamp
    eps: float                     # latest reported quarterly EPS (as of the date)
    yoy: Optional[float]           # YoY growth % (vs 4 quarters back), None if n/a
    qoq: Optional[float]           # QoQ growth % (vs previous report), None if n/a
    yoy_base: Optional[float]      # the EPS 4 quarters back (the YoY denominator)
    qoq_base: Optional[float]      # the EPS 1 quarter back (the QoQ denominator)


class EpsHistory:
    """Point-in-time EPS lookups for one ticker."""

    def __init__(self, df: Optional[pd.DataFrame]):
        self.df = df if df is not None and not df.empty else None

    @classmethod
    def load(cls, ticker: str, *, allow_fetch: bool = False) -> "EpsHistory":
        """Cache-only by default (compute path). ``allow_fetch=True`` downloads
        if missing (ingest / interactive use)."""
        df = get_eps(ticker) if allow_fetch else load_eps(ticker)
        return cls(df)

    def growth_as_of(self, date) -> Optional[EpsPoint]:
        """Latest report on/before ``date`` and its YoY growth (vs 4 quarters
        earlier). Returns None if no report is known yet."""
        if self.df is None:
            return None
        ts = pd.Timestamp(date).normalize()
        known = self.df[self.df.index <= ts]
        if known.empty:
            return None
        latest_eps = float(known["eps"].iloc[-1])

        def _base(back: int) -> Optional[float]:
            return float(known["eps"].iloc[-1 - back]) if len(known) > back else None

        def _growth(base: Optional[float]) -> Optional[float]:
            # % growth is meaningless across a sign change or on a non-positive
            # base (negative / near-zero EPS explodes). Guard both; the raw EPS
            # values are recorded so downstream can judge or recompute.
            if base is None or base <= 0 or latest_eps < 0:
                return None
            return round((latest_eps / base - 1) * 100, 1)

        yoy_base, qoq_base = _base(4), _base(1)
        return EpsPoint(
            report_date=known.index[-1],
            eps=latest_eps,
            yoy=_growth(yoy_base), yoy_base=yoy_base,   # vs 4 quarters back
            qoq=_growth(qoq_base), qoq_base=qoq_base,   # vs previous report
        )


@dataclass
class RevenuePoint:
    report_date: pd.Timestamp
    revenue: float                       # latest reported quarterly revenue
    qoq: Optional[float]                 # latest QoQ growth % (q0 vs q-1)
    qoq_prev: tuple                      # (qoq@q-1, qoq@q-2, qoq@q-3) — the trajectory
    yoy: Optional[float]                 # YoY growth % (vs 4 quarters back)


class RevenueHistory:
    """Point-in-time quarterly-revenue lookups for one ticker (FMP source)."""

    def __init__(self, df: Optional[pd.DataFrame]):
        self.df = df if df is not None and not df.empty else None

    @classmethod
    def load(cls, ticker: str) -> "RevenueHistory":
        """Cache-only (compute path). Ingest populates the cache via get_revenue."""
        return cls(load_revenue(ticker))

    @staticmethod
    def _qoq(cur: Optional[float], base: Optional[float]) -> Optional[float]:
        # revenue is ~always positive; still guard a non-positive/zero base.
        if cur is None or base is None or base <= 0:
            return None
        return round((cur / base - 1) * 100, 1)

    def growth_as_of(self, date) -> Optional[RevenuePoint]:
        """Latest report on/before ``date`` + the last 4 QoQ growth rates (each
        quarter vs the one before it) and YoY (vs 4 quarters back). Point-in-time:
        only reports filed on/before ``date`` are used."""
        if self.df is None:
            return None
        ts = pd.Timestamp(date).normalize()
        known = self.df[self.df.index <= ts]
        if known.empty:
            return None
        rev = known["revenue"].values  # oldest→newest

        def at(back: int) -> Optional[float]:
            return float(rev[-1 - back]) if len(rev) > back else None

        # QoQ at offset k = revenue[k] vs revenue[k+1-back]; compute 4 consecutive
        qoq_series = [self._qoq(at(k), at(k + 1)) for k in range(4)]
        return RevenuePoint(
            report_date=known.index[-1],
            revenue=float(rev[-1]),
            qoq=qoq_series[0],
            qoq_prev=tuple(qoq_series[1:]),           # q-1, q-2, q-3
            yoy=self._qoq(at(0), at(4)),
        )
