# Stage Analysis — Specification

A living document. Treat this as the source of truth for rules; `stage_analysis.py` is the source of truth for implementation. Update both together.

Last updated: 2026-05-01

---

## 1. Goal

Build a weekly **Stage Analysis state machine** (Stan Weinstein-style, with documented divergences) that, given a stock's weekly OHLC history, tells us which stage (0/1/2/3/4) the stock is in at any past or current week.

---

## 2. Inputs / Outputs

### Input
- `weekly: pd.DataFrame` — columns `Open, High, Low, Close, Volume`, weekly `DatetimeIndex`. Caller resamples daily → weekly.

### Output
- `compute_stage_analysis(weekly, **config) -> StageAnalysis`
- `StageAnalysis.state_at(date) -> StageSegment`
- `StageAnalysis.to_dict()` — serializable view

### Public dataclasses
- `WeeklyPivot` — one confirmed pivot (date, price, kind, week_index, is_confirmed)
- `StageSegment` — one contiguous span in a given state
- `StageAnalysis` — top-level container (segments, pivots, weekly metrics, RS)
- `RelativeStrength` — computed but not used by the state machine

---

## 3. Core Variables (all weekly)

| Var | Definition |
|-----|------------|
| `C` | Weekly Close |
| `H`, `L` | Weekly High / Low (wicks) |
| `SMA30` | 30-week SMA of `C` |
| `Slope30` | % change of `SMA30` over last `slope_lookback` (default 5) weeks: `(SMA30[t] / SMA30[t-5] - 1) × 100`. Categorised as `Rising` (> +2%), `Flat` (±2%), `Declining` (< -2%). Threshold `slope_flat_pct = 2.0` is configurable. |
| `PivotLow` | **Two-sided** swing low. Week t is a PivotLow if `L[t] ≤ L[t-k]` for k=1..window (lookback non-strict, ties allowed) and `L[t] ≤ L[t+k]` for k=1..window (lookforward non-strict, ties allowed). A week is only disqualified if a surrounding week is **strictly** lower. This allows double-bottom retests at the same price to register as a second pivot. Default `pivot_window = 5`. Tail pivots (last 5 weeks of data) are flagged `is_confirmed=False`. |
| `PivotHigh` | Same logic mirrored on `H`. |
| `Stage2_Line` | Linear `y = m·x + b` fit through the **strictly-ascending subsequence** of in-Stage-2 PivotLows (the rising lower envelope). Refit on every new pivot low. With 2 anchors → exact line from endpoints with colinear-tail selection. With ≥3 anchors → endpoints + colinear midpoints (within `trendline_tolerance_pct` of the endpoint line). With <2 anchors → line undefined; trendline-break trigger cannot fire. |

---

## 4. The State Machine

Initialize `state = 0`. Walk forward week-by-week. Evaluate triggers **in state order**; only the first firing trigger matters per week.

---

### State 0 → 2 (direct breakout, no Stage 1 base)

A stock can break out directly from Stage 0 into Stage 2 without forming a Stage 1 base. This captures V-shaped recoveries and stocks re-emerging from long declines on institutional volume.

**Trigger (all required):**
- Most recent confirmed `PivotHigh` exists (serves as the reference ceiling), **AND**
- `C ≥ PivotHigh × (1 + breakout_buffer_pct/100)`, **AND**
- Volume confirmed (spike or step path — same logic as S1→2).

**No SMA30 or slope condition.** The stock may not yet have 30 weeks of history. Volume is the sole confirmation gate and is mandatory (a low-volume pop above a prior high from Stage 0 is a dead-cat, not a stage change).

**Action on entry:**
- `s2_entry_ref_ceiling` = price of the most recent confirmed `PivotHigh`.
- Seed Stage 2 with the most recent confirmed `PivotLow` (if any).
- No `prior_s1_snapshot` (Stage 1 was skipped; there is nothing to revert to).

**Note:** Only applies to Stage 0 (`st == 0`). Stage 4 cannot transition directly to Stage 2 — a Stage 1 base is required after a Stage 4 decline.

---

### State 0 / 4 → 1

**Trigger:** a new confirmed `PivotLow` whose price ≥ the immediately prior confirmed `PivotLow` (a "higher pivot low").

**Action on entry:**
- `Stage1_Floor` = price of the prior `PivotLow` (the bottom of the preceding down-leg).
- `Stage1_Ceiling` = `None` (built up over the base — see Maintenance below).

**No slope condition on entry.** SMA30 may still be Declining when Stage 1 begins; that is normal.

---

### State 1 — Accumulation

