# Master Base Breakout Strategy Document

> **Purpose:** Single source of truth for implementing the base breakout strategy.
> Synthesized from: TraderLion Ultimate Trading Guide, TraderLion Twitter thread (base_breakout_strategy_notes.md), Richard Moglen Advanced Breakout Webinar, and gap analysis of current `advanced_base_breakout.py`.

---

## Table of Contents

1. [What Is a Base Breakout](#1-what-is-a-base-breakout)
2. [Pre-Requisites: TIGERS Framework](#2-pre-requisites-tigers-framework)
3. [Base Identification (CRITICAL - Needs Redesign)](#3-base-identification)
4. [Base Quality Criteria](#4-base-quality-criteria)
5. [Accumulation & Volume Signatures](#5-accumulation--volume-signatures)
6. [Tight Areas & Ranges](#6-tight-areas--ranges)
7. [Pivot Points](#7-pivot-points)
8. [Priming Patterns](#8-priming-patterns)
9. [Entry Tactics](#9-entry-tactics)
10. [Position Management & Exits](#10-position-management--exits)
11. [Market Context](#11-market-context)
12. [Failed Breakouts & Resets](#12-failed-breakouts--resets)
13. [Implementation Gaps & Action Items](#13-implementation-gaps--action-items)

---

## 1. What Is a Base Breakout

A **continuation setup** that follows a prior uptrend and forms a multi-week consolidation (typically >= 5 weeks). Distinguished from trend-starting setups (launchpad, bottoming base).

**Key concept (Darvis):** Stocks move in defined boxes/ranges. Breakout trading = trading the move from box to box.

**Three base types:**
- **IPO Base:** Shortly after IPO/direct listing. Absorbs initial supply. One of the best bases to focus on.
- **Bottoming Base:** Transition from longer-term downtrend to sideways to up. Less focus unless accompanied by earnings gap.
- **Continuation Base (PRIMARY FOCUS):** Stock already in uptrend, intermediate pause pulling back into moving averages, working off supply. This is the core setup.

**Source:** Moglen webinar, O'Neil model books, Weinstein Stage 2, Darvis Box Theory.

---

## 2. Pre-Requisites: TIGERS Framework

Before analyzing bases, the stock must pass the TIGERS screen:

| Letter | Criterion | Implementation Status |
|--------|-----------|----------------------|
| T | **Theme** - Part of a leading market theme | `tigers_framework.py` - `check_trend()` (partial - checks price trend, not thematic leadership) |
| I | **Innovation** - Industry group strength | `check_industry()` (stub - needs sector RS data) |
| G | **Growth** - Revenue/earnings growth | `check_growth()` (implemented) |
| E | **Edges/Earnings** - Earnings catalyst | `check_earnings_catalyst()` (implemented) |
| R | **Relative Strength** - RS vs market | `check_rs()` (implemented) |
| S | **Stage** - Weinstein Stage 2 | `check_stage()` + `stage_analysis.py` (implemented) |

**Gap:** Theme and Industry checks are weak. Revenue growth should be weighted equally or higher than earnings growth (Moglen: "revenue growth is preferred over just earnings growth" in current market).

---

## 3. Base Identification

### THIS IS THE #1 PROBLEM IN THE CURRENT IMPLEMENTATION

#### Current Implementation (Wrong)
- Uses **weekly** data exclusively for base identification
- Finds only the **most recent** base (last 54 weekly bars)
- Looks at the highest weekly High in the lookback and calls everything after it "the base"
- Cannot identify multiple bases or count base stages
- No concept of "prior uptrend" feeding into the base

#### What It Should Do

**Timeframe:** Use **daily** charts for base identification. Weekly charts are for stage analysis and accumulation counting. The base pivot (buy point) is a daily-chart concept.

**Moglen's definition of when a base begins:**
> "The base begins when there's a high that isn't really overcome for at least 10-15 days. That's when you can start drawing in the base pivot."

**Algorithm for finding ALL bases for a ticker:**

```
Input: Daily OHLCV data (at least 2 years)
Output: List of base objects, each with: start_date, end_date, base_high, base_low, depth_pct, length_days, stage_number, pattern_type

Step 1: Find all significant highs
  - A "significant high" = a daily High that is not exceeded for at least 10 trading days after
  - Use rolling max with confirmation window

Step 2: For each significant high, find the base
  - Base starts at the significant high
  - Base ends when price closes above the base_high (breakout) OR
    when a new significant high forms that redefines the base
  - Base low = lowest Low between base_high_date and base_end_date
  - Base depth = (base_low - base_high) / base_high * 100

Step 3: Validate base parameters
  - Minimum length: 5 weeks (25 trading days) for continuation bases
  - Maximum depth: guided by market drawdown (see Section 4.1)
  - Must have prior uptrend of >= 20% before the base_high

Step 4: Count base stages
  - Stage 1 base: first base after a major bottom or new uptrend
  - Stage 2 base: second consolidation after the first breakout
  - Stage N: etc.
  - A base "resets" the count when price undercuts the low of a prior base
  - Focus on stages 1-3; stages 4+ have higher failure rates

Step 5: Classify pattern
  - Cup with Handle: U-shaped, depth 15-33%, handle forms in upper half
  - Flat Base: depth 5-15%, relatively horizontal
  - VCP (Volatility Contraction Pattern): successive contractions in both price range and volume
  - Double Bottom: W-shape with second low at or slightly below first
  - High Tight Flag: prior run of 100%+ in <8 weeks, flag depth <25%, length 3-5 weeks
```

**Key insight from Moglen on drawing the pivot:**
> "It doesn't have to be at the exact highest or lowest point. It's the range that covers 85-95% of the price action. There's always going to be a little bit of randomness/volatility even within a range."

---

## 4. Base Quality Criteria

### 4.1 Base Depth Relative to Market Drawdown

**Rule (TraderLion notes):** Base depth should be less than 2.5x the general market (QQQ) drawdown during the same period.

**Example:** QQQ drew down 30.55% (Feb-Mar 2020). Leaders should have base depths < 76.4% (2.5 x 30.55%). Best leaders were well under:
- ZM: 22.30%, AMZN: 25.61%, DOCU: 29.9% (< 1x market)
- PTON: 48.84%, ETSY: 52.64% (< 2x market)

**Implementation:**
```python
def check_base_depth_vs_market(base_depth_pct, market_drawdown_pct):
    """
    base_depth_pct: negative number (e.g., -25.0)
    market_drawdown_pct: negative number (e.g., -30.55)
    Returns: (passes_2_5x, passes_2x, ratio)
    """
    ratio = abs(base_depth_pct) / abs(market_drawdown_pct)
    return ratio <= 2.5, ratio <= 2.0, ratio
```

**Current gap:** Not implemented at all. `_base_analysis` uses a hardcoded `-50` depth threshold.

### 4.2 Prior Uptrend of At Least 20%

**Rule (TraderLion notes):** The best bases have a prior uptrend of AT LEAST 20%. Bigger = better. Stocks should be above the 200 SMA.

**Moglen:** "Big moves come from big bases." Tesla's 1000% move came from a multi-year consolidation. PLTR: long-term stage 1 then multiple base opportunities.

**Implementation:**
```python
def check_prior_uptrend(daily_data, base_start_date, min_uptrend_pct=20):
    """
    Look backwards from base_start_date to find the prior low.
    The prior low is the most recent significant trough before the uptrend into the base.
    Calculate: (base_high - prior_low) / prior_low * 100
    Must be >= min_uptrend_pct
    """
```

**Current gap:** `_stage2_check` has `prior_uptrend` as `pct_above_52w_low >= 30` which is a crude proxy, not the actual prior uptrend measurement.

### 4.3 Early Stage Bases (Base Counting)

**Rule (TraderLion notes):** Focus on Stage 1 through Stage 3 bases. Later stages have higher failure rates.

**Psychology:**
- Not as obvious to the public in early stages
- Institutions accumulate more shares early in a run
- Price leads fundamentals

**Exception:** Some winners ($SHOP 2015-2021, $CSCO 1990-1994) ran 1000%+ with 5+ base stages. Study each base individually.

**Implementation:**
```python
def count_base_stage(bases_list, current_base_index):
    """
    Given a chronological list of all bases and the current base index,
    determine what stage this base is.
    
    Rules:
    - First base after a significant bottom = Stage 1
    - Each subsequent breakout + consolidation = next stage
    - If price undercuts the low of a prior base, reset the count
    - If there's a new multi-year high followed by a base, it can reset
    """
```

**Current gap:** No base counting whatsoever. Only finds one base.

---

## 5. Accumulation & Volume Signatures

### 5.1 Weekly Accumulation Count (O'Neil Technique)

**Rule (TraderLion notes):**
1. Go to the weekly chart
2. Count weeks with accumulation: positive price action + strong WCR (>= 40%) + volume above average
3. Count weeks with distribution: negative price action + poor WCR (< 40%) + volume above average
4. Accumulation weeks must outnumber distribution weeks

**Implementation:**
```python
def count_weekly_accumulation(weekly_data, base_start_date, base_end_date):
    """
    For each week within the base period:
    - Accumulation week: Close > Open AND WCR >= 40% AND Volume > 10w avg volume
    - Distribution week: Close < Open AND WCR < 40% AND Volume > 10w avg volume
    - Neutral: Volume below average (doesn't count either way)
    Returns: (accum_weeks, distrib_weeks, ratio, passes)
    """
    # WCR = (Close - Low) / (High - Low) * 100
```

**Current gap:** `_tight_area_detection` has `weekly_closing_range_pct` and `weekly_closing_range_pass` but only for the LAST 10 weeks. Does NOT count accumulation vs distribution weeks within the base. Does NOT compare the counts.

### 5.2 Three Weeks Tight (3WT) Pattern

**Rule (TraderLion notes):** 3 consecutive weekly closes within 1.5% of each other. Sign of subtle institutional accumulation / tight price control.

**Implementation:**
```python
def find_3wt_patterns(weekly_data):
    """
    Slide a 3-week window across weekly closes.
    For each window: max_close - min_close <= 1.5% of max_close
    Return list of (start_date, end_date, closes)
    """
```

**Current gap:** Not implemented. No 3WT detection exists.

### 5.3 Volume Accumulation Signatures

**Rule (TraderLion notes):**

| Signature | Definition |
|-----------|------------|
| **HVE** (Highest Volume Ever w/ Earnings Gap Up) | Volume is highest ever recorded + stock gaps up on earnings |
| **HV1** (Highest Volume in Over a Year w/ Earnings Gap Up) | Volume is highest in 52 weeks + earnings gap up |
| **HVIPO** (Highest Volume Since IPO Day/Week) | Volume highest since first trading day/week |

These tell us a fundamental shift has occurred, requiring institutions to accumulate in an obvious way.

**Current gap:** `_volume_analysis` tracks volume surges but does NOT specifically identify HVE/HV1/HVIPO patterns. Does not correlate with earnings dates or IPO dates.

### 5.4 Daily Accumulation Signatures

**Rule (TraderLion notes):** Especially up the right side of the base, strong up days with above-average volume should outnumber poor down days with above-average volume.

**Implementation:**
```python
def count_daily_accumulation(daily_data, right_side_start_date, base_end_date):
    """
    For each day in the right side of the base:
    - Accumulation day: Close > Open AND Volume > 50-day avg volume
    - Distribution day: Close < Open AND Volume > 50-day avg volume
    Returns: (accum_days, distrib_days, ratio, passes)
    """
```

**Current gap:** Not implemented as a distinct count. `_volume_analysis` has volume surge detection but doesn't count up/down days separately.

### 5.5 Pocket Pivot (10-Day)

**Rule:** An up-day where volume exceeds the maximum down-day volume of the prior 10 trading days.

**Current status:** Referenced in `_volume_analysis` as `pocket_pivot` - needs verification of correctness.

---

## 6. Tight Areas & Ranges

### 6.1 Definition of a Tight Area

**Moglen:**
- A tight area is a short-term consolidation (1-7 days) where highs and closes line up around the same level
- Price range ideally within 5% (Minervini says 10%, Moglen prefers tighter)
- Should be accompanied by declining volume (subtle accumulation)
- Over the course of the base, overall tightening/coiling of price action

**TraderLion notes:** Week-over-week ranges less than 10% = tight. The tighter, the stronger the institutional hold.

### 6.2 Where Tight Areas Should Form

**TraderLion notes:**
- Before breakouts (under previous resistance points)
- During handles (the higher in the base, the better)
- At the lows (shows distribution wearing out)

**Moglen:** The best range breakouts are against the **21 EMA**. This gives confidence and easy trade management.

### 6.3 Implementation

```python
def detect_tight_areas(daily_data, min_days=2, max_days=7, max_range_pct=5.0):
    """
    Slide a window across daily data.
    For each window of length min_days to max_days:
    - Calculate range: (max_high - min_low) / max_high * 100
    - If range <= max_range_pct AND volume is declining: mark as tight area
    - Record: start_date, end_date, high_of_range, low_of_range, range_pct, avg_volume_vs_normal
    
    Also check if tight area is near a key moving average (10, 21, 50 DMA).
    """
```

### 6.4 Relative Measured Volatility (RMV)

**Moglen:** An indicator that measures contraction. When RMV approaches zero, the stock is tightening. Can be used to screen for tight setups.

**Implementation idea:**
```python
def relative_measured_volatility(daily_data, period=10):
    """
    RMV = current ATR / rolling median ATR (or similar normalization)
    When RMV is near zero relative to its own history, price is contracting.
    """
```

**Current gap:** Not implemented. Could be a useful screening tool.

---

## 7. Pivot Points

### 7.1 Types of Pivots (Moglen)

| Pivot Type | Definition | Timeframe |
|------------|-----------|-----------|
| **Base Pivot** | The high of the entire base. Traditional O'Neil buy point. | Weeks-months |
| **Consolidation Pivot** | High of a shorter consolidation within a base (1-2 weeks) | 1-2 weeks |
| **Range Pivot** | High of a very short tight area (1-7 days) | Days |

**Best scenario:** A range breakout triggers a consolidation pivot triggers the base pivot. Expect a fast move.

### 7.2 Drawing Pivots

**Moglen's process:**
1. Identify where the base begins (high not overcome for 10-15 days)
2. Draw a horizontal line at the 85-95% coverage level (not necessarily the absolute high)
3. For ranges within the base: identify where highs and closes cluster
4. Prefer horizontal pivots over sloping ones (clearer, more actionable)

### 7.3 Implementation

```python
def identify_pivots(daily_data, bases_list):
    """
    For each base:
    1. Base pivot = high of the base (or 95th percentile of highs)
    2. Find consolidation pivots: clusters of highs within the base that form
       resistance levels lasting 5-10 days
    3. Find range pivots: clusters of highs lasting 2-5 days, especially
       up the right side of the base near the 21 EMA
    
    Return pivot objects with: level, type, date_range, primed (bool)
    """
```

**Current gap:** `_base_analysis` identifies base_high as the pivot but has no concept of consolidation pivots or range pivots. `_find_support_levels` finds support but not the hierarchical pivot structure.

---

## 8. Priming Patterns

### 8.1 The Four Priming Patterns (Moglen)

These occur at the right edge of a range, near a pivot. They signal the range is ready to break out.

#### 8.1.1 Inside Day
- All of today's range (high to low) is contained within yesterday's range
- Represents: **Contraction** / equilibrium in supply and demand
- Buy point: through the high of the inside day
- **Current status:** Implemented in `_priming_patterns` as `inside_day`

#### 8.1.2 Upside Reversal
- Today's low undercuts yesterday's low, then rallies up from it
- Represents: **Short-term momentum change** from downside to upside
- Best when occurring against 10 DMA or 21 EMA
- **Current status:** Implemented in `_priming_patterns` as `upside_reversal`

#### 8.1.3 Positive Expectation Breaker
- Prior day closed negative (bearish candle). Market expects gap down / continuation lower.
- Instead: stock gaps UP and holds, even closes well
- Represents: **Catching the crowd off guard** - everyone expecting down, gets up
- The high of the gap-up day becomes a short-term pivot
- **Current status:** NOT implemented

#### 8.1.4 Tight Setup Day
- A day near the pivot that has a very narrow range relative to recent price action
- Open and close are very close together (small real body)
- May look like a "failed breakout" but it's actually setting up
- Range from high to low is maybe 1-3% vs normal 4-5%
- Represents: **Contraction** right at the key level
- **Current status:** NOT implemented

### 8.2 Implementation

```python
def detect_priming_patterns(daily_data, pivot_level, lookback=5):
    """
    Check the last `lookback` days for priming patterns near `pivot_level`.
    
    Returns dict with:
    - inside_day: bool + date
    - upside_reversal: bool + date
    - positive_expectation_breaker: bool + date
    - tight_setup_day: bool + date
    - primed: bool (any of the above is True)
    
    For tight_setup_day:
      - today's range (H-L)/H < median range of last 20 days * 0.5
      - abs(Open - Close) / Close < 0.01 (1%)
    
    For positive_expectation_breaker:
      - yesterday: Close < Open (bearish)
      - today: Open > yesterday's Close (gap up) AND Close > Open (bullish)
    """
```

---

## 9. Entry Tactics

### 9.1 Four Ways to Trade Ranges (Moglen)

| Method | Description | Risk Profile |
|--------|-------------|-------------|
| **Accumulate vs Lows** | Buy small pieces each time price bounces off range low. Build 10-50% position. | Tight stop below range low |
| **Undercut & Rally** | Price briefly undercuts range low then reclaims. Buy on the reclaim. | Stop below the undercut low |
| **Anticipation** | Enter as price pushes toward range high, before actual breakout. Stop at higher low. | Tightest stop |
| **Standard Range Breakout** | Wait for close above range high. Stop at low of breakout day. | Most confirmed |

**Key insight:** These can be COMBINED to build a position. E.g., 50% on undercut & rally + 50% on standard breakout.

### 9.2 Early Entry Advantages (Moglen)

1. **Cushion** going into the base breakout - absorbs volatility at the pivot
2. **Better cost basis** - lower average price
3. **Tighter stop-loss** - allows larger position sizing
4. Full position set **before** the standard breakout

**Moglen on stops:** "O'Neil would use 7-8% stop-loss. That's too wide for me. Most market wizards I've talked to prefer tighter stops." Target: under 4% risk per entry.

### 9.3 Gap Handling

When stock gaps above the pivot:
- Do NOT buy the gap and hope
- Wait for an **intraday pullback / U-turn**
- Look for **anchored VWAP reclaim** on intraday chart
- Set stop at low of breakout day

### 9.4 Current Implementation Gaps

- `_entry_and_stops` calculates entries but has no concept of the 4 range-trading methods
- No early entry logic
- No gap handling logic
- No position building / pyramiding logic
- The method should use **daily** data for entry calculations (currently uses a mix)

---

## 10. Position Management & Exits

### 10.1 Day-by-Day Framework (Moglen)

| Timeframe | What to Look For |
|-----------|-----------------|
| **Day 1** | Trend above daily anchored VWAP from open. Close near top of day's range. If squat: wait for day 2 reconfirmation. |
| **Day 2-5** | Follow-through, constructive tightening, stair-step higher. Pullbacks to 10 EMA are fine. |
| **Day 7-20** | Trend above 10 EMA. The best breakouts respect the 10 EMA in this phase. |
| **Day 20+** | Trend above 21 EMA. Blips down to 21 EMA are normal for strongest stocks. Close below 21 EMA = warning. |

### 10.2 Key Moving Averages for Management

| Moving Average | Usage |
|---------------|-------|
| **10 DMA / 10 EMA** | Short-term trend. Best breakouts respect this for first 2-3 weeks. |
| **21 EMA** | Primary trend management line. Close below = potential sell. |
| **50 DMA** | Intermediate support. Stocks often bounce here during bases. |
| **200 DMA** | Major support. Extensions to 200 DMA in base are often bought (reversal point). |

### 10.3 Launch Pad Setup

**TraderLion notes:** Price reclaims important Key Moving Averages (10w, 10d, 21 EMA, 50d, 65 EMA, 200d) to the upside on volume.

**Current gap:** 65 EMA not tracked. Launch Pad pattern not detected.

---

## 11. Market Context

### 11.1 When Breakouts Work (Moglen)

> "Breakouts work best in the early stages of a new uptrend especially after a significant market correction. During downtrends, pivot breakouts will be sold into and will not follow through."

**Rules:**
- Best when QQQ is above 21 EMA, especially early in the uptrend
- First few weeks of a new uptrend = highest success rate
- Later in cycle / during downtrend = high failure rate
- **Do NOT force pivot buys in bad markets**

### 11.2 Implementation

```python
def assess_market_context(spy_or_qqq_daily):
    """
    Returns:
    - market_trend: 'uptrend' | 'choppy' | 'downtrend'
    - above_21ema: bool
    - uptrend_age_weeks: int (how long since QQQ reclaimed 21 EMA)
    - recent_correction_depth: float (max drawdown in last 3 months)
    - breakout_environment_score: 1-5 (5 = ideal for breakouts)
    """
```

**Current gap:** `_market_context` exists but uses SPY weekly only. Should also check QQQ. Does not assess uptrend age or breakout environment quality. Does not flag "don't force buys" conditions.

---

## 12. Failed Breakouts & Resets

### 12.1 Failed Breakout Reset (Moglen)

When a breakout fails (price reverses below the pivot):
1. Stock often pulls back to the **21 EMA**
2. A tight range forms at/near the 21 EMA
3. This creates a **secondary buy point** - often better than the original
4. Everyone has taken it off their radar = less obvious = better setup

**Implementation:**
```python
def detect_failed_breakout_reset(daily_data, base_pivot, lookback=20):
    """
    After a breakout above base_pivot:
    - Did price reverse below base_pivot within 5 days?
    - Did it then pull back to 21 EMA?
    - Is a tight range forming near the 21 EMA?
    If yes: flag as failed_breakout_reset with new entry point.
    """
```

**Current gap:** `_climax_check` detects extended situations but NOT failed breakout resets with secondary entries.

---

## 13. Implementation Gaps & Action Items

### Priority 1: Critical (Base Identification Redesign)

| # | Gap | Current State | Required Change |
|---|-----|--------------|----------------|
| 1.1 | **Base identification uses weekly data** | `_base_analysis` uses `stock_weekly` | Rewrite to use **daily** OHLCV |
| 1.2 | **Only finds most recent base** | Single 54-week lookback window | Find ALL bases in the ticker's history |
| 1.3 | **No base stage counting** | Not implemented | Implement counting algorithm (Section 4.3) |
| 1.4 | **No prior uptrend measurement** | Crude 52w-low proxy | Calculate actual prior uptrend % before each base |
| 1.5 | **Base depth not relative to market** | Hardcoded -50% threshold | Compare to QQQ drawdown during same period (2.5x rule) |

### Priority 2: High (Missing Strategy Components)

| # | Gap | Section Reference |
|---|-----|------------------|
| 2.1 | **Weekly accumulation count** (O'Neil technique) | Section 5.1 |
| 2.2 | **3 Weeks Tight pattern** detection | Section 5.2 |
| 2.3 | **HVE/HV1/HVIPO** volume signatures | Section 5.3 |
| 2.4 | **Daily accumulation count** (right side of base) | Section 5.4 |
| 2.5 | **Positive Expectation Breaker** priming pattern | Section 8.1.3 |
| 2.6 | **Tight Setup Day** priming pattern | Section 8.1.4 |
| 2.7 | **Range pivot hierarchy** (base > consolidation > range) | Section 7 |

### Priority 3: Medium (Entry & Management Improvements)

| # | Gap | Section Reference |
|---|-----|------------------|
| 3.1 | **4 range-trading entry methods** | Section 9.1 |
| 3.2 | **Early entry logic** and position building | Section 9.2 |
| 3.3 | **Gap handling** (intraday AVWAP reclaim) | Section 9.3 |
| 3.4 | **Market context quality score** | Section 11 |
| 3.5 | **Failed breakout reset detection** | Section 12 |
| 3.6 | **65 EMA** tracking | Section 10.3 |
| 3.7 | **Launch Pad setup** detection | Section 10.3 |
| 3.8 | **RMV indicator** for screening | Section 6.4 |

### Priority 4: Lower (Enhancements)

| # | Gap | Section Reference |
|---|-----|------------------|
| 4.1 | Ability to trend cleanly (prior trend quality assessment) | Moglen: "look to the left at prior trend" |
| 4.2 | Day-by-day post-breakout tracking | Section 10.1 |
| 4.3 | IPO base detection | Section 1 |
| 4.4 | Intraday AVWAP support (requires intraday data) | Section 9.3 |

---

## Appendix A: Key Moving Averages Reference

### Daily Chart
| MA | Usage |
|----|-------|
| 10 DMA | Short-term trend after breakout |
| 21 EMA | **Primary** - best range breakouts form here; trend management line |
| 50 DMA | Intermediate support in bases; stocks bounce here |
| 65 EMA | TraderLion-specific; not currently tracked |
| 200 DMA | Major support; extensions to 200 DMA in base often bought |

### Weekly Chart
| MA | Usage |
|----|-------|
| 10 WMA | Short-term weekly trend |
| 30 WMA (≈ 150 DMA) | Stage analysis (Weinstein); primary weekly MA |
| 40 WMA (≈ 200 DMA) | Long-term support |

## Appendix B: Scoring Framework (Proposed)

Each base gets scored on these dimensions:

| Dimension | Max Points | Criteria |
|-----------|-----------|----------|
| Stage 2 Confirmed | 10 | Above rising 30w SMA |
| Prior Uptrend | 10 | >= 20% prior move (10 pts), 30-50% (8), 50-100% (6), >100% (4) |
| Base Depth vs Market | 10 | < 1x market DD (10), < 1.5x (8), < 2x (6), < 2.5x (4) |
| Base Stage | 10 | Stage 1 (10), Stage 2 (8), Stage 3 (5), Stage 4+ (2) |
| Weekly Accumulation | 10 | accum_weeks / distrib_weeks ratio |
| Daily Accumulation (right side) | 5 | accum_days > distrib_days |
| Tight Areas Present | 10 | Tight areas up right side near MAs |
| 3WT or HVE/HV1 Signatures | 5 | Bonus points for these patterns |
| Priming Pattern Present | 10 | Inside day, upside reversal, PEB, tight setup day |
| Moving Average Structure | 10 | MAs curling up, price reclaiming key MAs |
| Relative Strength | 10 | RS vs SPY ranking |
| Market Context | 10 | Breakout environment quality |
| **Total** | **110** | |

---

*Document created: 2026-04-09*
*Sources: TraderLion Ultimate Trading Guide, TraderLion Twitter thread, Richard Moglen Advanced Breakout Webinar*
*To be used as the single reference for all implementation work on `advanced_base_breakout.py`*
