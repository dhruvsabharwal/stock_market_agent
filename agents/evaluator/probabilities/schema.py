"""Feature schema for the probability database — the 'keep adding things' knob.

This is the single place that declares WHICH entry-time features key the
probability DB and HOW continuous features are bucketed. Add a feature here,
rebuild the DB, and it becomes queryable.

Rules:
  * Only features known at the BUY bar may appear here (no outcome leakage).
    ``return_pct`` / ``peak_return_pct`` are outcomes — never features.
  * Each feature is (column_name -> bucketer). A bucketer maps a raw value to a
    discrete label (string/int). Identity for already-discrete features.
  * The DB is versioned by ``manifest()`` so a stale query can't read a DB built
    with a different feature set.
"""
from __future__ import annotations

import hashlib
from typing import Callable

import pandas as pd

Bucketer = Callable[[object], object]


def _bins(edges: list[float], labels: list[str]) -> Bucketer:
    def f(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        v = float(v)
        for edge, label in zip(edges, labels):
            if v < edge:
                return label
        return labels[-1]
    return f


def _identity(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return v


# ── Feature registry ──────────────────────────────────────────────────────────
# name -> (source column in the ledger, bucketer)
FEATURES: dict[str, tuple[str, Bucketer]] = {
    "range_height_bkt": (
        "range_height_pct",
        _bins([2, 3, 4, 6, 10], ["0-2", "2-3", "3-4", "4-6", "6-10", "10+"]),
    ),
    "expansion_move_bkt": (
        "expansion_move_pct",
        _bins([4, 6, 8, 12], ["<4", "4-6", "6-8", "8-12", "12+"]),
    ),
    "stage": ("stage", _identity),
    "sector_stage": ("sector_stage_segment", _identity),
    "industry_stage": ("industry_stage_segment", _identity),
    "stage_weeks_bkt": (
        "stock_stage_weeks_elapsed",
        _bins([4, 10, 20, 40], ["0-4", "4-10", "10-20", "20-40", "40+"]),
    ),
}

#: Outcome columns the DB carries for query-time success computation.
OUTCOME_COLS = ["return_pct", "peak_return_pct", "days_held", "mae_pct"]


def feature_names() -> list[str]:
    return list(FEATURES)


def bucketize(df: pd.DataFrame) -> pd.DataFrame:
    """Add a bucketed column per registered feature (NaN-safe). Missing source
    columns yield an all-None feature so the DB build still works as features
    are introduced over time."""
    out = df.copy()
    for name, (src, bucketer) in FEATURES.items():
        if src in out.columns:
            out[name] = out[src].map(bucketer)
        else:
            out[name] = None
    return out


def manifest() -> dict:
    """Versioned description of the feature set (stamped into the built DB)."""
    spec = {name: src for name, (src, _) in FEATURES.items()}
    blob = repr(sorted(spec.items())).encode()
    return {"features": spec, "version": hashlib.sha1(blob).hexdigest()[:12]}
