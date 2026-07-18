"""FETCH step — download everything compute needs, up front, into the caches.

Compute (run_backtest / run_scan / probabilities) is strictly **cache-only** and
never hits the network. This is the one place that does. For each ticker it
caches daily prices (``data/cache/``) and report-dated EPS (``fundamentals_cache/``),
plus the benchmark(s) used for market-return features.

Usage (from repo root, project venv):
    .venv/bin/python -m agents.evaluator.data.ingest                 # full US universe
    .venv/bin/python -m agents.evaluator.data.ingest AAPL NVDA       # specific tickers
    .venv/bin/python -m agents.evaluator.data.ingest --force         # re-download cached
    .venv/bin/python -m agents.evaluator.data.ingest --no-eps        # prices only

Revenue (SEC EDGAR XBRL — free, no key; deep quarterly history, filing-dated):
    .venv/bin/python -m agents.evaluator.data.ingest --revenue-only            # revenue pass, all
    .venv/bin/python -m agents.evaluator.data.ingest --revenue-only --limit 200  # cap fetches/run

Resume-safe: already-cached items are skipped unless --force is given. The
revenue pass prioritises tickers by setup count (most-analytically-valuable
first). SEC has no daily cap (just ~10 req/s fair-use), so the whole universe
fetches in one run.
"""
from __future__ import annotations

import sys
import time

from . import fundamentals, store

BENCHMARKS = ("SPY",)   # market-return benchmark(s); price-only


def _ingest_price(ticker: str, *, force: bool) -> str:
    if not force and store.is_cached(ticker):
        return "skip"
    df = store.fetch(ticker)
    if df is None:
        return "nodata"
    store.save(ticker, df)
    return "ok"


def _ingest_eps(ticker: str, *, force: bool) -> str:
    if not force and fundamentals.cache_path(ticker).exists():
        return "skip"
    return "ok" if fundamentals.get_eps(ticker, refresh=True) is not None else "nodata"


def _ingest_revenue(ticker: str, *, force: bool) -> str:
    """One SEC EDGAR revenue fetch (a few concept calls). No API key."""
    if not force and fundamentals.revenue_cache_path(ticker).exists():
        return "skip"
    return "ok" if fundamentals.get_revenue(ticker, refresh=True) is not None else "nodata"


def _revenue_priority(tickers: list[str]) -> list[str]:
    """Order tickers by setup count desc (fetch the most-valuable first), so an
    interrupted/limited run still covers the bulk of the analysis population.
    Falls back to the given order if the pooled dataset isn't built yet."""
    from pathlib import Path
    import pandas as pd
    runs = Path(__file__).resolve().parents[1] / "runs" / "setups_all.parquet"
    if not runs.exists():
        return tickers
    try:
        counts = pd.read_parquet(runs, columns=["ticker"])["ticker"].value_counts()
    except Exception:
        return tickers
    return sorted(tickers, key=lambda t: -int(counts.get(t, 0)))


def ingest_revenue(tickers: list[str], *, force: bool = False,
                   limit: int | None = None) -> dict:
    """SEC EDGAR revenue pass, resume-safe + setup-count-prioritised. No key/cap;
    ``limit`` caps the number of new fetches made this run (skips don't count)."""
    res = {"revenue": [], "skipped": [], "failed": []}
    fetched = 0
    for i, t in enumerate(_revenue_priority(tickers), 1):
        if limit is not None and fetched >= limit:
            print(f"\nReached --limit {limit} fetches this run. Re-run to continue.")
            break
        try:
            r = _ingest_revenue(t, force=force)
        except Exception as e:  # noqa: BLE001
            res["failed"].append(f"{t}:revenue ({e})")
            continue
        if r == "ok":
            res["revenue"].append(t); fetched += 1
        elif r == "skip":
            res["skipped"].append(t)
        else:
            res["failed"].append(f"{t}:revenue"); fetched += 1
        if i % 50 == 0:
            print(f"[{i}/{len(tickers)}] revenue={len(res['revenue'])} "
                  f"skipped={len(res['skipped'])} failed={len(res['failed'])}")
    return res


