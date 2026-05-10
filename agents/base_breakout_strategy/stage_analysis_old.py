"""
Stage Analysis Module — Stan Weinstein's 4-Stage Framework

All stage classification uses WEEKLY data exclusively, as per the TraderLion guide:
  "On a daily and weekly chart - Stan Weinstein uses 3 moving averages:
   a 50-day/10-week, 150-day/30-week, and a 200-day/40-week moving average."

The 30-week SMA is the primary stage indicator. The 10-week and 40-week SMAs
provide confirmation of substage and trend strength.

Stages:
  Stage 1  — Basing Area      (sideways under/near flat long-term MAs)
  Stage 1B — Late Basing      (RS improving, MAs flattening, approaching resistance)
  Stage 2A — Early Advancing  (fresh breakout above rising 30w SMA on volume)
  Stage 2B — Late Advancing   (extended above MAs, may show churning)
  Stage 3  — Topping Area     (price choppy above/below MAs, slope flattening)
  Stage 4  — Declining Phase  (price below falling 30w SMA, lower highs/lows)

Usage (standalone, no class needed):
    import yfinance as yf
    weekly = yf.download("AAPL", period="3y", interval="1wk", auto_adjust=True)
    result = classify_stage(weekly)
    print(result["stage"], result["substage"], result["stage_label"])
"""

import pandas as pd
import numpy as np
import math
import threading
from typing import Optional

# Serialize yfinance downloads — concurrent calls corrupt each other's column structure
_YF_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scalar(val) -> Optional[float]:
    """Extract a single float from a pandas scalar, Series, or raw float."""
    if val is None:
        return None
    if isinstance(val, pd.Series):
        val = val.iloc[0]
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _to_series(obj) -> Optional[pd.Series]:
    """
    Coerce a pandas object to a 1-D Series.
    Handles the case where yfinance returns a single-column DataFrame
    instead of a Series (can happen in concurrent downloads).
    """
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        if obj.shape[1] == 1:
            return obj.iloc[:, 0]
        # Multiple columns — take the first (shouldn't happen for single-ticker data)
        return obj.iloc[:, 0]
    return obj


def _slope(series, lookback: int = 4) -> Optional[float]:
    """Linear regression slope of the last `lookback` points (normalised by mean)."""
    series = _to_series(series)
    if series is None:
        return None
    s = series.dropna().tail(lookback)
    if len(s) < lookback:
        return None
    x = np.arange(len(s), dtype=float)
    try:
        vals = s.values.flatten().astype(float)
        slope = float(np.polyfit(x, vals, 1)[0])
        mean = float(s.mean())
        return (slope / mean) if mean != 0 else None
    except Exception:
        return None


def _pct_change(a, b) -> Optional[float]:
    """(a/b - 1) * 100, fully guarded."""
    a, b = _scalar(a), _scalar(b)
    if a is None or b is None or b == 0:
        return None
    result = (a / b - 1) * 100
    return None if (math.isnan(result) or math.isinf(result)) else round(result, 2)


# ---------------------------------------------------------------------------
# Core weekly indicator computation
# ---------------------------------------------------------------------------

