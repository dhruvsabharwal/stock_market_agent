# Base Breakout Strategy Analyser — Claude Code Prompt (v3)

## Overview

You are a quantitative trading strategy analyser. When given a ticker or list of tickers, produce a
structured JSON result for each stock evaluating the **Advanced Base Breakout Strategy**.

The output is a **pure JSON object** — no print statements, no DataFrames, no formatted report string.
The caller will handle display and MCP forwarding.

---

## Architecture Requirements

### 1. Single public entry point — parallelisable by design

Write one **standalone function** (not a class method) that takes a ticker string and returns a dict.
This makes it trivially parallelisable with `concurrent.futures.ThreadPoolExecutor`.

```python
def analyze_ticker(ticker: str) -> dict:
    """
    Run the full base breakout analysis for one ticker.
    Returns a JSON-serialisable dict. Never raises — catches all exceptions
    and returns an error dict instead.
    """
    try:
        ...
        return build_result(ticker, ...)
    except Exception as e:
        return {"ticker": ticker, "error": str(e), "score": 0, "quality": "ERROR"}


def analyze_tickers(tickers: list[str], max_workers: int = 5) -> list[dict]:
    """
    Run analyze_ticker for a list of tickers in parallel.
    Returns a list of result dicts in the same order as input.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(analyze_ticker, t.upper()): t.upper() for t in tickers}
        for future in as_completed(futures):
            t = futures[future]
            results[t] = future.result()
    return [results[t.upper()] for t in tickers]
```

### 2. Output is a JSON-serialisable dict — no DataFrames, no numpy types

Before returning, sanitise every value:

```python
import math

def _clean(val):
    """Recursively sanitise a value for JSON output."""
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None          # NaN and Inf become null in JSON — never "nan"
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return None if math.isnan(float(val)) else round(float(val), 4)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    if isinstance(val, dict):
        return {k: _clean(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_clean(v) for v in val]
    if hasattr(val, 'strftime'):   # datetime / Timestamp
        return val.strftime('%Y-%m-%d')
    return val


def build_result(ticker, **sections) -> dict:
    raw = {"ticker": ticker, **sections}
    return _clean(raw)
```

**Never store DataFrames in the result dict.** Extract only the scalar values and lists you need,
then discard the DataFrame.

### 3. NaN guard — mandatory before every division and every formatted value

```python
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
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else round(f, decimals)
    except Exception:
        return default
```

Use `safe_pct` and `safe_round` for every computed metric. Never use raw `/` on untrusted data.

---

## Data Loading

```python
def load_data(ticker: str) -> tuple:
    """
    Returns (stock_weekly, spy_weekly, stock_daily, info, financials).
    stock_weekly and spy_weekly are plain DataFrames with tz-naive DatetimeIndex.
    Raises on total failure so analyze_ticker can catch and return error dict.
    """
    import yfinance as yf

    symbols = list({ticker, "SPY"})
    raw = yf.download(symbols, period="3y", interval="1wk",
                      group_by="ticker", progress=False, auto_adjust=True)

    def extract(sym):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw[sym].dropna()
            else:
                df = raw.dropna() if sym == ticker else pd.DataFrame()
        except KeyError:
            df = pd.DataFrame()
        if not df.empty and hasattr(df.index, 'tz') and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df

    stock_weekly = extract(ticker)
    spy_weekly   = extract("SPY")

    # SPY fallback if batch download failed for it
    if spy_weekly.empty:
        spy_raw = yf.download("SPY", period="3y", interval="1wk",
                              progress=False, auto_adjust=True)
        spy_weekly = spy_raw.dropna()
        if not spy_weekly.empty and hasattr(spy_weekly.index, 'tz') and spy_weekly.index.tz is not None:
            spy_weekly.index = spy_weekly.index.tz_localize(None)

    t            = yf.Ticker(ticker)
    stock_daily  = t.history(period="1y", interval="1d", auto_adjust=True).dropna()
    if not stock_daily.empty and hasattr(stock_daily.index, 'tz') and stock_daily.index.tz is not None:
        stock_daily.index = stock_daily.index.tz_localize(None)

    info       = t.info or {}
    financials = t.quarterly_financials

    if stock_weekly.empty or stock_daily.empty:
        raise ValueError(f"No price data returned for {ticker}")

    # Add computed columns to daily — done once here, not repeated downstream
    stock_daily = stock_daily.copy()
    stock_daily['range_pct']  = (stock_daily['High'] - stock_daily['Low']) / stock_daily['Close'] * 100
    stock_daily['vol_vs_avg'] = stock_daily['Volume'] / stock_daily['Volume'].rolling(50).mean()
    stock_daily['EMA10']      = stock_daily['Close'].ewm(span=10, adjust=False).mean()
    stock_daily['EMA21']      = stock_daily['Close'].ewm(span=21, adjust=False).mean()
    stock_daily['SMA50']      = stock_daily['Close'].rolling(50).mean()

    return stock_weekly, spy_weekly, stock_daily, info, financials
```