**Failed S4→1 revert:**
- When Stage 4 → 1 fires, a snapshot of the Stage 4 state and the price of the entry pivot low (`s1_entry_pivot_low`) are saved on the Stage 1 working memory.
- On each subsequent confirmed PivotLow in Stage 1, three outcomes are possible:

| New PivotLow price | Action |
|---|---|
| `< Stage1_Floor` | **Revert to Stage 4.** The new low broke below the base floor — the stock is genuinely still declining. Stage 1 erased, Stage 4 restored from snapshot. |
| `≥ s1_entry_pivot_low` | **Stage 1 confirmed.** Ascending lows verified. Snapshot cleared; revert no longer available. |
| Between `Stage1_Floor` and `s1_entry_pivot_low` | **Normal Stage 1 dip.** Do nothing. Revert remains available but does not fire. |

- The revert checks against `Stage1_Floor` (the prior Stage 4 low that anchored the base), not the entry pivot low. This prevents false reverts when the entry pivot jumped far above the floor (e.g. a 90% bounce) and a small pullback still well above the floor is mistaken for a dead-cat.
- This check only applies when Stage 1 was entered from Stage 4 (not from Stage 0, where there is no prior Stage 4 to revert to).

**Maintenance — Stage1_Ceiling:**
- `Stage1_Ceiling` tracks the **running max of all confirmed PivotHighs** seen since Stage 1 started.
- It updates upward freely on each new higher PivotHigh. Each update records the week index of that pivot high (`Stage1_Ceiling_Week`).
- It does **not** lock or freeze on a lower PivotHigh or on an intervening PivotLow. The ceiling always represents the true Stage 1 high.
- **Staleness:** when a new confirmed PivotHigh arrives, if the pivot high that last set the ceiling is older than `s1_ceiling_max_age_weeks` (default 78 weeks ≈ 1.5 years), the new pivot high **replaces the ceiling unconditionally** — even if it is lower. The old resistance is no longer relevant; the most recent swing high defines the current base ceiling. The check only fires on pivot high arrival, not every week.

**Stage 4 trigger (failed accumulation):**

`C < Stage1_Floor` → transition to Stage 4 (`breakdown_below_stage1_floor`). No volume requirement — a close below the base floor is structurally sufficient to signal the accumulation has failed.

**Stage 2 trigger (either path fires):**

**(A) Pivot-derived ceiling breakout:**
- `Stage1_Ceiling` is set (at least one PivotHigh has formed), **AND**
- `C ≥ Stage1_Ceiling × (1 + breakout_buffer_pct/100)`, **AND**
- `C > SMA30`, **AND**
- `Slope30 = Rising`, **AND**
- Volume confirmed (either path below).

**(B) Prior S2 max-high breakout (fallback):**
- Used **only** when `Stage1_Ceiling` is `None` (no PivotHigh has formed in Stage 1 yet), **AND**
- `C ≥ prev_s2_max_high × (1 + breakout_buffer_pct/100)` (where `prev_s2_max_high` is the max weekly High of the most recently completed Stage 2), **AND**
- Same `C > SMA30`, `Slope30 Rising`, volume conditions as (A).
- Catches cases like a gap-up breakout before any base pivot high has confirmed.

**Volume confirmation — two paths (either sufficient). Volume is always required; there is no price-based override.**

| Path | Condition |
|------|-----------|
| Spike | `Volume[t] ≥ vol_mult × vol_avg` — single exceptional week (default 1.5×). `vol_avg` is computed over the prior `vol_lookback` weeks, excluding the breakout week itself. |
| Step | `Volume[t] ≥ vol_step_mult × vol_avg` (default 1.3×) **AND** `Volume[t] > Volume[t-1] > Volume[t-2]` — three consecutive weeks of rising volume, identifying institutions spreading their buys. |

The same two-path logic applies to the Stage 3 → 2 recovery breakout.

**Lower-pivot-low triggers (Option A — no descending lows allowed in Stage 2):**

When a new confirmed PivotLow arrives in Stage 2 and it is **lower** than the prior PivotLow in `s2_pivot_lows`:

| Condition | Trigger |
|-----------|---------|
| First pivot low in Stage 2 (only seed present) **AND** `pl < s2_entry_ref_ceiling` | `failed_breakout_lower_low` — low fell back into prior stage's trading range |
| First pivot low in Stage 2 (only seed present) **AND** `pl ≥ s2_entry_ref_ceiling` | `lower_pivot_low` — lower low but still above breakout level → new Stage 3 |
| Subsequent pivot low (Stage 2 had ≥1 confirmed higher low) | `lower_pivot_low` → new Stage 3 |

