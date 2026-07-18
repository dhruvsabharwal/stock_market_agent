# Oliver Kell — Complete Strategy Audit & Gap Analysis

_Source: "Victory in Stock Trading" (Kell, 2021). This maps his **entire** method
against what the evaluator implements, what we've tested, and — critically — what
generates his returns that we have NOT built. Read alongside `KELL_DEFINITIONS.md`
(the metric spec) and `../HANDOFF.md` §5 (findings)._

---

## 1. Kell's strategy in full

**Identity:** intermediate-term trend follower + swing trader. Buys the strongest
growth stocks (big earnings/sales, game-changing story) breaking out of large bases;
swing-trades pullbacks/continuations. "Slave to price, volume for clues, MAs to ride."

**Tools:** price, volume, and just four MAs — **10 EMA, 20 EMA (ALL timeframes)**,
50 SMA + 200 SMA (daily). **Multiple timeframes (fractal): 5m/15m/hourly/daily/
weekly/monthly.** The daily/intraday entry is taken *within* the weekly/monthly trend.

**The Cycle of Price Action** (his core framework — a stock rotates through these):
- Bottoming→up: **Reversal Extension → Wedge Pop → EMA Crossback → Base n' Break → Exhaustion Extension**
- Topping→down: **Exhaustion Extension → Wedge Drop → EMA Crossback → Base n' Break → Reversal Extension**
- The 5 patterns: (1) **Reversal Extension** = capitulation, extended from 10 EMA into
  higher-TF support (often 200 SMA), reversal bar on heavy volume; (2) **Wedge Pop** =
  first reclaim of the 10/20 EMA (coiled/tight) after a reversal; (3) **EMA Crossback**
  = first pullback retest of the 10/20 EMA; (4) **Base n' Break** = a longer base holding
  the 10/20 EMA, buy against MA + add on breakout; (5) **Exhaustion Extension** =
  blowoff; the MORE extensions from the 10 EMA, the more likely to re-base (2nd = take
  profits, 3rd = late).

**Name selection:** relative strength (buy strongest vs index), volume ("bull snorts"
= institutional), a big **theme/story**, **sales growth ≥25%**, high **earnings growth**,
trade-what-you-know, **high beta + liquid (≥1M shares/day)**, **avoid <$10 (usually
>$50)** for institutional sponsorship.