---

## Section 1: Market Context (SPY health — prerequisite check)

Run this first. If the broad market is in a downtrend, flag it prominently.
This does not affect individual stock scores but gates the overall quality rating.

```python
def market_context(spy_weekly: pd.DataFrame) -> dict:
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
```

---

## Section 2: Fundamentals

```python
def analyze_fundamentals(info: dict, financials: pd.DataFrame) -> tuple[dict, int]:
    """Returns (result_dict, score_points)."""
    score = 0
    res = {
        "eps_growth_yoy":  None,
        "rev_growth_yoy":  None,
        "net_margin_pct":  None,
        "eps_5yr_cagr":    None,
        "margin_pass":     False,
        "eps_pass":        False,
        "rev_pass":        False,
        "data_quality":    "OK"   # set to "DISTORTED" or "MISSING" if needed
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
            industry = info.get('industry', '').lower()
            is_tech = any(w in industry for w in ('software', 'technology', 'semiconductor'))
            is_low_margin = any(w in industry for w in ('retail', 'grocery', 'food', 'wholesale'))
            if is_low_margin:
                # Substitute: gross margin ≥ 20% or operating cash flow margin ≥ 5%
                gm = safe_round((info.get('grossMargins') or 0) * 100, 1)
                res['gross_margin_pct'] = gm
                res['margin_pass'] = (gm is not None and gm >= 20)
                if res['margin_pass']:
                    res['margin_note'] = "Low-margin business: gross margin used instead of net margin"
            else:
                threshold = 15 if is_tech else 8
                res['margin_pass'] = (margin is not None and margin >= threshold)

        # 5yr EPS CAGR — use as fallback signal if YoY is distorted
        eg = info.get('earningsGrowth')
        if eg is not None:
            cagr = safe_round(eg * 100, 1)
            res['eps_5yr_cagr'] = cagr
            # If YoY is negative but 5yr CAGR is strong, flag as potentially distorted
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
```

---

## Section 3: True Relative Strength

```python
def calculate_rs(stock_weekly: pd.DataFrame, spy_weekly: pd.DataFrame,
                 pivot_price: float, already_broken_out: bool) -> tuple[dict, int]:
    score = 0
    res = {
        "rs_4w": None, "rs_13w": None, "rs_26w": None, "rs_52w": None,
        "rs_line_current": None, "rs_52w_high": None,
        "rs_at_52w_high": False, "leading_rs": False
    }

    try:
        common = stock_weekly['Close'].index.intersection(spy_weekly['Close'].index)
        if len(common) < 13:
            return res, score

        sc = stock_weekly['Close'].loc[common]
        sp = spy_weekly['Close'].loc[common]
        rs = sc / sp

        res['rs_line_current'] = safe_round(float(rs.iloc[-1]), 4)

        for weeks, key in [(4, 'rs_4w'), (13, 'rs_13w'), (26, 'rs_26w'), (52, 'rs_52w')]:
            if len(rs) >= weeks:
                res[key] = safe_pct(rs.iloc[-1], rs.iloc[-weeks])

        if len(rs) >= 52:
            rs_high = float(rs.iloc[-52:].max())
            res['rs_52w_high'] = safe_round(rs_high, 4)
            res['rs_at_52w_high'] = float(rs.iloc[-1]) >= rs_high * 0.98

        # Leading RS: RS at/near 52w high while stock has NOT broken out yet
        res['leading_rs'] = (res['rs_at_52w_high'] and not already_broken_out)

        if res['rs_13w'] is not None and res['rs_13w'] > 0:
            score += 1

    except Exception:
        pass

    return res, score
```

