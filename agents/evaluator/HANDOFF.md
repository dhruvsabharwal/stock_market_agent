# Evaluator — Documentation

_Complete reference for the `agents/evaluator/` module: a leak-free backtest /
scan engine that records rich per-setup features and lets you swap the **buy**,
the **sell**, and the **analysis** independently. Covers the goal, architecture,
every file, the full feature catalog, data/caching, how to run, and gotchas.
(The ad-hoc ML/discovery analysis is intentionally not documented here.)_

---

## 0. The goal (the user's thesis)

**Find uptrends and stay on the ride; if there's no sign of an uptrend, don't
participate at all.** Concretely:
1. **Capture the meat of the big winners** — the current exit does NOT keep
   winners (gives back ~88% of the average peak). Fix the exit first.
2. **Then cut losses logically and avoid losers.**
3. **Evaluate what the high runners have that losers don't** (feature discovery).
4. Belief: not every stock runs — so **EPS growth** and **stock momentum**
   (1m/6m/12m returns) should identify uptrending names; and **when the broad
   market is in a downtrend, don't take positions** (a hypothesis to test).

---

## 1. Architecture — three independent axes (all pluggable)

- **Context** = ~105 cross-ticker-comparable features describing the world at
  the signal bar (all %/ratio/count, no absolute prices). Computed by provider
  classes in `strategy/`. Reusable across any buy/sell.
- **Buy** = the setup detector. Today: `RangeBreakoutStrategy` (tight box → ≥4%
  day-over-day expansion). Swap = new `Strategy` subclass.
- **Sell** = `ExitRule` in `engine/exits.py`. Swap freely; setups computed once,
  many exits applied cheaply (`collect_setups` → `apply_exit`).

No lookahead: features are as-of `signal_date` (= the day BEFORE `entry_date`;
entry fills at next open). Compute is **cache-only** (never hits the network);
all fetching happens in `data/ingest.py`.

---

## 1b. The engine — how it works (no lookahead by construction)

**The bar feed is the clock.** `engine/feed.py` yields `Bar`s strictly forward in
time; nothing downstream is handed the underlying frame, so a strategy can never
see the future — the bytes aren't there yet.

**Per-bar ordering (the guarantee).** For each bar T the runner does, in order:
1. `broker.on_bar(T)` — fill any order queued on T−1 at **T's open**; then check
   stops **intrabar** against T's low; mark equity at T's close.
