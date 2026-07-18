# Kell Cycle — metric definitions (single source of truth)

This documents **exactly** how `kell.py` computes each Oliver-Kell "Cycle of
Price Action" feature *as currently implemented*. If we change a definition in
`kell.py`, **update this file in the same edit**. Defaults shown are the
constructor defaults; every one is a tunable knob.

All features are **recorded on each trade** (never gate entries), measured **as
of the entry/breakout bar**, and are **point-in-time** (only bars ≤ the current
bar are used).

---

## 0. Reference EMA & extensions (the shared foundation)

- EMAs are computed on **close**, incrementally, seeded from the first bar seen
  (matches `pandas.ewm(adjust=False)`), for every period in `ema_periods`
  (default `[10, 20]`) plus `ext_ref_ema`.
- **`ext_ref_ema`** (default **20**) is the *reference EMA* for every extension
  measurement below/above.
- **Extension below** the ref EMA on a bar = `(ref_ema − low) / ref_ema × 100`.
- **Extension above** the ref EMA on a bar = `(high − ref_ema) / ref_ema × 100`.
- **Volume ratio** on a bar = `volume / (trailing average of the prior
  vol_avg_window volumes)` (default window **20**). `None` until the window fills.

---

## 1. Wedge Pop  (per EMA `p` → `wedge_pop_*_{p}ema`)

**Definition.** A pop fires on the bar where the **close crosses from below to
above the p-EMA** (a reclaim), *provided* the just-ended run of closes-below-the-
p-EMA reached a **maximum extension of ≥ `min_ext_below_pct` below the ref EMA**
at its deepest point (default **5%**).

- The 5% is the **deepest point of the whole down-run**, not the depth at the
  reclaim moment. Price may drop 8% below, base for weeks under the EMA, then
  reclaim — still a pop.
- A shallow dip-and-reclaim that never reached the threshold is **not** a pop.

**Columns**
| column | meaning |
|---|---|
| `wedge_pop_date_{p}ema` | date of the most recent pop |
| `wedge_pop_bars_since_{p}ema` | bars since that pop |
| `wedge_pop_bars_below_{p}ema` | length (bars) of the down-run before that pop |
| `wedge_pop_max_ext_below_{p}ema` | deepest % below the **ref** EMA during that down-run |
| `wedge_pop_vol_ratio_{p}ema` | volume ratio on the pop day |
| `wedge_pop_count_{p}ema` | # pops in the trailing `cycle_count_months` (default 12) |

## 2. Wedge Drop  (per EMA `p` → `wedge_drop_*_{p}ema`) — mirror of the pop

**Definition.** Fires on the bar where the **close crosses from above to below
the p-EMA**, provided the just-ended run of closes-above reached a **maximum
extension of ≥ `min_ext_above_pct` above the ref EMA** (default **5%**).

**Columns:** `wedge_drop_date/bars_since/bars_above/max_ext_above/vol_ratio/count_{p}ema`
(same meanings, mirrored to the upside).

## 3. "Reclaimed both" flags

- `wedge_pop_both_bars_since` — if the most recent pop on the 10-EMA **and** the
  20-EMA happened within `both_within_bars` (default 10) of each other, this is
  the bars since the *later* of the two; else `None`.
- `wedge_drop_both_bars_since` — same, for drops.

## 4. EMA Crossback  (per EMA `p` → `crossback_count_{p}ema`)

**Definition.** "After a pop, price pulls back and **holds support at the EMA**."
Counted only **after a wedge pop** on that EMA (count resets to 0 on each new
pop). A crossback is counted on a bar when **all**:

1. **Touch-and-hold** — the bar's **low** dips to the EMA
   (`ema × (1 − crossback_low_tol) ≤ low ≤ ema`, default tol **0.5%**) **and the
   close finishes above** the EMA (`close > ema`).
2. **EMA rising** — up vs the previous bar **OR** vs `crossback_ema_rising_lookback`
   bars ago (default **5**; union tolerates 1-day wiggles; `0` disables the check).
3. **Armed** — since the last count, at least one bar's **low cleared the EMA**
   (`low > ema × (1 + crossback_reset_pct)`, default reset **0.0**). This de-dupes:
   one continuous pullback counts once.

## 5. Reversal Extension  (single → `reversal_low_*`) — the capitulation