---

## Section 4: Stage 2

```python
def stage2_check(stock_weekly: pd.DataFrame, stock_daily: pd.DataFrame) -> tuple[dict, int]:
    score = 0
    res = {
        "confirmed": False, "sma30w": None, "current_price": None,
        "sma30w_rising": False, "pct_from_52w_high": None,
        "pct_above_52w_low": None, "prior_uptrend": False
    }

    try:
        w = stock_weekly.copy()
        w['SMA30w'] = w['Close'].rolling(30).mean()

        curr  = float(w['Close'].iloc[-1])
        sma30 = float(w['SMA30w'].iloc[-1])
        sma30_4w = float(w['SMA30w'].iloc[-5]) if len(w) >= 5 else None

        res['current_price'] = safe_round(curr, 2)
        res['sma30w']        = safe_round(sma30, 2)
        res['sma30w_rising'] = sma30_4w is not None and sma30 > sma30_4w

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
```

---

## Section 5: Base Analysis

```python
def base_analysis(stock_weekly: pd.DataFrame) -> tuple[dict, int, object]:
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
        base_high     = float(wc.iloc[-lookback:].max())
        base_high_idx = wc.iloc[-lookback:].idxmax()

        post = wc.loc[base_high_idx:]
        if post.empty:
            return res, score, None

        base_low  = float(post.min())
        depth_pct = safe_pct(base_low, base_high)
        length    = len(post)

        res['base_high']      = safe_round(base_high, 2)
        res['base_high_date'] = base_high_idx
        res['base_low']       = safe_round(base_low, 2)
        res['base_low_date']  = post.idxmin()
        res['depth_pct']      = depth_pct
        res['length_weeks']   = length
        res['pass_depth']     = depth_pct is not None and depth_pct >= -50

        # Pattern classification
        if depth_pct is not None:
            if -33 <= depth_pct <= -15 and length >= 7:
                res['pattern'] = "Cup with Handle"
            elif -15 < depth_pct <= -5 and length >= 5:
                res['pattern'] = "Flat Base"
            elif -35 <= depth_pct <= -10 and length >= 5:
                res['pattern'] = "VCP"

        # VCP swing contractions
        n = len(post)
        if n >= 6:
            segs = [post.iloc[:n//3], post.iloc[n//3:2*n//3], post.iloc[2*n//3:]]
            swings = []
            for seg in segs:
                hi, lo = float(seg.max()), float(seg.min())
                swings.append(safe_round(safe_pct(lo, hi) * -1, 1))  # positive %
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
```

---

## Section 6: Volume Analysis

```python
def volume_analysis(stock_weekly: pd.DataFrame, base_high_idx) -> tuple[dict, int]:
    score = 0
    res = {
        "avg_vol_up_weeks": None, "avg_vol_down_weeks": None,
        "accumulation_ratio": None, "accumulation_pass": False,
        "vol_dry_pct": None, "vol_dry_pass": False
    }

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
```

---

## Section 7: Tightness

```python
def tight_area_detection(stock_daily: pd.DataFrame) -> tuple[dict, int]:
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
        # Contracting = last half is quieter than first half (negative value)
        res['volatility_contracting']       = contraction is not None and contraction <= -20
        res['tight_area_pass']              = tight_count > 0

        if res['tight_area_pass']:
            score += 1

    except Exception:
        pass

    return res, score
```

---

## Section 8: Priming Patterns

