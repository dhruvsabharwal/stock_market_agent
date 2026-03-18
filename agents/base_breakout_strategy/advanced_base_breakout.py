import yfinance as yf
import pandas as pd
import numpy as np
import math
import sys
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Global lock for yfinance calls (known to be flaky in parallel threads)
YF_LOCK = threading.Lock()

# ──────────────────────────────────────────────────────────────────────────────
# 1. Utility & Sanitisation
# ──────────────────────────────────────────────────────────────────────────────

def _clean(val):
    """Recursively sanitise a value for JSON output."""
    if val is None:
        return None
    # Handle NaN/Inf floats
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    # Handle numpy scalars
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    # Handle pandas Series/Index (convert to list)
    if hasattr(val, 'to_list'):
        return _clean(val.to_list())
    # Handle pandas Timestamp or datetime
    if hasattr(val, 'strftime'):
        return val.strftime('%Y-%m-%d')
    # Handle dictionaries
    if isinstance(val, dict):
        return {k: _clean(v) for k, v in val.items()}
    # Handle lists/tuples
    if isinstance(val, (list, tuple)):
        return [_clean(v) for v in val]
    return val

def safe_div(a, b, default=None):
    """Division that returns default if b is zero, None, or NaN."""
    try:
        if b is None or (isinstance(b, float) and math.isnan(b)) or b == 0:
            return default
        result = a / b
        return None if math.isnan(result) or math.isinf(result) else result
    except Exception:
        return default

def safe_pct(a, b, default=None):
    """(a/b - 1)*100 with full NaN/zero safety."""
    r = safe_div(a, b)
    return None if r is None else round((r - 1) * 100, 1)

def safe_round(val, decimals=1, default=None):
    try:
        # Enforce scalar extraction for Series to avoid FutureWarnings
        if hasattr(val, 'iloc'):
            f = float(val.iloc[0])
        else:
            f = float(val)
            
        return default if math.isnan(f) or math.isinf(f) else round(f, decimals)
    except Exception:
        return default

def build_result(ticker, **sections) -> dict:
    raw = {"ticker": ticker, **sections}
    return _clean(raw)

class AdvancedBaseBreakoutAnalyzer:
    """
    Advanced Base Breakout Strategy Analyzer.
    Encapsulates all logic for technical and fundamental stock analysis.
    """
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers

# ──────────────────────────────────────────────────────────────────────────────
# 2. Data Loading
# ──────────────────────────────────────────────────────────────────────────────

    def _load_data(self, ticker: str) -> tuple:
        """
        Returns (stock_weekly, spy_weekly, stock_daily, info, financials).
        stock_weekly and spy_weekly are plain DataFrames with tz-naive DatetimeIndex.
        Uses a global lock to prevent yfinance threading issues.
        """
        ticker = ticker.upper()
        
        with YF_LOCK:
            try:
                # Download stock weekly
                stock_weekly = yf.download(ticker, period="3y", interval="1wk", 
                                           progress=False, auto_adjust=True)
                if not stock_weekly.empty:
                    # Flatten MultiIndex if present
                    if isinstance(stock_weekly.columns, pd.MultiIndex):
                        stock_weekly.columns = stock_weekly.columns.get_level_values(0)
                    
                    if hasattr(stock_weekly.index, 'tz') and stock_weekly.index.tz is not None:
                        stock_weekly.index = stock_weekly.index.tz_localize(None)
                    stock_weekly = stock_weekly.dropna()

                # Download SPY weekly
                spy_weekly = yf.download("SPY", period="3y", interval="1wk", 
                                         progress=False, auto_adjust=True)
                if not spy_weekly.empty:
                    # Flatten MultiIndex if present
                    if isinstance(spy_weekly.columns, pd.MultiIndex):
                        spy_weekly.columns = spy_weekly.columns.get_level_values(0)
                        
                    if hasattr(spy_weekly.index, 'tz') and spy_weekly.index.tz is not None:
                        spy_weekly.index = spy_weekly.index.tz_localize(None)
                    spy_weekly = spy_weekly.dropna()

                # Ticker object for daily data and info
                t = yf.Ticker(ticker)
                stock_daily = t.history(period="1y", interval="1d", auto_adjust=True)
                if not stock_daily.empty:
                    # history() usually returns single-level for one ticker, but safety first
                    if isinstance(stock_daily.columns, pd.MultiIndex):
                        stock_daily.columns = stock_daily.columns.get_level_values(0)
                        
                    if hasattr(stock_daily.index, 'tz') and stock_daily.index.tz is not None:
                        stock_daily.index = stock_daily.index.tz_localize(None)
                    stock_daily = stock_daily.dropna()

                info = t.info or {}
                financials = t.quarterly_financials

            except Exception as e:
                raise ValueError(f"YFinance Error for {ticker}: {str(e)}")

        if stock_weekly.empty:
            raise ValueError(f"No weekly price data returned for {ticker}")
        if stock_daily.empty:
            raise ValueError(f"No daily price data returned for {ticker}")
        if spy_weekly.empty:
            raise ValueError(f"No SPY data returned (required for market context)")

        # Add computed columns to daily
        stock_daily = stock_daily.copy()
        stock_daily['range_pct']  = (stock_daily['High'] - stock_daily['Low']) / stock_daily['Close'] * 100
        stock_daily['vol_vs_avg'] = stock_daily['Volume'] / stock_daily['Volume'].rolling(50).mean()
        stock_daily['EMA10']      = stock_daily['Close'].ewm(span=10, adjust=False).mean()
        stock_daily['EMA21']      = stock_daily['Close'].ewm(span=21, adjust=False).mean()
        stock_daily['SMA50']      = stock_daily['Close'].rolling(50).mean()

        return stock_weekly, spy_weekly, stock_daily, info, financials