**Definition.** The point of **maximum extension below the ref EMA** during the
most recent **below-episode** (a run of closes below the ref EMA) that reached
**≥ `reversal_min_ext_pct`** below the ref EMA (default **10%** — deliberately
deeper than a wedge drop; this is Kell's oversold capitulation, not a routine dip).

- Reported as the **ongoing** qualified capitulation if price is currently in
  one, otherwise the **last completed** qualified one.
- The reversal bar = the bar with the greatest % extension below the ref EMA in
  the episode; its **price = that bar's low**.
- Only episodes reaching the threshold "lock" — shallow declines never overwrite
  the last reversal extension.

**Columns**
| column | meaning |
|---|---|
| `reversal_low_date` | date of the capitulation bar |
| `reversal_low_price` | that bar's low |
| `reversal_low_bars_since` | bars since it |
| `reversal_low_ext_pct` | its extension % below the ref EMA (the "gap") |
| `reversal_low_vol_ratio` | volume ratio on that bar (Kell's volume climax) |
| `reversal_low_pct_above` | how far the entry price sits above it |
| `reversal_low_below_{p}ema_pct` | how far below **each** EMA `p` that low was |

## 6. Exhaustion Extension  (single → `exhaustion_high_*`) — mirror (the climax)

**Definition.** Max extension **above** the ref EMA during the most recent
**above-episode** reaching **≥ `exhaustion_min_ext_pct`** (default **10%**).
Columns mirror the reversal: `exhaustion_high_date/price/bars_since/ext_pct/
vol_ratio/pct_below/above_{p}ema_pct`.

---

## 7. Extension counts — rolling window (`kell.py`)

- **`exhaustion_high_count`** / **`reversal_low_count`** — number of *completed
  qualified* exhaustion / reversal episodes whose bar index falls within the
  trailing `cycle_count_months` (default 12 → ~252 bars) ending at the signal
  bar. A rolling-time count (not "since the base").

## 8. Per-run extension counts — Kell's 1st vs 2nd/3rd (`kell.py`)

Count of completed exhaustion episodes **since each cycle anchor** (reset per
run, not a rolling clock). `None` when the anchor doesn't exist.

- **`exh_count_since_reversal`** — exhaustions since the last completed
  **reversal extension** (the cycle bottom).
- **`exh_count_since_wedge_pop`** — exhaustions since the last **wedge pop** on
  the reference EMA.
- **`exh_count_since_uptrend`** — exhaustions since the current **uptrend run**
  began (see §9). `None` when not in an uptrend.

## 9. Trend identification — the primary read (`kell.py`)

Two related mechanisms. **The 3-state `trend_state` is the primary trend/base
definition.** The 2-state wedge-pop machine is kept only for the leg / from-reversal
features.

### 9a. `trend_state` — 3-state: uptrend / basing / downtrend  ← the definition

Per bar, from the ref-EMA slope + price position. `slope` = % change of the ref
(20) EMA over `trend_slope_window` (default 5) bars. Runs identically on **daily**
(`trend_state`) and **weekly** (`weekly_trend_state`, the trend authority).

- **downtrend** — `slope < −trend_flat_band` (default −1.5%). Pure slope: a bounce
  that pushes price back above a *still-falling* EMA is **still downtrend**.
- **uptrend** — `slope > +1.5%` **AND** `close > ref EMA` **AND** `close ≥ last
  confirmed swing high` (a real advance: rising EMA, above it, at new highs).
- **basing** — everything else: a **flat EMA** (`−1.5%…+1.5%`), or a *rising* EMA
  but price **pulled back** below the EMA *or* below the last swing high
  (consolidating). This absorbs the micro-noise a 1-bar slope would flip on.

`last swing high` = the most recent **confirmed ±`trend_pivot_window` (default 5)
swing-high pivot** — updates only when a real pivot forms, so it can't flip-flop
per bar. Columns: **`trend_state`** / **`weekly_trend_state`** (str),
**`trend_slope_pct`** / **`weekly_trend_slope_pct`**.

**`prev_trend_state`** / **`weekly_prev_trend_state`** — the previous DISTINCT
3-state label (updated only on a real change; `None` until the first transition).
So a `basing` bar reveals whether it followed an `uptrend` (continuation base) or a
`downtrend` (bottoming base) — Kell's "a Base n' Break appears in both cycles."

**Weekly horizon is independently tunable** (`StageRangeStrategy` args
`weekly_ext_ref_ema`/`weekly_trend_slope_window`/`weekly_trend_pivot_window`;
default inherits the daily 20/5/5). The **validated challenger is 10/3/5** —
Kell's weekly **10-EMA**, 3-week slope, ±5 pivot — which catches tops/bases ~1–2
weeks sooner and ~doubles the weekly up/down outcome separation (HANDOFF §5e).

### 9b. Per-run exhaustion counts (Kell "stages of the advance")

Anchored to the 3-state run:
- **`exh_since_downtrend`** — exhaustions since the last **downtrend** bar;
  **accumulates through basing periods**, resets only on downtrend = Kell's
  **1st / 2nd / 3rd extension = the stage** (higher = later/more extended).
- **`exh_since_base`** — exhaustions since the last **non-uptrend** bar (resets on
  every basing). Weekly: `weekly_exh_since_*`.

> **Counting rule (fixed HANDOFF §5e):** every `exh_since_*` / `*_count` **includes
> the live, in-progress exhaustion** (a qualified above-EMA episode not yet closed),
> not only completed/locked ones — because Kell counts an extension *as it happens*
> ("the second extension… since the traditional Cup n' Handle buy point"). This made
> `exh_since_base` non-zero (was 100% zero) and fixed the "0 while obviously extended"
> artifact (TSLA weekly 2014-02-26 went 0 → 1). No double-count: an open episode is
> never in `_exh_episodes` yet.

### 9c. 2-state wedge-pop machine (legs / from-reversal only)

A `up`/`down` state, symmetric & self-calibrating: **STARTS** (down→up) when
`close > ref EMA` AND the ref EMA **strictly rose** (a wedge-pop reclaim is the
special case); **ENDS** (up→down) when `close < ref EMA` AND it **strictly fell**;
a **flat EMA persists** (symmetric hysteresis). Kept for:
- **`kell_in_uptrend`** (bool), **`kell_uptrend_bars`**,
- **`kell_uptrend_from_reversal`** (bool — a reversal within `reversal_window_bars`
  (40) preceded the start), **`kell_uptrend_legs`** (Base-n'-Break legs),
- **`exh_count_since_uptrend`** (exhaustions since this 2-state start).

_(The old Minervini `base_in_uptrend` is RETIRED — superseded by `trend_state`.)_

---

# Companion providers (Kell/O'Neil features outside `kell.py`)

_Same contract: recorded per trade, as-of the breakout bar, point-in-time._

## 10. Higher-timeframe extension (`higher_timeframe.py`) — Kell's weekly gauge

Weekly/monthly EMAs are maintained **incrementally from the daily stream** (no
separate cache): committed through the last completed ISO-week / calendar-month,
plus one live EMA step for the in-progress period (what a live weekly chart shows
mid-week). Emitted once ≥ `span` periods have completed.

- **`close_ext_{10,20}wema_pct`** — `(close / weekly-N-EMA − 1) × 100`. The
  **weekly 10-EMA extension is Kell's preferred "needs to base" gauge.**
- **`above_{10,20}wema`** (bool).
- **`close_ext_10mema_pct`** / **`above_10mema`** — same on the monthly 10-EMA.

## 11. Trend state / base-vs-decline gate (`trend_state.py`)

- **`above_{50,200}sma_ts`** (bool) — close vs the SMA.
- **`sma{50,200}_slope_pct`** — % change of the SMA over `slope_lookback`
  (default 21); **`sma{50,200}_rising`** = slope > `min_slope_pct` (default 0).
- **`pct_from_252d_high`** — `(close / 52-week-high − 1) × 100` (0 = new high);
  **`bars_since_252d_high`**.
- **`base_in_uptrend`** (bool) — **THE base-vs-decline gate**: `close > 50 SMA`
  AND 50 SMA rising AND within `max_pullback_pct` (default **25%**) of the
  52-week high. _Heuristic: the 25% is Minervini's Trend-Template number, the
  rising-50 is a proxy — both tunable, not from Kell._
- **Swing structure** (confirmed pivots, `pivot_window`=5, causal):
  **`making_higher_high`** / **`making_higher_low`** (last swing vs the prior);
  **`higher_highs_count`** / **`lower_highs_count`** (up/down steps among the last
  4 swing highs); **`uptrend_structure`** = higher-high AND higher-low (Kell's
  healthy structure; its absence is his "wedging"/distribution warning).

## 12. Volume signature (`range_strategy.py`)

- **`breakout_vol_ratio`** — signal-bar volume ÷ trailing `vol_avg_window`
  (default **50**) average ending at the **prior** bar (O'Neil breakout surge).
- **`base_vol_ratio`** — mean volume of the **box** bars ÷ the 50-day average
  (`< 1` = a dry base).
- **`base_vol_dryup`** — mean volume of the **last third** of the box ÷ the
  **first third** (`< 1` = drying through the base). `None` for boxes < 6 bars.
- **`avg_dollar_vol_{n}d`** — trailing mean of `close × volume` over
  `dollar_volume_window` (default 20), ending at the prior bar (liquidity; also
  the optional `min_dollar_volume` entry gate).

## 13. Relative strength (`momentum.py` + `relative_strength.py`)

- **`rs_rank_{1,3,6,12}m`** — cross-sectional **percentile (0–100)** of the
  stock's trailing N-month return vs the whole cached universe on the signal date
  (O'Neil "RS Rating"). Precomputed to `rs_cache/`; **survivorship-biased**.
- **`rs_vs_spy_{1,3,6,12}m`** — `stock ret_Nm − SPY ret_Nm` (outperformance in
  percentage points; Minervini's "RS line"). Universe-independent → **preferred**.

## 14. Market regime — QQQ (`momentum.py` `MarketRegime`)

- **`qqq_above_20ema`** / **`qqq_above_50sma`** (bool) — index vs its causal
  20-EMA / 50-SMA (**Kell's cash/margin switch**).
- **`qqq_ext_20ema_pct`** — `(QQQ / QQQ-20-EMA − 1) × 100`.
- **`qqq_ret_{1,3,6,12}m`** — QQQ trailing returns.

## 15. Revenue (`fundamentals.py`, SEC EDGAR)

Point-in-time by **SEC filing date**; fiscal **Q4 reconstructed** as
`annual − (Q1+Q2+Q3)` (10-Ks report the full year, not a standalone Q4).

- **`revenue_qoq_growth`** — latest quarter vs the prior (%).
- **`revenue_qoq_growth_prev1/2/3`** — the prior three QoQ steps (the trajectory).
- **`revenue_yoy_growth`** — latest vs 4 quarters back (deseasonalized).
- **`revenue`** — raw latest quarterly $ (excluded from cross-ticker screens);
  **`revenue_report_date`** — the filing date used.

---

## Knobs (constructor defaults)

| knob | default | affects |
|---|---|---|
| `ema_periods` | `[10, 20]` | which EMAs get pop/drop/crossback columns |
| `ext_ref_ema` | `20` | reference EMA for all extension measurements |
| `min_ext_below_pct` | `5.0` | wedge **pop** gate |
| `min_ext_above_pct` | `5.0` | wedge **drop** gate |
| `reversal_min_ext_pct` | `10.0` | reversal **extension** gate |
| `exhaustion_min_ext_pct` | `10.0` | exhaustion **extension** gate |
| `crossback_low_tol` | `0.005` | how deep a crossback "touch" may pierce the EMA |
| `crossback_reset_pct` | `0.0` | separation needed to re-arm a crossback |
| `crossback_ema_rising_lookback` | `5` | lenient "rising EMA" window (`0` = off) |
| `cycle_count_months` | `12` | window for `*_count` (wedge + exhaustion/reversal) |
| `both_within_bars` | `10` | max gap for "reclaimed both EMAs" |
| `vol_avg_window` | `20` | trailing window for volume ratios |
| `reversal_window_bars` | `40` | max bars a reversal may precede the pop for `kell_uptrend_from_reversal` |
| `trend_slope_window` | `5` | bars over which the `trend_state` ref-EMA slope is measured |
| `trend_flat_band_pct` | `1.5` | \|slope\| within this = flat = **basing** |
| `trend_pivot_window` | `5` | ±bars for a confirmed swing-high pivot (the "last high") |

---

## Fidelity notes vs. Oliver Kell

- Kell gives **no fixed %** for these; his signals are qualitative (reclaim the
  MAs with volume after a downtrend; a *substantial* oversold gap for a reversal
  extension). Our thresholds are **pragmatic, tunable proxies**, not his numbers.
- Kell typically measures extension off the **10-EMA**; we use `ext_ref_ema`
  (20) for the *gate* but still record depth vs each EMA (`*_below_10ema_pct`).
- The crossback is a **single-bar** touch-and-hold; Kell's is a discretionary
  "consolidate into the EMA and resume," sometimes with a reversal candle.
- No-lookahead: all extensions/episodes/EMAs use only delivered bars; volume
  ratio uses a trailing average.

---

_Last updated to match `kell.py` + companion providers as of session 3: added
extension counts (§7), per-run counts (§8), the uptrend state machine (§9), and
the companion-provider features (§10–15: higher-timeframe extension, trend-state/
base gate, volume signature, relative strength, QQQ regime, revenue). Sections
0–9 are `kell.py`; 10–15 point to their own modules._