```python
def priming_patterns(stock_daily: pd.DataFrame, pivot_price: float) -> tuple[dict, int]:
    score = 0
    res = {"inside_bars": [], "upside_reversals": [], "tight_setup_days": [], "best_signal": None}

    try:
        last10 = stock_daily.tail(11)  # need i-1 for inside bar check

        for i in range(1, len(last10)):
            today = last10.iloc[i]
            yest  = last10.iloc[i-1]
            date  = last10.index[i].strftime('%Y-%m-%d')
            vva   = safe_round(today.get('vol_vs_avg'), 2)

            # Inside Bar
            if (today['High'] < yest['High'] and today['Low'] > yest['Low']
                    and vva is not None and vva < 0.75):
                res['inside_bars'].append({"date": date, "vol_ratio": vva})

            # Upside Reversal
            day_range = float(today['High']) - float(today['Low'])
            if day_range > 0:
                drop_pct  = safe_pct(today['Low'], today['Open'])
                close_pos = safe_div(float(today['Close']) - float(today['Low']), day_range)
                if (drop_pct is not None and drop_pct <= -1.0
                        and close_pos is not None and close_pos >= 0.70):
                    res['upside_reversals'].append({"date": date, "close_position_pct": safe_round(close_pos * 100, 1)})

            # Tight Setup Day
            if pivot_price:
                dist = safe_pct(today['Close'], pivot_price)
                rp   = safe_round(today.get('range_pct'), 2)
                if (dist is not None and abs(dist) <= 2.0
                        and rp is not None and rp <= 1.0
                        and vva is not None and vva <= 0.4):
                    res['tight_setup_days'].append({
                        "date": date, "dist_from_pivot_pct": safe_round(dist, 1), "vol_ratio": vva
                    })

        # Best signal priority: tight_setup > inside_bar > reversal
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
```

---

## Section 9: Support Levels

```python
def find_support_levels(stock_daily: pd.DataFrame, base_high_idx, current_price: float) -> dict:
    res = {"all_levels": [], "primary_support": None}
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
        res['all_levels'] = levels
        below = [s for s in levels if s < current_price]
        res['primary_support'] = max(below) if below else None

    except Exception:
        pass
    return res
```

---

## Section 10: Two-Entry Strategy + Stop Loss

The entry and stop loss sections are **always computed** for all potential entries.
The code has no way to know which entry was actually taken by the trader — it reports
the setup for both entries so the trader can match against their own position.

