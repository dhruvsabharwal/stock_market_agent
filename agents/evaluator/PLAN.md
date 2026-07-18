# Evaluator + Probabilities — Design & Execution Plan

_Status: Phases 0–1 scaffolding built & smoke-tested. Strategies (Phase 2) next.
Date: 2026-06-14._

## 0. Progress

- ✅ **Engine** (`engine/`): `feed.py` (forward-only clock), `broker.py`
  (next-open fills + intrabar stops, realistic gap handling), `runner.py`
  (no-lookahead per-bar loop with warm-up), `records.py` (canonical `Bar` /
  `Order` / `TradeRecord`). Smoke test passes: buy-and-hold equity matches raw
  return to the basis point; stops enforced.
- ✅ **Data** (`data/`): `store.py` (parquet cache, US-universe helper),
  `ingest.py` (resume-safe yfinance downloader). AAPL ingested as a probe
  (1980–2026).
- ✅ **Strategy** (`strategy/`): `base.py` (`Strategy` + `StrategyContext`,
  warm-up gating, `history_df()`), `examples.py` (fixtures), and the first real
  strategy `range_strategy.py` — **`RangeBreakoutStrategy`**, the on-bar port of
  `find_ranges` + `build_range_expansion_trade` (reuses the lab `RangeExpansion`
  / `StopLoss` / `build_stop_loss` via `_lab_imports.py`).
  - **Equivalence test passes** (`tests/test_range_equivalence.py`): vs the batch
    oracle on full AAPL history, 33/33 trades matched by entry date; all 16
    comparable (≥$1) trades match exactly on return / exit-reason / days-held.
  - **Finding:** the 17 sub-$1 (split-adjusted penny price) trades diverge purely
    because the **batch code rounds prices to 2 decimals** — at ~$0.06 a stop
    rounds up to equal the buy price, so batch "stops out" at 0%. The on-bar
    version does not round and is the more correct of the two. _(Candidate lab
    fix, separate from the evaluator.)_
- ✅ **Eval** (`eval/`): `metrics.py` + `run_eval.py` → writes
  `results/<strategy>_<window>/{trades.csv,summary.json,equity.csv}`.
- ✅ **Probabilities** (`probabilities/`): `schema.py` (feature registry +
  bucketing + versioned manifest), `build_db.py`, `query.py`. Build + query
  validated end-to-end.
- ✅ **Phase 2a done:** range-only on-bar strategy + equivalence harness.
- ✅ **Phase 2c done:** `StageRangeStrategy` — range entry **gated by a Minervini
  Stage-2 trend template** (`strategy/stage2.py`) + **point-in-time EPS YoY growth**
  recorded as a feature (`data/fundamentals.py`; revenue reserved for FMP).
  - Added base hooks `_pre_decision` / `_entry_allowed` / `_extra_features`;
    range logic untouched (range equivalence test still 100%).
  - `Stage2TrendTemplate` validated incrementally vs pandas (criteria 1-3,
    11,237 bars, 0 mismatch — `tests/test_stage2.py`). Criteria 4-6 leak-free by
    construction (confirmed-lagged pivots, completed weeks only).
  - **Finding:** strict 6/6 Minervini is very tight — criterion #5 (up-week
    volume) is ~coin-flip and blocked all 8 AAPL breakouts. Added a tunable
    `min_criteria` gate (default 6); every sub-criterion is recorded so its
    individual impact can be measured. #4/#5/#6 definitions are tunable.
- ✅ **Phase 2d done:** Oliver Kell cycle + overhead supply + richer features,
  all as reusable parameterized providers on `StageRangeStrategy` (record-only):
  - `strategy/kell.py` — per-EMA **wedge pop / wedge drop** (reclaim/lose the EMA
    after N bars below/above), **EMA crossback** count (rising-EMA support tests
    since the last pop), and per-lookback **reversal extension** (lowest low) /
    **exhaustion extension** (highest high) with extension-depth vs each EMA.
  - `strategy/overhead.py` — **overhead supply** per lookback window: nearest/
    highest prior swing high above the entry price, distances, `blue_sky`.
  - Tight days now multi-threshold (`tight_pcts=[1,2]`) with **body and range**
    variants. EPS growth gained **QoQ** + sign-guards + raw-EPS columns.
  - All list knobs (`ema_periods`, `*_lookback_months`, `overhead_windows_months`,
    `tight_pcts`) expand into columns automatically; ~87 columns total.
  - Validated: range-equivalence + Stage-2 tests still green; reversal/exhaustion
    cross-checked 20/20 vs pandas rolling min/max; overhead swing highs are
    confirmation-lagged (leak-free).
