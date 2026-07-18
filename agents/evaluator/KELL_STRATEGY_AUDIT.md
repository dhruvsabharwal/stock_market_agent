# Oliver Kell — *Victory in Stock Trading* — Complete Strategy & Gap Audit

_Full breakdown of Kell's method (2020 US Investing Champion, +941%) mapped against
what the `evaluator/` currently implements, with everything we're missing ranked.
Source: the book text (Introduction → Appendix). His method is O'Neil/CANSLIM +
Minervini + Livermore/Darvas, expressed through his "Cycle of Price Action."_

---

## 1. His complete strategy

**Self-description:** "intermediate-term trend follower and swing trader." Longer
holds = breakouts from big bases; swings = pullbacks / short continuation patterns.
"A slave to price, uses volume for clues, moving averages to ride the trend."

### Tools
- **Price, Volume, MAs**: 10 EMA + 20 EMA (**all timeframes**), 50 SMA + 200 SMA (daily).
- **Multiple timeframes / fractal**: 5m, 15m, hourly, **daily, weekly, monthly**. THE
  core of his edge: capture a higher-timeframe trend while defining risk on a lower
  timeframe entry.

### The Cycle of Price Action (his central framework)
Bottom→top: **Reversal Extension → Wedge Pop → EMA Crossback → Base n' Break → Exhaustion Extension**
Top→bottom: **Exhaustion Extension → Wedge Drop → EMA Crossback → Base n' Break → Reversal Extension**

1. **Reversal Extension (bottoming)** — capitulation; price extended from the 10 EMA
   into a higher-TF support (often the 200 SMA); reversal bar on **heavy volume**; go
   long; stop below the reversal-bar low; first target = the 20 EMA.
2. **Wedge Pop** — first move back above the MAs after a reversal extension; tight
   range working lower; RS to the index; 10/20 EMA tightening/coiling; buy when price
   recaptures the tight 10/20 EMA; stop below the consolidation; ride the EMAs.
3. **EMA Crossback** — first retest of the MAs after a Wedge Pop; buy against the
   10/20 EMA; stop below the MAs; trail with the EMAs.
4. **Base n' Break** — a longer base finding support at the 10/20 EMA (not a 1–3 day
   pullback); buy against the MAs, add on the breakout; **sell into pops away from the
   10 EMA**; final stop = loss of the EMAs.
5. **Exhaustion Extension (topping)** — blowoff/euphoria; **the more extensions from
   the 10 EMA, the more likely a re-base**; the **2nd extension = take profits**, the
   **3rd = late in the trend**. Especially when also extended from the **Weekly 10 EMA**.

### Name selection (CANSLIM-derived)
- **Relative Strength vs the index** — buy the strongest on a *relative* basis (holds
  up in downtrends, outperforms in uptrends). Found best *during corrections*.