```python
def entry_and_stops(stock_daily: pd.DataFrame, stock_weekly: pd.DataFrame,
                    pivot_price: float, primary_support: float,
                    priming: dict) -> tuple[dict, int]:
    score = 0
    res = {
        "pivot_price": safe_round(pivot_price, 2),
        "already_broken_out": False,

        # Entry 1 — Early (priming pattern)
        "entry1": {
            "trigger":       None,  # buy above this level
            "stop":          None,  # primary support
            "risk_pct":      None,
            "signal_type":   None,
            "signal_date":   None,
            "status":        "NOT_AVAILABLE",  # NOT_AVAILABLE / WATCHING / TRIGGERED
            "sizing_per_500_risk": {"shares": None, "position_value": None}
        },

        # Entry 2 — Standard breakout
        "entry2": {
            "trigger":        None,  # pivot * 1.005
            "stop":           None,  # pivot * 0.97
            "risk_pct":       None,
            "vol_required":   "≥1.5x 50-day average",
            "status":         "WATCHING",  # WATCHING / TRIGGERED / FAILED_LOW_VOL
            "sizing_per_500_risk": {"shares": None, "position_value": None}
        },

        # Stop loss status — computed for BOTH entries independently
        # Trader matches their actual entry against whichever applies
        "stop_levels": {
            "ema10":          None,
            "ema21":          None,
            "sma50":          None,
            "primary_support": safe_round(primary_support, 2) if primary_support else None,

            "ema10_dist_pct": None,
            "ema21_dist_pct": None,
            "sma50_dist_pct": None,
            "support_dist_pct": None,

            "ema10_state":    None,  # ABOVE / 1_CLOSE_BELOW / 2_CLOSES_BELOW
            "ema21_state":    None,  # ABOVE / BELOW
            "sma50_state":    None,  # ABOVE / BELOW

            "recommendation_if_in_entry1": None,
            "recommendation_if_in_entry2": None,
            "recommendation_reason":       None,

            # Hard stop prices — apply to whichever entry was taken
            "hard_stop_from_e1": None,  # entry1.trigger * 0.925
            "hard_stop_from_e2": None,  # entry2.trigger * 0.925
        }
    }

    try:
        curr = float(stock_daily['Close'].iloc[-1])
        res['already_broken_out'] = curr > pivot_price

        # ── Entry 2 setup ───────────────────────────────────────────────
        e2_trigger = safe_round(pivot_price * 1.005, 2)
        e2_stop    = safe_round(pivot_price * 0.970, 2)
        e2_risk    = safe_pct(e2_stop, e2_trigger) * -1 if e2_trigger else None  # positive %

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
            # Check if breakout had volume confirmation
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

        # ── Entry 1 setup ───────────────────────────────────────────────
        # Trigger = high of best priming signal. Stop = primary support.
        signal_map = {
            "TIGHT_SETUP_DAY": priming.get('tight_setup_days', []),
            "INSIDE_BAR":      priming.get('inside_bars', []),
            "UPSIDE_REVERSAL": priming.get('upside_reversals', []),
        }
        best = priming.get('best_signal')
        if best and signal_map.get(best):
            latest_signal = signal_map[best][-1]
            signal_date   = latest_signal['date']

            # Get the High of that signal day from stock_daily
            try:
                e1_trigger = float(stock_daily.loc[signal_date, 'High'])
            except KeyError:
                # date string lookup — try matching
                matches = stock_daily[stock_daily.index.strftime('%Y-%m-%d') == signal_date]
                e1_trigger = float(matches['High'].iloc[0]) if not matches.empty else None

            e1_stop = primary_support  # structural stop, not the single candle low

            if e1_trigger and e1_stop and e1_trigger > e1_stop:
                e1_risk = safe_round((e1_trigger - e1_stop) / e1_trigger * 100, 1)
                res['entry1']['trigger']     = safe_round(e1_trigger, 2)
                res['entry1']['stop']        = safe_round(e1_stop, 2)
                res['entry1']['risk_pct']    = e1_risk
                res['entry1']['signal_type'] = best
                res['entry1']['signal_date'] = signal_date

                # Status
                if curr > e1_trigger:
                    res['entry1']['status'] = "TRIGGERED"
                else:
                    res['entry1']['status'] = "WATCHING"

                # Sizing
                shares_e1 = safe_round(500 / (e1_trigger - e1_stop), 1) if (e1_trigger - e1_stop) > 0 else None
                res['entry1']['sizing_per_500_risk'] = {
                    "shares":         shares_e1,
                    "position_value": safe_round(shares_e1 * e1_trigger, 0) if shares_e1 else None
                }

                # Hard stops
                res['stop_levels']['hard_stop_from_e1'] = safe_round(e1_trigger * 0.925, 2)
                if e1_risk and e1_risk <= 4.0:
                    score += 1  # clean stop structure

        res['stop_levels']['hard_stop_from_e2'] = safe_round(e2_trigger * 0.925, 2) if e2_trigger else None

        # ── Stop level state ────────────────────────────────────────────
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
        above_ema10       = not below_ema10_today

        res['stop_levels']['ema10_state'] = (
            "2_CLOSES_BELOW" if (below_ema10_today and below_ema10_prev) else
            "1_CLOSE_BELOW"  if below_ema10_today else
            "ABOVE"
        )
        res['stop_levels']['ema21_state'] = "BELOW" if below_ema21 else "ABOVE"
        res['stop_levels']['sma50_state'] = "BELOW" if below_sma50 else "ABOVE"

        # Recommendation logic — stated for both entries separately
        def get_rec(entry_stop):
            """entry_stop: the stop price for whichever entry was taken."""
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

        e1_stop_price = res['entry1'].get('stop')
        e2_stop_price = res['entry2'].get('stop')

        rec1, reason1 = get_rec(e1_stop_price)
        rec2, reason2 = get_rec(e2_stop_price)

        res['stop_levels']['recommendation_if_in_entry1'] = rec1
        res['stop_levels']['recommendation_if_in_entry2'] = rec2
        res['stop_levels']['recommendation_reason']       = reason1  # usually the same

    except Exception as e:
        res['stop_levels']['error'] = str(e)

    return res, score
```

