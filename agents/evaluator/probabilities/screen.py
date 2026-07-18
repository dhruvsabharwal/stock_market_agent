"""Feature screening for the setup dataset.

Start simple: for a big pool of setups (from ``run_scan`` across many tickers),
find which single features actually separate winners from losers — the "obvious
ones" (e.g. below the 200-SMA → doesn't work). Then add features one at a time
with ``conditional`` and watch the win rate move.

Win is configurable; default ``win = return_pct > 4%``. Incomplete trades
(``exit_reason == 'end_of_data'``, truncated at the data edge) are excluded by
default so the label reflects a real stop/weakness outcome.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from ..data import store
from ..engine.scan import run_scan
from ..eval import metrics
from ..strategy.stage_range_strategy import StageRangeStrategy

# every build writes its parquet + csv here (separate from the cache/db)
RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"

# outcome / id / config columns — never screened as features
_NON_FEATURE = {
    "ticker", "signal_date", "entry_date", "exit_date", "exit_reason",
    "entry_price", "exit_price", "qty", "return_pct", "peak_return_pct",
    "mae_pct", "days_held", "days_to_peak", "price_mode", "stop_type", "ma_type",
    # absolute EPS ($/share) / revenue ($) — not comparable across tickers;
    # use the *_growth columns instead
    "eps", "eps_yoy_base", "eps_qoq_base", "revenue",
}


# ── build the pooled dataset ─────────────────────────────────────────────────
def build_dataset(
    tickers=None, *, strat_kwargs=None, start=None, end=None,
    notional: float = 1000.0, strategy_cls=StageRangeStrategy, exit_rule=None,
    name=None, save: bool = True, verbose: bool = True,
) -> pd.DataFrame:
    """Run ``run_scan`` over each ticker and pool every setup into one frame.

    Always saves the result to ``runs/<name>.{parquet,csv}`` (``save=True``); if
    ``name`` is omitted it is auto-generated from the exit rule + a timestamp, so
    every run is captured. ``exit_rule`` selects the sell (default = live sell).
    """
    tickers = tickers or store.cached_tickers()
    frames = []
    for t in tickers:
        if not store.is_cached(t):
            continue
        trades = run_scan(store.load(t), strategy_cls(**(strat_kwargs or {})),
                          ticker=t, start=start, end=end, notional=notional,
                          exit_rule=exit_rule)
        if trades:
            frames.append(metrics.ledger_df(trades))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if save and len(out):
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        if name is None:
            ex = getattr(exit_rule, "name", "default")
            name = f"run_{ex}_{datetime.now():%Y%m%d_%H%M%S}"
        out.to_parquet(RUNS_DIR / f"{name}.parquet")
        out.to_csv(RUNS_DIR / f"{name}.csv", index=False)
        if verbose:
            print(f"saved {len(out)} setups -> runs/{name}.csv (+ .parquet)")
    if verbose:
        print(f"total: {len(out)} setups across {len(frames)} tickers")
    return out


# ── helpers ───────────────────────────────────────────────────────────────--
def _completed(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["exit_reason"] != "end_of_data"] if "exit_reason" in df else df


def _is_feature(col: str) -> bool:
    """Keep only cross-ticker-comparable features: exclude outcomes/config, any
    absolute price column (``*_price*``) and any date column (``*_date*``).
    Absolute prices/EPS all have a % twin that carries the signal."""
    if col in _NON_FEATURE:
        return False
    return not ("_price" in col or "_date" in col)


def _bucket(s: pd.Series):
    """Boolean/low-cardinality -> as-is; numeric -> quartile bins."""
    non_null = s.dropna()
    if non_null.isin([True, False]).all() or s.nunique(dropna=True) <= 8:
        return s
    try:
        return pd.qcut(s, 4, duplicates="drop")
    except Exception:
        return s


# ── screens ───────────────────────────────────────────────────────────────--
def screen(df: pd.DataFrame, *, win_threshold: float = 4.0, min_count: int = 20) -> pd.DataFrame:
    """Rank every feature by how much its buckets separate the win rate.

    ``spread`` = best-bucket win rate − worst-bucket win rate. Big spread = the
    feature strongly discriminates. ``lift`` = best bucket vs the baseline.
    """
    df = _completed(df)
    win = (df["return_pct"] > win_threshold).astype(float)
    base = win.mean()
    rows = []
    for col in df.columns:
        if not _is_feature(col):
            continue
        stats = win.groupby(_bucket(df[col]), observed=True).agg(["count", "mean"])
        stats = stats[stats["count"] >= min_count]
        if len(stats) < 2:
            continue
        rows.append({
            "feature": col,
            "n_buckets": len(stats),
            "baseline_win": round(base, 3),
            "best_bucket": str(stats["mean"].idxmax()),
            "best_win": round(stats["mean"].max(), 3),
            "worst_bucket": str(stats["mean"].idxmin()),
            "worst_win": round(stats["mean"].min(), 3),
            "spread": round(stats["mean"].max() - stats["mean"].min(), 3),
            "lift_vs_base": round(stats["mean"].max() - base, 3),
        })
    return pd.DataFrame(rows).sort_values("spread", ascending=False).reset_index(drop=True)


def detail(df: pd.DataFrame, feature: str, *, win_threshold: float = 4.0) -> pd.DataFrame:
    """Full per-bucket breakdown of one feature: n, win rate, avg return."""
    df = _completed(df)
    b = _bucket(df[feature])
    win = (df["return_pct"] > win_threshold).astype(float)
    return pd.DataFrame({
        "n": win.groupby(b, observed=True).size(),
        "win_rate": win.groupby(b, observed=True).mean().round(3),
        "avg_return": df.groupby(b, observed=True)["return_pct"].mean().round(2),
        "median_return": df.groupby(b, observed=True)["return_pct"].median().round(2),
    })


def conditional(df: pd.DataFrame, filters: dict, *, win_threshold: float = 4.0) -> dict:
    """Win rate under a set of feature filters — add features one at a time.

    ``filters`` maps a column to an exact value OR a callable (e.g.
    ``{'above_200sma': True, 'stage2_pass_count': lambda s: s >= 4}``).
    """
    df = _completed(df)
    mask = pd.Series(True, index=df.index)
    for col, cond in filters.items():
        mask &= cond(df[col]) if callable(cond) else (df[col] == cond)
    sub = df[mask]
    base = (df["return_pct"] > win_threshold).mean()
    return {
        "n": int(len(sub)),
        "win_rate": round(float((sub["return_pct"] > win_threshold).mean()), 3) if len(sub) else None,
        "avg_return": round(float(sub["return_pct"].mean()), 2) if len(sub) else None,
        "baseline_win": round(float(base), 3),
        "baseline_n": int(len(df)),
    }