- 🟡 **Note:** the Weinstein state-machine port (old Phase 2b) is largely
  unnecessary for this strategy — the Minervini trend template is mostly causal
  MAs and covers the "Stage 2" intent without the pivot state machine.

## 1. Goal

Build a new `agents/evaluator/` module on top of the existing trading logic
(stage analysis + range expansion) that serves **two distinct purposes**:

1. **Evaluation** — measure how a strategy performs **over a period** (holdout
   **2015‑2026**) across the US universe. Aggregate metrics, per-trade ledger,
   equity curve. Each strategy/config gets its own results folder.

2. **Probabilities** — build a **conditional success-rate database** from the
   in-sample window (**1990‑2015**). Answer at runtime: _"given range≈4% +
   stage 2 + sector ETF stage 2, what was the historical win rate / expected
   return?"_ Grows as features are added. Used live, point-in-time.

## 2. The non-negotiable principle: no lookahead, independent of the strategy

> **The evaluator must be unbiased no matter how leaky any strategy is.**
> Strategies will be written by hand and *will* contain accidental lookahead.
> The evaluator must make future data **physically unavailable**, so the
> guarantee does not depend on the strategy being correct.

**How we guarantee it — event-driven bar feed.** The engine owns the clock and
delivers bars **strictly forward in time**, one at a time. A strategy keeps its
own rolling state, but is only ever handed bar `T`; bars after `T` do not exist
in anything it can touch until the clock advances.

- The engine needs **zero knowledge** of any strategy's internals.
- Leakage is prevented **by construction (physics), not by trust or auditing.**
- It resolves the known leak in `compute_stage_analysis` automatically: a
  two-sided swing pivot at week `t` (see `_find_pivots_two_sided`,
  `confirmed = forward_avail == window`) can only confirm once the engine has
  delivered `t + window` bars — exactly how it would behave live. No tagging,
  no per-strategy audit. _(We explicitly rejected a `knowable_at` tagging scheme
  because it would require the evaluator to know which facts each strategy
  derives from the future — a strategy dependency we refuse to take on.)_

**The one rule strategies must obey:** a strategy is a function of the bars it
has been fed and does **no I/O of its own** (no re-downloading full history from
yfinance). Enforced by convention — the engine's bar feed is the *only* data
source a strategy is given.

## 3. Decisions locked in (from review)

| Decision | Choice |
|---|---|
| No-lookahead enforcement | **Event-driven engine** feeds bars forward; future data physically absent. Engine is strategy-agnostic. |
| Strategy interface | **Stateful `on_bar(bar)`** (standard backtest-engine model). |
| I/O enforcement | **Convention** — engine's feed is the sole data source; no external fetches in strategy code. |
| Price data source | **Local cached store** (one-time download → parquet per ticker; reproducible, offline). |
| Universe | **US subset of `all_tickers.txt`** (622 non-`.NS` tickers). _Survivorship-biased; documented, revisit later._ |
| Success metric | **Configurable** — store raw outcomes; compute P(win) at query time for any threshold. |
| Folder layout | **One `agents/evaluator/`** with a `probabilities/` subfolder; shared engine + trade ledger. |
| In-sample / holdout split | **1990‑2015** → probabilities; **2015‑2026** → evaluation. |

## 4. The unifying insight: one engine run → both products

A single walk-forward run produces a **trade ledger**: one row per trade, with
the setup features snapshotted **at the entry bar** (guaranteed past-only) plus
the outcome recorded **at the exit bar**.

