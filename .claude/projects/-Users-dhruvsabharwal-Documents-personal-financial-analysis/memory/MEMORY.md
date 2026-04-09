# Project Memory: Financial Analysis / Base Breakout Strategy

## Project Goal
Generate wealth in financial markets by implementing and backtesting quantitative trading strategies.
Core principle: correctness over speed, never cut corners, validate data.

## Primary Codebase
- **Main file:** `agents/base_breakout_strategy/advanced_base_breakout.py`
- **Class:** `AdvancedBaseBreakoutAnalyzer`
- **Spec:** `agents/base_breakout_strategy/base_breakout_strategy_prompt.md`
- **Documentation:** `agents/base_breakout_strategy/TECHNICAL_DOCUMENTATION.md` (created 2026-03-25)

## Scoring System (11 points max)
EPS≥25% (1pt) + Rev≥20% (1pt) + RS 13w>0 (1pt) + Stage2 (1pt) + Base Pattern (1pt)
+ VCP (1pt) + Accumulation (1pt) + VolDryUp (1pt) + Tightness (1pt) + Priming (1pt) + CleanStop (1pt)

Quality: ≥10+uptrend=ACTIONABLE, ≥7=DEVELOPING, ≥5=NOT_READY, <5=AVOID

## Critical Bugs (must fix before live use)
1. **Pivot price wrong** — `analyze()` uses `base['base_high']` (max Close, 54w) instead of
   spec's `tail(10)['High'].max()`. Affects all entry trigger/stop calculations.
2. **`already_broken_out` inconsistent** — computed twice with two different pivot definitions.
   First uses tail(50) High max (for RS), second uses base_high (for everything else).
3. **base_high uses Close, not High prices** — understates true resistance level.
4. **`all_levels` missing from support output** — `_find_support_levels` drops this key.
5. **VCP crash on None** — `safe_pct(...) * -1` crashes when safe_pct returns None.
6. **`earningsGrowth` is quarterly YoY, not 5yr CAGR** — mislabeled in output.

## Key Data Sources
- yfinance: 3yr weekly (stock + SPY), 1yr daily, `t.info`, `t.quarterly_financials`
- YF_LOCK (threading.Lock) serializes all downloads — batch parallelism is compute-only

## Architecture Note
Spec calls for standalone functions; implementation uses class with private methods.
Functionally equivalent except for the bugs above.
