"""Runtime lookup against the probability DB.

Given a set of *bucketed* feature constraints (a partial setup description) and
a success threshold, return how that kind of setup historically performed:
sample size, win rate, average/median return, etc.

Because the DB is the bucketed ledger, any subset of features can be queried
(unspecified features are marginalised) and any win threshold can be applied —
both decided at query time, not bake time.

Example:
    db = ProbabilityDB.load()
    db.query({"range_height_bkt": "3-4", "stage": 2}, win_threshold_pct=0.0)
    # -> {"n": 412, "win_rate": 0.58, "avg_return_pct": 3.1, ...}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from . import schema
from .build_db import DB_PATH, MANIFEST_PATH


class ProbabilityDB:
    def __init__(self, df: pd.DataFrame, manifest: Optional[dict] = None):
        self.df = df
        self.manifest = manifest or {}

    @classmethod
    def load(cls, path: Path = DB_PATH) -> "ProbabilityDB":
        if not Path(path).exists():
            raise FileNotFoundError(f"No DB at {path}. Run build_db.py first.")
        df = pd.read_parquet(path)
        manifest = (
            json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {}
        )
        # Guard against a stale schema vs the built DB.
        live = schema.manifest()["version"]
        built = manifest.get("version")
        if built is not None and built != live:
            raise RuntimeError(
                f"Schema version mismatch: DB built with {built}, code is {live}. "
                "Rebuild the DB (build_db.py)."
            )
        return cls(df, manifest)

    def query(
        self, features: dict, *, win_threshold_pct: float = 0.0, min_n: int = 1
    ) -> dict:
        """Conditional stats for setups matching ``features``.

        ``features`` keys must be bucketed feature names (see schema.FEATURES);
        values are bucket labels. Unknown keys raise. Unspecified features are
        marginalised (ignored)."""
        unknown = set(features) - set(schema.feature_names())
        if unknown:
            raise KeyError(f"Unknown feature(s): {sorted(unknown)}. "
                           f"Valid: {schema.feature_names()}")
        mask = pd.Series(True, index=self.df.index)
        for k, v in features.items():
            mask &= (self.df[k] == v)
        sub = self.df[mask]
        n = len(sub)
        if n < min_n:
            return {"n": n, "win_rate": None, "note": "insufficient sample"}

        ret = sub["return_pct"].astype(float)
        wins = (ret > win_threshold_pct).sum()
        out = {
            "n": int(n),
            "win_rate": round(wins / n, 4),
            "avg_return_pct": round(float(ret.mean()), 3),
            "median_return_pct": round(float(ret.median()), 3),
            "win_threshold_pct": win_threshold_pct,
        }
        if "peak_return_pct" in sub.columns:
            out["avg_peak_return_pct"] = round(
                float(sub["peak_return_pct"].astype(float).mean()), 3
            )
        if "days_held" in sub.columns:
            out["avg_days_held"] = round(
                float(sub["days_held"].astype(float).mean()), 1
            )
        return out
