# Feature Audit — terminology consistency & column inventory

_A living inventory of every column in the pooled setup dataset (212 cols as of
the latest rebuild), grouped by the provider that emits it, with the **trend
definition** each one uses. Purpose: (1) make "trend" mean ONE thing, (2) find
duplicates/dead columns to prune, (3) reconcile the scattered "higher-high" and
"uptrend" definitions. Pairs with `KELL_DEFINITIONS.md` (metric-level defs) and
`HANDOFF.md` (findings)._

---

## 0. The canonical decision

**"Trend" = the Kell 3-state `trend_state` / `weekly_trend_state`** (ref-EMA(20)
5-bar slope ±1.5% flat band + confirmed swing high). This is THE trend vocabulary.
Everything else that currently reads like "trend" is really one of: **structure**
(higher-highs/lows), **MA-position** (above/below an MA), **momentum** (trailing
returns / RS), or **market regime** (SPY/QQQ). Those are inputs, not "the trend"
— and should be named so they can't be mistaken for it.

Counts that describe "where in the cycle are we" (`exh_since_downtrend`, etc.)
**must anchor to `trend_state`**, never to a second trend notion.

---

## 1. The five trend definitions found (the consistency problem)

| # | name | columns | exact basis | verdict |
|---|---|---|---|---|
| **1** | **Kell 3-state** (canonical) | `trend_state`, `weekly_trend_state` (+`_slope_pct`) | ref-EMA(20) slope over 5 bars, ±1.5% flat = basing; uptrend needs slope>+1.5% AND close>EMA AND close≥last swing high; downtrend = slope<−1.5% | **KEEP — the single definition** |
| **2** | **Kell 2-state wedge machine** | `kell_in_uptrend`, `kell_uptrend_bars/legs/from_reversal`, `exh_count_since_uptrend`; weekly twins `weekly_in_uptrend`, `weekly_uptrend_legs/from_reversal/weeks`, `weekly_exh_since_uptrend` | up when close>refEMA AND EMA strictly rose (Wedge Pop start → EMA-rollover end); a new pop while up = a Base-n'-Break leg | **DEMOTE** — keep only `legs`/`from_reversal` (unique); re-anchor or drop the `_since_uptrend` counts |
| **3** | **Minervini Stage-2 template** | `s2_above_150_200`, `s2_sma150_above_200`, `s2_sma200_rising`, `s2_higher_highs_lows`, `s2_up_week_volume`, `s2_more_up_weeks`, `stage2_pass_count` | 150 & 200 SMA relationships + confirmed HH/HL (±10-bar pivots) + weekly volume | **KEEP as `stage2_*`** (a named template, not "trend"); drop `s2_sma200_rising` (dup of #4) |
| **4** | **Structure / MA-slope** (the `TrendState` provider — misleadingly named) | `above_50sma_ts`, `above_200sma_ts`, `sma50_slope_pct`, `sma50_rising`, `sma200_slope_pct`, `sma200_rising`, `pct_from_252d_high`, `bars_since_252d_high`, `making_higher_high`, `making_higher_low`, `higher_highs_count`, `lower_highs_count`, `uptrend_structure` | 50/200-SMA slope + ±5-bar swing pivots; `uptrend_structure` = last-2 swing highs ↑ AND last-2 swing lows ↑ | **KEEP but RENAME** provider→`structure`; drop `_ts` dups; pick ONE HH/HL |
| **5** | **MA-position** (`MovingAverageStack`) | `above_10ema`, `above_20ema`, `above_50sma`, `above_200sma`, `above_all_mas`, `close_ext_*`, `ma_coil_spread_pct`, `coiled_up` | price vs each EMA/SMA at the signal bar | **KEEP** — this is "position", not "trend" |
| (regime) | Market regime (SPY + QQQ) | `mkt_ret_{1,3,6,12}m` (SPY), `qqq_ret_*`, `qqq_above_20ema`, `qqq_above_50sma`, `qqq_ext_20ema_pct` | index returns / index vs its own MAs | **KEEP** — it's the index, fine; two proxies (SPY+QQQ) is mild redundancy |

**The core inconsistency to fix:** the exhaustion "stage" counts mix #1 and #2 —
`exh_since_downtrend/base` (+weekly) anchor to the **3-state** ✅, but
`exh_count_since_uptrend` / `weekly_exh_since_uptrend` anchor to the **2-state** ❌.

---

## 2. The Kell state machine — what it emits and the logic

Two machines live in `kell.py`, both off the reference EMA (20); wedge pop/drop are
also tracked per EMA in `ema_periods=(10,20)`.

### 2a. Wedge Pop / Drop / Crossback (per EMA — 10 and 20)
- **Wedge Pop** (`kell.py:158-176`): fires when close crosses from **below to above**
  the p-EMA, *provided* the preceding below-run reached ≥ `min_ext_below_pct` (5%)
  below the ref EMA. No fixed "N days below". Records `(idx, bars_below, date,
  max_ext_below, vol_ratio)`.
- **Wedge Drop** (`kell.py:180-183`): mirror — close crosses **above to below** the
  p-EMA after an above-run ≥ `min_ext_above_pct` (5%) above the ref EMA.
- **EMA Crossback** (`kell.py:189-195`): count of rising-EMA support retests since the
  last pop (price dips to touch the EMA and closes back above, EMA rising).
- **Columns** (per `{10,20}ema`): `wedge_pop_date/bars_since/bars_below/max_ext_below/
  vol_ratio/count`, `wedge_drop_date/bars_since/bars_above/max_ext_above/vol_ratio/
  count`, `crossback_count`; plus `wedge_pop_both_bars_since`, `wedge_drop_both_bars_since`.

### 2b. Reversal / Exhaustion extension episodes (single, vs ref EMA)
- **Exhaustion** = max extension **above** the ref EMA during the current above-episode,
  once it reaches ≥ `exhaustion_min_ext_pct` (10%). **Reversal** = mirror below
  (capitulation, ≥ `reversal_min_ext_pct` 10%).
- An episode is **locked** (counted) only when price closes back across the ref EMA
  (`kell.py:200-202`); the in-progress episode feeds the *magnitude* columns but is NOT
  yet counted. _(This is the "open extension reads 0" behavior — being changed.)_
- **Columns**: `exhaustion_high_{date,price,bars_since,vol_ratio,ext_pct,pct_below,
  above_10ema_pct,above_20ema_pct}`, `reversal_low_{…,below_10ema_pct,below_20ema_pct}`,
  `exhaustion_high_count`, `reversal_low_count` (12-month rolling windows).

### 2c. The 2-state uptrend machine (`_up_state`, `kell.py:114-175`)
- STARTS at a Wedge Pop on the ref EMA (ideally after a Reversal Extension); a new pop
  while already up = a Base-n'-Break **leg** (`kell_uptrend_legs++`); ENDS when close<EMA
  AND the EMA turned down.
- **Columns**: `kell_in_uptrend`, `kell_uptrend_bars`, `kell_uptrend_legs`,
  `kell_uptrend_from_reversal`, `exh_count_since_uptrend` (+ weekly twins).
- **This is trend definition #2** — kept only because `legs` / `from_reversal` have no
  3-state equivalent. Everything else here duplicates `trend_state`.

### 2d. The 3-state trend + stage counts (`trend_state()`, `kell.py:240-264`)
- `trend_state`, `trend_slope_pct` (+ weekly). Stage counts anchored to it:
  `exh_since_downtrend` (accumulates through bases, resets on downtrend = Kell's
  1st/2nd/3rd extension), `exh_since_base` (resets each base). **This is #1 — canonical.**

---

## 3. Duplicate columns — delete one of each

| keep | delete | evidence |
|---|---|---|
| `above_50sma` | `above_50sma_ts` | **100% identical** (113,393 overlap) |
| `above_200sma` | `above_200sma_ts` | **100% identical** (109,453 overlap) |
| `sma200_rising` | `s2_sma200_rising` | **99.9% identical** — same "is the 200-SMA rising" |
| (pick one HH/HL — see §4) | the other two | `s2_higher_highs_lows` vs `uptrend_structure` agree only 70% |

---

## 4. "Higher high / higher low" — three different definitions

All three build swing pivots then compare, but with **different pivot windows**, so
they disagree:

| column(s) | source | pivot window | rule |
|---|---|---|---|
| `s2_higher_highs_lows` | `stage2.py` | **±10 bars** | last-2 swing highs ↑ AND last-2 swing lows ↑ |
| `uptrend_structure` | `trend_state.py` | **±5 bars** | last-2 swing highs ↑ AND last-2 swing lows ↑ (same rule, tighter pivots) |
| `making_higher_high` / `making_higher_low` | `trend_state.py` | **±5 bars** | each leg separately (highs only / lows only) |
| `higher_highs_count` / `lower_highs_count` | `trend_state.py` | **±5 bars** | net up/down steps among last 4 swing highs |
| (`trend_state`'s own swing high) | `kell.py` | **±5 bars, highs only** | `_last_swing_high` — used inside the 3-state uptrend test |

`s2_higher_highs_lows` and `uptrend_structure` are the *same rule* at ±10 vs ±5 → the
30% disagreement is purely pivot sensitivity. **Recommendation:** keep ONE HH/HL
definition (±5 `uptrend_structure`, to match the pivot window `trend_state` already
uses), retire `s2_higher_highs_lows`, and keep `making_higher_high/low` +
`higher_highs_count/lower_highs_count` as the finer-grained structure reads (same ±5).

---

## 5. Rename proposals (terminology alignment)

The most confusing collision: the provider **class named `TrendState`** does **not**
emit the `trend_state` column (that comes from `KellCycle`). It emits structure/MA
features. Rename it so "trend" means only #1.

| current | proposed | why |
|---|---|---|
| provider `TrendState` (`trend_state.py`) | `Structure` / `structure.py` | it produces structure + MA-slope, NOT the `trend_state` column |
| `above_50sma_ts`, `above_200sma_ts` | — (delete, dup) | see §3 |
| `sma50_slope_pct`, `sma50_rising`, `sma200_slope_pct`, `sma200_rising` | `struct_sma50_slope_pct`, … | mark as structure, keep the one canonical "rising" |
| `uptrend_structure` | `struct_higher_highs_lows` | it's a structure read, not "the uptrend" |
| `making_higher_high/low`, `higher_highs_count`, `lower_highs_count` | `struct_*` prefix | group under structure |
| `kell_in_uptrend`, `kell_uptrend_*`, `exh_count_since_uptrend` | keep name but **document** as the 2-state machine (distinct from `trend_state`) | avoid confusion with #1 |
| `ma_type` value `21ema` | reconcile to `20ema` | the Kell ref EMA is 20, not 21 |

_(Renames are a rebuild-time change — they ripple into the dataset columns. Batch them
into the next `min_dollar_volume` rebuild rather than a standalone pass.)_

---

## 6. Dead / near-constant columns

| column | state | action |
|---|---|---|
| `exh_since_base`, `weekly_exh_since_base` | **100% zero** today | KEEP — becomes live once the open-episode counting lands (will read 1 during an active extension in an uptrend) |
| `price_mode`, `stop_type`, `ma_type` | constant config | exclude from the feature set (not features) |
| `weekly_in_uptrend/legs/from_reversal/weeks/exh_since_uptrend` | 52% null (2-state only emits when "up") | candidate retire — redundant with `weekly_trend_state` (HANDOFF §5d) |
| revenue_*, eps_* | 58–66% null (XBRL ~nil pre-2015) | KEEP — holdout-only, known |

---

## 7. Action checklist (for the next rebuild)

1. ✅ **DONE (§5e):** count the **open/in-progress** qualified exhaustion in
   `exh_since_*` (daily+weekly) — all `exh_since_*`/`*_count` now include the live
   episode; `exh_since_base` is now live (was 100% zero). Also added
   `prev_trend_state`/`weekly_prev_trend_state`.
2. **Re-anchor** `exh_count_since_uptrend` / `weekly_exh_since_uptrend` to the 3-state
   `trend_state` (or drop them) — all stage counts on ONE trend. _(still open)_
3. Delete duplicates (§3); pick one HH/HL (§4). _(still open — do at rename time)_
4. Rename the `TrendState` provider → `Structure` and prefix its columns (§5). _(open)_
5. ✅ Keep `exh_since_base` (went live post-#1).
6. Reconcile `ma_type` label 21→20. _(open)_

_Also landed §5f: the weekly trend is independently tunable
(`weekly_ext_ref_ema`/`weekly_trend_slope_window`/`weekly_trend_pivot_window` on
`StageRangeStrategy`); the validated challenger is 10/3/5 (`setups_all_wk1035`)._