- **Volume** — "Bull snorts": heavy volume = institutional buying.
- **Big theme/story** — EV, cloud, telehealth, WFH… conviction to hold through pullbacks.
- **Sales growth 25%+** (O'Neil: "greatest predictor of future appreciation").
- **Earnings growth** — high EPS growth (Lynch: price tracks EPS long-term).
- **Trade what you know** (Lynch).
- **High beta & liquid** — ~1M+ shares/day; higher beta moves fast.
- **Avoid penny stocks** — nothing under $10, usually >$50 (institutional sponsorship).

### Chart patterns
- **Traditional CANSLIM**: Cup & Handle, Double-Bottom Base (2B shakeout), Flat Base /
  large base (6-week+; big monthly bases = major shift).
- **Non-traditional**: Bull Flag, Bull Pennant, Descending Channel.
- **Little tricks**: Inside Bars (volatility contraction, esp. light volume), Pivot-Low
  Failures / 2B Reversals (Sperandeo), Wick Play, Outside Reversal (bullish engulfing/
  piercing), candlestick patterns (Nison), **Too Tight For Too Long** (obvious tight
  base + RS weakness = trap), Events/news as catalysts ("reaction is what counts").

### Stop loss & selling
- **Reason for buying invalidated → sell.**
- **Breakout-day low** stop (esp. heavy volume); in chop, sell if the breakout *level*
  fails before the bar low.
- **Ignition-bar low** — wide-range/heavy-volume bar; stop below its low.
- **Reconfirming Price Strength** (Darvas Box) — when price retakes the high that began
  a pullback, raise the stop below the **new pivot low**. Repeats with each advance.
- **10/20 EMA trailing stop** — sell on a close below the 10/20 EMA (which one =
  discretion, whichever it's respecting; accelerating trend → shorter MA).
- **Rising-trendline break** (3+ touches; often near the daily 20 EMA).
- **Weekly 10 EMA / Daily 50 SMA extension** — extended → book partials (prefers the
  **Weekly 10 EMA** gauge).
- **Gap-up exhaustive extension** — extended + gap up → sell / partial.
- **Sell Some, Hold Some** — bank a multiple of risk, hold a core for a bigger move.

### Market timing / defense
- **Follow the QQQ/NASDAQ price cycle**: below the **20 EMA** → cautious, raise cash,
  be more selective; downtrend = Blowoff Extension confirmed by a Wedge Drop below 20 EMA.
- **Avoid margin below the QQQ 20 EMA** (margin only when "stars aligned").
- Shorting is harder — smaller size, tighter stops, take profits faster.
- **Uncover RS during corrections** — the next leaders are found there.

### Position sizing & portfolio (his P&L engine)
- **8–12 names**; top-weight the 3–7 best. Tiers: Top Idea (25–35% margin / 12–15%
  cash) → Conviction Core → Volatile Conviction Core (size down for volatility) → Core
  → Swing. **Reduce Top Ideas to 20–25% on the first extension from the 10 EMA.**
- **Buy in pieces, sell in pieces**; tighten stops as size grows; **pyramid using
  prior profits** so added size carries less principal risk.
- **Compound monthly returns** (22%/mo ≈ 1,000%/yr).
- Sayings: *Sell into strength or you'll sell into weakness · From failed moves come
  fast moves · Bigger the base, higher in space · Losers average losers · Price hurts,
  size kills · Buy right, sit tight.*

---

## 2. What the evaluator already implements (the mapping)

| Kell element | Our implementation |
|---|---|
| Cycle of Price Action (5 patterns) | `kell.py` — wedge pop/drop, EMA crossback, reversal_low, exhaustion_high (+ ext %, vol ratio, bars-since, counts) |
| Base n' Break / Flat Base breakout | `range_strategy.py` box + ≥4% expansion (a *generic* base breakout) |
| 10/20 EMA, 50/200 SMA stack, extension | `ma_stack.py` — `above_*`, `close_ext_{10,20}ema/50,200sma_pct`, coil spread |
| Relative strength | `rs_rank_{n}m` (universe percentile) + `rs_vs_spy_{n}m` (index-relative) |
| Volume / "bull snort" | `breakout_vol_ratio`, `base_vol_ratio`, `base_vol_dryup` |
| Sales / earnings growth | `revenue_qoq/yoy_growth` (SEC), `eps_yoy/qoq_growth` (yfinance) |
| Liquidity / avoid pennies | `avg_dollar_vol_20d`, `min_dollar_volume` gate |
| Trend template / stage | `stage2.py` (Minervini 6 criteria) |
| Overhead supply / "space" | `overhead.py` (nearest/highest prior highs, blue-sky) |
| 10/20 EMA trailing stop / Darvas reconfirm | `SwingLowTrailExit` (ratchet under swing lows) ≈ reconfirming price strength; `EmaTrailExit` ≈ EMA trail |
| Inside bars (volatility contraction) | `tight_body/range_days` counts |
| Extension count (1st/2nd/3rd) | `wedge_pop_count`, and now **`exhaustion_high_count` / `reversal_low_count`** (NEW) |

---

## 3. WHAT WE'RE MISSING (ranked by importance)

### TIER 1 — the structural core of his edge
1. **Multiple timeframes (weekly & monthly).** Kell is fundamentally *fractal* — daily
   entry inside a weekly/monthly trend. We are **daily-only**. Missing specifically:
   - **Weekly 10 EMA extension** — his *preferred* "too extended / needs to base" gauge
     (repeated ~10× in the book). ≈ 10-week EMA.
   - Weekly/monthly MA stack, weekly base detection, monthly base breakouts ("bigger
     the base, higher in space"). His biggest winners are *monthly* base breakouts.
   - **This is the #1 gap.** Almost every "odds are lower" call he makes references the
     weekly timeframe.
2. **Position management = his actual P&L.** Sell-into-strength partials, pyramiding
   with profits, 8–12 name portfolio, top-weighting, reduce-to-core on the 1st
   extension, margin gating. Our engine is single-position / equal-weight scan — it
   **cannot express** the money management that produced +941%. The setup was never the
   edge; the sizing/selling discipline is.
3. **Base STAGE / base count.** Stage 1/2/3/4 bases — "Stage 4 or later often fail."
   Late-stage bases of extended leaders fail (exactly why elite-RS breakouts underperform
   in our data). We don't count how many bases deep a stock is. `exhaustion_high_count`
   (just added) is a first proxy; a true base-stage counter is the real thing.

### TIER 2 — specific, buildable signals
4. **Reversal-Extension (capitulation) entries.** He *buys capitulation* into the 200
   SMA on heavy volume — a whole entry mode our box-breakout strategy never takes. We
   have `reversal_low_*` features but no strategy that acts on them.
5. **Earnings/catalyst-driven breakouts & "buyable gap-ups."** Breakout *on an earnings
   catalyst with a gap up* = his highest risk/reward pattern. We have `eps_report_date`
   but don't flag "breakout coincides with earnings / gap-up," nor gap features.
6. **QQQ 20-EMA market regime switch.** He gates aggression/margin on QQQ vs its 20 EMA.
   We use SPY *returns*; we don't have the "index above/below its 20 EMA" boolean, and
   NASDAQ/QQQ (growth) fits his universe better than SPY.
7. **Specific patterns vs a generic box.** Cup & Handle, Double-Bottom (2B shakeout),
   Bull Flag, Bull Pennant, Descending Channel — each has a distinct footprint and
   risk point. Our single "box" collapses them all.
8. **Beta / volatility tiering.** He sizes down high-volatility names. We don't compute
   beta (ATR/range is a partial proxy) — relevant given the volatility ceiling finding.

### TIER 3 — candlestick / bar tells (confluence)
9. **Bar patterns**: 2B reversal / pivot-low failure, wick play, outside reversal /
   bullish engulfing, shooting star, ignition bar (wide-range/heavy-vol), gap-up-and-
   fade. We have tight-day counts (≈ inside bars) but none of these.
10. **Too Tight For Too Long** — obvious tight base + RS weakness = trap (a specific
    *anti*-signal combining tightness with relative weakness).
11. **Ignition-bar-low / breakout-day-low stop** — a specific Kell exit we haven't
    tested as an `ExitRule`.

### Not practically capturable (noted, not gaps to build)
- Theme/story, "trade what you know," discretionary feel/intuition, "my lead analyst
  is my wife." These are the discretionary layer — precisely where his edge partly
  lives and a mechanical backtest cannot reach.

---

## 4. Recommended next builds (in order)
1. **Weekly 10-EMA extension** (`close_ext_10wema_pct`) + weekly MA stack — the single
   highest-value missing feature (his preferred extension gauge). Rebuild, then re-run
   the "too extended / enter on a base" analysis with his actual gauge.
2. **Rebuild to populate `exhaustion_high_count`** (added) and test it as the
   extension-count proxy (replacing `wedge_pop_count`), per the extension-number thesis.
3. **Base-stage counter** (how many bases since the trend began) — test "Stage 4+ fails."
4. **QQQ-above-20-EMA regime flag** + re-test market-timing gating.
5. Longer term: a **capitulation/reversal-extension entry mode**, and a
   **partial-profit / pyramiding** portfolio layer (to model the money-management edge).

_The overarching finding still holds: every mechanical entry signal is a downside/
consistency lever (weak, inverted-U); the exit + position management is the upside
lever. Kell's own text agrees — his returns come from selling into strength, pyramiding
winners, timeframe alignment, and defense, not from the entry pattern alone._