2. `strategy.on_bar(T, ctx)` — the strategy observes the just-**closed** bar T
   (all of T's OHLC is known) and may queue orders → they fill at **T+1's open**.

So a decision made on bar T's close fills at T+1's open — realistic and
leak-free. `signal_date` = T, `entry_date` = T+1.

**Warm-up.** Bars before the sim window are fed with `ctx.enabled=False`: the
strategy/providers update their rolling state (MAs, pivots, buffers) but orders
are ignored, so indicators are primed without trading. `warmup_bars` auto-sizes
to the longest lookback (200-SMA, 24-month overhead, etc.).

**Two run modes:**
- **Single-position** (`runner.run_backtest`) — a portfolio: `broker` holds ONE
  position at a time; the strategy manages the entry + the sell-in-weakness exit;
  the broker enforces the stop and produces the equity curve. Used for the
  equity-curve eval and the equivalence test.
- **Scan** (`scan.run_scan`) — evaluate EVERY setup independently (overlaps
  allowed), the population for feature analysis. Split in two:
  `collect_setups(...)` runs the strategy in scan mode → one record per breakout
  (features + entry + stop hints), exit-independent; `apply_exit(setups, df,
  rule)` simulates each setup forward under an `ExitRule` (buys `notional`, $1000
  default; no price rounding). So you compute the ~105 features ONCE and try many
  sells cheaply.

**Fills & stops.** Market orders fill at the next open. A protective stop fires
intrabar when `low ≤ stop`, filling at `min(open, stop)` (gap-downs fill worse).
Stops are finalized at fill and clamped **below entry** (see gap-up-fade fix, §6).

**Exits** (`engine/exits.py`) are pluggable `ExitRule`s sharing a `PriceData`
helper that lazily caches indicators (`ema`, `sma`, `atr`, `below_ma`). Current
rules: `StopAndWeaknessExit` (default = the live sell), `TrailingStopExit`,
`SwingLowTrailExit`, `EmaTrailExit`.

## 2. Every file and its job (complete reference)

**`engine/` — the leak-free simulation core (no strategy knowledge)**
| file | job |
|---|---|
| `feed.py` | `BarFeed` — the clock; yields `Bar`s strictly forward in time within an optional [start,end] window. The no-lookahead chokepoint. |
| `records.py` | Core dataclasses: `Bar` (OHLCV), `Order`/`Fill`, and `TradeRecord` (the canonical ledger row: entry/exit, return, peak/MAE, `mae_before_peak`, + the feature snapshot). |
| `broker.py` | Single-position broker: fills queued market orders at the next open, monitors the protective stop intrabar, finalizes the stop below entry, tracks cash/position/equity, emits `TradeRecord`s. |
| `runner.py` | `run_backtest(df, strategy, …)` — drives feed→broker→strategy for one ticker (single-position portfolio); handles warm-up gating; returns `BacktestResult` (trades + equity curve). |
| `scan.py` | `collect_setups` (run strategy in scan mode → every breakout's features + stop hints, exit-independent) · `apply_exit` (simulate each setup forward under an `ExitRule`, overlaps allowed, `$notional` each) · `run_scan` (chains both with a default exit). The population builder for analysis. |
| `exits.py` | Pluggable **sell** logic. `ExitRule` ABC + `PriceData` (cached `ema`/`sma`/`atr`/`below_ma`). Rules: `StopAndWeaknessExit` (default = live sell), `TrailingStopExit`, `SwingLowTrailExit` (ratchet under swing lows — **the best exit, §5f**), `EmaTrailExit`, `ReactiveMomentumExit`, `ScaleOutSwingTrailExit` (swing-trail + %-giveback cap + sell-into-strength scale-out, §5f). |

**`strategy/` — the buy (setup detection) + all feature providers**
| file | job |
|---|---|
| `base.py` | `Strategy` ABC + `StrategyContext` (buy/sell/set_stop, warm-up `enabled` flag, `history` buffer); extension hooks `_pre_decision`/`_entry_allowed`/`_extra_features`; scan-mode signal recording. |
| `range_strategy.py` | `RangeBreakoutStrategy` — THE buy: streaming maximal-box detection + ≥`expansion_pct` breakout; records range features (length, height hl/oc, max up/down day, recent-N range, tight-day counts, expansion quality). On-bar port of the batch `find_ranges`, equivalence-tested. |
| `stage_range_strategy.py` | `StageRangeStrategy` — the production strategy: subclasses the range buy and wires **all** providers via the hooks; optional Stage-2 gate (`min_criteria`); lazy-loads EPS. Every knob is a flat constructor arg. |
| `stage2.py` | `Stage2TrendTemplate` — the 6 Minervini Stage-2 criteria, incremental. |
| `kell.py` | `KellCycle` — Oliver Kell "cycle of price action": wedge pop/drop, EMA crossback, reversal/exhaustion extension (defs in `KELL_DEFINITIONS.md`). |
| `ma_stack.py` | `MovingAverageStack` — price vs each EMA/SMA (`above_*`, `close_ext_*`), `above_all_mas`, `coiled_up`, coil spread. |
| `momentum.py` | `TrailingReturns` (stock 1/3/6/12-month returns) + `MarketReturns` (SPY benchmark returns) — trend + regime. |
| `overhead.py` | `OverheadSupply` — prior swing highs above the entry (nearest/highest, distances, `blue_sky`) per lookback window. |
| `tight_day.py` | `TightDay` — a bar whose open/close are within n%; used for tight-day counts in the box. |
| `examples.py` | `BuyAndHold`/`StopAtFivePct` — trivial strategies for the engine smoke test. |
| `_lab_imports.py` | Path-shim bridge to reuse pure helpers (`RangeExpansion`, `build_stop_loss`) from the batch lab in `../base_breakout_strategy`. |

**`data/` — caching + the one fetch step**
| file | job |
|---|---|
| `store.py` | Price cache (parquet/ticker): `load` (cache-only), `get` (cache-or-fetch), `fetch`, `normalise` (yfinance→OHLCV), `us_universe()` (all_tickers.txt + universe_extra.txt), `cached_tickers`. |
| `fundamentals.py` | Report-dated quarterly EPS cache: `load_eps` (cache-only), `get_eps` (fetch), `EpsHistory.growth_as_of` (point-in-time YoY/QoQ with sign-guards). |
| `ingest.py` | **THE fetch step** (only network I/O): prices + EPS + SPY for the universe, resume-safe, throttle circuit-breaker (default 20 consecutive fails). |
| `universe_extra.txt` | 600 extra US common-stock symbols (from NASDAQ/NYSE listings) merged into the universe. |

**`eval/` — performance metrics for a ledger**
| file | job |
|---|---|
| `metrics.py` | `ledger_df` (trades→DataFrame), `summarise` (win rate/expectancy/payoff/drawdown), `by_feature`, `max_drawdown`. |
| `run_eval.py` | CLI: run a registered strategy over a window → writes `trades.csv`/`summary.json`/`equity.csv` bundle to `eval/results/`. |

**`probabilities/` — the analysis layer**
| file | job |
|---|---|
| `screen.py` | `build_dataset` (pool `run_scan` across tickers), univariate `screen`/`detail`, `conditional` (stack filters), `_is_feature` (the clean cross-ticker feature filter). |
| `trend_eval.py` | **The interactive cohort harness (§5f/§5e).** `load()` (→ `setups_all_wk1035` + stage buckets + `investable` flag), `cohort(by, query, …)` — slice win-rate by any grouping/filter, ALWAYS train/holdout split, cols `sell_win`/`mom_win`/`reward8`. The tool for iterating entry screens. |
| `db/setups_all.{parquet,csv}` | The pooled setup dataset (one row/setup, features + outcomes). Rebuilt on universe change. |
| `schema.py`/`build_db.py`/`query.py` | Older conditional-table approach (bucketed lookup DB). Superseded by `screen` + `trend_eval` + ad-hoc models. |

**Top-level + tests**
| file | job |
|---|---|
| `rebuild_datasets.py` | **The durable, reproducible dataset builder.** `python -m agents.evaluator.rebuild_datasets {base|wk1035}` → `runs/setups_all{,_wk1035}.{parquet,csv}` (`base`=20/5/5 weekly, `wk1035`=10/3/5 = Kell's weekly 10-EMA). Row-aligned; only the weekly trend horizon differs. |
| `logging_util.py` | `set_verbose`/`verbose()` — toggle bar-by-bar SIGNAL/BUY/SELL logs (off by default). |
| `inspect.py` | CLI to diff on-bar vs the batch-lab oracle for one ticker. |
| `tests/test_range_equivalence.py` | On-bar port == batch `find_ranges` (MUST stay green after engine/strategy edits). |
| `tests/test_stage2.py` | Stage-2 criteria == pandas recompute. |
| `tests/test_engine_smoke.py` | Engine fill/equity sanity (buy-and-hold). |
| `example_usage.ipynb` | Interactive walkthrough (single-position + scan + feature slicing). |
| `HANDOFF.md` / `PLAN.md` / `README.md` / `KELL_DEFINITIONS.md` | This doc / original plan / quickstart / Kell metric spec. |

**Env:** use `.venv/bin/python`. Added deps: pyarrow, sklearn, nbconvert.

## How to run (all the processes)

_From the repo root, always `.venv/bin/python`. Compute is cache-only — run the
FETCH step first or features come back None._

**1. FETCH (the only network step)** — prices + EPS + SPY, resume-safe:
```bash
.venv/bin/python -m agents.evaluator.data.ingest                       # full universe
.venv/bin/python -m agents.evaluator.data.ingest $(tr '\n' ' ' < agents/evaluator/data/universe_extra.txt)  # just the +600
```
**2. TESTS** (equivalence MUST stay green after engine/strategy edits):
```bash
.venv/bin/python -m agents.evaluator.tests.test_range_equivalence
.venv/bin/python -m agents.evaluator.tests.test_stage2
```
**3. SINGLE-TICKER scan** (every setup + features) and cheap sell-swap:
```python
from agents.evaluator.data import store
from agents.evaluator.engine.scan import run_scan, collect_setups, apply_exit
from agents.evaluator.engine.exits import SwingLowTrailExit
from agents.evaluator.strategy.stage_range_strategy import StageRangeStrategy
from agents.evaluator.eval import metrics
led = metrics.ledger_df(run_scan(store.load('AAPL'), StageRangeStrategy(box_pct=5.0,
        price_mode='open_close'), ticker='AAPL', start='2015-01-01', end='2026-01-01'))
su  = collect_setups(store.load('AAPL'), StageRangeStrategy(box_pct=5.0), ticker='AAPL')  # once
led2 = metrics.ledger_df(apply_exit(su, store.load('AAPL'), SwingLowTrailExit(8), ticker='AAPL'))  # many exits
```
**4. BUILD the pooled dataset (the "features" run)** — pool `run_scan` over the
whole universe (~4 min for 1,175 tickers → ~115k setups). **Always auto-saves**
parquet + CSV to `agents/evaluator/runs/` (dedicated output folder, gitignored):
```python
from agents.evaluator.data import store
from agents.evaluator.probabilities import screen
from agents.evaluator.engine.exits import SwingLowTrailExit
tickers = [t for t in store.cached_tickers() if t in set(store.us_universe())]
cfg = dict(box_pct=5.0, price_mode='open_close', expansion_pct=4.0, min_days=3)
df = screen.build_dataset(tickers, strat_kwargs=cfg, name='setups_all')            # default exit
df = screen.build_dataset(tickers, strat_kwargs=cfg,
        exit_rule=SwingLowTrailExit(8), name='setups_swingtrail')                  # a different sell
# name= is optional (auto = run_<exit>_<timestamp>); save=False to skip writing.
```
**5. EXIT BAKE-OFF (the "strategy" run)** — same setups, many exits, split at
2015 (collect once per ticker, apply each rule; ~4 min):
```python
import pandas as pd
from agents.evaluator.engine.exits import StopAndWeaknessExit, SwingLowTrailExit, EmaTrailExit
rules = {'stop+weakness': StopAndWeaknessExit(max_loss_pct=0.04),
         'swing_trail_8': SwingLowTrailExit(swing_window=8, trail_buffer_pct=1.0),
         'ema_trail_20':  EmaTrailExit(ema_period=20, buffer_pct=1.0)}
for tk in tickers:
    su = collect_setups(store.load(tk), StageRangeStrategy(**cfg), ticker=tk)
    for name, rule in rules.items():
        trades = apply_exit(su, store.load(tk), rule, ticker=tk)   # -> pool by name, split by signal_date
```
**Notebook:** open `example_usage.ipynb` (select the `.venv` kernel) — single-
position run, scan (all setups), and feature slicing, end to end.

---

## 2b. Feature catalog (~105 cross-ticker-comparable columns)

> **Where features are defined:** this catalog is the index; the authoritative
> per-metric definitions live in the code + docstrings of each provider
> (`strategy/*.py`), and the Kell-cycle metrics have a dedicated spec at
> **`strategy/KELL_DEFINITIONS.md`** (updated in lock-step with `kell.py`).

Every setup row = **outcomes** + **features** (all %/ratio/count — no absolute
prices; those exist for reference but are excluded by `screen._is_feature`).
Suffixes: `{p}ema`=EMA period, `{n}sma`, `{w}m`=lookback months, `{t}pct`=tight
threshold. Measured **as of `signal_date`** (point-in-time).

- **Outcomes** (labels, never features): `return_pct`, `peak_return_pct` (MFE),
  `mae_pct` (MAE), `mae_before_peak_pct` (heat before the peak), `days_held`,
  `days_to_peak`, `exit_reason`, `entry/exit_date`, `entry/exit_price`, `qty`.
- **Range** (`range_strategy.py`): `range_length_days`, `range_height_pct` +
  `_hl_pct`/`_oc_pct` (both price modes), `range_max_up_day_pct`/`_max_down_day_pct`,
  `range_last3_*` (height hl/oc + max up/down over the last N=`range_recent_days`),
  `tight_body_days/pct_{1,2}pct` + `tight_range_*` + their `_last3_` variants,
  `expansion_move_pct`, `expansion_closing_range`, `strong_close`, `price_mode`.
- **Stage-2 / Minervini** (`stage2.py`): `s2_above_150_200`, `s2_sma150_above_200`,
  `s2_sma200_rising`, `s2_higher_highs_lows`, `s2_up_week_volume`,
  `s2_more_up_weeks`, `stage2_pass_count` (0–6).
- **Kell cycle** (`kell.py`, full defs in `KELL_DEFINITIONS.md`): per EMA —
  `wedge_pop_*`/`wedge_drop_*` (`date`, `bars_since`, `bars_below/above`,
  `max_ext_below/above`, `vol_ratio`, `count`), `crossback_count_{p}ema`,
  `wedge_pop/drop_both_bars_since`; and single — `reversal_low_*` /
  `exhaustion_high_*` (`price`, `date`, `bars_since`, `ext_pct`, `vol_ratio`,
  `pct_above/below`, `below/above_{p}ema_pct`). **Trend (3-state, §5d/§9):**
  `trend_state`/`weekly_trend_state` (+`_slope_pct`), `prev_trend_state`/
  `weekly_prev_trend_state` (the previous DISTINCT label — a base shows whether it
  followed an uptrend or downtrend, §5e), stage counts `exh_since_{downtrend,base,
  uptrend}` (+weekly) — all now INCLUDE the live in-progress extension (§5e/§5f).
- **MA stack** (`ma_stack.py`): `above_{10,20}ema` / `above_{50,200}sma`,
  `close_ext_{p}ema_pct` / `_{n}sma_pct` (distance of entry above each MA),
  `above_all_mas`, `coiled_up`, `ma_coil_spread_pct`.
- **Overhead supply** (`overhead.py`, per window `{6,12,24}m`): `has_overhead_{w}m`,
  `blue_sky_{w}m`, `overhead_nearest_price/pct_{w}m`, `overhead_highest_price/pct_{w}m`,
  `overhead_nearest_bars_since_{w}m`.
- **Momentum** (`momentum.py`): stock `ret_{1,3,6,12}m` (+ optional `ret_{n}d`),
  market `mkt_ret_{1,3,6,12}m` (benchmark SPY).
- **EPS** (`fundamentals.py`, point-in-time by report date): `eps` (raw),
  `eps_yoy_growth`, `eps_qoq_growth`, `eps_yoy_base`, `eps_qoq_base`,
  `eps_report_date`, `revenue_yoy_growth` (reserved None — needs FMP).
- **Meta**: `ticker`, `signal_date` (= `entry_date` − 1 trading day).

All feature knobs (which EMAs/windows/thresholds) are constructor args on
`StageRangeStrategy`; every list knob expands into columns automatically.

## 3. Data state

- **Universe = 1,222 US tickers** = `all_tickers.txt` (non-.NS, 622) +
  `data/universe_extra.txt` (600 clean common stocks from NASDAQ/NYSE listings).
  `store.us_universe()` merges both.
- **Prices cached for 1,175 (96%); EPS for 1,001.** ~47 symbols (MMC/FI/CYBR/…)
  return empty from yfinance and are skipped — fine. To top up:
  `.venv/bin/python -m agents.evaluator.data.ingest $(tr '\n' ' ' < agents/evaluator/data/universe_extra.txt)`
- History depth: back to 1962 for old names, but only ~20% of tickers reach 1990
  (survivorship-skewed; best coverage 2005+).
- Benchmark SPY cached (market returns). Compute never fetches.

---

## 4. The pooled dataset

One row per setup = 144 cols (105 clean features via `screen._is_feature` +
outcomes). **`build_dataset` always auto-saves parquet + CSV to
`agents/evaluator/runs/`** (the dedicated, gitignored output folder — see §How
to run step 4). Current artifacts in `runs/`:
- `setups_all.{parquet,csv}` — default stop+weakness exit.
- `setups_swingtrail.{parquet,csv}` — swing-low-trail (w=8) exit (the winner).
- `exit_bakeoff_results.csv` — the 3-exit comparison table.
- `reactive_exit_sweep.csv` — session-2 12-rule holdout sweep (§5b.D).

Split train/holdout by `signal_date` at **2015-01-01** (pre = train, 2015-2026 =
holdout). Outcomes: `return_pct`, `peak_return_pct` (MFE), `mae_pct`,
`mae_before_peak_pct` (heat before the peak), `days_held`. Current build:
**114,586 setups** over 1,175 tickers (62,731 pre-2015 / 51,855 holdout).
Rebuild whenever the universe or features change. **⚠️ This build is UNFILTERED
(no `min_dollar_volume`)** — it still includes the illiquid zombie tickers behind
the heavy-loss tail (§5b.A); rebuild with `min_dollar_volume=500_000` (§7 step 1).

_(`probabilities/db/` still holds the older `schema/build_db/query` conditional-
table artifacts; new run outputs go to `runs/`.)_

---

## 5. Results — the two analysis runs (holdout-validated)

Two runs were done, both split at 2015-01-01 (pre = train, 2015-2026 = holdout):
**(A) the FEATURES run** — pooled setup dataset + feature discovery (points 1,3–5);
**(B) the STRATEGY run** — the exit bake-off (point 2 / table). Data build:
114,586 setups over 1,175 tickers (62,731 pre-2015 / 51,855 holdout).

1. **The exit is the giant lever.** Mean peak +7.8% vs mean realized +0.96% — the
   current stop+weakness gives back ~88% of the average peak. Batting (peak>0)
   ~64%, reward (peak>8%) ~27%.
2. **The swing-low trail is the best exit** (full-universe bake-off, 114,586
   setups, cfg `box_pct=5, open_close, exp=4, min_days=3`):

   | exit (holdout) | mean | median | win>0 | reward>8% | capture | days |
   |---|---|---|---|---|---|---|
   | stop+weakness (current) | +0.25% | −4.0% | 21.7% | 10.4% | 4% | 11 |
   | **swing_trail (w=8)** | **+0.91%** | −4.0% | 15.4% | 10.2% | 9% | 19 |
   | ema_trail (20) | +0.20% | −2.2% | 30.7% | 11.5% | 3% | 10 |

   `SwingLowTrailExit(swing_window=8)` ≈**3.6× the baseline expectancy** on the
   holdout (pre-2015: +1.92% vs +0.79%) with the **median still −4%** (losers cut
   fast) — all the gain is in the right tail; lower win rate (15%) is the expected
   let-winners-run tradeoff. `EmaTrailExit(20)` cuts losses tighter (−2.2% median,
   31% win) but caps winners → low mean/capture (wrong direction for "keep
   winners"). NB: the full universe is far more sober than the earlier 10-ticker
   momentum sample (which showed +8%).
3. **Setup features are a WEAK ranker** — GBM holdout AUC ≈ 0.58 for "any profit"
   (0.53 for ">8%"). Best used as a filter: top-decile ≈ 2× base expectancy.
   Robust features (small but real): `above_10ema` (below it ≈ half win rate),
   `ret_12m`/`ret_1m`, `expansion_closing_range` (strong close), `mkt_ret_12m`
   (market uptrend), `reversal_low_bars_since`, `eps_yoy_growth`, light overhead.
4. **Downtrend avoidance works** — `below 200-SMA AND ret_6m<0` ≈ dead money
   (+0.05%); avoiding it lifts base +0.54% → +0.70%. Simple trend metrics
   (`above_200sma`, `ret_6m`) beat the fancy `s2_higher_highs_lows` (too
   common/lagged) for "am I in a downtrend."
5. **Refuted**: "6-10% above the 20-EMA = exhaustion = fewer runs." Full universe
   shows extension above the 20-EMA doesn't degrade outcomes; only "below the
   EMA" is bad. (Weak partial: >10% above the FAST 10-EMA slightly caps realized.)

---

## 5b. Results — the reactive-exit + entry-filter deep dive (session 2)

All train/holdout split at 2015-01-01, thresholds derived from TRAIN only, judged
on the holdout. Scratch scripts + `runs/reactive_exit_sweep.csv` are the artifacts.

**A. The −4% quick failures are largely a data (liquidity) bug, not the exit.**
Pulled every swing-trail loss < −15% (1,109 setups, ~1%). **77% of the dollar
damage came from names trading < $50k/day** (frozen OTC/zombie tickers reprinting
on 0–1 shares — NEXM, QUBT, ABVC, CIIT, NIXX… a flat price is trivially a "tight
box", so they keep firing phantom breakouts then "crash" on an untradeable
reprint). The remaining 23% is REAL binary-event gap risk in liquid names (NVAX
−84% vaccine-trial fail, SRPT −53% FDA reject, AMLX −78%) — no stop can protect an
overnight gap. **Fix = a liquidity gate at entry** (see §6 / `min_dollar_volume`),
NOT a different exit.

**B. Half of every losing trade never closes green even once.** Of the 75,849
stop-loss exits, 54.6% never once closed above entry; 76% of those failed within 1
day, 87% within 2. By outcome bucket, "never green" (peak_return ≤ 0) rate: losers
50.0%, small winners 2.1%, big winners (>8%) **0.6%**. → a "no-follow-through" kill
(exit if no green close by day N) is a cheap, high-precision loser-cut.

**C. Winners' momentum BUILDS; fakeouts' momentum DECAYS.** Tracing day-1/2/3
closes: >8% winners run +4.9%→+6.7%→+8.2% (mean, %positive 81→86→89); fakeout
losers (went green then reversed) decay +1.6%→+1.2%→+0.8% (65→59→54%). But a
fixed day-3 fading check is too trigger-happy — every big winner (TSLA, MSTR, ZM,
CROX, IONQ…) has a normal early shakeout, so the `fading_momentum` leg CUT the
monsters (opportunity cost ≈ −$108k over its bucket). **Confirmed net-negative.**

**D. Exit sweep — `SwingLowTrailExit(8)` STILL wins decisively** (`runs/
reactive_exit_sweep.csv`, 12 rules, same setups). Holdout mean: swing_trail
**0.91%** vs every `ReactiveMomentumExit` variant 0.28–0.33%. Reactive exits raise
WIN RATE (20–25% vs 15%) but clip the tail (capture 9%→5%, reward>8% 10.2%→~9%,
days 19→8–11) → wrong trade for "keep winners". Removing the fading leg
(`reactive_no_fading`/`nft_only`) is the best reactive (0.33, reward>8% 10.1%) —
confirms the fading leg hurts — but still ~3× worse than swing_trail. The
`tighten_to_breakeven` soft mode didn't rescue it either. **Patience beats
reactivity for this strategy.**

**E. What separates >8% WINNERS from ≤0% LOSERS at entry** (full-data + holdout-
validated, medians/IQR so no outlier distortion; every feature holds direction in
BOTH periods). Five independent themes, each modest alone (matches the weak-ranker
AUC≈0.58): (1) **tighter/quieter base** (`tight_range_pct_2pct`, `ma_coil_spread`,
`range_height_hl` — strongest single theme); (2) **less overhead supply above**
(`overhead_highest_pct_{6,12,24}m` all lower for winners); (3) **momentum already
in place** (`ret_{3,6,12}m`, `close_ext_200sma`); (4) **calmer recent chart**
(higher `reversal_low_bars_since` / `exhaustion_high_bars_since`, lower exhaustion
extension, fewer wedge pop/drop cycles); (5) **a controlled, not-explosive
breakout day** (lower `expansion_move_pct`).

**F. Best validated entry buckets** (tercile stacks, TRAIN thresholds → holdout):
- **PREFER**: low `overhead_highest_pct_6m` + high `close_ext_200sma_pct` + high
  `exhaustion_high_bars_since` → holdout mean **+1.24%** (vs +0.25% base), win
  25.9%, reward>8% 13.0% (~1% of setups).
- **AVOID** (bigger, more useful): high `overhead_highest_pct_6m` + low
  `wedge_drop_bars_above_10ema` + high `ma_coil_spread_pct` → holdout mean
  **−0.23%**, win 17.0%, reward>8% 7.3% (**9% of all setups**, negative expectancy
  both periods).
- Momentum SWEET SPOT: 3m-uptrend + tight box lifts win 15%→21%, but only the
  MODERATE band (`0 ≤ ret_3m < 40%`) — **extended (>40%) decays back to baseline**.
  Want an *establishing* uptrend, not a *mature/exhausted* one.

**G. THE BIG ONE — the >8% move is unpredictable IN A CAPTURABLE SENSE.** A GBM/
gradient-boost on 102 price-action features (no fundamentals):
- Predicting *realized* return >8% (with a real exit): holdout **AUC 0.54** ≈ coin
  flip. Even the model's top 1% by score lands at only ~11% reward>8%.
- Predicting *exit-independent* forward MAX excursion (fwd_max_40d ≥ 25%, computed
  straight from raw highs, no exit): holdout **AUC 0.71** — LOOKS predictable…
- …but it's a MIRAGE: the model is purely learning **volatility**. Its top
  "potential" decile whips **+24% / −23%** (medians) vs the bottom decile's
  **+7% / −6%**; forward-downside ≤ −10% rises 29%→**78%** across the SAME deciles.
  So high predicted "potential" = high volatility = whips down into the stop first
  → realized win rate INVERTS to 25%→7% top-to-bottom. Top importance features are
  all volatility proxies (`overhead_highest_pct_24m`, wedge pop/drop counts,
  exhaustion). **"Big up" and "big down" are the same feature.**
- **Conclusion**: price action CANNOT pick capturable runners; it can only flag
  volatility (useless — cuts both ways) or, weakly, loss-avoidance (the win-rate
  filters above). The upside is capturable ONLY by being IN all setups and letting
  a patient exit hold the unpredictable few → **the exit is the sole upside lever;
  entry filtering is a downside/consistency lever only.** (Untested monetization
  idea: a PROFIT-TARGET exit on high-predicted-volatility setups, to bank the
  volatility spike a trailing stop can't hold — see §7.)

---

## 5c. Session 3 — the Kell/O'Neil pillars + multi-timeframe (holdout-validated)

Built the features Minervini/O'Neil/Kell weight most, tested each. Full strategy
map + gap audit in **`strategy/KELL_STRATEGY_AUDIT.md`**. All the same story:
real but weak **inverted-U** separators (moderate beats extreme) — win-rate/
consistency levers, never the tail.

- **Relative strength.** `rs_rank_*` (percentile vs cached universe) is INVERTED
  (elite ≥90 underperforms) AND survivorship-biased → distrust it. **`rs_vs_spy_*`**
  (stock−SPY return, universe-independent) is the clean one: sweet spot = beat SPY
  by −10..+30pp (6m) → best; huge (>30pp) and lagging (<−10pp) both worse. Best
  stack: sweet `rs_vs_spy` + tight coil + volume≥1× → 20.6% win / 12.8% reward (vs
  15.5%/10.3%). AVOID: lag SPY>10pp AND no volume → 7.6% reward / 0.11 mean.
- **Volume signature.** Below-average breakout volume (<1×) = worst; but big surges
  (≥2×) no better than modest — need confirmation, not a spike.
- **Revenue growth (SEC EDGAR).** Inverted-U: modest YoY (0–40%) best, hypergrowth
  (>40%) mildly NEGATIVE (volatile). QoQ/acceleration = ~no signal. Holdout-only
  (XBRL revenue ~nil pre-2015).
- **Weekly 10-EMA extension (Kell's preferred "needs to base" gauge)** — the SHARPEST
  signal built. Win rate monotone: near-base (0–8%) 17.1% → very-ext (18–35%) 11.1%
  → blowoff (>35%) 8.2% / mean **−0.91**. Cleaner/more monotone than the daily gauge;
  stable both sub-periods. Monthly 10-EMA agrees (>40% ext = worst).
- **Exhaustion-extension count** (Kell 1st vs 2nd/3rd) — early (0–1) 22.5%/18.5% win
  vs late (3+) 12.9%. Validates "3rd = late." BUT 3+ has the FATTEST tail (mean 1.20 —
  late-stage survivors are the monsters) → win-rate signal, not tail.
- **Kell "confluence"** (daily >10% AND weekly >18% extended) = 10.5% win — clean AVOID.
- **SMA200-rising binary was fooled by flat SMAs** (TSLA 2022-08-12: +0.007%/mo counted
  as "rising"). Added `min_slope_pct`; real signal is the inverted-U slope band
  (0.5–5%/mo best, >5% steep/late-stage worst) — record the continuous slope, not a binary.

**Why still no Kell-like returns:** we've built + validated his ENTRY signals (weak,
as shown) but NOT what generates his +941% — discretionary selection, concentration,
pyramiding, selling into strength, regime participation, monthly compounding. Those
aren't per-trade entry signals; a per-trade equal-weight scan structurally can't show
them. **Next frontier = portfolio simulation, not more entry features** (see
`KELL_STRATEGY_AUDIT.md` §4–5).

## 5d. Session 3b — the Kell TREND-MODEL overhaul (uptrend / basing / downtrend)

A long, iterative build to identify trend/base *Kell's way* (self-calibrating, no
arbitrary thresholds), on **daily + weekly** (weekly = the trend authority). Spec
lives in `KELL_DEFINITIONS.md` §9 (single source of truth). What landed:

- **`trend_state` (3-state, daily) + `weekly_trend_state`** — THE trend/base
  definition, from the ref-EMA slope (over `trend_slope_window`=5) + price:
  - **downtrend** = slope < −1.5% (pure slope; a bounce back above a *falling* EMA
    stays downtrend — user's explicit call).
  - **uptrend** = slope > +1.5% AND close > ref EMA AND close ≥ last swing high.
  - **basing** = flat EMA (±1.5%), OR rising EMA but pulled back below the EMA / the
    last swing high (= consolidation/continuation pause).
  - `last swing high` = confirmed **±5-bar pivot** (updates only on a real pivot →
    no per-bar flip-flop). `trend_slope_pct` recorded too.
- **Per-run exhaustion "stage" counts** (anchored to the 3-state run):
  `exh_since_downtrend` accumulates through bases, resets on downtrend = Kell's
  **1st/2nd/3rd extension = stage** (validated: NVDA hit stage 3 right before its
  Aug-2023 top; stayed 0–1 through the fresh 2024 run). `exh_since_base` (resets on
  every basing; usually ~0 since an exhaustion completing IS what starts a base —
  kept anyway). Weekly twins: `weekly_exh_since_{downtrend,base}`.
- **`weekly_kell.py`** — the SAME cycle on weekly bars, aggregated incrementally
  from the daily stream (no separate cache), point-in-time as of the last COMPLETED
  week. Fractal reads confirmed: daily leads, weekly confirms/sustains (NVDA daily
  Jan-2023 vs weekly May-2023; TSLA April-2014 daily-base within a weekly uptrend).
- **2-state wedge-pop machine kept** (symmetric strict-slope: up when close>EMA &
  EMA strictly rose, down when close<EMA & strictly fell, flat persists) but ONLY
  for `kell_uptrend_legs` / `kell_uptrend_from_reversal` / `exh_count_since_uptrend`.
- **Minervini `base_in_uptrend` RETIRED** — it was a heuristic (rising-50-SMA +
  25%-from-high) that flagged extended late-stage pushes as "base" (e.g. TSLA
  Aug-Sep 2013). Superseded by `trend_state`.

**Design decisions locked (don't re-litigate):** trend end = EMA turned down (not a
bar count — the EMA self-calibrates N); flat EMA (±1.5%) = basing, not a flip;
downtrend is slope-only; the weekly is the authoritative trend, the daily is entry
timing (daily flip-flop is fine). Bugs found & fixed along the way: a laggy 50-SMA
end held TSLA's 2014 uptrend too long; removing the continuation-start stranded
TSLA in `down` through Feb-2014 (fixed by the symmetric price>EMA+rising start); a
micro-flat-EMA 1-bar flip (TSLA 12-20-2013) — resolved by the 5-bar-slope 3-state
(the flat zone is now `basing`).

**Result unchanged from §5c:** even this full multi-timeframe trend model is a
win-rate/consistency lever, not a tail lever. `weekly_in_uptrend` is the one
mean-mover (holdout mean 1.08 vs 0.77, stable) but redundant with the core gate.
The conclusion holds: entry = downside/consistency, exit + portfolio = the tail.

## 5e. Session 4 — the weekly trend overhaul, the momentum ceiling, and the
##      mega-winner reality check (grounded in Kell's actual BOOK)

This session read Kell's book directly (`Victory in Stock Trading`, 2021 — user's
copy) and used it to fix the trend model, then ran an exhaustive search for any
predictable edge. The interactive analysis harness is **`probabilities/trend_eval.py`**
(load a dataset → `cohort(by=..., query=...)`, always train/holdout split, cols
= `sell_win` (realized>0) / `mom_win` (peak>4%) / `reward8` (peak>8%)). Datasets
built via `rebuild_datasets.py {base|wk1035}`.

**A. Exhaustion counting fixed (Kell counts the LIVE extension).** Kell's book
counts an extension **as it is happening** and **through the intra-trend bases**,
resetting only at the cycle end (the Wedge Drop that "officially ends the uptrend
cycle") — NOT at each base. So `exh_since_downtrend` (accumulates through bases,
resets on downtrend) is his staging metric; `exh_since_base` is not. FIX: all
`exh_since_*`/`*_count` now include the **qualified in-progress episode** (TSLA
weekly 2014-02-26 went 0→1). `exh_since_base` is now live (was 100% zero). Book
quotes: extensions counted "since the traditional Cup n' Handle buy point";
1st=hold, 2nd=take profits, 3rd=late.

**B. `prev_trend_state` / `weekly_prev_trend_state` added** (the previous DISTINCT
3-state label) — so a `basing` bar shows whether it followed an `uptrend`
(continuation base) or a `downtrend` (bottoming base). Kell: a "Base n' Break"
appears in BOTH the up- and down-cycle; base ≠ trend, it's a step within one.

**C. Weekly trend re-tuned to 10/3/5 (Kell's weekly 10-EMA) — VALIDATED BETTER.**
The weekly cycle had inherited the DAILY defaults (20-week EMA / 5-week slope / ±5
pivot) → it lagged tops/bases by 3–4 weeks. Kell uses the **weekly 10-EMA**. New
weekly params on `StageRangeStrategy` (`weekly_ext_ref_ema`,
`weekly_trend_slope_window`, `weekly_trend_pivot_window`; default = old 20/5/5).
A/B on the FULL universe (row-aligned `setups_all` vs `setups_all_wk1035`): the
EMA period is the responsiveness lever (slope window barely matters for tops; it
mainly catches base-flattening sooner). **10/3/5 ~doubles the weekly up-minus-down
mean-return separation (0.38→0.67)** and correctly penalizes bad regimes
(weekly-up+daily-down −0.50→−1.83). **Decision pending: flip the strategy default
to 10/3/5** (currently still 20/5/5). ⚠ `range_length_days` swing-high lag: a
±5-week pivot confirms a swing high only 5 weeks late, so a fresh high isn't the
reference for ~5 weeks (documented, inherent to causal pivots).

**D. What raises the (realized) WIN RATE** — measured on `setups_all_wk1035`,
investable (weekly & daily both basing/uptrend) + `avg_dollar_vol_20d>500k` base:
- **Base SHAPE is the biggest lever** (and it's Kell's actual buy criterion):
  **length** monotone (3-day base 19% → 14+ day 31% holdout sell_win) and
  **tightness** (last-3-day `range_last3_height_hl_pct` tight<4% ≫ wide>6%, ~12pp).
  Length and tightness are independent and stack. `min_days=3` setups drag the
  book — a future build with `min_days≥6` would lift the baseline.
- **stage 0** (fresh trend, 0 exhaustions) is the trend/stage win-rate king
  (~26–29% vs ~22% base, both periods); **late stage + daily-uptrend** (chasing an
  extension) is the floor (~14%). **Buy the daily BASE, not the daily extension**
  (holds at every matched stage). Independent single filters that help (both
  periods): `coiled_up` (+4pp), `rs_vs_spy_6m` SWEET BAND −10..+30 (+3.2, ≫ raw >0),
  `eps_yoy_growth>0` (+3.2), `overhead_highest_pct_6m<10` (+2.3), moderate `ret_3m`
  0..40 (+1.7). All are **inverted-U / moderate-beats-extreme**.
- **Market-regime filters do NOT help per-trade** (SPY/QQQ up-filters all break-even
  or slightly hurt — they exclude early-recovery leaders; Kell: "next big winners
  are found during corrections"). Regime is a **portfolio/sizing lever**, not an
  entry gate. Liquidity `vol>500k` is a data-hygiene base filter (removes zombie
  outliers, stabilizes the mean, +~1pp).

**E. THE MOMENTUM CEILING — we CANNOT predict winning breakouts (verified 4 ways).**
`mom_win` (peak>4% = "did momentum show up, exit-independent") is pinned **~40%
base, ~43% max** across every robust slice. (1) Exhaustive single-feature scan: NO
feature's best quintile exceeds ~43% holdout. (2) Full gradient-boost on 142
features (all interactions): **train AUC 0.711 → holdout 0.536**; top-decile mom
75% train → **45% holdout** (base 40%) — the train edge is memorization +
period-specific patterns that don't generalize (shuffled-label control confirms).
(3) Per-cohort mom flatness. (4) The reward8/reward15 tail is flat across every
entry slice too. **AUC 0.54 ≈ +5pp win rate in the most selective slice** — a
rounding-error edge. To get a top-decile win rate near 60–70% you'd need AUC ~0.70+;
we're at 0.54. **Base quality is a VOLATILITY dial, not a tail lever**: long+tight
bases = higher batting but SMALLER fat tail (reward15 18%→10%); short/wide =
lower batting, fatter tail. Batting and the tail are anti-correlated (same as §5b.G).

**F. THE MEGA-WINNER REALITY CHECK (TSLA, NVDA, AMD, …).** Even the greatest
winners had **~base-level per-breakout WIN RATES** — **TSLA 21.9% (below the 23.7%
universe base!)**, NVDA 25%, AMD 26%; pooled 28.7%. Their edge is **100% the TAIL**:
pooled mean 3.30 (5× base 0.61), reward30 ~2×, max_peak TSLA +347%. Zooming into the
ACTUAL prime runs (TSLA 2020, NVDA 2023-24 AI, etc.): even in TSLA's 2020 run only
**28.6% of breakouts closed green**. Stacking the quality filters INSIDE the primes
(wk-up + daily-base + tight + stage0/1) lifts mom_win to ~55% and mean to +29.5% —
**but that is hindsight-selected & tiny-n** (you can't know ex-ante you're in the
prime; the robust ex-ante ceiling stays ~43%). What's observable ex-ante is
**leadership** (sustained trend + EPS/RS) — a *correlate* of a future prime, not a
predictor of which breakout wins.

**THE SETTLED CONCLUSION (user agrees): we cannot predict the market / winning
breakouts.** Entry is a **bounded consistency lever capped at ~43% win rate** — it
cuts failures and raises batting, but cannot manufacture momentum and does not touch
the upside. **A perfect exit would cap at ~43% realized win rate; we're at ~22% — the
entire remaining opportunity (22%→~43%) is the EXIT'S job.** Capturing the monsters =
identify the leader + participate in quality breakouts + let a patient exit ride the
tail + portfolio concentration/sizing — i.e. **exit + portfolio, NOT entry selection.**

## 5f. Session 4b — the exit dial, the failure anatomy, the batting screen, and
##      WHY traders make 100–1000% (the reframe that ends the entry inquiry)

**G. The exit is a monotone WIN-RATE-vs-EXPECTANCY dial (you can't have both).**
New exit `ScaleOutSwingTrailExit` (`engine/exits.py`): swing-low ratchet + a hard
%-giveback-from-peak cap + a sell-into-strength scale-out (sell `scaleout_frac`
at the Nth exhaustion extension, blended return). Full-universe A/B on wk1035
(investable, holdout):

  | exit | win% | mean | capture |
  |---|---|---|---|
  | pure swing8 (no cap) | 17.0 | **1.20** | 11% |
  | swing5 + 25% giveback | 19.8 | 0.78 | 9% |
  | swing5 + 15% giveback | 20.9 | 0.54 | 7% |
  | swing5 +15%gb +40% scaleout | 21.0 | 0.51 | 7% |
  | default stop+weakness | 22.5 | 0.52 | 7% |

  Perfectly monotone: **tighter giveback cap → lower mean, higher win rate.**
  **`pure SwingLowTrailExit(8)` is the best exit — mean 1.20 investable / 0.91 ALL
  = ~3.6× the default** (reproduces §5's original finding). A giveback cap and the
  40% scale-out BOTH neutralized it (clipped the tail = the whole edge). Giveback
  isn't worthless — it trades return for smoothness (a portfolio drawdown/Sharpe
  question, judgeable ONLY in a portfolio sim, not per-trade). **Win rate ⟂
  expectancy** — the money-making exit has a brutal 17% hit rate.

**H. Anatomy of the −4% losers — the clean failures are ~unavoidable.** 68% of
investable trades hit the −4% stop. Of those: **~48% NEVER close green** (straight
down, out in a median of **1 day** — the stop does its job, unavoidable); **~52%
go green first then round-trip to −4%**, and **18% pop to a mean +8.5% peak then
give it ALL back** (peak at day 3, dead by day 9 — the recoverable-money slice a
breakeven stop would target, but §5b.C says the same shakeouts cut the monsters).
At entry the clean failures are **~indistinguishable** from winners (medians dead-
even on volume/close/tightness/RS) — the momentum-unpredictability finding on the
loss side — **with ONE real separator: EXTENSION FROM THE 10-EMA** (Kell's #1
gauge). Weekly 10-EMA extension is the sharpest single quality signal in the whole
feature set (holdout, monotone, both periods): ext **0–8% → 25.6% win / 31% never-
green**; **18–35% → 15.6% win**; **>35% → 10.3% win / 52% never-green**. Daily
10-EMA (sweet 3–6%, >10% bad) and monthly agree. **Don't chase; buy near the
10-EMA.** (20-EMA extension is a weaker version — use the 10-EMA, per Kell.)

**I. The best validated BATTING SCREEN → ~32% holdout win, ceiling intact.**
Cumulative stack (all both-period-stable, `trend_eval.cohort`): investable+vol500k
→ weekly-10EMA-ext≤18% → base length≥6 → tight last-3<6% → RS-sweet(−10..30) →
early stage(0/1) → eps 0–30%. Holdout win rate climbs **22.5% → 32.5%** (n=1,476,
train 34.5% ≈ holdout — robust). **But `mom_win` stays pinned ~40–42% the whole
way** — the screen cuts failures (batting) and unlocks ZERO extra upside. Note the
refinements this session: RS/eps/overhead/ret are all **moderate-band inverted-U**
(eps SWEET BAND 0–30%, NOT hypergrowth >100% which is worst; RS −10..+30, not raw
>0). Filters that DON'T help: market-regime gates (portfolio lever, not entry),
`above_10ema`/`strong_close`/`expansion_closing_range` (redundant in the base).

**J. WHY Kell/Minervini make 100–1000% and we're at ~1% mean (the reframe).**
Apples-to-oranges: our number is the average of 115k **equal-weighted $1,000**
breakouts, all-weather, whole (junk-included) universe = the raw SIGNAL edge (small,
positive, fat-tailed). Their number is a **compounded, concentrated, sometimes-
leveraged annual PORTFOLIO return** in selected years. The 100–1000% comes from the
LAYER a per-trade equal-weight scan structurally cannot show: **concentration**
(5–10 names, not 115k slivers), **conviction sizing**, **pyramiding into winners**
(position biggest during the run), **compounding**, **regime EXPOSURE** (margin in
bulls / cash in bears — the portfolio use of the market signal that's useless per-
trade), **leader pre-selection** (trade the ~20–40 true leaders, not everything),
and **soft/discretionary info** (catalyst, theme, group) not in our 214 columns.
Caveats: Kell's +941% was ONE year (2020, pandemic bull, TSLA-heavy, leveraged) —
his long-run CAGR is ~30–50%; their win rates are also ~40–50% (edge = asymmetry +
management, not hit rate); and survivorship applies to the trader population too.
**The signal is ENOUGH** — a fat-tailed +EV edge + disciplined sizing + compounding
= large CAGR (Kelly math). **Nothing is "wrong" with the analysis — we measured the
ingredient, not the recipe. The recipe = the PORTFOLIO SIMULATOR** (sizing,
concentration, pyramiding, regime exposure, leader selection, monthly compounding →
measure CAGR / maxDD / Sharpe). That is THE next build; every thread now points to it.

## 6. Bugs fixed (don't re-introduce)

- **Stop-above-entry (gap-up-fade)**: `expansion_open` stop could land ABOVE the
  entry (breakout day gaps up & fades) → instant 0% "breakeven" stop. FIXED in
  `exits.py` `_stop` and `broker._finalize_stop` (drop candidates ≥ entry, fall
  back to −4%). ~3.3% of setups; e.g. TSLA 2020-11-18 went 0% → +74%. Equivalence
  test updated to skip these intentional divergences.
- **Penny-price rounding**: batch lab rounds to 2dp → breaks sub-$1 adjusted
  prices; on-bar version doesn't round (more correct). Equivalence test excludes
  buy price < $1.

## 6b. New code added (session 2)

- **Liquidity gate** — `RangeBreakoutStrategy(min_dollar_volume=None,
  dollar_volume_window=20)`. Causal trailing avg of `Close×Volume` ending at the
  PRIOR bar (breakout-day spike can't self-qualify); hard gate in the entry
  condition (applies regardless of subclass `_entry_allowed`); records
  `avg_dollar_vol_20d` on every setup. Default `None` = off (tests stay green).
  `min_dollar_volume=500_000` cut zombie NEXM from 155 phantom setups → 2.
- **`ReactiveMomentumExit`** (`engine/exits.py`) — 4 pluggable legs, all knobs:
  (1) no-follow-through kill (`no_follow_through_days`), (2) fading-momentum
  reaction (`momentum_ref_day`/`momentum_check_day`/`fading_momentum_action` =
  `"exit"` | `"tighten_to_breakeven"`), (3) pivot-low trailing stop
  (`swing_window`/`trail_buffer_pct`, = SwingLowTrail mechanic), (4) confirmed-
  weakness sell (2 closes below `ema_period`-EMA AND 2nd ≥
  `weakness_min_deterioration_pct` below the 1st). Findings: legs 1+3 good, leg 2
  net-negative, whole rule still < swing_trail (§5b.C/D). Note leg 4 compares
  each below-EMA close to the *prior* bar (sliding), not to the first dip bar.
- `runs/reactive_exit_sweep.csv` — the 12-rule holdout sweep (§5b.D).
- **Session-3 features** (all wired into `StageRangeStrategy`, rebuilt → 170 cols):
  liquidity `avg_dollar_vol_20d`; volume `breakout_vol_ratio`/`base_vol_ratio`/
  `base_vol_dryup`; RS `rs_rank_{1,3,6,12}m` (`data/relative_strength.py` +
  `rs_cache/`, cross-sectional) & `rs_vs_spy_{…}m`; revenue `revenue_*_growth`
  (`data/revenue_cache/` from SEC EDGAR, filing-dated, Q4-reconstructed); higher-TF
  `close_ext_{10,20}wema_pct`/`close_ext_10mema_pct` (`higher_timeframe.py`, weekly/
  monthly EMAs derived incrementally from daily — NO separate cache); Kell
  `exhaustion_high_count`/`reversal_low_count`; Stage-2 `min_slope_pct` knob.
  Data sources: **SEC EDGAR** (revenue, free/keyless) — NOT FMP (free tier caps at
  5 quarters). `strategy/KELL_STRATEGY_AUDIT.md` = the full strategy map + gap list.
- **Session-3b trend model** (rebuilt → 212 cols): `trend_state`/`weekly_trend_state`
  (+`_slope_pct`), `exh_since_downtrend`/`exh_since_base` (+weekly), `weekly_kell.py`
  (weekly cycle from the daily stream), QQQ regime `qqq_above_20ema`/`_50sma`/
  `qqq_ext_20ema_pct`/`qqq_ret_*` (`MarketRegime` in `momentum.py`). `base_in_uptrend`
  RETIRED. Full trend spec in `KELL_DEFINITIONS.md` §9 (§9a 3-state / §9b stages /
  §9c 2-state). See §5d. **⚠ column count grew across rebuilds (144→205) — the
  latest rebuild (`rebuild_datasets.py`) is the source of truth.**
- **Session-4 code** (§5e): `kell.py` — exhaustion counts include the live
  in-progress episode + `prev_trend_state`; `weekly_kell.py` — `weekly_prev_trend_state`;
  `stage_range_strategy.py` — weekly trend params (`weekly_ext_ref_ema` /
  `weekly_trend_slope_window` / `weekly_trend_pivot_window`, default = old 20/5/5).
  New: **`probabilities/trend_eval.py`** (interactive cohort harness — the tool for
  slicing win-rate by any filter, always train/holdout), **`rebuild_datasets.py`**
  (`base`|`wk1035` builds), **`FEATURE_AUDIT.md`** (column inventory / terminology).
  Datasets: `runs/setups_all.{parquet,csv}` (20/5/5) + `runs/setups_all_wk1035.*`
  (10/3/5), both 214 cols / 114,586 setups, row-aligned.

---

## 7. NEXT STEPS (in order)

- ✅ Session 1: ingested +600 (1,175 cached), rebuilt 114,586 setups, exit bake-
  off → `SwingLowTrailExit(8)` wins.
- ✅ Session 2: liquidity bug found + gate added; reactive exit built + swept
  (swing_trail still wins); winner/loser feature separation validated; **proved
  the >8% move is unpredictable-in-a-capturable-sense (it's just volatility) →
  exit is the only upside lever, entry filter is downside-only** (§5b).
- ✅ Session 3: built + tested the Minervini/O'Neil/Kell pillars (revenue via SEC
  EDGAR, volume, RS, weekly/monthly extension, QQQ regime) — all weak inverted-U
  (§5c). Then the full Kell TREND MODEL (3-state `trend_state` daily+weekly,
  exhaustion stages, `weekly_kell.py`) — §5d. Datasets rebuilt (212 cols).
- ✅ Session 4 (§5e): read Kell's BOOK; fixed exhaustion counting (live episode) +
  added `prev_trend_state`; re-tuned the weekly to **10/3/5** (validated ~2× better
  separation, A/B `setups_all_wk1035`); built the `trend_eval.py` harness; and ran
  the entry question to ground: **the momentum move is UNPREDICTABLE (4 ways; AUC
  0.54 holdout, ~43% mom ceiling), even TSLA/NVDA had ~base-level per-breakout win
  rates (edge is 100% tail).** **User concluded: we can't predict winning breakouts.**

**The entry question is CLOSED.** Entry = a bounded consistency lever (≤~43% win),
downside-only. All remaining upside is the exit + portfolio. Forward path:

1. **Flip the weekly default to 10/3/5** in `StageRangeStrategy` (validated §5e.C),
   and consider a build with `min_days≥6` (3-day bases drag the book, §5e.D). Also
   still outstanding: a `min_dollar_volume=500_000` build (use `vol>500k` as the base
   filter for now via `trend_eval`).
2. **THE MAIN FRONTIER — PORTFOLIO SIMULATION** (not more entry work). This is where
   every thread now points. Build: position sizing + concentration + **pyramiding**
   into the leaders, a **patient exit** riding the tail (swing_trail_8 baseline; test
   a profit-target on high-vol setups §5b.G), **regime-based exposure** (QQQ/SPY as a
   cash/margin switch — the portfolio lever it failed to be as an entry gate, §5e.D),
   and **leadership selection** (sustained trend + EPS/RS as the ex-ante correlate of
   a prime, §5e.F). Judge by portfolio CAGR/drawdown, not per-trade win rate.
3. **Layer the validated ENTRY filters as a batting/consistency screen only** (§5e.D):
   base length + tightness (biggest), stage-0, buy-the-daily-base, RS sweet band,
   eps>0, low overhead — all inverted-U, all ~+2–4pp win rate, none touch the tail.
4. Tighten stats (ticker-clustered significance); survivorship caveat stands (esp.
   the mega-winner tail base-rates in §5e.F carry upward survivorship drift).

---

## 8. Gotchas

- **Compute is cache-only**; ingest first or features silently come back None.
- **Non-independence**: many setups share a ticker/regime — naive CIs overstate
  significance. **Survivorship**: current list omits delisted losers → all win
  rates optimistic (esp. pre-2005).
- **`signal_date` ≠ `entry_date`** (features as-of the day before the fill).
- Small-sample discovery misleads (10 momentum tickers "confirmed" a hypothesis
  the 600-universe refuted) — always validate on the full universe + holdout.
- **Two trend notions exist** — use **`trend_state`/`weekly_trend_state`** (3-state,
  the primary definition, §5d/§9). `kell_in_uptrend` is the OLD 2-state wedge-pop
  machine, kept only for `kell_uptrend_legs`/`from_reversal`. Don't confuse them.
- **Weekly features are point-in-time as of the last COMPLETED week** (the in-
  progress week isn't fed until it closes) — so a daily signal mid-week reads the
  prior week's weekly state; correct, but weekly values only change on week rollover.
- **Most breakouts are "within a base"**: at the signal bar the daily `trend_state`
  is **basing 51% / uptrend 28% / downtrend 21%** (weekly: downtrend 41 / basing 44 /
  uptrend 21). A breakout is above the EMA but often still BELOW the last confirmed
  swing high → `basing` ("breakout within the uptrend-base"). So `basing` is the
  MODAL state at signal bars, not absent — a fresh new-high `uptrend` breakout (28%)
  is the minority. (Rebuilt dataset = 212 cols / 114,586 setups.)