**Chart patterns:** cup-&-handle, double-bottom (2B shakeout), **flat/large base**
(bigger the base, higher in space); non-traditional: bull flag, bull pennant,
descending channel. **Tells:** inside bars (volatility contraction), 2B/pivot-low
reversals, wick play, outside reversal, bearish engulfing / shooting star, **ignition
bar** (wide-range/heavy-vol), gap-ups ("street caught off guard"), TTFTL ("too tight
for too long" = trap when a laggard), events/earnings as **catalysts** (reaction > news).

**Stops & selling:** sell if the **reason for buying** is violated; stop at the
**breakout-day low** / **ignition-bar low**; **Reconfirming Price Strength** (Darvas —
raise stop under each new pivot low as price reclaims the pullback high); **10/20 EMA
trailing stop** (which MA = discretion); rising-trendline breaks; **book partials into
extensions from the Weekly 10 EMA / Daily 50 SMA** (prefers the *weekly 10 EMA* gauge);
gap-up-while-extended = sell; **"Sell Some, Hold Some"** at a multiple of risk.

**Market regime:** trade the **QQQ/NASDAQ price cycle** — below the **20 EMA** = raise
cash, no margin, be selective; downtrend = blowoff + wedge-drop below the 20 EMA. Uncover
the next leaders by screening for **relative strength during corrections**. Shorting is
harder — smaller/tighter, mostly avoided.

**Position sizing / portfolio (where the returns come from):** 8–12 names, **top-weight
the 3–7 best ideas** (Top Idea 25–35% on margin / 12–15% cash), tiers = Top Idea →
Conviction Core → Volatile Conviction Core (size down for vol) → Core → Swing.
**Reduce a Top Idea to 20–25% on the FIRST extension** (bank into strength, hold a
confident core). **Buy in pieces, sell in pieces; pyramid with profits while raising
stops** (double size at *less* principal risk). Margin only when "stars aligned."
**Compound monthly returns** (22%/mo ≈ 1,000%/yr). His 2020 = +941%.

---

## 2. Component-by-component: BUILT / PARTIAL / MISSING

| Kell component | status | notes |
|---|---|---|
| Cycle of Price Action (wedge pop/drop, crossback, reversal/exhaustion) | **BUILT** | `kell.py` features (per EMA). |
| 10/20 EMA + 50/200 SMA stack, extension, above/below | **BUILT** | `ma_stack.py`. |
| Daily 10-EMA extension | **BUILT** | `close_ext_10ema_pct`. |
| **Weekly / monthly 10-EMA extension (his PREFERRED gauge)** | **BUILT (new)** | `higher_timeframe.py` → `close_ext_10wema/20wema/10mema_pct`. |
| **Exhaustion-extension COUNT (1st vs 2nd/3rd)** | **BUILT (new)** | `exhaustion_high_count` / `reversal_low_count`. |
| Volume: breakout surge + base dry-up | **BUILT (new)** | `breakout_vol_ratio`, `base_vol_ratio`, `base_vol_dryup`. |
| Relative strength (rank vs universe + vs SPY) | **BUILT (new)** | `rs_rank_*` (survivorship-biased), `rs_vs_spy_*` (clean). |
| Sales growth (≥25%) + earnings growth | **BUILT (new)** | `revenue_*_growth` (SEC EDGAR), `eps_*_growth`. |
| Liquidity floor / avoid penny (<$10) | **BUILT** | `avg_dollar_vol_20d`, `min_dollar_volume` gate. |
| Stage-2 trend template + 200-SMA rising (slope-thresholded) | **BUILT** | `stage2.py` (+ `min_slope_pct`). |
| Overhead supply / base position | **BUILT** | `overhead.py`. |
| Swing-low trailing stop (≈ Darvas "reconfirm price strength") | **BUILT** | `SwingLowTrailExit` — the winning exit. |
| Base n' Break entry (box → expansion) | **PARTIAL** | our box+4% ≠ his specific VCP/cup/flat-base; a noisy superset. |
| Market regime gating (QQQ < 20 EMA = stand aside) | **PARTIAL** | we record `mkt_ret_*` (SPY) but don't GATE on QQQ-20EMA. |
| Ignition-bar / breakout-day-low stop | **MISSING** | not built as an `ExitRule`. |
| **Specific chart patterns** (cup&handle, double-bottom, flat base, bull flag/pennant, descending channel) | **MISSING** | only a generic box. |
| **Candlestick/bar tells** (inside bar, 2B reversal, wick play, outside reversal, engulfing, shooting star, gap-up, ignition) | **MISSING** | tight-days ≈ inside bars only. |
| **Reversal-Extension entry** (buy capitulation into 200 SMA on heavy vol) | **MISSING** | we record reversal features but the strategy only box-breaks. |
| **Beta** (high-beta preference) | **MISSING** | not computed. |
| Theme / story / catalyst (earnings gap-up) | **MISSING** | unquantified; `eps_report_date` only. |
| **Discretionary selection** (top handful by eye) | **MISSING (structural)** | we take EVERY setup. |
| **Selling into strength / partial profits** | **MISSING (structural)** | single full exit; no partials. |
| **Pyramiding / adding to winners** | **MISSING (structural)** | no adds. |
| **Concentration / top-weighting 3–7 ideas** | **MISSING (structural)** | equal-weight $1k/trade. |
| **Compounding / portfolio construction** | **MISSING (structural)** | per-trade scan, not a portfolio. |

---

## 3. What we TESTED and found (holdout-validated) — see HANDOFF §5b/§5c

Every Kell entry signal we built is **real but individually weak — a win-rate /
consistency lever, not a tail/expectancy lever.** Repeatedly an **inverted-U**
(moderate beats extreme):
- **Weekly 10-EMA extension** (his preferred gauge) — the sharpest: win rate falls
  monotonically 17.1% (near base) → 8.2% (blowoff >35%, NEGATIVE mean −0.91). Cleaner
  and more monotone than the daily gauge — Kell's weekly preference is justified.
- **Exhaustion-extension count** — early (0–1) = 22.5%/18.5% win vs late (3+) = 12.9%.
  Validates "1st extension tradeable, 3rd is late." (But 3+ has the *fattest tail*,
  mean 1.20 — late-stage survivors are the monsters → win-rate signal, not tail.)
- **Double-extended confluence** (daily >10% AND weekly >18%) = 10.5% win — a clean AVOID.
- **RS-vs-SPY** sweet spot (beat SPY by −10..+30pp) best; huge (>30pp) and lagging
  (<−10pp) both worse. **RS-rank** is INVERTED (elite >90 underperforms) AND survivorship-
  biased — prefer `rs_vs_spy`.
- Revenue/EPS growth, tightness, volume surge — all the same inverted-U, all modest.

**The ceiling:** the >8% winner is unpredictable *in a capturable sense* — a GBM on all
price-action features gets holdout AUC 0.54 for realized >8% (0.71 for forward MFE, but
that's pure volatility that whips both ways). No entry signal unlocks the tail.

---

## 4. Why we're NOT seeing Kell-like results (the honest synthesis)

We've faithfully built and validated the **ENTRY signals** of Kell's method — and
confirmed they're real but weak. We have NOT built the parts that actually generate his
+941%, because they aren't per-trade entry signals:

1. **We measure the AVERAGE of a mechanical scan; he trades a DISCRETIONARY top handful.**
   114,586 setups (every box breakout) vs his 8–12 hand-picked names. The edge lives in
   the selection (theme/story/leadership/base-quality confluence + gut) we can't mechanize.
   Our filters each add a few points of win rate precisely because they're weak proxies
   for that judgment.
2. **His edge is money management, which our engine can't express.** Selling into
   strength (partials), pyramiding into winners with house money, concentrating 25–35%
   in Top Ideas, compounding monthly. Equal-weight $1k/trade with one exit *cannot*
   turn a ~20% batting average into 941% — that transformation IS the position sizing.
3. **Fractal multi-timeframe is his gate; we only just started measuring it.** He takes a
   daily/intraday entry *inside* a confirmed weekly/monthly trend. We added weekly/monthly
   *extension features* but still ENTER on a daily box breakout with no HTF gate.
4. **Our setup is a loose superset.** "Box + 4% pop" catches a much noisier population —
   including all the late-stage/extended/low-volume junk he'd never touch by eye.
5. **He sits out bad markets; we trade every regime.** Below QQQ-20EMA he's in cash / no
   margin. Our scan eats every correction.
6. **The unpredictability ceiling is real, not a bug.** He doesn't beat it by prediction
   either — he beats it by being in many, cutting losers fast, letting the few runners run
   (trail + pyramid), concentrating on conviction, and compounding. Our work proves the
   entry can't pick the tail; his results come from everything AROUND the entry.

**Conclusion:** the evaluator has (correctly) shown that Kell's *entry signals* are
weak batting-average levers and that the tail is unpredictable at entry. The reason we
don't see his returns is that his returns don't come from the entry — they come from
**discretionary selection + concentration + pyramiding + selling into strength + market-
regime participation + monthly compounding**, none of which a per-trade equal-weight scan
reproduces. To close the gap, the next frontier is **portfolio-level simulation** (sizing,
concentration, pyramiding, regime gating), not more entry features.

---

## 5. Highest-leverage next steps (ranked)

1. **Portfolio simulation** — concentration (top-weight N ideas), pyramiding/partials
   (sell-some-hold-some into extensions), monthly compounding. This is where his returns
   live; per-trade metrics structurally can't show it.
2. **Market-regime gate** — only take setups when QQQ (or SPY) > its 20 EMA; size up only
   then. Test the equity-curve impact (drawdown avoidance).
3. **HTF entry gate** — require "near the weekly base" (weekly 10-EMA extension in the
   0–8% sweet spot, not blowoff) and early exhaustion count (≤2). Fold the validated AVOID
   buckets (weekly blowoff / double-extended / 3+ exhaustions) into the entry screen.
4. **Reversal-Extension entry** — add the capitulation-into-200SMA-on-volume buy as a
   second setup type (currently we only box-break).
5. Specific patterns + bar tells (ignition bar, 2B reversal, wick play) as refinements.
