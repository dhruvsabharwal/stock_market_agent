"""Aggregate metrics over a trade ledger + equity curve.

Pure functions over the canonical ``TradeRecord`` list (and optional equity
series). The win threshold is configurable so "success" stays a query-time
decision (see PLAN §3).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..engine.records import TradeRecord


def ledger_df(trades: list[TradeRecord]) -> pd.DataFrame:
    """Closed trades as a flat DataFrame (one row per trade)."""
    rows = [t.to_dict() for t in trades if not t.is_open]
    return pd.DataFrame(rows)


def summarise(
    trades: list[TradeRecord],
    *,
    win_threshold_pct: float = 0.0,
    equity_curve: Optional[pd.Series] = None,
) -> dict:
    """Headline performance stats. ``win_threshold_pct`` defines a 'win'."""
    df = ledger_df(trades)
    n = len(df)
    if n == 0:
        return {"n_trades": 0}

    ret = df["return_pct"].astype(float)
    wins = ret[ret > win_threshold_pct]
    losses = ret[ret <= win_threshold_pct]
    win_rate = len(wins) / n
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    expectancy = ret.mean()
    payoff = (avg_win / abs(avg_loss)) if avg_loss != 0 else None
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    profit_factor = (gross_win / gross_loss) if gross_loss != 0 else None

    out = {
        "n_trades": n,
        "win_threshold_pct": win_threshold_pct,
        "win_rate": round(win_rate, 4),
        "expectancy_pct": round(float(expectancy), 3),
        "avg_win_pct": round(float(avg_win), 3),
        "avg_loss_pct": round(float(avg_loss), 3),
        "payoff_ratio": round(payoff, 3) if payoff is not None else None,
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "median_return_pct": round(float(ret.median()), 3),
        "best_pct": round(float(ret.max()), 2),
        "worst_pct": round(float(ret.min()), 2),
        "avg_days_held": round(float(df["days_held"].astype(float).mean()), 1),
    }
    if equity_curve is not None and len(equity_curve) > 1:
        out["max_drawdown_pct"] = round(max_drawdown(equity_curve), 3)
        out["total_return_pct"] = round(
            (float(equity_curve.iloc[-1]) / float(equity_curve.iloc[0]) - 1) * 100, 2
        )
    return out


def by_feature(
    trades: list[TradeRecord], feature: str, *, win_threshold_pct: float = 0.0
) -> pd.DataFrame:
    """Win-rate / expectancy broken down by a feature column (e.g. 'stage')."""
    df = ledger_df(trades)
    if df.empty or feature not in df.columns:
        return pd.DataFrame()
    ret = df["return_pct"].astype(float)
    df = df.assign(_win=(ret > win_threshold_pct).astype(int), _ret=ret)
    g = df.groupby(feature)
    return pd.DataFrame(
        {
            "n": g.size(),
            "win_rate": g["_win"].mean().round(4),
            "expectancy_pct": g["_ret"].mean().round(3),
        }
    ).sort_values("n", ascending=False)


def add_r_metrics(
    df: pd.DataFrame,
    *,
    k_atr: float = 1.0,
    flat_stop_pct: float = 4.0,
    vol_col: str = "atr_pct",
) -> pd.DataFrame:
    """Re-express each trade's outcome in the stock's own volatility unit (R).

    Jeff Sun's whole R framework rests on risk being a *per-stock* number, not a
    flat %. Given ``atr_pct`` (or ``adr_pct``) recorded at the signal bar, this
    adds, without mutating the input:

      * ``risk_pct``        — the R risk unit = ``k_atr * vol_col`` (a k-ATR stop, in %).
      * ``r_multiple``      — realised return in R = ``return_pct / risk_pct``.
      * ``ret_in_atr``      — realised return in raw ATR units (k=1 view).
      * ``peak_in_atr`` / ``mae_in_atr`` — MFE / MAE in ATR units (exit-independent shape).
      * ``flat_stop_in_atr``— how many ATRs the flat ``flat_stop_pct`` stop sits at
        (= ``flat_stop_pct / vol_col``). This is the key diagnostic: it shows the
        flat 4% stop is 0.5 ATR on a wild name and 4 ATR on a calm one — i.e. the
        same "4%" is wildly different *risk* stock to stock.

    Rows with a missing/zero ``vol_col`` get NaN (can't form an R for them)."""
    out = df.copy()
    if vol_col not in out.columns:
        raise KeyError(f"{vol_col!r} not in ledger — rebuild with the Volatility provider")
    vol = pd.to_numeric(out[vol_col], errors="coerce")
    vol = vol.where(vol > 0)                      # 0 / negative -> NaN (no R)
    ret = pd.to_numeric(out["return_pct"], errors="coerce")

    out["risk_pct"] = (k_atr * vol).round(3)
    out["r_multiple"] = (ret / (k_atr * vol)).round(3)
    out["ret_in_atr"] = (ret / vol).round(3)
    if "peak_return_pct" in out:
        out["peak_in_atr"] = (pd.to_numeric(out["peak_return_pct"], errors="coerce") / vol).round(3)
    if "mae_pct" in out:
        out["mae_in_atr"] = (pd.to_numeric(out["mae_pct"], errors="coerce") / vol).round(3)
    out["flat_stop_in_atr"] = (flat_stop_pct / vol).round(3)
    return out


def max_drawdown(equity: pd.Series) -> float:
    """Worst peak-to-trough decline of the equity curve, in percent (negative)."""
    eq = equity.astype(float)
    running_max = eq.cummax()
    dd = (eq / running_max - 1.0) * 100
    return float(dd.min())
