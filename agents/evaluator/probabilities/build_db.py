"""Build the conditional-probability DB from an in-sample trade ledger.

Input: a ledger CSV (the canonical trade rows produced by an engine run over
the 1990-2015 in-sample window). Output: a single 'bucketed ledger' parquet —
each trade plus its bucketed feature columns and outcome columns.

We deliberately store the bucketed *ledger* rather than a pre-aggregated table:
that keeps the success threshold and the feature subset fully configurable at
query time (see query.py). The feature manifest is written alongside so a query
can verify it matches.

Usage:
    .venv/bin/python -m agents.evaluator.probabilities.build_db \
        --ledger agents/evaluator/eval/results/<run>/trades.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from . import schema

DB_DIR = Path(__file__).resolve().parent / "db"
DB_PATH = DB_DIR / "probabilities.parquet"
MANIFEST_PATH = DB_DIR / "manifest.json"


def build(ledger: pd.DataFrame) -> pd.DataFrame:
    bucketed = schema.bucketize(ledger)
    keep = schema.feature_names() + [
        c for c in schema.OUTCOME_COLS if c in bucketed.columns
    ]
    # carry a couple of identifiers for traceability
    for c in ("ticker", "entry_date"):
        if c in bucketed.columns:
            keep.append(c)
    return bucketed[keep]


def main() -> None:
    p = argparse.ArgumentParser(description="Build the probability DB from a ledger.")
    p.add_argument("--ledger", required=True, help="path to a trades.csv ledger")
    args = p.parse_args()

    ledger = pd.read_csv(args.ledger)
    db = build(ledger)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    db.to_parquet(DB_PATH)
    MANIFEST_PATH.write_text(json.dumps(
        {**schema.manifest(), "n_trades": len(db), "source": args.ledger},
        indent=2,
    ))
    print(f"Built DB: {len(db)} trades -> {DB_PATH}")
    print(f"Manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
