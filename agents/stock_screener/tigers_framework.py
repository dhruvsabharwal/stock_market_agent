"""
TIGERS Framework — Standalone Stock Screening Criteria

TIGERS is a multi-factor screening framework used to identify Champion Stocks
(TraderLion guide: "Finding Champion Stocks", p69-77). Each letter maps to one
screening dimension, each implemented as a pure function testable in isolation.

  T — Trend       : Market uptrend + stock in Stage 2 advancing phase
  I — Industry    : Stock is in a leading industry group (top RS performers)
  G — Growth      : Accelerating EPS and revenue growth (≥25% YoY)
  E — Earnings    : Catalyst quality — earnings gaps, HV edges, surprises
  R — RS          : Relative Strength line at or near 52-week high before breakout
  S — Stage       : Stock confirmed in Stage 2 per Stan Weinstein (30w SMA rising)

Design principles:
  - Each function is standalone: it takes a DataFrame or scalar inputs, returns a dict.
  - No side effects, no class state.
  - All yfinance fetching is isolated in thin wrapper functions at the bottom.
  - Can be tested on pre-fetched DataFrames — no network required for core logic.

Usage:
    import yfinance as yf
    weekly = yf.download("NVDA", period="3y", interval="1wk", auto_adjust=True)
    daily  = yf.download("NVDA", period="1y", interval="1d",  auto_adjust=True)
    info   = yf.Ticker("NVDA").info
    fin    = yf.Ticker("NVDA").quarterly_financials

    t = check_trend(spy_weekly)
    g = check_growth(info, fin)
    r = check_rs(weekly, spy_weekly)
    s = check_stage(weekly)
    ...
    score = tigers_score(t, g=g, r=r, s=s)
"""

import math
import pandas as pd
import numpy as np
from typing import Optional

# ---------------------------------------------------------------------------
# Shared helpers (no external deps)
# ---------------------------------------------------------------------------

def _scalar(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, pd.Series):
        val = val.iloc[0]
    if isinstance(val, pd.DataFrame):
        val = val.iloc[0, 0]
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _pct(a, b) -> Optional[float]:
    a, b = _scalar(a), _scalar(b)
    if a is None or b is None or b == 0:
        return None
    r = (a / b - 1) * 100
    return None if (math.isnan(r) or math.isinf(r)) else round(r, 2)


def _clean_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a yfinance weekly DataFrame: flatten MultiIndex, tz-strip, numeric."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    for col in ["Close", "High", "Low", "Open", "Volume"]:
        if col in df.columns:
            c = df[col]
            if isinstance(c, pd.DataFrame):
                c = c.iloc[:, 0]
            df[col] = pd.to_numeric(c, errors="coerce")
    return df.dropna(subset=["Close"])


# ---------------------------------------------------------------------------
# T — Trend: broad market + stock trend
# ---------------------------------------------------------------------------