```
                          ┌─ aggregate by feature buckets, window 1990-2015 ─► PROBABILITIES DB
   engine run ► ledger ───┤
                          └─ summarize, window 2015-2026 ─────────────────────► EVAL METRICS
```

Same engine, same trade definition. Only the **window** and the **aggregation**
differ. The canonical ledger row is the contract between engine, eval, and
probabilities (mirrors today's `results/trades_*.csv`: `stage`,
`expansion_move_pct`, `stock_stage_weeks_elapsed`, `sector_stage_segment`,
`return_pct`, `peak_return_pct`, `days_held`, `sell_type`, …). Outcome fields
(`return_pct`, `peak_return_pct`) are **labels only — never lookup keys.**

## 5. Proposed structure

```
agents/evaluator/
├── PLAN.md                      # this doc
├── README.md                    # run instructions + survivorship caveat
├── data/
│   ├── ingest.py                # US universe → local parquet cache (resume-safe)
│   ├── store.py                 # read cache; chronological bar iteration; window slicing
│   └── cache/                   # gitignored: <TICKER>.parquet
├── engine/
│   ├── feed.py                  # the clock: yields bars strictly forward in time (≤T guarantee)
│   ├── broker.py                # order→fill simulation (e.g. next-open fills), positions, cash, equity
│   ├── runner.py                # drives feed → strategy.on_bar → broker; emits ledger + equity curve
│   └── records.py               # canonical trade-row schema (features@entry + outcome@exit)
├── strategy/
│   ├── base.py                  # Strategy base: accumulates past buffer, on_bar hook, history_df() helper
│   ├── examples.py              # buy_and_hold / stop_5pct — engine test fixtures, NOT real strategies
│   └── (range.py, stage.py, stage_range.py — added in Phase 2, see §6)
├── eval/
│   ├── run_eval.py              # window 2015-2026 → metrics + ledger + equity
│   ├── metrics.py               # win rate, expectancy, payoff, max DD, exposure, by-stage breakdown
│   └── results/                 # gitignored: <strategy>_<window>/ {trades.csv, summary.json, equity.csv}
└── probabilities/
    ├── build_db.py              # window 1990-2015: aggregate ledger entry-features → conditional DB
    ├── schema.py                # feature definitions + bucketing; declares which features key the DB
    ├── query.py                 # runtime lookup: features in → {n, win_rate, avg_return, ...} out
    └── db/                      # gitignored: probabilities.parquet + feature manifest (versioned)
```

### Separation of concerns
- **`engine/`** = unbiased, strategy-agnostic. Owns time, fills, accounting, the ledger. This is where the no-lookahead guarantee lives, once.
- **`strategy/`** = where rules live. A strategy consumes the bar feed and emits orders. How it computes features internally (incrementally, or by recomputing over its own past buffer) is its own business — **either way it is leak-proof**, because its buffer only ever holds bars ≤ T.
- **`probabilities/schema.py`** = the "keep adding things" knob: one place declares which features key the DB and how continuous ones are bucketed (e.g. `range_height_pct` → {0‑2, 2‑3, 3‑4, 4‑6, 6+}%). Add a feature → rebuild.

## 6. Lab vs. strategy: how batch dev code becomes on-bar strategies

Two layers with a clear relationship:

| Layer | Role | Shape |
|---|---|---|
| `base_breakout_strategy/` | **The lab** — research, tuning, visualization, finding what's valuable. Sees all history. | Batch |
| `evaluator/strategy/` | **The promoted, trade-ready logic** — on-bar only, no future. | Incremental |

The lab code is **not imported** by strategies — it becomes their **oracle**.
You develop in batch, decide what's valuable, then re-express that logic as an
on-bar state machine in `strategy/`, and *prove the port is faithful* by
comparing the two.

**Porting recipe (per piece of logic):**
1. **Extract parameters, not code.** Pull thresholds (`box_pct`, `expansion_pct`,
   `min_days`, stop/sell rules) into a shared config both layers read, so tuning
   in the lab can't drift from the strategy.