---

## Section 11: Climax Top Check

```python
def climax_check(stock_weekly: pd.DataFrame, already_broken_out: bool) -> dict:
    res = {"climax_warning": False, "gain_5w_pct": None, "vol_surge_ratio": None}
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
        res['gain_5w_pct']      = gain5w
        res['vol_surge_ratio']  = safe_round(vsurge, 2)
        res['climax_warning']   = (
            gain5w is not None and gain5w > 25
            and vsurge is not None and vsurge > 2.0
            and close_pos is not None and close_pos < 0.30
        )
    except Exception:
        pass
    return res
```

---

## Main Orchestrator

```python
def analyze_ticker(ticker: str) -> dict:
    """
    Full base breakout analysis for one ticker.
    Returns a JSON-serialisable dict. Never raises.
    """
    ticker = ticker.upper()
    try:
        stock_weekly, spy_weekly, stock_daily, info, financials = load_data(ticker)

        curr_price = float(stock_daily['Close'].iloc[-1])
        pivot_price = float(stock_weekly.tail(10)['High'].max())

        # Run all sections
        mkt          = market_context(spy_weekly)
        fund, fs     = analyze_fundamentals(info, financials)
        score        = fs

        # Need pivot before RS (leading RS check requires broken_out)
        already_bo   = curr_price > pivot_price

        rs, rss      = calculate_rs(stock_weekly, spy_weekly, pivot_price, already_bo)
        score       += rss

        s2, s2s      = stage2_check(stock_weekly, stock_daily)
        score       += s2s

        base, bs, base_idx = base_analysis(stock_weekly)
        score       += bs

        vol, vs      = volume_analysis(stock_weekly, base_idx) if base_idx is not None else ({}, 0)
        score       += vs

        tight, ts    = tight_area_detection(stock_daily)
        score       += ts

        prim, ps     = priming_patterns(stock_daily, pivot_price)
        score       += ps

        support      = find_support_levels(stock_daily, base_idx, curr_price) if base_idx is not None else {"all_levels": [], "primary_support": None}

        entry, es    = entry_and_stops(stock_daily, stock_weekly, pivot_price,
                                       support['primary_support'], prim)
        score       += es

        climax       = climax_check(stock_weekly, already_bo)

        # Quality rating — cap at DEVELOPING if market is in downtrend
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


def analyze_tickers(tickers: list, max_workers: int = 5) -> list:
    """Parallel analysis for a list of tickers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(analyze_ticker, t.upper()): t.upper() for t in tickers}
        for future in as_completed(futures):
            t = futures[future]
            results[t] = future.result()
    return [results[t.upper()] for t in tickers]
```

---

## Usage

```python
import json
from datetime import datetime

# Single ticker
result = analyze_ticker("WMT")
print(json.dumps(result, indent=2))

# Multiple tickers in parallel
results = analyze_tickers(["WMT", "NVDA", "AAPL", "MSFT"], max_workers=4)
for r in results:
    print(r['ticker'], r['score'], r['quality'])
```

---

## Key Design Rules (do not change these)

1. **Never store DataFrames in result dicts.** Extract scalars and lists only.
2. **Never let NaN reach the output.** Use `_clean()`, `safe_round()`, `safe_pct()`, `safe_div()` everywhere.
3. **Never print anything.** The function returns data; the caller handles display.
4. **Stop loss is always computed** for both entry points, unconditionally. The trader decides which entry applies to their position.
5. **Hard stop is always relative to the actual entry trigger price** (e1_trigger or e2_trigger) — never relative to the pivot alone.
6. **Early entry stop = primary support level**, not the single candle low.
7. **Early entry risk % = (trigger - stop) / trigger × 100** — not trigger/stop - 1.
8. **Macro volatility contraction is a decrease** — negative value = contracting. Positive = expanding (bad).
9. **Market context gates quality rating** — a score of 10+ in a bear market is capped at DEVELOPING.
10. **`analyze_tickers()` is parallelised with ThreadPoolExecutor** — each ticker is independent, no shared state.