`s2_entry_ref_ceiling` is the reference ceiling used to fire the Stage 2 breakout: the Stage 1 ceiling (path A) or prior S2 max-high (path B) for S1→2; the Stage 3 ceiling or prior S2 max-high for S3→2.

**Note:** Since any lower pivot low exits Stage 2 before being appended, all PivotLows in `s2_pivot_lows` are guaranteed to be strictly ascending. The `_ascending_lows` filter is therefore redundant within Stage 2; the trendline is fit through all accumulated pivot lows directly.

**Failed-breakout revert (Stage 2 → back to Stage 1):**
- When Stage 1 → 2 fires, a snapshot of the Stage 1 state is saved.
- Stage 2 is "unconfirmed" until its max weekly High exceeds the original Stage 1 ceiling by `s2_confirmation_pct` (default 10%), OR a confirmed PivotLow forms above the Stage 1 ceiling.
- If a `failed_breakout_lower_low` or `trendline_break` trigger fires **before** Stage 2 confirms, the failed Stage 2 is erased: the Stage 1 segment is restored with its ceiling raised to the max High reached during the failed Stage 2, and we continue in Stage 1 from that week. The raised ceiling is **not locked** — it keeps tracking higher PivotHighs.

**Failed-breakout revert (Stage 2 → back to Stage 3):**
- When Stage 3 → 2 fires, a snapshot of the Stage 3 state is saved.
- Stage 2 is "unconfirmed" until its max weekly High exceeds the Stage 3 reference ceiling by `s2_confirmation_pct` (default 10%), OR a confirmed PivotLow forms above the Stage 3 reference ceiling.
- If a `failed_breakout_lower_low` or `trendline_break` trigger fires **before** Stage 2 confirms, the failed Stage 2 is erased: the Stage 3 segment is restored with its ceiling raised to the max High reached during the failed Stage 2, and we continue in Stage 3 from that week.

---

### State 2 — Advancing

**Trendline seeding on entry:**

When Stage 2 is entered from Stage 1 or Stage 3, the initial `Stage2_Line` is seeded immediately from historical pivot lows rather than waiting for a second in-Stage-2 pivot low to form.

- **S1→2:** All confirmed PivotLows from the entire Stage 1 period (`s1_start_idx` to breakout week) are collected, plus one seed anchor from the last confirmed PivotLow before Stage 1 began (the Stage 4/0 bottom). These represent the ascending support built during the base.
- **S3→2:** All confirmed PivotLows from the entire Stage 3 period (`s3_trigger_idx` to breakout week) are collected, plus one seed anchor from just before Stage 3 began. These capture the ascending support during the Stage 3 consolidation — including weeks where the stock was already above the S3 ceiling but volume had not yet confirmed.

In both cases: the collected lows are filtered through `_ascending_lows` (keep strictly ascending subsequence) then `_select_colinear_anchors` (keep the largest colinear tail within `trendline_tolerance_pct`). The resulting trendline is set immediately on Stage 2 entry.

The initial trendline is typically flat or shallow (Stage 1/3 lows are horizontal support). As Stage 2 progresses and new higher PivotLows arrive, the trendline steepens. `_select_colinear_anchors` naturally drops older flat anchors that no longer fit the new rising slope.

**Why this matters:** Without seeding, Stage 2 starts with a single anchor and no trendline. A sharp crash during Stage 2 (e.g. an exogenous shock) has no trendline to break — the crash sits invisibly inside Stage 2 until a confirmed PivotLow eventually forms weeks later. With seeding, a trendline exists from day one, catching early breakdowns immediately.

**Maintenance:**
- Every confirmed PivotLow in Stage 2 is appended to `s2_pivot_lows`.
- `Stage2_Line` is refit after each new pivot low using the ascending-lows subsequence + colinear-anchor selection.
- `s2_max_high` tracks the running max of all weekly Highs in this Stage 2 (used by the retroactive collapse check in Stage 3).

**Stage 3 triggers:**
- **(B) Trendline break:** `C` closes below the projected value of `Stage2_Line` at week t. Checked **before** incorporating this week's pivot data (a new pivot low that simultaneously breaks the prior line is a distribution signal, not an anchor).

*(Trigger A — failed higher high — disabled; fired too eagerly. Trigger C — SMA flat oscillation — removed; fired immediately after retroactive collapses and on legitimate continuation bases, producing rapid S2→S3→S2 cycles.)*

---

### State 3 — Distribution

