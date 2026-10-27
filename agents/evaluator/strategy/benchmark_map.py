"""Resolve a ticker to its 'correct' RS benchmark (Jeff Sun: bench a name against
its group, not SPY — a semi vs a semis ETF, a miner vs BTC).

Order of resolution:
  1. ``THEMATIC_OVERRIDE`` — hand-curated group drivers yfinance can't classify
     (crypto miners -> BTC-USD; yfinance files them under 'Capital Markets').
  2. ``INDUSTRY_MAP`` — yfinance industry string -> precise industry ETF.
  3. ``SECTOR_MAP``  — yfinance sector -> macro sector ETF (fallback).
  4. ``'SPY'``       — final fallback (also used for ETFs / unclassified names).

The industry/sector maps are the batch-lab tables in
``base_breakout_strategy/industry_mapping.py`` (reused via a sys.path shim, the
same mechanism as ``_lab_imports``) so there is one source of truth.

Dash normalisation: the maps use em-dashes ('Banks—Regional') but yfinance
returns spaced hyphens ('Banks - Regional'), so a raw dict lookup silently
misses and falls back to the macro ETF. ``_norm`` canonicalises both sides.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_LAB_DIR = Path(__file__).resolve().parents[1].parent / "base_breakout_strategy"
if str(_LAB_DIR) not in sys.path:
    sys.path.insert(0, str(_LAB_DIR))

from industry_mapping import INDUSTRY_MAP, SECTOR_MAP  # noqa: E402

DEFAULT_BENCHMARK = "SPY"

# Groups yfinance can't classify to their real driver. Keyed by ticker.
# Crypto miners file under 'Capital Markets' -> would wrongly map to KCE.
CRYPTO_MINERS = {
    "WULF", "MARA", "RIOT", "CLSK", "BITF", "HUT", "CIFR", "IREN", "APLD",
    "CORZ", "BTBT", "HIVE", "BTDR", "SDIG", "GREE", "DGHI", "SLNH", "CANG",
}
THEMATIC_OVERRIDE = {t: "BTC-USD" for t in CRYPTO_MINERS}


def _norm(s) -> str:
    """Lower-case, collapse any dash (—/–/-) + surrounding spaces to a single '-'."""
    if not s:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"\s*[—–-]\s*", "-", s)
    return re.sub(r"\s+", " ", s)


_NORM_INDUSTRY = {_norm(k): v for k, v in INDUSTRY_MAP.items()}
_NORM_SECTOR = {_norm(k): v for k, v in SECTOR_MAP.items()}


def resolve(ticker: str, sector=None, industry=None) -> str:
    """Best benchmark ETF/symbol for ``ticker`` given its sector/industry
    (thematic override → industry ETF → sector ETF → SPY)."""
    if ticker in THEMATIC_OVERRIDE:
        return THEMATIC_OVERRIDE[ticker]
    ind = _NORM_INDUSTRY.get(_norm(industry))
    if ind:
        return ind
    sec = _NORM_SECTOR.get(_norm(sector))
    if sec:
        return sec
    return DEFAULT_BENCHMARK


def resolve_cached(ticker: str) -> str:
    """Resolve using the cache-only sector/industry lookup (None if not ingested
    → falls back to SPY)."""
    from ..data import sectors
    sec, ind = sectors.get(ticker)
    return resolve(ticker, sec, ind)