def compute_weekly_indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Add stage-analysis indicators to a weekly OHLCV DataFrame.

    Required columns: Close, High, Low, Volume
    Returns a copy with added columns:
        sma10w, sma30w, sma40w
        sma10w_slope, sma30w_slope, sma40w_slope
        closing_range_pct   — (close - low) / (high - low) * 100 per week
        vol_10w_avg         — 10-week average volume
        vol_vs_10w          — current volume / 10-week avg
    """
    df = weekly.copy()

    # Flatten MultiIndex columns (yfinance now returns MultiIndex for single tickers too)
    # Columns are like ("Close", "AAPL") — drop the Ticker level, keep the Price level
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
        # After flattening there may be duplicate column names (e.g. two "AAPL" tickers).
        # For single-ticker data the result is just the standard OHLCV names.

    # Ensure tz-naive index
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # Ensure numeric columns (yfinance can return object dtype on some versions)
    for col in ["Close", "High", "Low", "Volume", "Open"]:
        if col in df.columns:
            col_data = df[col]
            # If it's a single-column DataFrame (yfinance quirk), squeeze to Series
            if isinstance(col_data, pd.DataFrame):
                col_data = col_data.iloc[:, 0]
            df[col] = pd.to_numeric(col_data, errors="coerce")

    close = df["Close"]
    df["sma10w"] = close.rolling(10, min_periods=8).mean()
    df["sma30w"] = close.rolling(30, min_periods=25).mean()
    df["sma40w"] = close.rolling(40, min_periods=35).mean()

    # Weekly closing range: where the close landed within the week's range
    # >= 40% is constructive (guide: "weekly closing range of 40% or higher is a sign")
    week_range = df["High"] - df["Low"]
    df["closing_range_pct"] = (
        (df["Close"] - df["Low"]) / week_range.replace(0, np.nan) * 100
    ).round(1)

    df["vol_10w_avg"] = df["Volume"].rolling(10, min_periods=5).mean()
    df["vol_vs_10w"] = (df["Volume"] / df["vol_10w_avg"]).round(3)

    return df


# ---------------------------------------------------------------------------
# Stage classification
# ---------------------------------------------------------------------------

def classify_stage(weekly: pd.DataFrame) -> dict:
    """
    Classify the current stage of a stock using weekly data only.

    Parameters
    ----------
    weekly : pd.DataFrame
        Weekly OHLCV data (typically 2-3 years). Columns must include
        Close, High, Low, Volume. tz-aware or tz-naive index both accepted.

    Returns
    -------
    dict with keys:
        stage           int   1, 2, 3, or 4
        substage        str   "1", "1B", "2A", "2B", "3", "4"
        stage_label     str   Human-readable description
        price           float Current weekly close
        sma10w          float 10-week SMA
        sma30w          float 30-week SMA
        sma40w          float 40-week SMA
        sma30w_slope    float Normalised slope of 30w SMA (positive = rising)
        pct_above_30w   float % price is above/below 30w SMA
        closing_range_pct float  This week's closing range % (40%+ is constructive)
        vol_vs_10w      float  This week's volume vs 10-week average
        higher_highs    bool  Price making higher highs over last 10 weeks
        higher_lows     bool  Price making higher lows over last 10 weeks
        lower_highs     bool  Price making lower highs over last 10 weeks
        lower_lows      bool  Price making lower lows over last 10 weeks
        data_weeks      int   How many weeks of data were available
        error           str or None
    """
    result = {
        "stage": None, "substage": None, "stage_label": None,
        "price": None, "sma10w": None, "sma30w": None, "sma40w": None,
        "sma30w_slope": None, "pct_above_30w": None,
        "closing_range_pct": None, "vol_vs_10w": None,
        "higher_highs": None, "higher_lows": None,
        "lower_highs": None, "lower_lows": None,
        "data_weeks": 0, "error": None
    }

    try:
        if weekly is None or len(weekly) < 15:
            result["error"] = "Insufficient weekly data (need ≥15 weeks)"
            return result

        df = compute_weekly_indicators(weekly)
        result["data_weeks"] = len(df)

        # ── Current values ──────────────────────────────────────────────────
        price   = _scalar(df["Close"].iloc[-1])
        sma10w  = _scalar(df["sma10w"].iloc[-1])
        sma30w  = _scalar(df["sma30w"].iloc[-1])
        sma40w  = _scalar(df["sma40w"].iloc[-1])
        cr_pct  = _scalar(df["closing_range_pct"].iloc[-1])
        v10w    = _scalar(df["vol_vs_10w"].iloc[-1])

        result.update({
            "price": round(price, 2) if price else None,
            "sma10w": round(sma10w, 2) if sma10w else None,
            "sma30w": round(sma30w, 2) if sma30w else None,
            "sma40w": round(sma40w, 2) if sma40w else None,
            "closing_range_pct": cr_pct,
            "vol_vs_10w": v10w,
        })

        if price is None or sma30w is None:
            result["error"] = "Missing price or 30w SMA — not enough history"
            return result

        # ── Slopes (normalised, 4-week window) ───────────────────────────────
        slope30 = _slope(df["sma30w"], lookback=4)
        slope10 = _slope(df["sma10w"], lookback=4)
        slope40 = _slope(df["sma40w"], lookback=4) if sma40w else None
        result["sma30w_slope"] = round(slope30, 6) if slope30 is not None else None

        pct_above_30w = _pct_change(price, sma30w)
        result["pct_above_30w"] = pct_above_30w

        # ── Higher highs / lower lows over 10 weeks ──────────────────────────
        recent = df.tail(10)
        highs  = recent["High"].dropna().values.astype(float)
        lows   = recent["Low"].dropna().values.astype(float)

        if len(highs) >= 6:
            mid = len(highs) // 2
            hh = float(highs[:mid].max()) < float(highs[mid:].max())
            hl = float(lows[:mid].min())  < float(lows[mid:].min())
            lh = float(highs[:mid].max()) > float(highs[mid:].max())
            ll = float(lows[:mid].min())  > float(lows[mid:].min())
            result.update({"higher_highs": hh, "higher_lows": hl,
                           "lower_highs": lh, "lower_lows": ll})
            hh, hl, lh, ll = hh, hl, lh, ll
        else:
            hh = hl = lh = ll = False

        # ── Core stage rules (weekly price vs 30w SMA) ───────────────────────
        # Primary indicator per the guide: price position relative to 30-week SMA
        # and the *slope* (direction) of that SMA.
        above_30w     = price > sma30w
        below_30w     = price < sma30w
        slightly_below = (pct_above_30w is not None and -5 <= pct_above_30w < 0)
        sma30_rising  = slope30 is not None and slope30 > 0.001
        sma30_falling = slope30 is not None and slope30 < -0.001
        sma30_flat    = not sma30_rising and not sma30_falling

        # Stage 2: price above a rising 30w SMA (main advancing phase)
        if above_30w and sma30_rising:
            # 2A = early/mid advance (within 30% of SMA, HH+HL intact)
            # 2B = extended (>30% above SMA) or structure weakening (lower highs forming)
            extended = pct_above_30w is not None and pct_above_30w > 30
            weakening = lh and not hh  # lower highs forming even while above SMA
            if not extended and not weakening:
                substage = "2A"
                label    = "Stage 2A — Early Advancing Phase (buy zone)"
            elif weakening:
                substage = "2B"
                label    = "Stage 2B — Late Advancing Phase (watch for Stage 3 transition)"
            else:
                substage = "2B"
                label    = "Stage 2B — Extended Advance (hold, set tight stops)"
            result.update({"stage": 2, "substage": substage, "stage_label": label})

        # Stage 3 (topping): price churning above/near flat or slowing 30w SMA
        elif above_30w and (sma30_flat or (sma30_rising and (lh and ll))):
            result.update({
                "stage": 3, "substage": "3",
                "stage_label": "Stage 3 — Topping Area (reduce / exit)"
            })

        # Stage 3 early / Stage 2 breakdown:
        # Price has dipped below a still-rising 30w SMA with deteriorating structure.
        # The SMA hasn't rolled over yet, but price action is negative — early warning.
        elif below_30w and sma30_rising and (lh and ll):
            result.update({
                "stage": 3, "substage": "3",
                "stage_label": "Stage 3 — Early Breakdown (below rising 30w SMA — exit / stop)"
            })

        # Stage 2 pullback: slightly below rising 30w SMA but structure intact (HH or HL)
        # This is a normal correction within an uptrend, not a stage change.
        elif slightly_below and sma30_rising and (hh or hl):
            result.update({
                "stage": 2, "substage": "2A",
                "stage_label": "Stage 2A — Pullback to 30w SMA (potential continuation buy)"
            })

        # Stage 4: price below a falling 30w SMA
        elif below_30w and sma30_falling:
            result.update({
                "stage": 4, "substage": "4",
                "stage_label": "Stage 4 — Declining Phase (avoid / short candidates)"
            })

        # Stage 1B: 30w SMA has flattened after a decline, structure beginning to improve
        elif below_30w and sma30_flat and (hh or hl):
            result.update({
                "stage": 1, "substage": "1B",
                "stage_label": "Stage 1B — Late Basing (watchlist — approaching breakout)"
            })

        # Stage 1: general basing, no actionable signal yet
        else:
            result.update({
                "stage": 1, "substage": "1",
                "stage_label": "Stage 1 — Basing Area (no action needed yet)"
            })

    except Exception as e:
        result["error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# Convenience: classify from a ticker string (requires yfinance)
# ---------------------------------------------------------------------------

def classify_stage_for_ticker(ticker: str, period: str = "3y") -> dict:
    """
    Fetch weekly data and classify stage for a single ticker.

    Example:
        result = classify_stage_for_ticker("NVDA")
        print(result["substage"], result["stage_label"])
    """
    try:
        import yfinance as yf
        with _YF_LOCK:
            weekly = yf.download(ticker.upper(), period=period, interval="1wk",
                                 auto_adjust=True, progress=False)
        if weekly.empty:
            return {"ticker": ticker, "error": "No data returned", "stage": None,
                    "substage": None, "stage_label": None}
        result = classify_stage(weekly)
        result["ticker"] = ticker.upper()
        return result
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "stage": None,
                "substage": None, "stage_label": None}


# ---------------------------------------------------------------------------
# Batch classification
# ---------------------------------------------------------------------------

def classify_stages_batch(tickers: list, period: str = "3y") -> list:
    """
    Classify stage for a list of tickers using a single yfinance batch download.

    This avoids the yfinance thread-safety bug where concurrent per-ticker
    downloads corrupt each other's column structure.

    Returns list of result dicts in the same order as input.
    """
    try:
        import yfinance as yf
    except ImportError:
        return [{"ticker": t, "error": "yfinance not installed", "stage": None,
                 "substage": None, "stage_label": None} for t in tickers]

    tickers_upper = [t.upper() for t in tickers]

    with _YF_LOCK:
        raw = yf.download(
            tickers_upper, period=period, interval="1wk",
            auto_adjust=True, progress=False, group_by="ticker"
        )

    results = {}
    for ticker in tickers_upper:
        try:
            if len(tickers_upper) == 1:
                # Single ticker: raw has simple column index
                data = raw
            else:
                # Multi-ticker: raw has MultiIndex — select this ticker's slice
                if isinstance(raw.columns, pd.MultiIndex):
                    data = raw[ticker]
                else:
                    data = raw

            if data.empty:
                results[ticker] = {"ticker": ticker, "error": "No data", "stage": None,
                                   "substage": None, "stage_label": None}
                continue

            result = classify_stage(data)
            result["ticker"] = ticker
            results[ticker] = result

        except Exception as e:
            results[ticker] = {"ticker": ticker, "error": str(e), "stage": None,
                               "substage": None, "stage_label": None}

    return [results[t] for t in tickers_upper]


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "NVDA", "META"]
    print(f"Classifying stages for: {tickers}\n")
    for r in classify_stages_batch(tickers):
        print(
            f"{r.get('ticker','?'):6s}  "
            f"Stage {str(r.get('substage') or '?'):3s}  "
            f"{r.get('stage_label') or 'N/A'}"
        )
        if r.get("error"):
            print(f"         ERROR: {r['error']}")