def ingest(tickers: list[str], *, force: bool = False, with_eps: bool = True,
           pause: float = 0.4, stop_after: int = 20) -> dict:
    """Fetch prices (+ EPS) for each ticker, resume-safe.

    ``stop_after`` — if this many price fetches fail **in a row** (the signature
    of a yfinance throttle, which fails many consecutive *valid* tickers), stop
    cleanly rather than burning through the list. Set high enough to power past
    small clusters of invalid/delisted symbols. Everything fetched so far is
    cached; re-run the same command to resume.
    """
    res = {"price": [], "eps": [], "skipped": [], "failed": [], "stopped_early": False}

    # benchmarks: price only
    for b in BENCHMARKS:
        try:
            if _ingest_price(b, force=force) == "ok":
                res["price"].append(b)
                time.sleep(pause)
        except Exception as e:  # noqa: BLE001
            res["failed"].append(f"{b}:price ({e})")

    consec_fail = 0
    for i, t in enumerate(tickers, 1):
        # price (drives the throttle circuit-breaker)
        try:
            r = _ingest_price(t, force=force)
        except Exception as e:  # noqa: BLE001
            r, _ = "error", res["failed"].append(f"{t}:price ({e})")
        if r == "ok":
            res["price"].append(t)
            consec_fail = 0
            time.sleep(pause)
        elif r == "skip":
            res["skipped"].append(t)          # cached already — neutral
        else:                                  # nodata / error
            if r == "nodata":
                res["failed"].append(f"{t}:price")
            consec_fail += 1

        if consec_fail >= stop_after:
            res["stopped_early"] = True
            print(f"\n⚠ Stopped after {consec_fail} consecutive price-fetch "
                  f"failures at '{t}' (index {i}/{len(tickers)}) — likely a "
                  f"yfinance throttle. Everything fetched so far is cached; "
                  f"re-run the SAME command to resume from here.")
            return res

        # eps (a throttle here doesn't trip the breaker; prices are the signal)
        if with_eps and r in ("ok", "skip"):
            try:
                if _ingest_eps(t, force=force) == "ok":
                    res["eps"].append(t)
                    time.sleep(pause)
            except Exception as e:  # noqa: BLE001
                res["failed"].append(f"{t}:eps ({e})")

        if i % 25 == 0:
            print(f"[{i}/{len(tickers)}] prices={len(res['price'])} "
                  f"eps={len(res['eps'])} skipped={len(res['skipped'])} "
                  f"failed={len(res['failed'])}")
    return res


def main(argv: list[str]) -> None:
    force = "--force" in argv
    with_eps = "--no-eps" not in argv
    limit = None
    for a in argv:
        if a.startswith("--limit"):
            limit = int(a.split("=", 1)[1]) if "=" in a else int(argv[argv.index(a) + 1])
    args = [a for a in argv if not a.startswith("--") and (limit is None or a != str(limit))]
    tickers = args or store.us_universe()
    if not tickers:
        print("No tickers to ingest (all_tickers.txt missing?).")
        return

    if "--revenue-only" in argv:
        print(f"Revenue pass (SEC EDGAR) for {len(tickers)} tickers "
              f"(force={force}, limit={limit}) ...")
        res = ingest_revenue(tickers, force=force, limit=limit)
        print(f"\nDone. revenue={len(res['revenue'])} "
              f"skipped={len(res['skipped'])} failed={len(res['failed'])}")
        if res["failed"]:
            print("Failed:", ", ".join(res["failed"][:40]))
        return

    print(f"Ingesting {len(tickers)} tickers (force={force}, eps={with_eps}) "
          f"+ benchmarks {BENCHMARKS} ...")
    res = ingest(tickers, force=force, with_eps=with_eps)
    status = "STOPPED EARLY (throttle?) — re-run to resume" if res["stopped_early"] else "Done"
    print(f"\n{status}. prices={len(res['price'])} eps={len(res['eps'])} "
          f"skipped={len(res['skipped'])} failed={len(res['failed'])}")
    if res["failed"]:
        print("Failed:", ", ".join(res["failed"][:40]))


if __name__ == "__main__":
    main(sys.argv[1:])