**Action on entry:**
- Snapshot the prior Stage 2 working state (`s2_max_high`, pivot lows, trendline) for the retroactive-collapse mechanism.
- `Stage3_Floor`: wait for the **first confirmed PivotLow after the trigger week**. Until it is set, the floor-based Stage 4 trigger cannot fire. If the trigger week itself is a confirmed PivotLow, that low becomes the floor immediately.
- `Stage3_Ceiling`: tracks the **running max of confirmed PivotHighs** that occur at or after `Stage3_Floor` is set. Stays `None` until the first such PivotHigh confirms. When `None`, the S3→2 recovery check uses the prior S2 max-high as a fallback reference (see below).

**Retroactive collapse → Stage 2 (trendline-too-aggressive guard):**

On entering Stage 3, a snapshot of the prior Stage 2 (`s2_max_high`, pivot lows, trendline) is saved. While in Stage 3, we watch for the **first** confirmed PivotHigh (before any S3 ceiling is set). If at that point **both** of the following hold, the Stage 3 is erased and merged back into the continuing Stage 2:

- **Higher high:** first confirmed S3 PivotHigh ≥ `s2_max_high × (1 + collapse_min_pct/100)`
- **Higher low:** `Stage3_Floor is None` (no pivot low formed in Stage 3 — the stock never even made a lower low, the strongest possible continuation signal) **OR** `Stage3_Floor ≥ last confirmed S2 PivotLow × (1 + collapse_min_pct/100)`

The higher-high condition must clear `collapse_min_pct` (default 5%) above `s2_max_high`. The higher-low condition passes automatically when `Stage3_Floor` is `None`.

**Both required.** Either condition alone is not enough — a higher high with a confirmed lower low means the stock expanded its range (could still be distribution); a higher low without a higher high means a failed rally. Only when both pass is the trendline definitively wrong.

**After collapse:** the S3 PivotLow is now an ascending anchor in the merged pivot-low sequence. The trendline is rebuilt immediately from the full history including this new anchor, producing a flatter, more honest slope. No need to clear and wait for the next pivot.

**Later S3 pivot highs do not trigger collapse.** Only the first confirmed S3 PivotHigh (before `Stage3_Ceiling` is set) is checked. If it doesn't satisfy both conditions, `Stage3_Ceiling` is set from it and the stock remains in an established Stage 3. This is Weinstein's "continuation base" vs genuine distribution distinction.

**Stage 3 → 2 (recovery):**

Fires when:
- `C ≥ ref_ceiling × (1 + breakout_buffer_pct/100)`, **AND**
- `C > SMA30`, **AND**
- `Slope30 = Rising`, **AND**
- `Volume ≥ vol_mult × vol_avg`

Where `ref_ceiling` is:
- `Stage3_Ceiling` if at least one confirmed S3 PivotHigh exists, otherwise
- Prior `s2_max_high` (fallback when no S3 pivot high has formed yet — e.g. the stock broke above the S2 level before any Stage 3 swing high was confirmed).

**Stage 3 → 4 (breakdown):**

Only fires when `Slope30 ≠ Rising` (a Rising SMA30 means the underlying trend is healthy; dips are shakeouts, not breakdowns). Either:
- **(A)** `C < Stage3_Floor`, **or**
- **(B)** `C < SMA30` **and** `Slope30 = Declining`.

---

### State 4 — Declining

**Entry:** from Stage 3 only (see Stage 3 → 4 above).

**False-breakdown revert → Stage 3:**
- On entering Stage 4, the Stage 3 working state (floor, ceiling, all prior context) is snapshotted.
- If within `s4_revert_window` weeks (default 5) of the breakdown, the revert condition is met, the breakdown is treated as a shakeout: the Stage 4 segment is erased, Stage 3 is restored verbatim (floor and ceiling unchanged), and the state machine continues in Stage 3.
- After the window expires the revert is no longer available; Stage 4 stands.

Revert condition depends on the entry trigger:

| Entry trigger | Revert condition |
|---|---|
| `breakdown_below_stage3_floor` (A) | `C ≥ Stage3_Floor` |
| `breakdown_below_sma_declining` (B) | `C ≥ Stage3_Floor` **AND** `C > SMA30` |

For SMA-based breakdowns both conditions are required — recovering above the floor alone is insufficient if the stock is still below a declining SMA.

**Maintenance:** lower-low sequence. Exit via the State 0/4 → 1 trigger (higher pivot low).

**Stage 4 cannot transition directly to Stage 2.** A Stage 1 base must form first.

---

## 5. Transition Summary

```
0 ──vol breakout above PivotHigh──► 2   (direct, no Stage 1 base)
0 ──higher PivotLow──────────────► 1
4 ──higher PivotLow──────────────► 1
1 ──vol breakout──────────────────► 2
2 ──trendline break───────────────► 3
3 ──vol breakout──────────────────► 2   (recovery before floor breaks)
3 ──floor/SMA break───────────────► 4   (only if Slope30 ≠ Rising)
4 ──within 5w revert─────────────► 3   (false breakdown cancel)
```