def check_trend(spy_weekly: pd.DataFrame) -> dict:
    """
    T criterion: Is the broad market (SPY) in an uptrend?

    Guide: "If the market environment is good, we want to focus on going long
    with the strongest stocks in the strongest groups."

    TIMEFRAME: Weekly SPY data.

    Returns
    -------
    dict:
        pass_         bool   True if market is in uptrend
        market_state  str    "UPTREND" | "DOWNTREND" | "UNCLEAR"
        spy_price     float
        spy_50w_sma   float
        spy_above_50w bool   Price > 50-week SMA (primary uptrend test)
        spy_above_40w bool   Price > 40-week (200-day equiv) SMA
        breadth_note  str    Explanation of current market state
    """
    res = {
        "pass_": False, "market_state": "UNCLEAR",
        "spy_price": None, "spy_50w_sma": None,
        "spy_above_50w": False, "spy_above_40w": False,
        "breadth_note": None,
    }
    try:
        df = _clean_weekly(spy_weekly)
        df["sma50w"] = df["Close"].rolling(50, min_periods=40).mean()
        df["sma40w"] = df["Close"].rolling(40, min_periods=30).mean()

        price   = _scalar(df["Close"].iloc[-1])
        sma50   = _scalar(df["sma50w"].iloc[-1])
        sma40   = _scalar(df["sma40w"].iloc[-1])

        above50 = price is not None and sma50 is not None and price > sma50
        above40 = price is not None and sma40 is not None and price > sma40

        res.update({
            "spy_price":     round(price, 2) if price else None,
            "spy_50w_sma":   round(sma50, 2) if sma50 else None,
            "spy_above_50w": above50,
            "spy_above_40w": above40,
        })

        if above50 and above40:
            res["pass_"]        = True
            res["market_state"] = "UPTREND"
            res["breadth_note"] = "SPY above both 50w and 40w SMA — favourable for breakouts"
        elif above50 or above40:
            res["market_state"] = "MIXED"
            res["breadth_note"] = "SPY in mixed trend — reduce position sizing, be selective"
        else:
            res["market_state"] = "DOWNTREND"
            res["breadth_note"] = "SPY below long-term SMAs — elevated breakout failure rate"

    except Exception as e:
        res["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# I — Industry: leading group membership
# ---------------------------------------------------------------------------

def check_industry(weekly: pd.DataFrame, spy_weekly: pd.DataFrame,
                   peer_weeklies: Optional[dict] = None) -> dict:
    """
    I criterion: Is the stock in a leading industry group?

    Guide: "Industry Group Confirmation: Other stocks breaking out in the same
    group can confirm a stock's potential success." (p58, Launch Pad Setup)

    TIMEFRAME: Weekly data for RS calculation.

    Parameters
    ----------
    weekly       : Target stock weekly OHLCV
    spy_weekly   : SPY weekly OHLCV (benchmark)
    peer_weeklies: Optional dict {ticker: weekly_df} of same-industry peers.
                   If provided, we check how many peers also have strong RS.

    Returns
    -------
    dict:
        pass_           bool   True if RS_13w > 0 AND (no peers, or ≥1 peer confirms)
        rs_13w          float  Stock's 13-week RS vs SPY (%)
        rs_52w          float  Stock's 52-week RS vs SPY (%)
        peers_checked   int
        peers_positive  int    Peers with positive 13w RS
        group_confirming bool  ≥1 peer also has positive RS
        note            str
    """
    res = {
        "pass_": False, "rs_13w": None, "rs_52w": None,
        "peers_checked": 0, "peers_positive": 0,
        "group_confirming": False, "note": None,
    }
    try:
        stock = _clean_weekly(weekly)
        spy   = _clean_weekly(spy_weekly)
        common = stock.index.intersection(spy.index)

        if len(common) < 13:
            res["note"] = "Insufficient data for RS calculation"
            return res

        sc = stock["Close"].loc[common]
        sp = spy["Close"].loc[common]
        rs_line = sc / sp

        res["rs_13w"] = round(float(_pct(rs_line.iloc[-1], rs_line.iloc[-13]) or 0), 2)
        if len(rs_line) >= 52:
            res["rs_52w"] = round(float(_pct(rs_line.iloc[-1], rs_line.iloc[-52]) or 0), 2)

        stock_rs_positive = res["rs_13w"] is not None and res["rs_13w"] > 0

        # Peer confirmation
        if peer_weeklies:
            confirmed = 0
            for _pticker, pdf in peer_weeklies.items():
                try:
                    p = _clean_weekly(pdf)
                    pc = p["Close"].loc[p.index.intersection(spy.index)]
                    sp_p = spy["Close"].loc[pc.index]
                    rs_p = pc / sp_p
                    if len(rs_p) >= 13 and _pct(rs_p.iloc[-1], rs_p.iloc[-13]) > 0:
                        confirmed += 1
                except Exception:
                    pass
            res["peers_checked"]   = len(peer_weeklies)
            res["peers_positive"]  = confirmed
            res["group_confirming"] = confirmed >= 1

        res["pass_"] = stock_rs_positive and (not peer_weeklies or res["group_confirming"])
        res["note"]  = (
            "RS positive and group confirming" if res["pass_"] else
            "RS positive but no peer confirmation" if stock_rs_positive else
            "RS negative — not a leading stock in its group"
        )

    except Exception as e:
        res["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# G — Growth: EPS and Revenue acceleration
# ---------------------------------------------------------------------------

def check_growth(info: dict, quarterly_financials: pd.DataFrame) -> dict:
    """
    G criterion: Does the stock have accelerating fundamental growth?

    Guide: "strong earnings and revenue growth" — EPS ≥25% YoY, Revenue ≥20% YoY.
    These are the same thresholds used in the existing screener.

    INPUTS: yfinance .info dict + .quarterly_financials DataFrame.

    Returns
    -------
    dict:
        pass_           bool   True if eps_pass AND rev_pass
        eps_growth_yoy  float  Most recent quarter YoY EPS growth (%)
        rev_growth_yoy  float  Most recent quarter YoY Revenue growth (%)
        eps_pass        bool   EPS growth ≥ 25%
        rev_pass        bool   Revenue growth ≥ 20%
        industry        str    From yfinance info
        note            str
    """
    res = {
        "pass_": False, "eps_growth_yoy": None, "rev_growth_yoy": None,
        "eps_pass": False, "rev_pass": False,
        "industry": None, "note": None,
    }
    try:
        res["industry"] = info.get("industry")
        fin = quarterly_financials

        # EPS YoY — compare most recent quarter to same quarter one year ago (4 quarters back)
        for label in ["Diluted EPS", "Basic EPS"]:
            rows = [i for i in fin.index if label.lower() in i.lower()]
            if rows:
                eps = fin.loc[rows[0]].dropna()
                if len(eps) >= 5:
                    g = _pct(eps.iloc[0], eps.iloc[4])
                    res["eps_growth_yoy"] = g
                    res["eps_pass"]       = g is not None and g >= 25
                break

        # Revenue YoY
        rev_rows = [i for i in fin.index if "total revenue" in i.lower()]
        if rev_rows:
            rev = fin.loc[rev_rows[0]].dropna()
            if len(rev) >= 5:
                g = _pct(rev.iloc[0], rev.iloc[4])
                res["rev_growth_yoy"] = g
                res["rev_pass"]       = g is not None and g >= 20

        res["pass_"] = res["eps_pass"] and res["rev_pass"]
        res["note"]  = (
            f"EPS {res['eps_growth_yoy']}% YoY, Revenue {res['rev_growth_yoy']}% YoY"
            + (" — PASS" if res["pass_"] else " — needs ≥25% EPS and ≥20% Revenue")
        )

    except Exception as e:
        res["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# E — Earnings/Entry catalyst: gap-up and HV edge detection
# ---------------------------------------------------------------------------

def check_earnings_catalyst(daily: pd.DataFrame) -> dict:
    """
    E criterion: Is there a significant earnings/catalyst gap-up (HV edge)?

    Guide: "Many major moves start with a large gap up on extraordinary volume.
    This is a sign that the market has been caught off guard and institutions have
    been forced to start/increase their positions." (p71)

    HV edge categories (guide p55):
      HVE  — Highest Volume Ever
      HV1  — Highest Volume in over a year
      HVIPO — Highest Volume since IPO
      HVLE — Highest Volume since Last Earnings

    TIMEFRAME: Daily data.

    Returns
    -------
    dict:
        pass_           bool   True if a qualifying HV gap event is detected
        hv_edge         str    "HVE" | "HV1" | "HVLE" | "NONE"
        gap_pct         float  Gap-up % on event day
        event_date      str    Date of the HV event
        vol_ratio       float  Event day volume / 50-day avg volume
        close_in_upper_half bool  Close in upper 50% of day range (guide requirement)
        note            str
    """
    res = {
        "pass_": False, "hv_edge": "NONE",
        "gap_pct": None, "event_date": None,
        "vol_ratio": None, "close_in_upper_half": None,
        "note": None,
    }
    try:
        df = daily.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Compute 50-day avg volume (use rolling to capture historical)
        df["vol_50d_avg"] = df["Volume"].rolling(50, min_periods=20).mean()
        # Gap = today's open vs previous close
        df["gap_pct"] = (df["Open"] / df["Close"].shift(1) - 1) * 100
        # Day's closing range position
        day_range = df["High"] - df["Low"]
        df["close_range_pos"] = (df["Close"] - df["Low"]) / day_range.replace(0, np.nan)

        # Look for qualifying gap-up events in the last 90 trading days
        recent = df.tail(90).copy()
        # Only consider gap-ups ≥ 3%
        gaps = recent[recent["gap_pct"] >= 3].copy()
        if gaps.empty:
            res["note"] = "No qualifying gap-up found in last 90 days"
            return res

        # Pick the highest-volume gap day
        best = gaps.loc[gaps["Volume"].idxmax()]
        vol_ratio = _scalar(best["Volume"] / best["vol_50d_avg"]) if best["vol_50d_avg"] > 0 else None
        close_upper = _scalar(best["close_range_pos"]) >= 0.5 if _scalar(best["close_range_pos"]) is not None else False

        # Determine HV edge tier
        all_time_high_vol = float(df["Volume"].max())
        one_year_high_vol = float(df["Volume"].tail(252).max()) if len(df) >= 252 else all_time_high_vol
        event_vol = float(best["Volume"])

        if event_vol >= all_time_high_vol * 0.98:
            hv_edge = "HVE"
        elif event_vol >= one_year_high_vol * 0.98:
            hv_edge = "HV1"
        else:
            hv_edge = "HVLE"

        res.update({
            "hv_edge":            hv_edge,
            "gap_pct":            round(float(_scalar(best["gap_pct"]) or 0), 2),
            "event_date":         best.name.strftime("%Y-%m-%d"),
            "vol_ratio":          round(vol_ratio, 2) if vol_ratio else None,
            "close_in_upper_half": close_upper,
            # Pass if HVE/HV1 + closed in upper half, or HVLE + very strong volume (≥2x)
            "pass_": (
                hv_edge in ("HVE", "HV1") and close_upper
            ) or (
                hv_edge == "HVLE" and (vol_ratio or 0) >= 2.0 and close_upper
            ),
        })
        res["note"] = (
            f"{hv_edge} gap on {res['event_date']}: "
            f"+{res['gap_pct']}% gap, {res['vol_ratio']}x avg vol"
            + (" — PASS" if res["pass_"] else " — volume or close position insufficient")
        )

    except Exception as e:
        res["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# R — Relative Strength: RS line leading price
# ---------------------------------------------------------------------------

def check_rs(weekly: pd.DataFrame, spy_weekly: pd.DataFrame,
             pivot_price: Optional[float] = None) -> dict:
    """
    R criterion: Is the RS line at or near a new 52-week high?

    Guide: "RS Line: Look for stocks with a strong RS line that is currently in
    new high ground before the price action." (p58, Launch Pad Setup)
    The RS line leading the price (making new highs before the stock) is one of
    the most important signs of institutional accumulation.

    TIMEFRAME: Weekly data.

    Returns
    -------
    dict:
        pass_           bool   True if RS line is at or near 52-week high
        rs_at_new_high  bool   RS within 2% of 52-week high
        rs_leading      bool   RS at new high while price is not yet broken out
        rs_4w           float  4-week RS performance (%)
        rs_13w          float  13-week RS performance (%)
        rs_26w          float  26-week RS performance (%)
        rs_52w          float  52-week RS performance (%)
    """
    res = {
        "pass_": False, "rs_at_new_high": False, "rs_leading": False,
        "rs_4w": None, "rs_13w": None, "rs_26w": None, "rs_52w": None,
    }
    try:
        stock = _clean_weekly(weekly)
        spy   = _clean_weekly(spy_weekly)
        common = stock.index.intersection(spy.index)
        if len(common) < 13:
            return res

        sc = stock["Close"].loc[common]
        sp = spy["Close"].loc[common]
        rs = sc / sp

        curr_rs = float(rs.iloc[-1])
        curr_px = float(sc.iloc[-1])

        for weeks, key in [(4, "rs_4w"), (13, "rs_13w"), (26, "rs_26w"), (52, "rs_52w")]:
            if len(rs) >= weeks:
                res[key] = round(float(_pct(rs.iloc[-1], rs.iloc[-weeks]) or 0), 2)

        if len(rs) >= 52:
            rs_52w_high = float(rs.iloc[-52:].max())
            near_high = curr_rs >= rs_52w_high * 0.98
            res["rs_at_new_high"] = near_high

            # "Leading" = RS at new high but price has not yet broken out above pivot
            not_broken_out = pivot_price is None or curr_px <= pivot_price
            res["rs_leading"] = near_high and not_broken_out

        res["pass_"] = res["rs_at_new_high"]

    except Exception as e:
        res["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# S — Stage: Stage 2 advancing phase
# ---------------------------------------------------------------------------

def check_stage(weekly: pd.DataFrame) -> dict:
    """
    S criterion: Is the stock in Stage 2 (advancing phase)?

    This is a thin wrapper around stage_analysis.classify_stage() so the TIGERS
    framework can use stage classification without importing the full module.

    TIMEFRAME: Weekly data.

    Returns
    -------
    dict:
        pass_           bool   True if stage is 2 (substage 2A or 2B)
        stage           int    1, 2, 3, or 4
        substage        str    "1", "1B", "2A", "2B", "3", "4"
        stage_label     str
        sma30w          float
        sma30w_rising   bool
        pct_above_30w   float
    """
    try:
        from agents.base_breakout_strategy.stage_analysis import classify_stage
    except ImportError:
        # Fallback: inline minimal Stage 2 check if module not on path
        return _inline_stage2_check(weekly)

    r = classify_stage(weekly)
    return {
        "pass_":        r.get("stage") == 2,
        "stage":        r.get("stage"),
        "substage":     r.get("substage"),
        "stage_label":  r.get("stage_label"),
        "sma30w":       r.get("sma30w"),
        "sma30w_rising": (r.get("sma30w_slope") or 0) > 0,
        "pct_above_30w": r.get("pct_above_30w"),
        "error":        r.get("error"),
    }


def _inline_stage2_check(weekly: pd.DataFrame) -> dict:
    """Fallback Stage 2 check if stage_analysis module is unavailable."""
    res = {"pass_": False, "stage": None, "substage": None,
           "stage_label": None, "sma30w": None, "sma30w_rising": False,
           "pct_above_30w": None}
    try:
        df = _clean_weekly(weekly)
        df["sma30w"] = df["Close"].rolling(30, min_periods=25).mean()
        price = _scalar(df["Close"].iloc[-1])
        sma30 = _scalar(df["sma30w"].iloc[-1])
        sma30_prev = _scalar(df["sma30w"].iloc[-5]) if len(df) >= 8 else None
        rising = sma30_prev is not None and sma30 > sma30_prev
        above  = price is not None and sma30 is not None and price > sma30
        res.update({
            "sma30w": round(sma30, 2) if sma30 else None,
            "sma30w_rising": rising,
            "pct_above_30w": _pct(price, sma30),
            "pass_": above and rising,
            "stage": 2 if (above and rising) else 1,
            "substage": "2A" if (above and rising) else "1",
        })
    except Exception as e:
        res["error"] = str(e)
    return res


# ---------------------------------------------------------------------------
# Composite TIGERS score
# ---------------------------------------------------------------------------

def tigers_score(trend: dict, industry: Optional[dict] = None,
                 growth: Optional[dict] = None,
                 earnings: Optional[dict] = None,
                 rs: Optional[dict] = None,
                 stage: Optional[dict] = None) -> dict:
    """
    Combine individual TIGERS criteria into a composite score.

    Each criterion that passes contributes 1 point (max 6).
    Guide: "The more edges and winning characteristics that a stock exhibits
    the more you should focus on it. Put the odds in your favor." (p70)

    Returns
    -------
    dict:
        score       int    0–6
        max_score   int    Number of criteria actually evaluated
        pass_pct    float  score / max_score * 100
        quality     str    "A+" (≥5) | "A" (4) | "B" (3) | "C" (<3)
        breakdown   dict   Per-criterion pass/fail
        market_ok   bool   Trend (T) passes — if False, don't trade regardless of score
    """
    checks = {
        "T_trend":    (trend.get("pass_", False),    trend is not None),
        "I_industry": (industry.get("pass_", False) if industry else False, industry is not None),
        "G_growth":   (growth.get("pass_", False)   if growth   else False, growth   is not None),
        "E_earnings": (earnings.get("pass_", False) if earnings else False, earnings is not None),
        "R_rs":       (rs.get("pass_", False)       if rs       else False, rs       is not None),
        "S_stage":    (stage.get("pass_", False)    if stage    else False, stage    is not None),
    }

    evaluated = {k: v for k, v in checks.items() if v[1]}
    passed    = {k: v[0] for k, v in evaluated.items()}
    score     = sum(passed.values())
    max_score = len(evaluated)

    pass_pct = round(score / max_score * 100, 1) if max_score > 0 else 0
    quality  = "A+" if score >= 5 else "A" if score == 4 else "B" if score == 3 else "C"

    return {
        "score":      score,
        "max_score":  max_score,
        "pass_pct":   pass_pct,
        "quality":    quality,
        "market_ok":  trend.get("pass_", False),
        "breakdown":  {k: "PASS" if v else "FAIL" for k, v in passed.items()},
    }


# ---------------------------------------------------------------------------
# Convenience: full TIGERS evaluation for a single ticker
# ---------------------------------------------------------------------------

def evaluate_tigers(ticker: str, spy_weekly: Optional[pd.DataFrame] = None,
                    period: str = "3y") -> dict:
    """
    Run a full TIGERS evaluation for one ticker using yfinance.

    Parameters
    ----------
    ticker     : Stock ticker (e.g., "NVDA")
    spy_weekly : Pre-fetched SPY weekly DataFrame (reuse across calls to save time)
    period     : yfinance period string (default "3y")

    Returns
    -------
    Full dict with 'score', 'quality', 'breakdown', and per-criterion detail.
    """
    import threading, yfinance as yf

    _lock = threading.Lock()
    ticker = ticker.upper()

    try:
        with _lock:
            weekly = yf.download(ticker, period=period, interval="1wk",
                                  auto_adjust=True, progress=False)
            daily  = yf.Ticker(ticker).history(period="1y", interval="1d",
                                               auto_adjust=True)
            t_obj  = yf.Ticker(ticker)
            info   = t_obj.info or {}
            fin    = t_obj.quarterly_financials

        if spy_weekly is None:
            with _lock:
                spy_weekly = yf.download("SPY", period=period, interval="1wk",
                                          auto_adjust=True, progress=False)

        t_result = check_trend(spy_weekly)
        i_result = check_industry(weekly, spy_weekly)
        g_result = check_growth(info, fin)
        e_result = check_earnings_catalyst(daily)
        r_result = check_rs(weekly, spy_weekly)
        s_result = check_stage(weekly)
        composite = tigers_score(t_result, i_result, g_result, e_result, r_result, s_result)

        return {
            "ticker": ticker,
            **composite,
            "detail": {
                "T_trend":    t_result,
                "I_industry": i_result,
                "G_growth":   g_result,
                "E_earnings": e_result,
                "R_rs":       r_result,
                "S_stage":    s_result,
            }
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "score": 0, "quality": "ERROR"}


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, json

    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AAPL", "META"]
    print(f"Running TIGERS evaluation for: {tickers}\n")

    import yfinance as yf
    spy_w = yf.download("SPY", period="3y", interval="1wk", auto_adjust=True, progress=False)

    for ticker in tickers:
        r = evaluate_tigers(ticker, spy_weekly=spy_w)
        print(f"{r['ticker']:6s}  Score {r['score']}/{r['max_score']}  "
              f"Quality {r.get('quality','?'):3s}  "
              f"Market {'OK' if r.get('market_ok') else 'CAUTION':7s}  "
              f"{r.get('breakdown', {})}")
        if r.get("error"):
            print(f"       ERROR: {r['error']}")
