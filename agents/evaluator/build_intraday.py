"""Full-universe build for the intraday box-break buy.

    .venv/bin/python -m agents.evaluator.build_intraday [--start 2023-08-01]

Pools `collect_intraday_setups` + `apply_intraday_exit` over every ticker that
has BOTH a daily and an hourly cache, and writes one row per setup (features +
outcomes) to runs/setups_intraday_boxclear.{parquet,csv}.
"""
from __future__ import annotations
import argparse, sys, time
import pandas as pd

from .data import store, intraday
from .strategy.intraday_box_strategy import IntradayBoxBreakStrategy
from .engine.intraday_scan import collect_intraday_setups, apply_intraday_exit
from .engine.exits import LodStopExit
from .eval import metrics
from .probabilities import screen as _screen   # noqa: F401  (runs/ path helper)

RUNS = __file__.rsplit("/", 1)[0] + "/runs"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--box-pct", type=float, default=5.0)
    ap.add_argument("--min-days", type=int, default=3)
    ap.add_argument("--clear", type=float, default=1.0)
    ap.add_argument("--min-dollar-vol", type=float, default=500_000)
    ap.add_argument("--name", default="setups_intraday_boxclear")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    universe = set(store.us_universe())
    tickers = [t for t in intraday.cached_tickers()
               if t in universe and store.cache_path(t).exists()]
    tickers.sort()
    if a.limit:
        tickers = tickers[: a.limit]
    print(f"tickers with daily+hourly: {len(tickers)}", flush=True)

    cfg = dict(box_pct=a.box_pct, price_mode="open_close", min_days=a.min_days,
               require_box_clear_pct=a.clear, min_dollar_volume=a.min_dollar_vol)
    rows, n_set, fails, t0 = [], 0, [], time.time()
    for i, tk in enumerate(tickers, 1):
        try:
            df = store.load(tk)
            if df is None or df.empty:
                continue
            su = collect_intraday_setups(df, IntradayBoxBreakStrategy(**cfg),
                                         ticker=tk, start=a.start)
            if not su:
                continue
            n_set += len(su)
            for tr in apply_intraday_exit(su, df, LodStopExit(), ticker=tk):
                rows.append(tr.to_dict())
        except Exception as e:                      # keep going; report at the end
            fails.append((tk, f"{type(e).__name__}: {e}"))
        if i % 50 == 0:
            print(f"  {i}/{len(tickers)}  setups={n_set}  rows={len(rows)}  "
                  f"fails={len(fails)}  {time.time()-t0:.0f}s", flush=True)

    if not rows:
        print("no setups produced", file=sys.stderr)
        return 1
    out = pd.DataFrame(rows)
    out.to_parquet(f"{RUNS}/{a.name}.parquet")
    out.to_csv(f"{RUNS}/{a.name}.csv", index=False)
    print(f"\nwrote {RUNS}/{a.name}.parquet  rows={len(out)}  cols={out.shape[1]}"
          f"  tickers={out.ticker.nunique()}  {time.time()-t0:.0f}s", flush=True)
    if fails:
        print(f"failed tickers: {len(fails)}  e.g. {fails[:5]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
