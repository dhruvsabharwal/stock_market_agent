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
| `exits.py` | Pluggable **sell** logic. `ExitRule` ABC + `PriceData` (cached `ema`/`sma`/`atr`/`below_ma`). Rules: `StopAndWeaknessExit` (default = live sell), `TrailingStopExit`, `SwingLowTrailExit` (ratchet under swing lows — **the best exit, §5f**), `EmaTrailExit`, `ReactiveMomentumExit`, `ScaleOutSwingTrailExit` (swing-trail + %-giveback cap + sell-into-strength scale-out, §5f). **Session 6:** `AtrStopExit` (stop = entry − k×ATR, same-stock-relative risk; beats flat-4% universe-wide, §5j) · `StructuralAtrExit` (structural swing-low stop, ATR-capped — named honestly: NOT Jeff Sun's intraday LoD, just the daily structural low; his real LoD lives in `eval/opening_range.or_low`) · `ThreeStopExit` (his 3-tranche scale-out; cuts losses to the designed −0.67R but ALSO clips winners without his re-entry mechanic — net negative alone, §5j). |

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
| `volatility.py` | **Session 6.** `Volatility` — the R risk unit: `adr_pct`/`atr_pct` (ADR%=range-only, ATR%=gap-inclusive), `ext_{n}sma_{atr,adr}` (extension in per-stock volatility multiples), contraction (`{adr,atr}_contraction_pct` = now vs `contraction_lookback` bars ago, gapped windows; `_box_pct` variants — **broken/undefined for boxes <6 days, 63% of the population; superseded by `tightness_score.py`, don't use**). |
| `vars.py` | **Session 6.** `VolAdjRelativeStrength` — VARS (Jeff Sun): `stock_ret/ATR%` minus `benchmark_ret/ATR%`, so raw % gain can't fake relative strength in a volatile name. `vars_spy_{m}m` (vs SPY, every name) + `vars_{m}m`/`rs_benchmark` (vs the resolved group benchmark via `benchmark_map.py`). |
| `benchmark_map.py` | **Session 6.** Ticker→benchmark resolver: thematic override (crypto miners→BTC-USD, yfinance miscategorizes them) → industry ETF → sector ETF → SPY. Reuses `base_breakout_strategy/industry_mapping.py` (same shim pattern as `_lab_imports`); fixes an em-dash/hyphen mismatch between that table and yfinance's industry strings. |
| `vcp.py` | **Session 6, heavily revised.** `VCPFootprint` — pivot-based VCP leg contraction (successive pullbacks off the base high, each shallower). `vcp_contracting_run` = TRAILING streak of shrinking legs (an early unrelated leg no longer poisons the whole read); `vcp_in_range`/`vcp_last_leg_{high,low}` = staleness + expansion guards (a base is resolved the instant ANY close since the leg's low exceeds the leg's high — even a later pullback back inside, a retest, doesn't revive it); `_last_legs` attribute exposes the actual completed-leg pivots for external plotting (`eval/viz.py`). |
| `tightness_score.py` | **Session 6, new.** `VCPTightnessScore` — the joshdm_94/@ETFbreakouts ADR-adjusted percentile tightness score Jeff Sun actually references (not a generic formula): `ratio = (5d closing/swing spread) / (20d ADR%-or-ATR%)`, `score = 100 − percentile_rank(ratio vs its own trailing 50-day history)` — self-relative, not an absolute cutoff. Emits 4 flavors (`{closing,swing}×{adr,atr}`). A second, longer-horizon instance (`tight_window=15`, `baseline_window=200`, fed by a SEPARATE `Volatility(50,50)`, `key_suffix='_long'`) is wired into `StageRangeStrategy` alongside the short one — the short version reacts to the last week, the long one to multi-month structure; they can (and do) disagree. |

**`data/` — caching + the one fetch step**
| file | job |
|---|---|
| `store.py` | Price cache (parquet/ticker): `load` (cache-only), `get` (cache-or-fetch), `fetch`, `normalise` (yfinance→OHLCV), `us_universe()` (all_tickers.txt + universe_extra.txt), `cached_tickers`. |
| `fundamentals.py` | Report-dated quarterly EPS cache: `load_eps` (cache-only), `get_eps` (fetch), `EpsHistory.growth_as_of` (point-in-time YoY/QoQ with sign-guards). |
| `ingest.py` | **THE fetch step** (only network I/O): prices + EPS + SPY for the universe, resume-safe, throttle circuit-breaker (default 20 consecutive fails). |
| `universe_extra.txt` | 1,600 extra US common-stock symbols (NASDAQ/NYSE listings) merged into the universe — the original 600 + **1,000 appended in session 7** (first 1,000 alphabetical AA…CNOB of 4,922 new candidates; ~1,017 universe names still uncached, §5k.K). |
| `sectors.py` | **Session 6.** Per-ticker sector/industry cache (yfinance `.info`), one parquet (`sector_cache/sectors.parquet`). Structural metadata (not price-derived) → using it to pick a benchmark is NOT a leak. Resume-safe `ingest()`; **1,175/1,175 universe done**, 95% resolve to a real group benchmark (5% fall back to SPY — null sector from yfinance). |
| `intraday.py` | **Session 6.** Hourly price cache. yfinance keeps only ~2.9yr of 60m bars (a hard wall — cannot backtest pre-2023 intraday); `save` MERGES each fetch (union by timestamp) so coverage extends forward on repeated ingests instead of being overwritten. **1,120/1,175 universe cached** (~2023-09 to ingest date). |

**`eval/` — performance metrics for a ledger**
| file | job |
|---|---|
| `metrics.py` | `ledger_df` (trades→DataFrame), `summarise` (win rate/expectancy/payoff/drawdown), `by_feature`, `max_drawdown`. **Session 6:** `add_r_metrics(df, k_atr, flat_stop_pct)` — re-expresses outcomes in the stock's OWN volatility unit: `r_multiple`, `ret_in_atr`, `peak_in_atr`, `mae_in_atr`, `flat_stop_in_atr` (the key diagnostic — shows a flat 4% stop is ~2.3 ATR on a calm name, ~0.6 ATR — inside daily noise — on a volatile one). |
| `run_eval.py` | CLI: run a registered strategy over a window → writes `trades.csv`/`summary.json`/`equity.csv` bundle to `eval/results/`. |
| `opening_range.py` | **Session 6.** Per-session first-hour (09:30-10:30) metrics from the hourly cache: decision-time (`or_high/low`, `gap_pct`, `rvol_open_{20,50}`, `lod_dist_pct`, `or_next_open`=the 10:30 price) vs rest-of-day (`reorh`, `move_high_pct`/`move_1030_pct`, `close_above_orh`) — never mixed. `attach_opening_range(df, entry_col, prefix)` JOINS these onto setups post-hoc by date — NEVER an on-bar signal-bar feature (at the signal bar the entry-day open hasn't happened yet). Default joins on `entry_date` (T+1, our actual fill day); pass `entry_col='signal_date', prefix='t0_'` to join the BREAKOUT day (T0) instead. |
| `reclaim_entry.py` | **Session 6, new — a prototype, not validated at scale.** A standalone, ADDITIVE alternative to the flat-4% `RangeBreakoutStrategy` (does not touch it): `collect_box_breaks` (the SAME validated flat-5% box, `expansion_pct` forced to ~0 so every break is a candidate) → `find_reclaim_entries` (box-relative anchor ≥`min_expansion_pct` + first-hour RVOL + intraday dip-then-reclaim of the opening-range high + LoD<`max_lod_atr`(0.6) at the reclaim, his actual gate) → `simulate_exit` (reuses the REAL, tested `AtrStopExit` — treats T0 as the buy bar with the intraday reclaim price). Only tested on a 60-ticker sample (§5j) — small, unswept, not run at full-universe scale. |
| `portfolio_sim.py` | **Session 7, new.** `simulate_portfolio(signals, initial_equity, risk_pct_per_trade, max_positions, max_position_pct, min_ticket_dollars, rank_by, rank_ascending)` → `PortfolioResult(trades, missed, events, …)`; `summarise_portfolio`. Jeff Sun sizing `equity×risk%÷stop_dist_pct`, slot/cash-constrained, same-day ranking, equity moves only at exits (no MTM). Needs `ticker, entry_date, exit_date, return_pct, stop_dist_pct`. §5k.D. |
| `build_trade_book.py` | **Session 7, new.** Runnable CONFIG-driven pipeline: filter gates → `compute_composite_score` (whole-pool percentile blend, ±weights) → `simulate_portfolio` → trade-book CSV. ⚠ CONFIG on disk = last (losing) experiment; baseline recipe in §5k.I. |
| `chart_server.py` | **Session 7, new.** Local stdlib HTTP server (127.0.0.1:8765) serving `viz.plot_ticker` PNGs on `/chart?ticker=&start=&end=&recency_window_days=&label_trades=&source=`; Agg backend; per-path cache; **defaults to `build_trade_book.SETUPS_PATH` — no flag or prompt needed** (`--trades` / per-request `?source=` override it); relative paths resolve cwd → repo root. |
| `tradebook_viewer.html` | **Session 7, new.** Standalone drag-and-drop trade-book viewer (timeline Gantt + log, ticker/win-loss filters, year/quarter/month filter that zooms the timeline, click → live chart modal via `chart_server` or a copy-paste `viz.plot_ticker` command). Tiles don't yet follow the period filter (§5k "Not done"). |

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
| `scan_volatility_contraction.py` | **Session 7, new.** Decoupled-from-the-box contraction-event scan over every cached US ticker (3-yr window): flags `tight_pctile_flag` (short `VCPTightnessScore` ≥90 on any flavor) and `vcp_structural_flag` (`VCPFootprint.vcp_contracting`) on episode START, attaches trailing `ret_*`/`rs_vs_spy_*`/`vars_*` + `above_{10ema,20ema,50sma,200sma}` → `runs/volatility_contraction_events.{parquet,csv}` (58,523 rows, 35 cols, no forward outcomes). §5k.J. |
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
**6. PORTFOLIO SIM → TRADE BOOK → VIEWER (session 7, §5k)** — edit the CONFIG block in
`eval/build_trade_book.py` (gates / `COMPOSITE_WEIGHTS` / `RANK_BY` / sizing), then:
```bash
.venv/bin/python -m agents.evaluator.eval.build_trade_book                 # prints summary, writes the book CSV
.venv/bin/python -m agents.evaluator.eval.chart_server                     # optional: live charts for the viewer
open agents/evaluator/eval/tradebook_viewer.html                           # then drag the CSV in
```
```python
from agents.evaluator.eval.portfolio_sim import simulate_portfolio, summarise_portfolio
res = simulate_portfolio(df, initial_equity=10_000, risk_pct_per_trade=0.20,
        max_positions=8, max_position_pct=25.0, rank_by='vars_6m')   # df needs stop_dist_pct etc.
summarise_portfolio(res); res.trades; res.missed; res.events
```
**7. VOLATILITY-CONTRACTION EVENT SCAN (session 7, §5k.J)** (~5 min, 1,175 tickers):
```bash
.venv/bin/python -m agents.evaluator.scan_volatility_contraction   # → runs/volatility_contraction_events.parquet
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

- **Universe = 2,222 US tickers (as of session 7)** = `all_tickers.txt` (non-.NS, 622) +
  `data/universe_extra.txt` (1,600: the original 600 + 1,000 NASDAQ/NYSE names added
  in session 7). `store.us_universe()` merges both. _(Through session 6 it was 1,222;
  every dataset/cache built before session 7 — setups parquets, sectors, hourly,
  contraction events — covers only the original 1,175 cached names.)_
- **Prices cached: 1,259 files, 1,175 of them in the original universe; ~1,017 universe
  tickers uncached** (the interrupted session-7 download, §5k.K); EPS for ~1,001 (old
  names only). ~47 symbols (MMC/FI/CYBR/…) return empty from yfinance and are skipped —
  fine. To top up / resume (resume-safe — skips cached):
  `.venv/bin/python -m agents.evaluator.data.ingest $(tr '\n' ' ' < agents/evaluator/data/universe_extra.txt)`
  (add `--no-eps` for a faster prices-only pass; ~0.4s/request pause)
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

## 5g. Session 4c — exit decision, trade visualizer, prev-weekly-state test

**Default exit = `StopAndWeaknessExit` (user's call, for now).** Swing8 has the
higher per-trade mean (1.20 vs 0.52) but it earns it by **holding through weekly
downtrends** (a single trade can ride 2019→2021 with no swing-low break) — a
drawdown the user won't hold. The weakness exit rolls out when momentum breaks
(≈ "don't hold the downtrend") and re-enters on the next breakout (the scan counts
re-entries as separate trades). Per-trade mean ≠ portfolio truth: the weakness
exit's higher win rate + faster turnover + shallower drawdown may compound better
in a concentrated book. **Swing vs weakness is a PORTFOLIO-sim question** (turnover /
compounding / maxDD), not a per-trade one — revisit there. Reactive day-3 kill was
net-negative on both bases (§5f/G-analog); ScaleOut giveback cap clipped the tail.

**`weekly_prev_trend_state` hypothesis — REFUTED.** "A weekly base that came from a
downtrend has worse success" is not supported: `basing←downtrend` (holdout n=11.6k,
win 23.2%, mean +0.72, mom 40.3%) ≈ `basing←uptrend` (n=4.4k, win 23.6%, +0.39,
40.1%). Nearly identical win/mom; the mean gap flips train↔holdout (noise). If
anything the bottoming base (from downtrend) is slightly better on holdout mean —
consistent with fresh Stage-2 launches starting out of a downtrend base. Caveat:
`prev` is only the immediately-prior distinct state (not weeks-since); a finer
"weeks since downtrend ended" cut is untested.

**Trade visualizer — `eval/viz.py`** (new): `plot_trade` (one trade, normalized to
% from entry: path + peak/MFE + exit + swing-trail), `plot_trades` (overlay any
subset, normalized), `plot_ticker` (every trade on the ACTUAL price chart, log,
with weekly-regime shading + weekly-10-EMA + a volume panel). Renders inline via
IPython PNG bytes (backend-agnostic) or `out='x.png'` to file. NB Antigravity IDE
doesn't render inline images → use `out=` or JupyterLab.

## 5h. Session 4d — the STAGE-2 GATE (Weinstein "only trade in Stage 2") + the
##      potential reframe + the trade visualizer

**The reframe: judge on POTENTIAL (peak), not win>0.** A "win" (return>0) counts a
+0.1% scratch; the user wants MASSIVE moves. Metric switched to the exit-independent
peak: `mom_win`=peak>4%, and the tail `>30/50/100%`. On TSLA this is stark — Stage-1
2015-2019 setups: mean peak 9.6%, **0% reached +100%, 4% reached +50%**; the 2019-2021
launch: mean peak **113%, 19% reached +100%, 24% >+50%, max +1383%**. Same stock/
detector — **12× the potential. The Stage-1 vs Stage-2 difference is the TAIL (big-move
rate), not the win rate** (both ~26-43% peak>8%).

**Long saga: can we AVOID Stage-1 dead zones? First the negatives.** Tested many
"meaningful resistance" definitions, all judged on potential, holdout: `resist_lookback`
(bars since price was last this high) — the ONE modest signal: in-range pop (<25 bars,
~52% of setups) mean peak 5.2%/win 18% vs fresh high (>25) ~10%/27%; **robust across
uptrend AND basing** (a good base-break makes a *new* high → kept; a stall under the
recent high → cut). But the elaborate structural versions (2yr-ceiling, swings-capped,
bars-held, 200-SMA-slope-flat, blue-sky) ALL **fail to separate the tail** (weak/noisy,
holdout). **THE key proof:** TSLA broke to a **new ALL-TIME HIGH in June 2017** (perfect
"resistance broken") → peaked +0.6%, lost −4%; the *same* signal in 2019 → +1383%.
Identical setup, opposite outcome → resistance-clearing CANNOT predict. And **TSLA
2014-2019 was NOT a dead zone at all** — it went $10→$22 (**2.2×**), made **38 new
52-week highs**, 200-day MA **+63% (~10%/yr)**. The "flat dead zone" is an OPTICAL
ILLUSION from the 2020 17× compressing the log chart. So Stage-1 avoidance can't be a
*predictive* filter (the launch breakout is indistinguishable from the failures — the
momentum ceiling, one more time).

**BUT as a DISCIPLINE rule it IS definable (user's explicit choice).** "Never trade in
Stage 1 — only participate after the long-term ceiling breaks (Stage 2), accepting I'll
miss the occasional early launch." Iterated with the user (visually, on TSLA) to the
final **STAGE-2 = REJECTED-CEILING rule** (matches their discretionary 13-yr TSLA read):
> **Skip** a setup if price is under a *governing high* (3-yr max) that has **REJECTED a
> rally** — a swing high (±10-bar pivot) that got within ~15% of the high and was **then
> FOLLOWED BY a daily downtrend** (a real reversal via the Kell daily `trend_state`, NOT
> just a pause/swing-high). **Trade** when making new highs, OR pulling back from a high
> that has not yet failed (rally paused, no downtrend). Self-sizing, no arbitrary clock.

Reproduces the user's reading: skip 2015-17 (<$19.41), skip 2018-19 (<$26), trade the
2020 launch + June-2020 recovery + pullbacks, skip 2022-24 (<2021 high), trade 2025+.
The daily-downtrend refinement was crucial (April-30-2020 was a swing high but the trend
never went down → not a failed rally → June-2020 stays tradeable). **DECISION: the
strategy will only trade in Stage 2.** Planned future additions (user): layer weekly-
uptrend + higher-highs on top. **⚠ NOT yet universe-validated or baked into the build —
it lives only in `viz.py` (`_stage2`) as a per-bar rule; next step is to test it
universe-wide (does the discipline earn its keep on mean AND tail, train/holdout) and,
if kept, wire it into a provider.**

**Stage-2 mechanics (precise, as implemented in `viz._stage2`).** Ceiling = **rolling
3-yr max high** (`lookback=756`, tunable). Stage 2 = **TRUE if EITHER** (a) close ≥ the
rolling 3-yr high (new high, cleared the ceiling), **OR** (b) below it but the high has
**0 failed rallies** yet (fresh pullback). Stage 2 = **FALSE** only once a rally has
failed at the ceiling (≥1 swing high within `near=15%` of it, then a daily downtrend) —
then you must break the high to re-enter. So it is NOT "the 3-yr high must always be
broken"; a fresh pullback under an *unproven* high is fine. **Far-below-ceiling is
handled correctly** (NVDA-verified): a topping stock leaves failed rallies *near its
high on the way down* which stick as resistance, so a stock 200% below its 3-yr high
(e.g. NVDA 2022 low) reads NOT Stage 2, its whole recovery *under* the old high reads
"wk-uptrend but no Stage 2" (yellow), and it flips green only when it breaks the high.
Known **edge case** (rare): if the 3-yr high was set with NO near failed rally (fast
gap-down crash / one-day spike), a deep-below stock can wrongly read Stage 2. Known
**limitation**: single 3-yr ceiling (no intermediate-resistance stack) — the "clears a
nearer resistance = succeeded rally → Stage 2 earlier" refinement is unbuilt.

**Trade visualizer — `eval/viz.py` (much expanded).** `plot_trade` (one trade,
normalized), `plot_trades` (overlay), and **`plot_ticker`** (every trade on the real
log-price chart) now with: **Stage-2 × weekly 4-color background** (green=St2+wk-uptrend,
light-green=St2+wk-base, **light-red=St2+wk-downtrend** = "allowed but weekly rolling
over", light-yellow=wk-uptrend-but-no-St2, white=skip), a **volume panel**, **exhaustion-
STAGE markers** (Kell ①②③ = `exh_since_downtrend` per leg, now placed at each extension's
PEAK — visibly ①→②→③ into TSLA's Feb-2020 blow-off top), readable price ticks (both
sides), dates under both panels. Renders inline via IPython PNG or `out=`. Helpers:
`_stage2`, `_exh_stages`, `_weekly_regime`. **⚠ Exhaustion-stage OPEN ISSUE:** the count
resets on EVERY daily downtrend (any normal pullback), so a big sustained advance
fragments into repeated ①'s (TSLA 2020-21 reads ①→②→reset→①→reset→① instead of Kell's
①→②→③→④). To align with Kell, reset on a DURABLE break (weekly downtrend / sustained
daily downtrend), not every daily wiggle — same "confirmed-downtrend reset" open item
as the stage counts. User accepted the peak-positioning fix; reset alignment deferred.

## 5i. Session 5 — meaningful-resistance ("stuck") gate, 2LYNCH base features,
##      broad-index weekly regime (dataset rebuilt → 240 cols)

Three feature families added and baked into `setups_all_wk1035` (114,586 setups ×
**240 cols**). All are RECORDED features (no gating) — evaluate first.

- **Meaningful-resistance / Stage-1 "stuck below resistance"** — `strategy/resistance.py`
  (`MeaningfulResistance`). **NOT** a Stage-2 definition (Kell's weekly uptrend still
  owns Stage 2); this flags the *dead zone* — price stuck under a repeatedly-tested
  overhead resistance. **Swing-high driven, no trend-state dependency** (a rejected
  rally fails *below* a high so it reads `basing`, not `uptrend` — using ±`pivot_window`
  confirmed swing highs catches every rally-and-fail). Swing highs within `band_pct`
  (12%) are ONE **zone** (symmetric — 25.8 & 26.0 are the same level); the zone price
  tracks its highest test. Formation doesn't count; each *subsequent* retest swing high
  = a rejection; `>= min_rejections` (2) = "significant". **Cleared** = a daily close
  above the zone by more than `clear_buffer_pct` (3%) — a marginal poke is a test, not
  a break (fixes double-top fragmentation). Knobs (flat on `StageRangeStrategy`,
  `resistance_*`): `pivot_window=10`, `band_pct=12`, `min_rejections=2`,
  `clear_buffer_pct=3`, `recency_window_days=None`, `max_dist_pct=None`. Emits:
  `stuck_under_resistance_{6_mo,1_yr,2_yr}` (windowed via `stuck_at(close, bars)`),
  `broke_above_resistance_{6_mo,1_yr,2_yr}` (breakout *above* a meaningful resistance =
  escaped the dead zone), `stuck_below_resistance` + `resistance_dist_pct` /
  `resistance_test_count` / `resistance_level_count` / `significant_resistance_count` /
  `next_resistance_dist_pct`. Finding: **stuck is not a strong per-trade edge** —
  pk8/tail differences ~1pp and flip train↔holdout; the one consistent effect is
  pure-trapped modestly caps pk30 (~1pp). On TSLA the keep/drop split looks strong but
  is tiny-n (illustration, not edge). ⚠ The daily `_stage2` in `viz.py` is the OLD
  rejected-ceiling rule; the new detector is `_stuck_below_resistance`.
- **2LYNCH base features** (Bonde's checklist) — added in `strategy/range_strategy.py`
  (flow to all setups): `up_days_in_a_row` (consec up-days ending AT the breakout — "2"),
  `day_before_range_pct` / `day_before_body_pct` (pre-breakout day tightness — "N"),
  `range_down_days_gt4` (# >4% down days in the box — "C" cleanliness),
  `prior_move_linearity` (Kaufman efficiency ratio of the `linearity_window`=30 bars
  BEFORE the box; ~1 straight, ~0 choppy — "L") + `prior_move_net_pct` (signed net %,
  direction). We already had Y (`exh_since_downtrend`/stage-0), C-tightness, H
  (`expansion_closing_range`). Finding: full 6-condition 2LYNCH gate passes ~3.3% of
  liquid setups and lifts win/pk8 ~1–2pp, stable but tiny (momentum ceiling holds).
- **Broad-index weekly regime** — `strategy/index_regime.py` (`IndexRegime`) + a monitor
  `eval/regime.py` (`regime.snapshot()`). Runs the SAME weekly Kell cycle used per stock
  (`WeeklyKellContext`, same weekly config) on **SPY / IWM / IJR / IJH**, point-in-time
  as of the signal bar (cached per (symbol,config) at module scope). Emits
  `{spy,iwm,ijr,ijh}_weekly_state` (uptrend/basing/downtrend) + `_risk_on` (uptrend|basing
  = the sticky, non-flip-flopping read; downtrend=risk-off). ⚠ Fetch the ETFs first:
  `store.get('IWM'/'IJR'/'IJH')` (SPY already cached). Weekly machine is on WEEKLY bars
  (not daily); the week-to-week wiggle is only uptrend↔basing — collapse to `risk_on`.

**Viz (`eval/viz.py plot_ticker`) extras** (session 5): dead-zone red shading with
`_stuck_below_resistance`, meaningful-resistance level lines, `exclude_stuck=True`
(drop trades whose signal bar is in the dead zone + print KEEP/DROP stats),
`label_trades=True` (MM/DD at each entry + printed trade table), and `win8` (realized
return >8%) on the stats line. All `resistance_*` knobs are `plot_ticker` args.
**Bug fixed:** an `end`-variable shadow in the resistance-line loop leaked trades past
the window — renamed the loop vars (don't re-introduce).

## 5j. Session 6 — Jeff Sun's R mechanism, VARS, the VCP/tightness overhaul,
##      opening-range/intraday, and the reclaim-entry prototype

Triggered by reading Jeff Sun's methodology docs (`artifacts/jeff-sun-methodology_2.html`,
`artifacts/Jeff_Sun_Process_Report.html`) against the evaluator with fresh eyes. Core
insight repeated throughout: **our flat-% thresholds (4% breakout, 4% stop, 5% box)
are exactly the thing his ATR-relative framework rejects** — the same % means wildly
different things across different volatility regimes. This session translated his R/
ADR/ATR/VARS/tightness machinery into the codebase, all additive (nothing existing
was removed; the flat-4% `RangeBreakoutStrategy` is untouched and still the default).

**A. The R mechanism — `strategy/volatility.py` + `eval/metrics.add_r_metrics`.**
Built `adr_pct`/`atr_pct` (ADR=range-only, ATR=gap-inclusive — Jeff Sun prefers ATR
for extension/LoD "because it includes gaps") and `ext_{n}sma_{atr,adr}`. The
`flat_stop_in_atr` diagnostic proved the thesis on real data: a flat 4% stop is
**~2.3 ATR on a calm name (KO) but ~0.6 ATR on a volatile one (OKLO)** — literally
inside daily noise, explaining a chunk of the "quick −4% failure" tail from §5b.A.
New exits `AtrStopExit`/`StructuralAtrExit` beat the flat-4% default universe-wide
(114,586 setups, holdout, same setups): mean **+0.36R vs +0.25R**. Full sweep
(150-ticker sample, holdout — the shape that motivated picking `k_atr=1` as the
default reference point though wider k wins outright on mean):

| exit | mean | median | win | reward>8% | days | ret_in_atr |
|---|---|---|---|---|---|---|
| flat4 (current) | 0.68% | −4.00% | 21.5% | 26.1% | 10.2 | 0.155 |
| atr1.0 | 0.79% | −3.44% | 23.7% | 28.7% | 10.9 | 0.170 |
| atr1.5 | 1.15% | −3.98% | 28.7% | 34.5% | 14.1 | 0.229 |
| **atr2.0** | **1.39%** | −3.72% | **31.6%** | **37.7%** | 16.1 | **0.277** |
| struct_cap2 | 1.08% | −3.72% | 27.8% | 33.4% | 13.7 | 0.230 |
| struct_cap3 | 1.12% | −3.63% | 28.4% | 34.0% | 14.2 | 0.237 |

Wider stops win on absolute expectancy; per unit of risk taken (R = ret_in_atr/k)
the tighter stop wins — a sizing question, not a "which is better" one.

**The validated batting screen (§5e.D) re-run ON atr1** (same cumulative stack,
holdout) — confirms the earlier finding holds under the new exit, and sharpens it:
avg LOSS stays pinned at **−1.00R the entire way down** (an entry filter structurally
cannot touch it — only loss management can) while the tail (reward>8%) *shrinks*
as the screen tightens (26.9%→20.3%, the batting-vs-tail anti-correlation, §5f.H):

| screen (cumulative) | n | win% | avgWin | avgLoss | exp | reward>8% |
|---|---|---|---|---|---|---|
| baseline (all holdout) | 51,766 | 23.1% | 3.42R | −0.99R | +0.033R | 26.9% |
| + investable + vol>500k | 23,518 | 22.1% | 3.93R | −1.00R | +0.089R | 27.6% |
| + weekly 10EMA ext ≤18% | 20,561 | 22.4% | 3.89R | −1.00R | +0.095R | 26.9% |
| + base length ≥6 | 9,003 | 22.5% | 3.94R | −1.01R | +0.104R | 23.5% |
| + tight last3 <6% | 7,458 | 22.2% | 3.86R | −1.02R | +0.064R | 22.3% |
| + RS sweet (−10..30) | 4,785 | 23.1% | 3.79R | −1.01R | +0.095R | 21.5% |
| + early stage (0/1) | 4,400 | 23.5% | 3.77R | −1.02R | +0.106R | 21.4% |
| + eps 0..30% | 1,476 | **25.6%** | 3.63R | −1.02R | **+0.173R** | 20.3% |
| + VARS-group not extended (<15) | 1,381 | 25.7% | 3.61R | −1.02R | 0.170R | 20.6% |

The screen alone (+0.033R→+0.173R, ~5×) plus loss management back to −0.78R
(arithmetic: 0.256×3.63 + 0.744×(−0.78) ≈ **+0.35R**) together land almost exactly
on **Jeff Sun's own +0.328R expectancy** — neither piece alone gets there; you need
both. This is the R-mechanism analog of the §5f.J reframe, now with a number attached.

**THE central finding of the session**: comparing our per-trade R-distribution to
Jeff Sun's own published year (24.8% win, +3.70R avg winner, −0.78R avg loser,
+0.328R expectancy) — **our avg winner (+3.59R) is nearly identical to his.** The
ENTIRE gap is the loser: **we take −0.96R (the full stop), he takes −0.78R** (his
3-Stop scale-out). **We are NOT missing the ingredient (the entry signal finds the
same-quality winners); we're missing loss management + the portfolio layer.**
Built `ThreeStopExit` to test this directly: it cuts losses to the designed
**median −0.67R exactly**, but ALSO clips winners (his shakeout-tolerant re-entry
mechanic isn't modeled) → net LOWER expectancy alone (+0.10R vs atr1's +0.16R on
the same sample). **The 3-Stop only works with the re-entry it's paired with in
his method — that re-entry is inherently intraday** (his rule: re-enter on a
same-day re-ORH if stopped before Day 4), which is exactly what motivated (D)/(E)
below.

**B. VARS + sector benchmarking — `strategy/vars.py`, `benchmark_map.py`,
`data/sectors.py`.** VARS = `stock_ret/ATR% − benchmark_ret/ATR%` (his own
framing: *"a 10% move in a 20%-ADR name shouldn't count as RS if SPY is up 2% on
1% ADR"*). Benchmark resolution: thematic override (crypto miners→BTC-USD) →
industry ETF → sector ETF → SPY, via `industry_mapping.py` (reused from
`base_breakout_strategy`, same shim pattern as `_lab_imports`) — fixed a real
em-dash/hyphen mismatch that silently dropped precise-industry ETFs to the macro
fallback. Full-universe sector ingest: **1,175/1,175, 95% resolve to a real
group benchmark**. Worked example: OKLO's raw RS vs SPY (+34.8%, looks elite) is
actually **−2.3% vol-adjusted vs SPY** and only **+2.4% vs its own sector (XLU)**
— raw % gain and true relative strength are different rankings, exactly as he
claims. Validated on the full holdout: VARS shows a real (if modest) inverted-U —
sweet band 0–5% (~28% win vs 24% base), extremes worse both directions.

**C. The tightness/VCP overhaul — the session's longest thread, several dead
ends worth remembering.** Started by adding `adr_contraction_pct`/`atr_contraction_pct`
to `volatility.py` (now-vs-`lookback`-bars-ago, gapped windows) — **this was NOT
Jeff Sun's actual method**, just an ad-hoc construction; retired in favor of (below).
A box-relative contraction variant (`_box_pct` suffix) was found **completely
undefined for boxes under 6 days — 63% of the whole setup population** (a `half<3`
guard silently returned None); don't resurrect it. Found via web research that
Jeff Sun's real tightness tool is a specific published indicator — **joshdm_94 /
@ETFbreakouts' "VCP Tightness Score (ADR-Adjusted)"** — and rebuilt it precisely:
`strategy/tightness_score.py` `VCPTightnessScore`, `ratio = (5d spread)/(20d ADR-or-
ATR%)`, `score = 100 − percentile_rank(ratio vs its own trailing 50-day history)` —
self-relative (a 15% swing can score "tight" for a volatile name if it's unusually
quiet FOR THAT NAME lately — verified on SHOP during the Apr-2025 tariff selloff,
ATR%=11.3%, still scored 98). A LONG-horizon twin (`tight_window=15`,
`baseline_window=200`, its own `Volatility(50,50)` instance, `key_suffix='_long'`)
was added because the short version (5d/50d) structurally cannot see a multi-month
base — confirmed on NVDA: the long score independently rediscovered the user's own
visually-identified tightest day (Oct 2, 2024, score 99.5) that the short score
missed. **The two scores are complementary, not redundant** — worth keeping both.

`VCPFootprint` (existing from an earlier session) got THREE real bug fixes here,
each found by the user tracing a specific date against a chart and asking "why
does this read True":
  1. **`vcp_contracting` required EVERY leg to be smaller than the one before it,
     from the very first leg** — one small early wiggle anywhere in the sequence
     poisoned the whole read to False even when the recent, dominant legs were
     cleanly contracting (NVDA Sep-2024: 16.14%→33.39%→23.09%, the real signal
     was 33.39%→23.09% but the leading 16.14% broke `all()`). **Fixed**: added
     `vcp_contracting_run` (the TRAILING streak of shrinking legs, `min_contracting_run`
     param) — `vcp_contracting` now uses only the counted trailing legs.
  2. **No staleness check** — legs are built purely from confirmed pivot history
     with zero reference to current price; a real-but-OLD contraction could read
     True long after price had run far away (NVDA Jan-2024: last leg topped
     $50.43, price was $61.53 by the read date — 22% past it, not "still coiling").
     **Fixed**: `vcp_last_leg_high`/`vcp_last_leg_low` + `vcp_in_range`.
  3. **The staleness check itself was a same-day snapshot** — missed a breakout
     that had ALREADY happened and since retraced back inside the old range on a
     retest (NVDA: closed above the leg's $188 high from Dec 23 through Jan 7,
     then pulled back to $185.81 by Jan 13 — still "in range" by a same-day check,
     but the base had already resolved on Dec 23). **Fixed**: `vcp_in_range` now
     scans EVERY close since the leg's low confirmed (a new bounded `_closes`
     deque on the class), not just today's — "the moment it has expanded it
     should end," even if price later drifts back inside (user's framing).
  4. `_last_legs` (the actual completed-leg pivots) is now a public attribute —
     needed because the viz function was drawing the raw zigzag including a
     dangling, not-yet-paired swing high that was never part of any completed leg
     (a real display bug, not a calculation bug — fixed in `eval/viz.py`, see F).

**D. Opening-range / intraday layer — `data/intraday.py`, `eval/opening_range.py`.**
yfinance keeps only ~2.9yr of 60-minute bars (a hard wall — cannot backtest
pre-2023 intraday; this is a forward/recent-window layer only, not a full-history
one). `intraday.save` MERGES on each fetch so coverage extends forward over
repeated ingests. Full-universe hourly ingest: **1,120/1,175 tickers**. New
`--sectors`/`--intraday`/`--sectors-only`/`--intraday-only` flags on the main
`data/ingest.py`; `rebuild_datasets.py` now passes `with_opening_range=True`.
`opening_range_metrics()` computes first-hour (09:30-10:30) decision-time metrics
(`or_high/low`, `gap_pct`, `rvol_open_{20,50}`, `lod_dist_pct`) vs rest-of-day
outcomes (`reorh`, `move_high_pct`/`move_1030_pct`) — never conflated.
`attach_opening_range()` JOINS these onto setups post-hoc by date (default
`entry_date`=T+1, our real fill day; or `signal_date`+`prefix='t0_'` for the
breakout day itself) — **never as an on-bar signal-bar feature**, since at the
signal bar the entry day's open hasn't happened (would be a leak).

Investigated whether our flat-4% breakouts are even real expansions: **46% of
liquid setups sit in stocks whose ATR is already >4%; ~16% of ALL setups moved
LESS than a full ATR on the "breakout" day** — real evidence the flat trigger
under-discriminates, same story as the stop. On the entry-timing side: of
investable+liquid breakouts, only **~23% offer a first-hour LoD<0.6×ATR entry**
(his tight-entry gate) at all; RVOL and first-hour tightness are ANTI-correlated
in the raw population (a quiet price hour is usually a quiet-volume hour too) —
this looked like a real finding but turned out to be **mostly explained by
`stuck_below_resistance` (73% of the raw population)**, a correlation the user
correctly caught as NOT yet established as causal — set aside, not resolved.
RVOL definition: web-checked Jeff Sun's own published indicator distinguishes
RVol (ambiguous exact formula, page doesn't disclose it) from a separate PVol
(projected full-day total) — **could not verify our `rvol_open_{20,50}`
(hour-1-vs-trailing-hour-1-average) exactly matches his RVol**, but it does match
the generic industry-standard convention (same-time-of-day cumulative comparison).
An earlier claim in this session that his RVOL means "% of a normal day already
done" was investigated and **retracted** — treat as unresolved, not fixed.

**D2. Worked example — combining every session-6 tool on two real candidate
entries (NVDA Sep 24 vs Oct 7, 2024, both user-identified as "good entries" by
eye).** The point of this exercise: show HOW the tools combine, and that they
don't all agree — which is itself the finding.

*Daily tools* (`tight_short`/`tight_long`/VARS/box/VCP):

| | Sep 24 | Oct 7 |
|---|---|---|
| close | $120.87 | $127.72 |
| tight_short (5d/50d) | 76.0 | **10.0** ← disagrees |
| tight_long (15d/200d) | 90.5 | 96.0 |
| VARS vs SPY (6m) | −0.93 | +2.52 |
| VARS vs group (6m) | 4.50 | 7.98 |
| box_len / height | 4d / 4.20% | 2d / 2.24% |
| vcp_contracting / run | True / 2 | True / 2 |

`tight_short` actively DISAGREES (76 vs 10) — Oct 7 was already a few days into
the rally, so its last-week reading reads "loose," while `tight_long`/VCP/VARS/box
all agree it's a good entry. This is the concrete case for keeping both tightness
horizons (§5j.C) — the short one would have wrongly excluded Oct 7. Caveat found
alongside: Oct 7's "2-day box" traced back to a box that was CHURNING (resetting
every 1–3 days during an active rally, never holding >3 days) — a short box
during a fast move is a much weaker signal than one that's held for many days;
`box_len` (how long it's held) likely matters as much as `box_height_pct`.

*Opening-range first hour* (`eval/opening_range.py`, the same two dates):

| | Sep 24 | Oct 7 |
|---|---|---|
| or_open → or_close | $116.51 → $115.98 (down) | or_open = or_low ($124.95, never dipped) |
| or_close_pos | 8.1% (near hour's low) | 80.2% (near hour's high) |
| move by 10:30 | −0.22% | +1.89% |
| RVOL (hour 1) | 0.59× (below average) | 1.00× (average, NOT elevated) |
| lod_dist_pct | 0.579% | **0.0%** (the tightest possible reading) |
| day path | weak open, strong close (close $120.95 near day-high $121.80) | immediate break, high $130.64, slight fade to $127.79 |

Two genuinely different entry ARCHETYPES: Sep 24 is a quiet-build day (matches
the multi-day held box — strength arrives late in the session); Oct 7 is a
decisive, zero-hesitation break (matches the user's "breaking the declining
trendline" visual read) — but **RVOL does NOT confirm Oct 7** (1.00×, merely
average). A strict RVOL gate, applied literally, would have passed on a textbook
price-action entry. Real tension between his stated rules worth remembering when
building the intraday trigger properly: the price-action gate and the volume
gate may need different logic for a "coiled base" entry vs a "clean break" entry,
not one uniform rule.

**E. The reclaim-entry prototype — `eval/reclaim_entry.py`, NOT validated at
scale.** A standalone, fully additive alternative to the flat-4% detector, built
to test his real trigger: box-relative anchor (ORH clears `box_high` by
≥`min_expansion_pct`, replacing the flat 4%) + first-hour RVOL + an intraday
dip-then-reclaim → **fixed to just "the reclaim happens," no dip required** (the
dip precondition was near-vacuous and excluded legitimate hold-then-push-through
cases) → LoD<`max_lod_atr`(0.6) gate AT the reclaim moment (his actual rule,
finally implementable now that intraday data exists). Exit reuses the REAL,
tested `AtrStopExit` by treating T0 as the buy bar with the intraday reclaim
price — **this required fixing a genuinely bad first-draft bug**: the original
exit sim used a static, never-trailing stop held over the ENTIRE remaining price
history, which is nearly guaranteed to eventually "lose" no matter how good the
entry (proof: AGQ ran to +255% peak, the static sim recorded it as a −5.5% loss
because the stop never moved up and the position was still open a year later).
Iterating with real (60-ticker sample) numbers: −0.81R (broken exit, dip-required
reclaim) → −0.52R (dip-requirement removed) → **+0.068R** (box changed back to
the flat 5% — an ATR-relative box was tried and reverted, see below — plus the
LoD<0.6ATR gate added). **Still a tiny, unswept sample — not a validated edge.**
A box-tightness-in-ATR-units variant was built, then explicitly reverted per the
user: containment (the box itself) should stay the flat ~5% he actually uses
(*"swing-setup tightness within 5% above and below"*, his own documented number);
volatility-relative scaling belongs on the separate CONTRACTION side, not blended
into containment — this is why `tightness_score.py`/`vcp.py`'s guards exist
instead. Two structural findings from the daily-vs-first-hour analysis worth
remembering: by 10:30 on the breakout day, **~52% of breakouts have already
reached the ≥4% threshold, and price has typically already moved ~0.9 ATR** —
most of a breakout's move front-loads into hour one, which is exactly why a T+1
entry (our current fill) structurally cannot be tight, and why this intraday
layer is the only path to his actual mechanic.

**F. Trade visualizer — `eval/viz.py plot_vcp_legs()` (new).** Draws the ACTUAL
`VCPFootprint` state on real candlesticks: EVERY distinct qualifying episode in a
`[start, end]` range (not just whichever is live at `end` — an earlier version
only showed one snapshot and silently dropped multi-episode ranges), each leg
labeled with its % depth, restricted to ONLY the legs that count toward
`vcp_contracting_run` (an earlier excluded leg is dropped entirely, not dimmed —
user's explicit call). Optional overlays, both opt-out: short-term ATR tightness
shading (green, min-consecutive-days filtered — a 1-2 day flash is real but not
sustained compression, `min_tight_days` param) and a VARS sub-panel below the
main chart (its own axis — a continuous number doesn't belong crammed onto the
price panel). `viz.plot_vcp_legs('TICKER', start=..., end=...)` — see the
function's own docstring for every knob.

**Data ingest state at session end**: sectors 1,175/1,175 (full universe);
hourly 1,120/1,175 (~3yr each); 45/49 benchmark ETFs cached (BJK/EATZ/IGN/RTL
failed — delisted/renamed, negligible coverage impact). **⚠ The daily price
cache is stale** (~7 weeks behind as of when checked this session) — `store.get`
never refreshes already-cached tickers, only `--force` does (slow, whole
universe); worth a refresh before the next full rebuild.

**Not done / deferred**: a full universe rebuild carrying ALL of this session's
columns (R, VARS, contraction, VCP with both fixes, tightness scores, opening
range) — the existing `setups_all_wk1035_atr1` predates the VCP/tightness fixes.
`reclaim_entry.py` only tested on 60 tickers. The portfolio simulator (§7 below,
still THE main frontier) — untouched. Jeff Sun's exact RVOL formula — unresolved.

## 5k. Session 7 — the portfolio simulator (v1), trade-book tooling, ranking
##      experiments, tail analysis, volatility-contraction scan, universe expansion

Built the layer §7 called "THE main frontier" (a capital- and slot-constrained
portfolio replay), then used it to test **selection/ranking**. Everything below runs
on `runs/setups_intraday_clear2_d4.parquet` (23,644 setups × 298 cols, entry
2023-09-27 → 2026-07-08; signal on T-1, fill on T0 after 10:30). **R here = the
low-of-day stop distance** — `stop_dist_pct = (entry/lod_stop − 1)×100` (a recorded
column) — NOT the ATR-based `metrics.add_r_metrics` R. `r_multiple_lod = return_pct /
stop_dist_pct`. Per the user: **report expectancy on MEANS** (Jeff Sun's published
numbers — 24.8% win, +3.70R avg winner, −0.78R avg loser, +0.328R — are means; the
winner distribution is so right-skewed that medians mislead: winners mean 4.88R vs
median 2.46R).

**A. The working filter stack ("clean universe").** (1) drop 15 tickers FFAI, EDBL,
ELPW, SGLY, AEMD, IESC, ALUR, NRDY, BANL, CIIT, IPW, MIDD, LBRDA, LBRDK, B (AEMD/ALUR/MIDD
aren't in the data; 113 rows removed, 23,644→23,531); (2) **hard gate
`weekly_trend_state != 'downtrend'`** (→15,740; the 21 `None` rows are kept);
(3) `avg_dollar_vol_20d > 500k` and `entry_price > 5` (→15,233: win 22.69%, median
−2.28%); (4) `vars_6m > 0` (→**7,797**: win 23.80%, mean +1.06%, median −2.24%) —
the best candidate pool found. Setups per quarter run ~1,650–2,550 (2023Q3 = 110 and
2026Q3 = 200 are partial quarters).

**B. Single-feature checks on that pool (win rate / expectancy; all weak).**
- *VARS/RS (split at 0, outperform vs underperform):* vs **SPY** (`vars_spy_*`) and
  raw `rs_vs_spy_*`, the **under**performers win MORE at every horizon (raw 3m: 21.3% vs
  25.7%; 6m: 21.2% vs 25.4%) — a mild mean-reversion in raw RS. vs the **group
  benchmark** (`vars_*`, vol-adjusted + peer-relative) the **out**performers win more
  at every horizon, strongest at **6m (23.80% vs 21.10%, P75 −0.52 vs −1.06)** — the
  only RS flavor with a usable edge, hence `vars_6m` as the working rank signal.
  `vars_3m>0 AND/OR vars_6m>0` add nothing over `vars_6m>0` alone.
- *Tightness length (on `vars_6m>0`, expectancy in R):* `tight_body_days_2pct` and
  `tight_range_days_2pct` — win rate rises monotonically with more tight days
  (body 21.4%→28.3%, range 22.5%→32.0%) while winner size falls (win-R 4.7→3.6 /
  5.1→2.9) → a win-rate-vs-payoff dial, expectancy flat/peaked mid-range (range 9-11
  days best at 0.523R, 12+ worst at 0.289R). `range_length_days`: expectancy **peaks
  at 7-8 days (0.513R)**, dead zone at 13-15 (0.088R, n=343), 16+ 0.527R (n=336).
- *VCP percentile tightness (`vcp_tight_{swing,closing}_{atr,adr}`)* — buckets <30 /
  30-50 / 50-70 / 70+: win rate 21.4–23.8%, no monotone relationship, expectancy
  0.41/0.25/0.11/0.15R for swing-ATR. **Not a discriminator on this universe**
  (`vcp_tightest_pct` 0-2 bucket shows 1.6R but n=38).
  "swing" = spread on intraday highs/lows (full wicks); "closing" = closes only.
- *Daily `trend_state != downtrend` + `stuck_below_resistance == False` as extra
  gates* on the 7,797 pool → 3,410 left, win 21.20% (↓), mean +1.18% (↑ slightly).
- *"Weekly uptrend now OR previous week, never downtrend"* on the 9,018 pool (clean
  universe + vars_6m>0, no weekly gate): passes 6,367 → win 22.74%, mean +0.96%; the
  FAILING group (basing with no recent uptrend) wins MORE: 28.93%, mean +2.33%, tail
  0.60% vs 0.53% — counter-intuitive, **not yet checked by year/quarter**.

**C. Expectancy by quarter, `vars_6m>0` pool (mean of `r_multiple_lod`).** Overall:
win 23.80%, avg winner +4.88R, avg loser −1.01R, **expectancy +0.394R (+1.06%)**, 1R =
**2.84% median / 3.23% mean** (P25 2.14, P75 3.86, P90 5.20; stable by quarter,
2.9–3.4% mean). By quarter (n / win% / expR): 23Q3 43/4.7/−0.84 · 23Q4 793/34.3/+1.50 ·
24Q1 709/28.9/+1.36 · 24Q2 511/21.5/+0.29 · 24Q3 938/18.9/+0.13 · 24Q4 604/22.7/+0.23 ·
25Q1 614/10.6/−0.63 · 25Q2 574/28.7/+0.87 · 25Q3 810/24.8/+0.12 · 25Q4 621/24.8/+0.17 ·
26Q1 604/17.2/−0.23 · 26Q2 907/26.2/+0.41 · 26Q3 69/37.7/+0.01. Losers cluster at
−0.97R (the LoD stop works as designed); the edge is entirely the fat right tail.

**D. The portfolio simulator — `eval/portfolio_sim.py` (v1).** Event-driven replay of
a candidate pool as ONE portfolio. **Sizing = Jeff Sun's rule**: `position_$ = equity ×
(risk%/100) ÷ (stop_dist_pct/100)`, capped at `max_position_pct` of equity and at free
cash (tight stop → bigger position, ~same $ risk each). Mechanics: candidates grouped by
`entry_date`; exits due on/before that date are processed FIRST (freeing slots/cash);
tickers already held are skipped; the day's candidates are sorted by `rank_by`
(`na_position='last'`) and filled top-down until slots/cash run out — **ranking only
ever competes WITHIN a day, never across days**; leftovers are logged in `missed` with
reason `no_slot` / `insufficient_cash`. Each trade's `return_pct`/`exit_date` is taken
as fixed from the independent per-setup backtest (no re-simulation under portfolio
constraints, no price impact). **Equity is cost-basis between events — it only moves at
exits (no daily mark-to-market)**; fine for sizing/capacity, not a real NAV curve.
*Baseline run* ($10K, 0.20% risk, 8 slots, 25% cap, `vars_6m>0` pool, rank `vars_6m`
desc): **$15,583.23 (+55.83%, ~17%/yr by hand over 2.78y)**; 391 taken / 7,131 missed
(94.8%, **all `no_slot`, zero `insufficient_cash`**); win 25.58%; avg position $889, max
$3,373. Time-weighted **49.75% invested / 50.25% cash** (median 7 of 8 slots open) —
slots, not cash, are the binding constraint; R-based sizing keeps positions small
(~9% of equity). Calendar years: 2023 (Sep→) −1.29% · **2024 +43.13%** · 2025 +1.47% ·
2026 (→Jul) +8.70% — almost the whole return is 2024.

*Jeff Sun's portfolio rules we have NOT yet modeled* (from `artifacts/Jeff_Sun_Process_
Report.html`; for sim v2): risk **0.15–0.25% of equity per trade (explicitly NOT 1%** —
1% is mechanically infeasible with tight stops); positions **5–15 (≤~12 typical), max 3
NEW per day, ~3% new risk ("portfolio heat") per day**; size 15–20% of equity (≤30%) at
a <60%-of-ATR LoD stop; **dynamic risk** — cut % risk after a drawdown month; **regime
brake** — add no risk when SPY is >4×ATR% above its 50-MA; 3-Stop tranches → avg loss
≈ −0.67R; T+3 shave 33% + move stops to breakeven; day-4+ mental stop on the 10-MA.
Capital need falls ~half per doubling of ADR% (INTC 7.7% ADR needs 5.5% of equity at
0.25% risk; INTW 15.6% ADR needs 2.75%). Our sim has none of the caps/brakes yet.

**E. Ranking/selection experiments (same sim params).** Every blend lost to plain
`vars_6m`:

| ranking / gates | final equity | return | win | trades |
|---|---|---|---|---|
| **`vars_6m` desc**, pool `vars_6m>0` + weekly gate | **$15,583** | **+55.8%** | 25.6% | 391 |
| `stop_dist_pct` ascending (lowest R first) | $13,698 | +37.0% | 21.9% | 502 |
| `vars_6m` + gates daily-not-downtrend & not-stuck-below-resistance (3,410 pool) | $14,706 | +47.1% | 20.8% | 418 |
| composite (vars_6m 1, ext_50sma_atr −1, close_ext_10ema −1, close_ext_20ema −1, tight_range_days 0.5) + weekly+daily+not-stuck-6mo gates, no vars gate (8,382 pool) | $12,429 | +24.3% | 24.9% | 393 |
| same composite, extra gates removed (weekly only, 15,233 pool) | $10,611 | +6.1% | 28.7% | 428 |
| composite vars_6m 1 / close_ext_20ema −1 / tight_range_days 0.5 (weekly gate) | $10,364 | +3.6% | 28.0% | 379 |
| same + hard gate `close_ext_20ema_pct > 0` (above 20-EMA, prefer less extension) (11,890 pool) | **$9,559** | **−4.4%** | 22.2% | 405 |

Pattern: blending ANY extension-from-MA term into `vars_6m` raises win rate and
destroys return (win-rate-for-tail trade, §5f.H again). Extension features carry ~0
signal (below), so mixing them 50/50 into a ranking that has some signal just dilutes
it. **Open/untested**: `vars_6m` alone + a tiny-weight (0.1–0.2) tight-days tiebreaker;
raising `max_positions` (Jeff Sun runs 5–15).

**F. 2025 post-mortem (the weak year).** 2025 taken: 154 trades, win 26.0%, mean return
−0.01%, ~$729 summed P&L; **missed 2,370, win 22.4%, mean +0.30% — the skipped pool was
better than the taken pool.** Biggest 2025 setups skipped (all `no_slot`, all LOW
`vars_6m`, so never ranked into a slot): AEVA 2025-05-02 +265.8% (vars_6m 6.7), LFMD
04-17 +134.4% (4.0), AMLX 07-07 +107.1% (5.6), QBTS 09-09 +101.7% (19.9), RBLX 04-22
+89.5% (8.4). (Taking every missed one at the avg $979 size ≈ +$6.9K — illustrative of
what was left on the table, not a realistic policy.) In the 2025 pool (n=2,619) every
feature's correlation with `return_pct` is |r|<0.03 and big-winner (top 5%, ≥16.5%,
n=131) medians equal everyone else's; win/loss point-biserial is weak (best:
`tight_range_days_2pct` +0.079, `stop_dist_pct` +0.070). Reading: 8 slots at 95–97%
miss rate + a rank signal uncorrelated with winner MAGNITUDE = high year-to-year
variance; **not** a volume spike in 2025.

**G. Tail analysis — `peak_return_pct` (the user flagged: it's MFE reached INSIDE the
trade, not capturable — diagnostic of setup potential only).**
- `weekly_trend_state` does NOT separate the tail here (% peak>100%: downtrend 0.46,
  uptrend 0.43, basing 0.38; n=22,204 clean universe, no weekly/vars gate) → the §5h
  TSLA "Stage-2 separates the tail" result **does not generalize** across the universe.
- Correlation with `peak_return_pct` (9,018 pool): `stop_dist_pct` +0.119 (strongest —
  volatility is a precondition for a big move, not a selector), `vcp_tightest_pct`
  +0.058, `vars_6m` +0.041, `tight_range_days_2pct` −0.046, `tight_body_days_2pct`
  −0.042, `range_length_days` −0.037; **more tight-range days → FEWER >100% peaks**
  (0.76/0.11/0.30/0/0 % across 0-2/3-5/6-8/9-11/12+) — opposite of "tighter = coiled".
- `ext_50sma_atr`: corr 0.004 (peak) / 0.0003 (return); Jeff Sun's <4×ATR% gate: under
  24.5% win vs over 21.1%, tail 0.46% vs 0.53% — no separation. EMA-extension family
  likewise dead.
- **★ `stuck_below_resistance` / `stuck_under_resistance_{6_mo,1_yr,2_yr}` — the only
  consistent tail lever found** (9,018 pool; every window agrees): NOT stuck → lower win
  rate but 3–8× the big-peak rate; stuck → higher win rate, tail nearly gone.

| flag | group | n | win | % peak>50% | % peak>100% |
|---|---|---|---|---|---|
| stuck_below_resistance | not stuck / stuck | 4,023 / 4,995 | 22.2 / 26.4 | 3.03 / 1.88 | 0.85 / 0.32 |
| 6 mo | not stuck / stuck | 5,969 / 3,049 | 23.1 / 27.5 | 2.88 / 1.44 | 0.79 / 0.10 |
| 1 yr | not stuck / stuck | 5,285 / 3,733 | 22.3 / 27.8 | 2.95 / 1.61 | 0.76 / 0.27 |
| 2 yr | not stuck / stuck | 4,592 / 4,426 | 22.2 / 27.0 | 3.01 / 1.76 | 0.85 / 0.25 |

Matches all four traders' "clear overhead / room to run". **Not yet used as a ranking
preference** — only tested as a hard gate (E, row 3), which cost return.

**H. What Sun / Kell / Minervini / Weinstein say about SELECTION (research pass over
the repo's artifacts).** *Sun*: "RS first, setup second"; VARS vs the right benchmark;
extension gate <4×ATR% from the 50-MA applied BEFORE RS ranking; group/industry
leadership top-down; Master (~200) → Stalk (~142) → Focus (~3–5) funnel; a name is held
back for earnings ≤5d, loose price, 10/20-MA not caught up, declining 200-MA; his
"back watchlist" found ~80% of names that ran after leaving the stalk list would have
failed the breakout anyway. *Kell*: RS vs index (best reads found DURING corrections),
sales growth 25%+, avoid <$10, extension-count off the WEEKLY 10-EMA (2nd extension =
worse entry), top-weight the 3–7 best of 8–12 names. *Minervini*: thin in-repo — only
the Stage-2 trend template (`strategy/stage2.py`) and a 10% base-tightness tolerance
(`artifacts/master_base_breakout_strategy.md`); his RS-rating/SEPA/VCP-count rules are
NOT documented here. *Weinstein*: 4 stages, only trade Stage 2 (`artifacts/
traderlion_guide.txt` ~L748-850; repo impl `agents/base_breakout_strategy/
stage_analysis.py`). The repo's own prior conclusion (§5c/e/f/h/J) matches today's:
single scalar entry features are weak win-rate-only levers; their real edge is
portfolio-level (tiered watchlist funnel, concentration, top-weighting a short leader
list). **Not built**: a group/industry relative-strength feature ("leading stocks in
leading groups") — the one selection lever none of our columns capture.

**I. Trade-book tooling.**
- `eval/build_trade_book.py` — runnable (`python -m agents.evaluator.eval.build_trade_book`)
  with a CONFIG block: ticker exclusions, hard gates (`REQUIRE_WEEKLY_NOT_DOWNTREND`,
  `REQUIRE_DAILY_NOT_DOWNTREND`, `REQUIRE_NOT_STUCK_RESISTANCE_6MO`,
  `REQUIRE_ABOVE_20EMA`, `MIN_VARS_6M`), `COMPOSITE_WEIGHTS` (+/− weight = higher/lower
  preferred) → `compute_composite_score`, sim params, `RANK_BY`. Writes the taken trades
  (entry/exit price, `exit_reason`, `stop_dist_pct`, `days_held`, $ size/P&L, equity) to CSV.
  **⚠ The CONFIG on disk is left at the LAST (losing) experiment** — composite +
  `REQUIRE_ABOVE_20EMA=True`, writing `portfolio_trade_book_composite.csv`. To reproduce
  the best run: `MIN_VARS_6M = 0.0`, `REQUIRE_ABOVE_20EMA = False`, `RANK_BY = "vars_6m"`,
  `OUT_PATH = ".../portfolio_trade_book_vars6m.csv"` (that CSV, 391 trades, is kept and
  is **the best trade book so far**: +55.8%, win 25.58%; the `_composite.csv` is the worst).
- `eval/tradebook_viewer.html` — standalone local viewer (double-click; PapaParse from
  cdnjs, nothing uploaded): drag in any CSV with `ticker, entry_date, exit_date,
  return_pct` (optional `dollar_size, pnl_dollars, entry/exit_price, exit_reason,
  days_held`; common aliases accepted). Position-timeline Gantt (bar color = return) +
  sortable log; ticker search; winners/losers; **period filter (year/quarter/month) that
  also ZOOMS the timeline with month ticks**; **click a bar/ticker → modal** showing the
  live `viz.plot_ticker` chart (start = entry−90d, end = exit+30d,
  `recency_window_days=252`, `label_trades=True`) or a copy-paste notebook command
  fallback. The popup shows the server's actual error text (not a generic "unreachable").
  An OPTIONAL feature-file override field (localStorage, in the loader and the popup,
  sent as `?source=`) exists but should be left blank — the server defaults itself.
- `eval/chart_server.py` — stdlib `http.server` on 127.0.0.1:8765, `/chart?ticker=&start=&
  end=&recency_window_days=&label_trades=&source=` → PNG from `viz.plot_ticker(out=BytesIO)`.
  `matplotlib.use("Agg")` (required off the main thread — the macOS backend crashed) + a
  lock around pyplot; per-path DataFrame cache; defaults to `SETUPS_PATH` (imported from `build_trade_book`, so changing that one constant moves the charts too); `--trades`
  overrides it. Verified by `curl`, including launched from another directory with no
  flags/`source` (valid 1760×1045 PNG with shading/resistance/markers);
  the in-app browser blocked the image request (`ERR_BLOCKED_BY_CLIENT`) so the
  **browser→server image path was never verified end-to-end** (expected fine in a normal
  browser). Note `plot_ticker` given the full features file draws ALL setups for that
  ticker, not just the portfolio-taken ones.
- A static artifact of the baseline book exists (claude.ai/code/artifact/71cb6c5b-bb10-
  4636-832e-dd504a737371) — now stale vs the local viewer (no period filter/modal).

**J. Volatility-contraction scan — `scan_volatility_contraction.py` (top level;
decoupled from the box/breakout strategy).** Walks every cached US ticker's daily history
through the same providers with NO box/breakout requirement; emits one row per
contraction-EPISODE START (first day a flag turns on), 3-yr window, then the same
liquidity/price filter. Two flags, kept as separate columns: **`tight_pctile_flag`** =
any of the 4 SHORT `VCPTightnessScore` flavors (5d spread ÷ 20d ADR/ATR, percentile vs its
own trailing 50d) ≥ `TIGHT_PCTILE_THRESHOLD=90`; **`vcp_structural_flag`** =
`VCPFootprint.vcp_contracting`. The user explicitly rejected `atr_contraction_pct` ("now
vs 40 bars ago") as "not a great calculation" — don't use it. Output
`runs/volatility_contraction_events.{parquet,csv}`: **58,523 events, 1,175 tickers,
2023-09-11→2026-07-08 (35 cols)**; tight-pctile only 47,624 · VCP-structural only 9,534 ·
both 1,365 (the two definitions barely overlap); by year 2023 6,352 · 2024 20,206 ·
2025 21,504 · 2026 10,461. Columns: ticker, date, close, avg_dollar_vol_20d;
`above_{10ema,20ema,50sma,200sma}` (close > MA; NaN 0/0/123/902 — short warm-up);
the 2 flags + 4 raw tightness scores + `tight_pctile_max_score`; `vcp_num_contractions`,
`vcp_tightest_pct`, `vcp_depth_ratio`; trailing `ret_{1,3,6,12}m`, `rs_vs_spy_*`,
`vars_spy_*`, `vars_*`, `rs_benchmark` (all point-in-time). **It contains NO forward
outcomes and nothing has been analyzed on it yet.** `vcp_num_contractions == 1` is a
warm-up state, not a weak signal (`vcp_contracting` needs ≥2 legs; ratio is 1.0). One
contraction = one confirmed swing-high→next-swing-low leg off the highest swing high in the
120-bar zigzag, capped at 4 legs.

**K. Universe expansion — started, INTERRUPTED.** `us_universe()` was 1,222 (622
`all_tickers.txt` non-.NS + 600 `universe_extra.txt`), 1,175 cached — only 47 uncached, so
"1,000 more" needed new names. Pulled NASDAQ Trader's symbol directory
(`nasdaqtrader.com/dynamic/SymDir/{nasdaqlisted,otherlisted}.txt`), kept ETF=N / Test=N,
NYSE+NYSE American only from `otherlisted`, dropped names containing
Warrant/Right/Unit/Preferred/Depositary Receipt/Note, ≤5-char alnum → 5,998 clean, **4,922
not already in our universe**; appended the **first 1,000 alphabetically (AA…CNOB — a
deterministic cut, NOT a quality/liquidity ranking; the directory has no cap/volume)** to
`data/universe_extra.txt` (now 1,600 names → `us_universe()` = **2,222**). The background
`ingest` died with the session: cache is 1,259 files (≈30 of the 1,000 fetched);
**1,017 universe tickers are uncached** (the new names + ~17 earlier yfinance-empty
ones). 3,922 further candidates remain beyond this batch. Sectors (1,175) and hourly
(1,120) caches don't cover the new names yet, so their VARS group benchmark falls back to
SPY until `--sectors-only` / `--intraday-only` are run.

**Not done / deferred (explicit asks left open):** (1) viewer summary tiles do NOT
yet react to the period filter and have no R-metric tiles or **CAGR** — both were
requested; (2) test `max_positions` 12–15 (esp. for 2025); (3) use `stuck_under_
resistance_6_mo == False` as a RANKING preference rather than a hard gate; (4) check the
"basing, no recent uptrend wins more" finding by year/quarter; (5) attach forward
returns/exits to the contraction-event dataset and analyze trailing ret/RS/VARS vs
outcome; (6) finish the universe download → sectors/intraday → rebuild setups + rescan;
(7) the group-RS ("leading groups") feature; (8) real-browser check of the chart-server
image path; (9) refresh the stale daily price cache (end dates vary by ticker — AAPL
ends 2026-06-12, events run to 2026-07-08).

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
- **Session-4b/c/d code** (§5f/§5g/§5h): `engine/exits.py` — `ScaleOutSwingTrailExit`
  (swing-trail + %-giveback cap + sell-into-strength scale-out; the giveback cap
  clips the tail, net-negative) and `ReactiveDay3Exit` (2-red-closes kill on any base
  exit; net-negative — patience wins). **`eval/viz.py`** — the trade visualizer
  (`plot_trade`/`plot_trades`/`plot_ticker` + `_stage2`/`_exh_stages`/`_weekly_regime`;
  Stage-2×weekly shading, volume, exhaustion-stage ①②③, price ticks, dates). Exit
  A/B datasets in `runs/`: `setups_all_wk1035_{swing8,pure_swing5,scaleout,trail5gb,
  day3_swing8,day3_weakness}` (row-aligned, only the exit differs). **The Stage-2
  rejected-ceiling rule lives in `viz._stage2` only — not yet a strategy provider.**

## 6c. New code added (session 6) — quick reference

- **New files**: `strategy/volatility.py` (`Volatility` — R unit, contraction),
  `strategy/vars.py` (`VolAdjRelativeStrength`), `strategy/benchmark_map.py`
  (ticker→benchmark resolver), `strategy/vcp.py` (`VCPFootprint`, pre-existing but
  heavily fixed this session), `strategy/tightness_score.py` (`VCPTightnessScore`,
  new), `data/sectors.py`, `data/intraday.py`, `eval/opening_range.py`,
  `eval/reclaim_entry.py` (new, prototype). All wired into `StageRangeStrategy`
  via the same `.update(bar)`/`.features(bar)` provider pattern as everything else.
- **New exits** (`engine/exits.py`): `AtrStopExit`, `StructuralAtrExit`,
  `ThreeStopExit` — see §5j.A for the finding each one produced.
- **`range_strategy.py`** (additive only, gate/trigger logic untouched, equivalence
  green): `box_high`/`box_low`/`expansion_box_move_pct` now recorded on every setup
  (raw reference fields, needed by `reclaim_entry.py`'s box-relative anchor).
- **`eval/metrics.py`**: `add_r_metrics()`.
- **`eval/viz.py`**: `plot_vcp_legs()` (new) + a `_candles()` hollow-candlestick
  helper reused by it.
- **`data/ingest.py`**: `--sectors`/`--intraday`/`--sectors-only`/`--intraday-only`
  flags. **`rebuild_datasets.py`**: now passes `with_opening_range=True`.
- **Full detail, findings, and the dead ends not to repeat: §5j.**

## 6d. New code added (session 7) — quick reference

- **New files** (all additive — no existing strategy/engine file touched):
  `eval/portfolio_sim.py` (`simulate_portfolio`, `summarise_portfolio`, `PortfolioResult`),
  `eval/build_trade_book.py` (CONFIG-driven filters → composite rank → sim → CSV),
  `eval/chart_server.py` (local PNG server around `viz.plot_ticker`),
  `eval/tradebook_viewer.html` (standalone viewer), `scan_volatility_contraction.py`
  (top-level contraction-event scan). Details + findings: **§5k**.
- **Data**: `data/universe_extra.txt` 600 → 1,600 names (`us_universe()` 1,222 → 2,222);
  ~1,017 of them still uncached (§5k.K).
- **Outputs in `runs/`**: `portfolio_trade_book_vars6m.csv` (baseline, 391 trades),
  `portfolio_trade_book_composite.csv` (last experiment), `volatility_contraction_events.
  {parquet,csv}` (58,523 events).

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
- ✅ Session 4b/c/d (§5f/§5g/§5h): the **exit is a monotone win-rate↔expectancy dial**
  — `pure SwingLowTrailExit(8)` best (3× default) but **user adopts `StopAndWeaknessExit`
  as the DEFAULT** (won't hold through downtrends; swing-vs-weakness is a portfolio-sim
  question). Reactive day-3 kill & giveback caps both net-negative (clip the tail).
  Batting screen → 32% holdout; **10-EMA extension** (weekly, sharpest failure filter);
  the **`prev_trend_state` "downtrend→basing = worse" hypothesis REFUTED**. Built the
  **trade visualizer** and the **STAGE-2 rejected-ceiling gate** (matches the user's
  discretionary TSLA read; **DECISION: only trade Stage 2**) — §5h.
- ✅ Session 6 (§5j): Jeff Sun's R mechanism (ADR%/ATR%, `AtrStopExit` beats flat-4%
  universe-wide) + **the central finding — our avg winner (+3.59R) already matches
  his (+3.70R); the whole gap is loss management (−0.96R vs his −0.78R) + the
  portfolio layer, not the entry signal**. VARS + sector benchmarking (full universe).
  Rebuilt tightness around his ACTUAL tool (`VCPTightnessScore`, short+long horizon)
  and fixed 3 real `VCPFootprint` bugs (trailing-run, staleness, expansion guards).
  Built the opening-range/intraday layer (~3yr, hard yfinance wall) and a `reclaim_entry.py`
  prototype of his real intraday trigger — small-sample only, not yet validated.
- ✅ Session 7 (§5k): **portfolio simulator v1** (`eval/portfolio_sim.py`, Jeff Sun
  equity-risk sizing, slot/cash-constrained): `vars_6m>0` pool ranked by `vars_6m` →
  $10K→$15.6K (+55.8%, 391 trades, 95% of candidates missed, **slots not cash bind**,
  ~50% invested), but ~all of it is 2024. **Every composite ranking blend (extension
  from 10/20-EMA/50-SMA, tight days, daily/stuck gates) LOST to plain `vars_6m`** (best
  blend +24%, worst −4.4%). 2025 post-mortem: skipped pool beat the taken pool (low-
  `vars_6m` mega-winners AEVA/LFMD/AMLX/QBTS/RBLX). Tail analysis: nothing predicts
  winner magnitude except raw volatility; **`stuck_under_resistance_*` is the one
  consistent win-rate-vs-tail lever** (not stuck → 3–8× the >100% peaks). Built the
  trade-book CSV → viewer → live-chart-server loop, the decoupled volatility-contraction
  event scan (58,523 events, trailing ret/RS/VARS, no outcomes yet), and started a +1,000-
  ticker universe expansion (**interrupted — 1,017 uncached**).

**The entry-PREDICTION question is CLOSED** (can't pick winning breakouts). Entry =
a bounded consistency lever + a REGIME GATE (Stage 2). Upside = exit + portfolio.
Forward path:

0. **Validate + bake in the STAGE-2 gate** (§5h) — run `viz._stage2` universe-wide,
   test whether "only trade Stage 2" earns its keep (mean AND tail, train/holdout); if
   yes, wire it into a strategy provider. Then layer weekly-uptrend + higher-highs.
1. **Flip the weekly default to 10/3/5** in `StageRangeStrategy` (validated §5e.C),
   and consider a build with `min_days≥6` (3-day bases drag the book, §5e.D). Also
   still outstanding: a `min_dollar_volume=500_000` build (use `vol>500k` as the base
   filter for now via `trend_eval`). Exit default = `StopAndWeaknessExit` (§5g).
1b. **NEW (§5j): refresh the stale daily price cache, then a full universe rebuild**
   carrying R/VARS/contraction/the FIXED VCP+tightness scores/opening-range together
   (the existing `setups_all_wk1035_atr1` predates the VCP/tightness fixes) — then
   actually evaluate whether VARS/tightness/VCP separate outcomes at scale, not just
   on single-ticker spot checks. Scale `reclaim_entry.py` past its 60-ticker sample.
1c. **NEW (§5k) — finish the universe expansion**: re-run the resume-safe ingest for
   the 1,017 uncached names, then sectors/intraday passes, then rebuild setups + rerun
   `scan_volatility_contraction`. Also refresh the stale daily cache first (§5j note).
2a. **NEW (§5k) — portfolio sim v2** (the sim exists now; this is its roadmap): test
   `max_positions` 12–15; try `stuck_under_resistance_6_mo == False` as a ranking
   PREFERENCE (not a gate); `vars_6m` + tiny-weight tight-days tiebreaker; a group-RS
   ("leading groups") feature; per-day (point-in-time) rather than whole-pool percentile
   ranks in `compute_composite_score`; true daily mark-to-market equity; CAGR/drawdown +
   R-metrics in the viewer tiles, filtered by period (requested, not built).
2b. **NEW (§5k) — contraction-event study**: attach forward returns / exit outcomes to
   `volatility_contraction_events` and test whether trailing ret/RS/VARS + the two flags
   separate outcomes (nothing analyzed yet).
2. **THE MAIN FRONTIER — PORTFOLIO SIMULATION** (v1 now built, §5k.D; not more entry work). This is where
   every thread now points, MORE than ever after §5j: since our per-trade R already
   matches his, the 50×-return gap is ENTIRELY sizing/concentration/pyramiding/
   compounding. Build: position sizing off the R risk unit + concentration +
   **pyramiding** into the leaders (his re-entry-after-stop mechanic, which is what
   would make `ThreeStopExit` actually work — §5j.A/E), a **patient exit** riding the
   tail (swing_trail_8 baseline; test a profit-target on high-vol setups §5b.G),
   **regime-based exposure** (QQQ/SPY as a cash/margin switch), and **leadership
   selection** (sustained trend + EPS/RS + VARS as the ex-ante correlate of a prime,
   §5e.F). Judge by portfolio CAGR/drawdown, not per-trade win rate.
3. **Layer the validated ENTRY filters as a batting/consistency screen only** (§5e.D,
   now also VARS §5j.B): base length + tightness (biggest), stage-0, buy-the-daily-base,
   RS sweet band, eps>0, low overhead, VARS 0-5% sweet band — all inverted-U, all
   ~+2–4pp win rate, none touch the tail.
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
- **§5j.** `VCPFootprint`'s internal bar counter (`self._i`) is 1-BASED (increments
  before use); when manually mapping its pivot indices to calendar dates for
  debugging, the correct lookup is `dates[idx-1]`, NOT `dates[idx]` (a 0-based
  positional array) — got this wrong mid-session tracing a pivot date, confirmed
  and fixed. `eval/viz.plot_vcp_legs` handles this correctly internally; only an
  issue when reaching into `VCPFootprint._pivots`/`._zigzag()` directly.
- **§5j.** yfinance hourly (60m) data has a HARD ~2.9-year retention wall — there
  is no way to backtest pre-2023 intraday from this source. The opening-range/
  reclaim-entry work is inherently a recent/forward layer, not a full-history one.
- **§5j.** Don't invent specifics of Jeff Sun's method from a plausible-sounding
  number (happened twice this session: a fabricated breakout-geometry example, and
  an incorrect RVOL-formula guess — both caught by the user and retracted). His
  source docs (`artifacts/jeff-sun-methodology_2.html`,
  `artifacts/Jeff_Sun_Process_Report.html`) and web search are the only valid
  sources; say "not specified" rather than construct a plausible-sounding detail.
- **§5j.** A raw correlation isn't causation even when it looks clean: an
  entry-timing "sweet spot" that later turned out to be mostly explained by
  `stuck_below_resistance` (73% of the raw population) — caught by the user, not
  by the analysis. Check what a filtered population's composition actually is
  before reading a slice as a standalone signal.
- **§5j.** The daily price cache can go stale silently — `store.get`/`store.load`
  never auto-refresh an already-cached ticker (only `--force` does). Check
  `store.load(ticker).index.max()` before trusting "as of today" analysis.
- **§5k.** `compute_composite_score` ranks each feature over the **WHOLE candidate pool**
  (`df[col].rank(pct=True)`), NOT within each day and NOT point-in-time. (It was
  described as per-day in session chat — wrong.) For a single feature the within-day
  order is unchanged, but for blends the weighting depends on the full-sample
  distribution, and it uses future rows' values — treat composite results as
  directional, and fix before trusting a blend.
- **§5k.** Signed extension columns (`close_ext_{10,20}ema_pct`, 31% negative, down to
  −71%): "lower is better" on the raw value REWARDS stocks far BELOW the average. Use
  `abs()` or gate to `> 0` first (the gate version was tried; still lost).
- **§5k.** `stuck_*`/`stuck_under_resistance_*` are bool-or-None: filter with
  `== False` (a bare `~col` or `!= True` would let the 9 None rows through).
- **§5k.** The portfolio sim treats each setup's outcome as fixed and independent;
  equity changes only at exits (cost-basis, no mark-to-market); ranking is a same-day
  tournament only. With ~95% of candidates missed, which ones win a slot dominates the
  result — expect large year-to-year swings (2024 +43% vs 2025 +1.5%).
- **§5k.** `peak_return_pct` is the in-trade MFE — a setup-potential diagnostic, not a
  capturable return. Don't read a feature's tail effect as realizable P&L.
- **§5k.** `build_trade_book.py` CONFIG on disk is the LAST experiment (losing), not the
  best config — see §5k.I for the reproduce-baseline settings. `OUT_PATH` differs per run;
  the viewer loads whichever CSV you drop in.
- **§5k.** `chart_server.py` needs `matplotlib.use("Agg")` before `viz` imports pyplot
  (macOS backend crashes on a worker thread). Background ingest/servers started inside a
  session die when it closes — re-run the (resume-safe) command.
- **§5k.** `vcp_num_contractions == 1` ⇒ `vcp_contracting` is always False (needs ≥2
  legs; ratio = 1.0) — a warm-up state, not a weak signal. Capped at 4 legs.
- **§5k.** The NASDAQ symbol directory has no market-cap/volume — the +1,000 batch is the
  first 1,000 alphabetically, not "the best 1,000". Prefer a liquidity-ranked cut for the
  next batch. New names also lack sector/intraday caches until those passes run.
- **§5k.** Corrections from the user this session: R is the **LoD stop distance**
  (`stop_dist_pct`), not the ATR-based `add_r_metrics`; **expectancy on means, not
  medians**; `atr_contraction_pct` (now vs 40 bars ago) is not an acceptable contraction
  measure — use the self-relative percentile score and the swing-based VCP.
- **§5k.** Chart-server failure mode: if the server was started WITHOUT a default
  feature file and the viewer sends no `?source=`, it returns `400 missing ?source=` (the
  viewer used to hide this behind "not reachable"). Fixed — server now defaults to
  `build_trade_book.SETUPS_PATH`; **a server started before that change must be
  restarted** to pick it up.
- **§5k.** Box-geometry columns in the intraday dataset: `range_height_pct` ≡
  `range_height_oc_pct` (identical in 100% of rows — open/close-based, hard-capped at
  5.00 by the box screen; not an organic distribution); `range_height_hl_pct` (wick-
  inclusive) is the looser one (median ~6.1%, max 53%). `range_length_days` is floored at
  4 (min-length filter), median 6, 90th pct 12, max 59.
