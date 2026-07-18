# Evaluator

Two products from one engine run, on top of the stage + range-expansion logic:

- **Evaluation** — how a strategy performs over a period (holdout **2015–2026**).
- **Probabilities** — a conditional success-rate DB from in-sample **1990–2015**,
  queried point-in-time at runtime.

See [PLAN.md](PLAN.md) for the original design. **[HANDOFF.md](HANDOFF.md) is the
living source of truth** — architecture, every file, the feature catalog, all
findings, and next steps. Start there.

## Current workflow (the short version — full detail in HANDOFF §"How to run")

```bash
# 1. Fetch prices + EPS + revenue + SPY into the local cache (the only network step)
.venv/bin/python -m agents.evaluator.data.ingest

# 2. Build the pooled setup dataset (one row per breakout, features + outcomes)
.venv/bin/python -m agents.evaluator.rebuild_datasets wk1035     # 10/3/5 weekly (current best)

# 3. Slice win-rate by any filter, always train/holdout split, in a REPL/notebook:
python -c "from agents.evaluator.probabilities import trend_eval as te; df=te.load(); \
  te.cohort(df, by=['weekly_trend_state','trend_state','dstage'], \
            query='avg_dollar_vol_20d>500000')"
```

`sell_win` = realized>0 (the exit), `mom_win` = peak>4% (momentum, exit-independent),
`reward8` = peak>8%. Swap the SELL via any `ExitRule` in `engine/exits.py`
(`SwingLowTrailExit(8)` is the best; see HANDOFF §5f).

**Headline findings (HANDOFF §5b–§5f):** the >4% momentum move is unpredictable from
entry features (~40–43% ceiling, verified 5 ways) → entry is a bounded consistency
lever; the **exit** owns the realized win rate + the tail; and the 100–1000% trader
returns live in the **portfolio layer** (concentration/sizing/pyramiding/regime
exposure), the next build. `runs/` datasets are gitignored — rebuild via step 2.

## The one principle

The engine **feeds bars strictly forward in time**. A strategy is only ever
handed bars up to the current one, so it **cannot look ahead no matter how it is
written** — the evaluator is unbiased independent of strategy correctness, and
needs zero knowledge of any strategy's internals. Strategies do no I/O of their
own; the bar feed is their only data source.

## Layout

```
data/         price cache + ingestion       (store.py, ingest.py, cache/)
engine/       the clock, broker, runner      (feed.py, broker.py, runner.py, records.py)
strategy/     where rules live               (base.py; real strategies added later)
eval/         period backtests + metrics     (run_eval.py, metrics.py, results/)
probabilities/ conditional success DB        (schema.py, build_db.py, query.py, db/)
tests/        engine validation
```

`cache/`, `results/`, `db/` are gitignored (generated artifacts).

## Data cache

Daily OHLCV lives as one parquet per ticker under `data/cache/`.

- `store.get(ticker)` — **cache-on-first-use**: reads the cache, or downloads full
  history from yfinance + saves it on first run. Use this in notebooks so a new
  ticker is cached automatically. `refresh=True` forces a re-download.
- `store.load(ticker)` — cache-only (raises if not cached); reproducible/offline.
- Bulk pre-load the whole US universe: `.venv/bin/python -m agents.evaluator.data.ingest`

## Usage

All commands from the repo root, using the project venv.

```bash
# 1. Ingest price data into the local cache (one-time / incremental, resume-safe)
.venv/bin/python -m agents.evaluator.data.ingest                 # full US universe
.venv/bin/python -m agents.evaluator.data.ingest AAPL NVDA META  # specific tickers

# 2. Validate the engine
.venv/bin/python -m agents.evaluator.tests.test_engine_smoke

# 3. Run an evaluation over a window (writes eval/results/<strategy>_<window>/)
.venv/bin/python -m agents.evaluator.eval.run_eval buy_and_hold \
    --tickers AAPL,MSFT --start 2015-01-01 --end 2026-01-01

# 4. Build the probability DB from an in-sample ledger, then query it
.venv/bin/python -m agents.evaluator.probabilities.build_db \
    --ledger agents/evaluator/eval/results/<run>/trades.csv
.venv/bin/python -c "from agents.evaluator.probabilities.query import ProbabilityDB; \
    print(ProbabilityDB.load().query({'stage': 2, 'range_height_bkt': '3-4'}))"
```

## Status

- ✅ Engine, data store, eval, probabilities scaffolding built and smoke-tested.
- ✅ First real strategy: **`range_breakout`** (`strategy/range_strategy.py`),
  the on-bar port of the range-expansion trade. Verified against the batch lab
  oracle by `tests/test_range_equivalence.py`.
- ✅ **`stage_range`** (`strategy/stage_range_strategy.py`): range entry **gated
  by a Minervini Stage-2 trend template** (`strategy/stage2.py`) + **point-in-time
  EPS YoY growth** recorded as a feature (`data/fundamentals.py`; revenue reserved
  for FMP). `min_criteria` (default 6) tunes how many of the 6 Stage-2 checks the
  gate requires; all are recorded regardless. Validated by `tests/test_stage2.py`.
- ⏳ Next: probabilities DB over the recorded features (incl. EPS growth).

```bash
.venv/bin/python -m agents.evaluator.tests.test_range_equivalence       # port vs oracle
.venv/bin/python -m agents.evaluator.eval.run_eval range_breakout \
    --tickers AAPL --start 2015-01-01 --end 2026-01-01
```

## Seeing what the strategy does

**Inspector** — runs the on-bar strategy and the batch oracle side by side and
flags every difference (penny-price batch-rounding artifacts are labelled):

```bash
.venv/bin/python -m agents.evaluator.inspect AAPL --start 2015-01-01 --end 2026-01-01
.venv/bin/python -m agents.evaluator.inspect AAPL --verbose   # + stream events
```

**Event log** — silent by default; turn it on to watch BUY / SELL / SIGNAL
events bar by bar:

```python
from agents.evaluator.logging_util import set_verbose, verbose
set_verbose(True)                 # global on/off
with verbose():                   # or scope to one run
    run_backtest(df, strat, ticker="AAPL")
```

## Caveat

Universe = current US tickers (`all_tickers.txt`, non-`.NS`) → **survivorship
biased**: delisted 1990–2015 names are absent, which inflates historical success
rates. Documented; revisit with a point-in-time universe later.