Special reversions:
- Stage 2 → Stage 1 (failed-breakout revert, unconfirmed Stage 2 only)
- Stage 2 → Stage 3 (failed-breakout revert, unconfirmed S3→2 only)
- Stage 3 → Stage 2 (retroactive collapse, trendline-too-aggressive)

---

## 6. Configurable Parameters

| Param | Default | Purpose |
|-------|---------|---------|
| `pivot_window` | 5 | Weeks each side for two-sided pivot detection |
| `slope_lookback` | 3 | Weeks for SMA30 slope calc |
| `slope_flat_pct` | 1.0 | abs(slope%) ≤ this → Flat |
| `breakout_buffer_pct` | 1.0 | Required % above ceiling for Stage 1→2 and S3→2 breakouts |
| `vol_mult` | 1.5 | Volume multiple for spike path — single week ≥ 1.5× prior 10w avg |
| `vol_step_mult` | 1.3 | Volume multiple for step path — ≥ 1.3× AND vol[t] > vol[t-1] > vol[t-2] |
| `collapse_min_pct` | 5.0 | Both higher-high and higher-low must exceed their Stage 2 reference by at least this % for a retroactive collapse to fire |
| `s1_ceiling_max_age_weeks` | 78 | Stage 1 ceiling resets to None if the pivot high that set it is older than this many weeks (~1.5 years) |
| `vol_lookback` | 10 | Weeks to average volume over (baseline for all vol paths) |
| `osc_window` | 4 | Weeks used in Stage 3 trigger C (SMA oscillation) — trigger C removed; param unused |
| `trendline_tolerance_pct` | 5.0 | Max % deviation of a middle anchor from the endpoint line to be considered colinear |
| `s2_confirmation_pct` | 10.0 | Stage 2 is unconfirmed until its max High exceeds original S1/S3 reference ceiling by this %. Failed-breakout revert available until confirmation. |
| `s4_revert_window` | 5 | Weeks after S3→4 breakdown in which a close above S3 floor cancels the breakdown |

---

## 7. Relative Strength (auxiliary)

Tracked but **not used** by the state machine. `RelativeStrength` class computes: weekly RS line (stock / benchmark, re-based to 100), RS slope over last 5 weeks, whether RS is making new highs. Exposed on `StageAnalysis.relative_strength`.

---

## 8. Open Questions

1. **Volume averaging inflates with trend.** `vavg` is a 10w rolling average (prior weeks only, breakout week excluded). As price trends up and attracts more volume, the required threshold rises too. Consider anchoring `vavg` to a fixed pre-breakout window in a future revision.
2. **Bootstrapping State 0 — partially addressed.** `S0→S2` direct transition now catches V-shaped recoveries and fast breakouts that skip Stage 1 entirely. Remaining gap: stocks that drift gradually out of Stage 0 on low volume still wait indefinitely for a Stage 1 trigger.
3. **RS confirmation as Stage 2 gate.** Tracked separately; could be promoted to a required condition.
4. **Pivot window tuning.** `pivot_window = 5` may cause Stage 3 to start later than the natural top. `pivot_window = 4` catches entries faster but adds noise. Worth testing on a broader universe.
5. **Stage 2 seed trendline flatness.** The seeded trendline from Stage 1/3 pivot lows is initially flat (horizontal support). If the stock pulls back sharply immediately after the breakout before any Stage 2 pivot low forms, the flat line may exit Stage 2 prematurely. Monitor for false exits caused by the seed trendline in strong immediate advances.
6. **SMA30 slope lookback sensitivity.** The slope is computed as `(SMA30[t] / SMA30[t-5] - 1) × 100` over a fixed 5-week lookback (`slope_lookback`). This can be too slow to react — a SMA that has clearly flattened or turned in recent weeks may still read as `Rising` because the 5-week window includes older rising data. Observed case: S3→4 floor break blocked because `sc=Rising` (5w lookback), but a 2–3 week lookback would have read `Flat`. The right lookback length is unclear — shorter is more reactive but noisier; longer is smoother but lags real turns. Worth testing multiple lookbacks (2, 3, 5w) and possibly using a slope-of-slope or angle-based measure instead of a simple % change. The `slope_flat_pct` threshold (currently 1.0%) is a second tuning knob that interacts with this.

---

## 9. Out of Scope

- Daily-resolution stage transitions (weekly-only).
- Sector / market-stage overlay.
- Backtesting performance of the stages.
- Modifying `RangeExpansionTrade` behavior.