# ──────────────────────────────────────────────────────────────────────────────
# 3. Section Analysis Functions
# ──────────────────────────────────────────────────────────────────────────────

    def _market_context(self, spy_weekly: pd.DataFrame) -> dict:
        spy_weekly = spy_weekly.copy()
        spy_weekly['SMA50w'] = spy_weekly['Close'].rolling(50).mean()

        curr_spy   = safe_round(spy_weekly['Close'].iloc[-1])
        sma50w_spy = safe_round(spy_weekly['SMA50w'].iloc[-1])
        spy_above_50w = (curr_spy is not None and sma50w_spy is not None
                         and curr_spy > sma50w_spy)

        return {
            "spy_price":       curr_spy,
            "spy_50w_sma":     sma50w_spy,
            "spy_above_50w":   spy_above_50w,
            "market_state":    "UPTREND" if spy_above_50w else "DOWNTREND",
            "warning":         None if spy_above_50w else
                               "SPY below 50w SMA — elevated breakout failure rate. "
                               "Reduce position sizing. Do not chase Entry 2 without exceptional volume."
        }

    def _analyze_fundamentals(self, info: dict, financials: pd.DataFrame) -> tuple[dict, int]:
        """Returns (result_dict, score_points)."""
        score = 0
        res = {
            "eps_growth_yoy":  None,
            "rev_growth_yoy":  None,
            "net_margin_pct":  None,
            "gross_margin_pct": None,
            "eps_5yr_cagr":    None,
            "margin_pass":     False,
            "eps_pass":        False,
            "rev_pass":        False,
            "data_quality":    "OK",
            "industry":        info.get('industry'),
            "sector":          info.get('sector'),
            "margin_note":     None,
            "distortion_note": None,
            "error_detail":    None
        }

        try:
            # EPS YoY — look for Diluted EPS first, fall back to Basic EPS
            for label in ['Diluted EPS', 'Basic EPS']:
                rows = [i for i in financials.index if label.lower() in i.lower()]
                if rows:
                    eps_data = financials.loc[rows[0]].dropna()
                    if len(eps_data) >= 5:
                        growth = safe_pct(eps_data.iloc[0], eps_data.iloc[4])
                        res['eps_growth_yoy'] = growth
                        if growth is not None and growth >= 25:
                            score += 1
                            res['eps_pass'] = True
                    break

            # Revenue YoY
            rev_rows = [i for i in financials.index if 'total revenue' in i.lower()]
            if rev_rows:
                rev_data = financials.loc[rev_rows[0]].dropna()
                if len(rev_data) >= 5:
                    growth = safe_pct(rev_data.iloc[0], rev_data.iloc[4])
                    res['rev_growth_yoy'] = growth
                    if growth is not None and growth >= 20:
                        score += 1
                        res['rev_pass'] = True

            # Net margin
            pm = info.get('profitMargins')
            if pm is not None:
                margin = safe_round(pm * 100, 1)
                res['net_margin_pct'] = margin
                industry = (info.get('industry') or '').lower()
                is_tech = any(w in industry for w in ('software', 'technology', 'semiconductor'))
                is_low_margin = any(w in industry for w in ('retail', 'grocery', 'food', 'wholesale'))
                if is_low_margin:
                    gm = safe_round((info.get('grossMargins') or 0) * 100, 1)
                    res['gross_margin_pct'] = gm
                    res['margin_pass'] = (gm is not None and gm >= 20)
                    if res['margin_pass']:
                        res['margin_note'] = "Low-margin business: gross margin used instead of net margin"
                else:
                    threshold = 15 if is_tech else 8
                    res['margin_pass'] = (margin is not None and margin >= threshold)

            # 5yr EPS CAGR
            eg = info.get('earningsGrowth')
            if eg is not None:
                cagr = safe_round(eg * 100, 1)
                res['eps_5yr_cagr'] = cagr
                if (res['eps_growth_yoy'] is not None and res['eps_growth_yoy'] < 0
                        and cagr is not None and cagr >= 15):
                    res['data_quality'] = "DISTORTED"
                    res['distortion_note'] = (
                        f"YoY EPS negative ({res['eps_growth_yoy']}%) but 5yr CAGR is "
                        f"{cagr}% — likely distorted by one-off items. Use 5yr CAGR as primary signal."
                    )

        except Exception as e:
            res['data_quality'] = "MISSING"
            res['error_detail'] = str(e)

        return res, score

    def _calculate_rs(self, stock_weekly: pd.DataFrame, spy_weekly: pd.DataFrame,
                     pivot_price: float, already_broken_out: bool) -> tuple[dict, int]:
        score = 0
        res = {
            "rs_4w": None, "rs_13w": None, "rs_26w": None, "rs_52w": None,
            "rs_line_current": None, "rs_52w_high": None,
            "rs_at_52w_high": False, "leading_rs": False
        }

        try:
            common = stock_weekly.index.intersection(spy_weekly.index)
            if len(common) < 13:
                return res, score

            # Explicitly select 'Close' as a Series
            sc = stock_weekly['Close'].loc[common]
            if isinstance(sc, pd.DataFrame):
                sc = sc.iloc[:, 0]
                
            sp = spy_weekly['Close'].loc[common]
            if isinstance(sp, pd.DataFrame):
                sp = sp.iloc[:, 0]
                
            rs = sc / sp

            res['rs_line_current'] = safe_round(float(rs.iloc[-1]), 4)

            for weeks, key in [(4, 'rs_4w'), (13, 'rs_13w'), (26, 'rs_26w'), (52, 'rs_52w')]:
                if len(rs) >= weeks:
                    res[key] = safe_pct(rs.iloc[-1], rs.iloc[-weeks])

            if len(rs) >= 52:
                rs_high = float(rs.iloc[-52:].max())
                res['rs_52w_high'] = safe_round(rs_high, 4)
                res['rs_at_52w_high'] = float(rs.iloc[-1]) >= rs_high * 0.98

            res['leading_rs'] = (res['rs_at_52w_high'] and not already_broken_out)

            if res['rs_13w'] is not None and res['rs_13w'] > 0:
                score += 1

        except Exception:
            pass

        return res, score

    def _stage2_check(self, stock_weekly: pd.DataFrame, stock_daily: pd.DataFrame) -> tuple[dict, int]:
        score = 0
        res = {
            "confirmed": False, "sma30w": None, "current_price": None,
            "sma30w_rising": False, "pct_above_30w_sma": None,
            "pct_from_52w_high": None, "pct_above_52w_low": None,
            "prior_uptrend": False, "high_52w": None, "low_52w": None
        }

        try:
            w = stock_weekly.copy()
            w['SMA30w'] = w['Close'].rolling(30).mean()

            # Enforce scalar extraction to avoid FutureWarnings
            curr  = float(w['Close'].iloc[-1]) if not isinstance(w['Close'].iloc[-1], pd.Series) else float(w['Close'].iloc[-1].iloc[0])
            sma30 = float(w['SMA30w'].iloc[-1]) if not isinstance(w['SMA30w'].iloc[-1], pd.Series) else float(w['SMA30w'].iloc[-1].iloc[0])
            
            # Relative shift for rising check
            if len(w) >= 5:
                s_prev = w['SMA30w'].iloc[-5]
                sma30_4w = float(s_prev) if not isinstance(s_prev, pd.Series) else float(s_prev.iloc[0])
            else:
                sma30_4w = None

            res['current_price']     = safe_round(curr, 2)
            res['sma30w']            = safe_round(sma30, 2)
            res['sma30w_rising']     = sma30_4w is not None and sma30 > sma30_4w
            res['pct_above_30w_sma'] = safe_pct(curr, sma30)

            above_sma = curr > sma30
            res['confirmed'] = above_sma and res['sma30w_rising']

            high52 = stock_daily['High'].rolling(252, min_periods=50).max().iloc[-1]
            low52  = stock_daily['Low'].rolling(252, min_periods=50).min().iloc[-1]

            res['pct_from_52w_high']  = safe_pct(curr, float(high52))
            res['pct_above_52w_low']  = safe_pct(curr, float(low52))
            res['prior_uptrend']      = (res['pct_above_52w_low'] is not None
                                         and res['pct_above_52w_low'] >= 30)
            res['high_52w'] = safe_round(float(high52), 2)
            res['low_52w']  = safe_round(float(low52), 2)

            if res['confirmed']:
                score += 1

        except Exception:
            pass

        return res, score

    def _base_analysis(self, stock_weekly: pd.DataFrame) -> tuple[dict, int, object]:
        """Returns (result, score_points, base_high_idx)."""
        score = 0
        res = {
            "pattern": "Unclear", "length_weeks": None, "depth_pct": None,
            "base_high": None, "base_high_date": None,
            "base_low": None, "base_low_date": None,
            "pass_depth": False, "vcp_swings": [None, None, None],
            "vcp_contracting": False
        }
        base_high_idx = None

        try:
            wc = stock_weekly['Close']
            lookback = min(54, len(wc))
            
            # Use max().item() or similar to ensure scalar
            b_high_val = wc.iloc[-lookback:].max()
            base_high  = float(b_high_val) if not isinstance(b_high_val, pd.Series) else float(b_high_val.iloc[0])
            base_high_idx = wc.iloc[-lookback:].idxmax()
            if isinstance(base_high_idx, pd.Series):
                base_high_idx = base_high_idx.iloc[0]

            post = wc.loc[base_high_idx:]
            if post.empty:
                return res, score, None

            b_low_val = post.min()
            base_low  = float(b_low_val) if not isinstance(b_low_val, pd.Series) else float(b_low_val.iloc[0])
            depth_pct = safe_pct(base_low, base_high)
            length    = len(post)

            res['base_high']      = safe_round(base_high, 2)
            res['base_high_date'] = base_high_idx
            res['base_low']       = safe_round(base_low, 2)
            res['base_low_date']  = post.idxmin()
            res['depth_pct']      = depth_pct
            res['length_weeks']   = length
            res['pass_depth']     = depth_pct is not None and depth_pct >= -50

            if depth_pct is not None:
                if -33 <= depth_pct <= -15 and length >= 7:
                    res['pattern'] = "Cup with Handle"
                elif -15 < depth_pct <= -5 and length >= 5:
                    res['pattern'] = "Flat Base"
                elif -35 <= depth_pct <= -10 and length >= 5:
                    res['pattern'] = "VCP"

            n = len(post)
            if n >= 6:
                segs = [post.iloc[:n//3], post.iloc[n//3:2*n//3], post.iloc[2*n//3:]]
                swings = []
                for seg in segs:
                    s_max = seg.max()
                    s_min = seg.min()
                    hi = float(s_max) if not isinstance(s_max, pd.Series) else float(s_max.iloc[0])
                    lo = float(s_min) if not isinstance(s_min, pd.Series) else float(s_min.iloc[0])
                    swings.append(safe_round(safe_pct(lo, hi) * -1, 1))
                res['vcp_swings']      = swings
                res['vcp_contracting'] = (None not in swings
                                          and swings[2] < swings[1] < swings[0])

            if res['pattern'] != "Unclear" and res['pass_depth']:
                score += 1
            if res['vcp_contracting']:
                score += 1

        except Exception:
            pass

        return res, score, base_high_idx

    def _volume_analysis(self, stock_weekly: pd.DataFrame, base_high_idx) -> tuple[dict, int]:
        score = 0
        res = {
            "avg_vol_up_weeks": None, "avg_vol_down_weeks": None,
            "accumulation_ratio": None, "accumulation_pass": False,
            "vol_dry_pct": None, "vol_dry_pass": False
        }

        if base_high_idx is None:
            return res, score

        try:
            df = stock_weekly.loc[base_high_idx:].copy()
            ret = df['Close'].pct_change()

            up_vol   = df.loc[ret > 0, 'Volume'].mean()
            down_vol = df.loc[ret < 0, 'Volume'].mean()

            ratio = safe_div(up_vol, down_vol)
            res['avg_vol_up_weeks']    = safe_round(float(up_vol), 0) if up_vol else None
            res['avg_vol_down_weeks']  = safe_round(float(down_vol), 0) if down_vol else None
            res['accumulation_ratio']  = safe_round(ratio, 2)
            res['accumulation_pass']   = ratio is not None and ratio > 1.0

            n = len(df)
            if n >= 4:
                left  = float(df['Volume'].iloc[:n//2].mean())
                right = float(df['Volume'].iloc[n//2:].mean())
                dry   = safe_pct(right, left)
                res['vol_dry_pct']  = dry
                res['vol_dry_pass'] = dry is not None and dry <= -20

            if res['accumulation_pass']:
                score += 1
            if res['vol_dry_pass']:
                score += 1

        except Exception:
            pass

        return res, score

    def _tight_area_detection(self, stock_daily: pd.DataFrame) -> tuple[dict, int]:
        score = 0
        res = {
            "tight_area_count": 0, "last_tight_date": None,
            "first_30d_avg_range_pct": None, "last_30d_avg_range_pct": None,
            "volatility_contraction_pct": None, "volatility_contracting": False,
            "tight_area_pass": False
        }

        try:
            recent = stock_daily.tail(60).copy()

            tight_count, current_run = 0, 0
            last_tight_date = None
            for idx, row in recent.iterrows():
                rp  = row.get('range_pct')
                vva = row.get('vol_vs_avg')
                is_tight = (rp is not None and not math.isnan(float(rp)) and float(rp) <= 1.5
                            and vva is not None and not math.isnan(float(vva)) and float(vva) <= 0.5)
                if is_tight:
                    current_run += 1
                    last_tight_date = idx
                else:
                    if current_run >= 2:
                        tight_count += 1
                    current_run = 0
            if current_run >= 2:
                tight_count += 1

            first30 = safe_round(recent['range_pct'].iloc[:30].mean(), 2)
            last30  = safe_round(recent['range_pct'].iloc[30:].mean(), 2)
            contraction = safe_pct(last30, first30) if first30 else None

            res['tight_area_count']             = tight_count
            res['last_tight_date']              = last_tight_date
            res['first_30d_avg_range_pct']      = first30
            res['last_30d_avg_range_pct']       = last30
            res['volatility_contraction_pct']   = contraction
            res['volatility_contracting']       = contraction is not None and contraction <= -20
            res['tight_area_pass']              = tight_count > 0

            if res['tight_area_pass']:
                score += 1

        except Exception:
            pass

        return res, score

    def _priming_patterns(self, stock_daily: pd.DataFrame, pivot_price: float) -> tuple[dict, int]:
        score = 0
        res = {"inside_bars": [], "upside_reversals": [], "tight_setup_days": [], "best_signal": None}

        try:
            last10 = stock_daily.tail(11)

            for i in range(1, len(last10)):
                today = last10.iloc[i]
                yest  = last10.iloc[i-1]
                date  = last10.index[i].strftime('%Y-%m-%d')
                vva   = safe_round(today.get('vol_vs_avg'), 2)

                if (today['High'] < yest['High'] and today['Low'] > yest['Low']
                        and vva is not None and vva < 0.75):
                    res['inside_bars'].append({"date": date, "vol_ratio": vva})

                day_range = float(today['High']) - float(today['Low'])
                if day_range > 0:
                    drop_pct  = safe_pct(today['Low'], today['Open'])
                    close_pos = safe_div(float(today['Close']) - float(today['Low']), day_range)
                    if (drop_pct is not None and drop_pct <= -1.0
                            and close_pos is not None and close_pos >= 0.70):
                        res['upside_reversals'].append({"date": date, "close_position_pct": safe_round(close_pos * 100, 1)})

                if pivot_price:
                    dist = safe_pct(today['Close'], pivot_price)
                    rp   = safe_round(today.get('range_pct'), 2)
                    if (dist is not None and abs(dist) <= 2.0
                            and rp is not None and rp <= 1.0
                            and vva is not None and vva <= 0.4):
                        res['tight_setup_days'].append({
                            "date": date, "dist_from_pivot_pct": safe_round(dist, 1), "vol_ratio": vva
                        })

            if res['tight_setup_days']:
                res['best_signal'] = "TIGHT_SETUP_DAY"
            elif res['inside_bars']:
                res['best_signal'] = "INSIDE_BAR"
            elif res['upside_reversals']:
                res['best_signal'] = "UPSIDE_REVERSAL"

            if res['best_signal']:
                score += 1

        except Exception:
            pass

        return res, score

    def _find_support_levels(self, stock_daily: pd.DataFrame, base_high_idx, current_price: float) -> dict:
        res = {"primary_support": None}
        if base_high_idx is None:
            return res
        try:
            base_daily = stock_daily.loc[base_high_idx:]
            lows = base_daily['Low'].dropna()
            seen = set()
            for low in lows:
                low_f = float(low)
                nearby = lows[(lows >= low_f * 0.985) & (lows <= low_f * 1.015)]
                if len(nearby) >= 2:
                    rounded = round(low_f, 2)
                    seen.add(rounded)

            levels = sorted(seen)
            below = [s for s in levels if s < current_price]
            res['primary_support'] = max(below) if below else None

        except Exception:
            pass
        return res

    def _entry_and_stops(self, stock_daily: pd.DataFrame, stock_weekly: pd.DataFrame,
                        pivot_price: float, primary_support: float,
                        priming: dict) -> tuple[dict, int]:
        score = 0
        res = {
            "pivot_price": safe_round(pivot_price, 2),
            "already_broken_out": False,
            "entry1": {
                "trigger":       None,
                "stop":          None,
                "risk_pct":      None,
                "signal_type":   None,
                "signal_date":   None,
                "status":        "NOT_AVAILABLE",
                "sizing_per_500_risk": {"shares": None, "position_value": None}
            },
            "entry2": {
                "trigger":        None,
                "stop":           None,
                "risk_pct":       None,
                "vol_required":   "≥1.5x 50-day average",
                "status":         "WATCHING",
                "breakout_date":  None,
                "breakout_vol_ratio": None,
                "sizing_per_500_risk": {"shares": None, "position_value": None}
            },
            "stop_levels": {
                "ema10":          None,
                "ema21":          None,
                "sma50":          None,
                "primary_support": safe_round(primary_support, 2) if primary_support else None,
                "ema10_dist_pct": None,
                "ema21_dist_pct": None,
                "sma50_dist_pct": None,
                "support_dist_pct": None,
                "ema10_state":    None,
                "ema21_state":    None,
                "sma50_state":    None,
                "recommendation_if_in_entry1": None,
                "recommendation_if_in_entry2": None,
                "recommendation_reason":       None,
                "hard_stop_from_e1": None,
                "hard_stop_from_e2": None,
            }
        }

        try:
            curr = float(stock_daily['Close'].iloc[-1])
            res['already_broken_out'] = (pivot_price is not None and curr > pivot_price)

            if pivot_price:
                e2_trigger = safe_round(pivot_price * 1.005, 2)
                e2_stop    = safe_round(pivot_price * 0.970, 2)
                e2_risk    = safe_pct(e2_stop, e2_trigger) * -1 if e2_trigger else None

                res['entry2']['trigger']   = e2_trigger
                res['entry2']['stop']      = e2_stop
                res['entry2']['risk_pct']  = safe_round(e2_risk, 1)

                if e2_trigger and e2_stop and e2_risk:
                    shares_e2 = safe_round(500 / (e2_trigger - e2_stop), 1)
                    res['entry2']['sizing_per_500_risk'] = {
                        "shares":         shares_e2,
                        "position_value": safe_round(shares_e2 * e2_trigger, 0) if shares_e2 else None
                    }

                if res['already_broken_out']:
                    df_d = stock_daily
                    bo_days = df_d[df_d['Close'] > pivot_price]
                    if not bo_days.empty:
                        bo_day = bo_days.index[0]
                        bo_vol = float(df_d.loc[bo_day, 'Volume'])
                        avg_vol = float(df_d.loc[:bo_day, 'Volume'].rolling(50).mean().iloc[-1])
                        vol_ratio = safe_div(bo_vol, avg_vol)
                        res['entry2']['status'] = (
                            "TRIGGERED_STRONG_VOL" if vol_ratio and vol_ratio >= 2.0 else
                            "TRIGGERED_WEAK_VOL"   if vol_ratio and vol_ratio < 1.5 else
                            "TRIGGERED"
                        )
                        res['entry2']['breakout_date']       = bo_day.strftime('%Y-%m-%d')
                        res['entry2']['breakout_vol_ratio']  = safe_round(vol_ratio, 2)
                else:
                    res['entry2']['status'] = "WATCHING"

            signal_map = {
                "TIGHT_SETUP_DAY": priming.get('tight_setup_days', []),
                "INSIDE_BAR":      priming.get('inside_bars', []),
                "UPSIDE_REVERSAL": priming.get('upside_reversals', []),
            }
            best = priming.get('best_signal')
            if best and signal_map.get(best):
                latest_signal = signal_map[best][-1]
                signal_date   = latest_signal['date']
                try:
                    e1_trigger = float(stock_daily.loc[signal_date, 'High'])
                except KeyError:
                    matches = stock_daily[stock_daily.index.strftime('%Y-%m-%d') == signal_date]
                    e1_trigger = float(matches['High'].iloc[0]) if not matches.empty else None

                e1_stop = primary_support

                if e1_trigger and e1_stop and e1_trigger > e1_stop:
                    e1_risk = safe_round((e1_trigger - e1_stop) / e1_trigger * 100, 1)
                    res['entry1']['trigger']     = safe_round(e1_trigger, 2)
                    res['entry1']['stop']        = safe_round(e1_stop, 2)
                    res['entry1']['risk_pct']    = e1_risk
                    res['entry1']['signal_type'] = best
                    res['entry1']['signal_date'] = signal_date

                    if curr > e1_trigger:
                        res['entry1']['status'] = "TRIGGERED"
                    else:
                        res['entry1']['status'] = "WATCHING"

                    shares_e1 = safe_round(500 / (e1_trigger - e1_stop), 1) if (e1_trigger - e1_stop) > 0 else None
                    res['entry1']['sizing_per_500_risk'] = {
                        "shares":         shares_e1,
                        "position_value": safe_round(shares_e1 * e1_trigger, 0) if shares_e1 else None
                    }
                    res['stop_levels']['hard_stop_from_e1'] = safe_round(e1_trigger * 0.925, 2)
                    if e1_risk and e1_risk <= 4.0:
                        score += 1

            if res['entry2']['trigger']:
                res['stop_levels']['hard_stop_from_e2'] = safe_round(res['entry2']['trigger'] * 0.925, 2)

            last = stock_daily.iloc[-1]
            prev = stock_daily.iloc[-2]

            ema10 = safe_round(float(last['EMA10']), 2)
            ema21 = safe_round(float(last['EMA21']), 2)
            sma50 = safe_round(float(last['SMA50']), 2)

            res['stop_levels']['ema10'] = ema10
            res['stop_levels']['ema21'] = ema21
            res['stop_levels']['sma50'] = sma50

            res['stop_levels']['ema10_dist_pct']    = safe_pct(curr, ema10)
            res['stop_levels']['ema21_dist_pct']    = safe_pct(curr, ema21)
            res['stop_levels']['sma50_dist_pct']    = safe_pct(curr, sma50)
            res['stop_levels']['support_dist_pct']  = safe_pct(curr, primary_support) if primary_support else None

            below_ema10_today = curr < float(last['EMA10'])
            below_ema10_prev  = float(prev['Close']) < float(prev['EMA10'])
            below_ema21       = curr < float(last['EMA21'])
            below_sma50       = curr < float(last['SMA50'])
            below_support     = primary_support is not None and curr < primary_support * 0.99

            res['stop_levels']['ema10_state'] = (
                "2_CLOSES_BELOW" if (below_ema10_today and below_ema10_prev) else
                "1_CLOSE_BELOW"  if below_ema10_today else
                "ABOVE"
            )
            res['stop_levels']['ema21_state'] = "BELOW" if below_ema21 else "ABOVE"
            res['stop_levels']['sma50_state'] = "BELOW" if below_sma50 else "ABOVE"

            def get_rec(entry_stop):
                if below_support or below_sma50:
                    return "EXIT", "Thesis broken — below support or SMA50"
                if below_ema21:
                    return "REDUCE_50_TO_100_PCT", "Trend weakening — EMA21 broken"
                if below_ema10_today and below_ema10_prev:
                    vol_high = float(last.get('vol_vs_avg') or 0) > 1.0
                    return ("REDUCE_50_PCT" if vol_high else "REDUCE_25_TO_33_PCT",
                            "2 consecutive closes below EMA10" + (" on high volume" if vol_high else ""))
                if below_ema10_today:
                    return "WATCH", "1 close below EMA10 — monitor closely"
                return "HOLD", "Trend is healthy"

            rec1, reason1 = get_rec(res['entry1'].get('stop'))
            rec2, reason2 = get_rec(res['entry2'].get('stop'))

            res['stop_levels']['recommendation_if_in_entry1'] = rec1
            res['stop_levels']['recommendation_if_in_entry2'] = rec2
            res['stop_levels']['recommendation_reason']       = reason1

        except Exception as e:
            res['stop_levels']['error'] = str(e)

        return res, score

    def _climax_check(self, stock_weekly: pd.DataFrame, already_broken_out: bool) -> dict:
        res = {
            "climax_warning": False,
            "gain_5w_pct": None,
            "vol_surge_ratio": None,
            "close_position_pct": None
        }
        if not already_broken_out:
            return res
        try:
            last5  = stock_weekly.tail(5)
            gain5w = safe_pct(float(last5['Close'].iloc[-1]), float(last5['Close'].iloc[0]))
            vsurge = safe_div(
                float(stock_weekly['Volume'].iloc[-1]),
                float(stock_weekly['Volume'].rolling(10).mean().iloc[-1])
            )
            close_pos = safe_div(
                float(last5['Close'].iloc[-1]) - float(last5['Low'].iloc[-1]),
                float(last5['High'].iloc[-1])  - float(last5['Low'].iloc[-1])
            )
            res['gain_5w_pct']        = gain5w
            res['vol_surge_ratio']    = safe_round(vsurge, 2)
            res['close_position_pct'] = safe_round(close_pos * 100, 1) if close_pos is not None else None
            res['climax_warning']     = (
                gain5w is not None and gain5w > 25
                and vsurge is not None and vsurge > 2.0
                and close_pos is not None and close_pos < 0.30
            )
        except Exception:
            pass
        return res

# ──────────────────────────────────────────────────────────────────────────────
# 4. Orchestrator Functions
# ──────────────────────────────────────────────────────────────────────────────

    def analyze(self, ticker: str) -> dict:
        """
        Run the full base breakout analysis for one ticker.
        Returns a JSON-serialisable dict. Never raises — catches all exceptions
        and returns an error dict instead.
        """
        ticker = ticker.upper()
        try:
            # 1. Load Data
            stock_weekly, spy_weekly, stock_daily, info, financials = self._load_data(ticker)

            # 2. Market Context
            mkt = self._market_context(spy_weekly)

            # 3. Fundamentals
            fund, fs = self._analyze_fundamentals(info, financials)
            score = fs

            # 4. Technical Sections
            curr_price = float(stock_daily['Close'].iloc[-1])
            
            # Temporary breakout check for RS calculation logic
            # Using tail(50) to find a recent base high as a pivot
            p_max = stock_weekly.tail(50)['High'].max()
            temp_pivot = float(p_max) if not isinstance(p_max, pd.Series) else float(p_max.iloc[0])
            already_bo = curr_price > temp_pivot

            rs, rss     = self._calculate_rs(stock_weekly, spy_weekly, temp_pivot, already_bo)
            score      += rss

            s2, s2s     = self._stage2_check(stock_weekly, stock_daily)
            score      += s2s

            base, bs, bh = self._base_analysis(stock_weekly)
            score       += bs

            pivot_price = base['base_high']
            
            # Recalculate breakout status with actual base high
            if pivot_price:
                already_bo = curr_price > pivot_price

            vol, vs      = self._volume_analysis(stock_weekly, bh)
            score       += vs

            tight, ts    = self._tight_area_detection(stock_daily)
            score       += ts

            prim, ps     = self._priming_patterns(stock_daily, pivot_price)
            score       += ps

            support      = self._find_support_levels(stock_daily, bh, curr_price)

            entry, es    = self._entry_and_stops(stock_daily, stock_weekly, pivot_price,
                                           support['primary_support'], prim)
            score       += es

            climax       = self._climax_check(stock_weekly, already_bo)

            if score >= 10 and mkt['spy_above_50w']:
                quality = "ACTIONABLE"
            elif score >= 7:
                quality = "DEVELOPING"
            elif score >= 5:
                quality = "NOT_READY"
            else:
                quality = "AVOID"

            result = {
                "ticker":          ticker,
                "analysis_date":   datetime.now().strftime('%Y-%m-%d'),
                "current_price":   safe_round(curr_price, 2),
                "score":           score,
                "max_score":       11,
                "quality":         quality,
                "market_context":  mkt,
                "fundamentals":    fund,
                "relative_strength": rs,
                "stage2":          s2,
                "base":            base,
                "volume":          vol,
                "tightness":       tight,
                "priming":         prim,
                "support":         support,
                "entry":           entry,
                "climax":          climax,
            }

            return _clean(result)

        except Exception as e:
            return _clean({
                "ticker":   ticker,
                "error":    str(e),
                "score":    0,
                "quality":  "ERROR"
            })

    def analyze_batch(self, tickers: list[str]) -> list[dict]:
        """Parallel analysis for a list of tickers using ThreadPoolExecutor."""
        results = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(self.analyze, t.upper()): t.upper() for t in tickers}
            for future in as_completed(futures):
                t = futures[future]
                results[t] = future.result()
        return [results[t.upper()] for t in tickers]

    def analyze_batch_to_csv(self, tickers: list[str]) -> str:
        """Runs batch analysis and saves results to a flattened CSV."""
        results = self.analyze_batch(tickers)
        
        # Flatten nested JSON results
        df = pd.json_normalize(results)
        
        # Ensure results directory exists
        base_dir = os.path.dirname(os.path.abspath(__file__))
        results_dir = os.path.join(base_dir, "results")
        if not os.path.exists(results_dir):
            os.makedirs(results_dir)
            
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"analysis_{timestamp}.csv"
        filepath = os.path.join(results_dir, filename)
        
        df.to_csv(filepath, index=False)
        return filepath

# ──────────────────────────────────────────────────────────────────────────────
# 5. Entry Point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    analyzer = AdvancedBaseBreakoutAnalyzer(max_workers=5)
    
    if len(sys.argv) > 1:
        tickers = sys.argv[1:]
        if len(tickers) == 1:
            res = analyzer.analyze(tickers[0])
            print(json.dumps(res, indent=2))
        else:
            print(f"Analyzing {len(tickers)} tickers...")
            csv_path = analyzer.analyze_batch_to_csv(tickers)
            print(f"Results saved to: {csv_path}")
            
            # Brief summary to console
            import pandas as pd
            df = pd.read_csv(csv_path)
            for _, r in df.iterrows():
                print(f"{r['ticker']}: Score {r['score']}, Quality {r['quality']}")
                if pd.notna(r.get('error')):
                    print(f"  Error: {r['error']}")
    else:
        # Default behavior: run on a few stocks
        test_tickers = ["NVDA", "AAPL", "MSFT"]
        print(f"Running default analysis for: {test_tickers}")
        csv_path = analyzer.analyze_batch_to_csv(test_tickers)
        print(f"Results saved to: {csv_path}")