2. **Re-express as a per-bar state machine / accumulator.** e.g. a range:
   `BUILDING → ARMED (expansion seen) → IN_TRADE → EXITED`, updated each bar.
3. **Where batch peeked forward → make the on-bar version *wait*.** The swing
   pivot needs `window` future bars; the on-bar version simply does not emit the
   pivot until those bars have *arrived*. Waiting = the leak fix.

**The equivalence test (the safety net that makes a port trustworthy):**
for any ticker, run the batch lab function over full history and the incremental
strategy over the same history. Assert **incremental ⊆ batch's *confirmed*
events**, and away from the right edge they are **equal**. The only events that
differ are ones batch "saw early" via future bars near the end — exactly the
leaks the on-bar version correctly refuses. So this single test proves the port
is both **faithful** and **leak-free**, and becomes the CI check for every
promoted strategy.

Outcomes (`return_pct`, `peak_return_pct`) stay out of the strategy entirely —
the **engine** computes them after the fact for labelling. The strategy only
decides buy/hold/sell.

## 7. Execution phases (each = a reviewable checkpoint, I stop for go-ahead)

**Phase 0 — engine skeleton** ✅ _done_
- `engine/{feed,broker,runner,records}.py`. Validated: buy-and-hold equity
  matches raw return to the basis point; intrabar stops enforced (incl. gap
  fills). See `tests/test_engine_smoke.py`.

**Phase 1 — data store** ✅ _scaffolding done_
- `data/{store,ingest}.py`. AAPL ingested as a probe. _Still to do: run the full
  622-ticker ingest and report how many have pre-2015 / pre-2000 history._

**Phase 2 — first real strategy (the big chunk), range-first**
- **2a. Range-only port** (`strategy/range_strategy.py`) ✅ _done_ — proves the
  engine + base class + **equivalence-test harness** end-to-end. Matches the
  batch oracle exactly where batch rounding is immaterial.
- **2b. Stage state machine port** (`strategy/stage.py`): the harder port. The
  machine is already left-to-right; only pivot confirmation is forward-looking,
  so the port is mechanical (treat a pivot as unknown until `window` bars pass).
  Validate via the equivalence test against batch `compute_stage_analysis`.
- **2c. Combined** (`strategy/stage_range.py`): the first promoted real strategy,
  populating the entry-feature snapshot (stage, range height, ETF stages, …).

**Phase 3 — evaluation** ✅ _scaffolding done; revisit with a real strategy_
- `eval/{metrics,run_eval.py}` write the results bundle. Re-run over 2015‑2026
  with the Phase-2 strategy and sanity-check against known names.

**Phase 4 — probabilities** ✅ _scaffolding done; populate with a real strategy_
- `probabilities/{schema,build_db,query}.py` built and validated end-to-end.
  Initial features already declared in `schema.py`. Rebuild over the 1990‑2015
  ledger once a real strategy emits the feature snapshot; validate a known
  lookup (range 4% + stage 2) → sensible n/win-rate.

**Phase 5 — integration & docs**
- Wire `query.py` for point-in-time use by a live strategy. README written.

## 8. Open items to confirm (not blockers)

1. **Fill model** — defaulted to **next-bar-open** fills (realistic), with
   intrabar stops and gap-down handling in `broker.py`. ✅ decided.
2. **Decision cadence** — strategies may decide daily or weekly; affects speed
   only, not bias. Decide per strategy in Phase 2.
3. **Config naming** — reuse the `high_low_5526`-style names from today's
   `trades_*.csv` so runs stay comparable. _Confirm at Phase 2._
4. **Survivorship** — flagged; US current list for now (see README caveat).

## 9. Next step

Phase 2b: port the **stage state machine** to on-bar. The machine is already
left-to-right; only pivot confirmation is forward-looking (a swing pivot needs
`window` future bars), so the port is mechanical — treat a pivot as unknown
until `window` bars pass. Validate with an equivalence test against batch
`compute_stage_analysis` (confirmed segments) in the same style as 2a. Then 2c
combines stage + range and populates the stage features in the trade snapshot.
