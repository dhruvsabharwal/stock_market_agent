"""Local chart server for tradebook_viewer.html — click a trade, get its chart.

Runs ``viz.plot_ticker`` on demand and serves the PNG over HTTP so the trade
book HTML can show it inline instead of you pasting a command into a notebook.
Stdlib only (``http.server``), no new dependencies. Everything stays local —
this only listens on localhost, and never sends anything anywhere else.

The feature/ledger file (what ``inv`` was in the notebook) defaults to the same
file ``build_trade_book.py`` reads (``SETUPS_PATH``), so nothing needs to be passed:
load the trade-book CSV in the viewer and click. ``--trades`` overrides the default
for the session; a request's own ``?source=`` overrides both. Relative paths resolve
against the cwd, then the repo root.

Usage:
    .venv/bin/python -m agents.evaluator.eval.chart_server
    .venv/bin/python -m agents.evaluator.eval.chart_server --trades <other file>   # optional override

Then open tradebook_viewer.html — its modal will load the chart automatically
whenever this server is running, and fall back to the copy-paste command when
it isn't.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import matplotlib
matplotlib.use("Agg")   # non-interactive backend — required off the main thread

import pandas as pd

from . import viz
from .build_trade_book import SETUPS_PATH   # single source of truth for the feature file

REPO_ROOT = Path(__file__).resolve().parents[3]

_LOCK = threading.Lock()   # matplotlib's pyplot state isn't thread-safe
_CACHE: dict[str, pd.DataFrame] = {}
DEFAULT_TRADES_PATH: str | None = None


def _resolve(path: str) -> str:
    """Relative paths resolve against the cwd first, then the repo root — so the
    server works no matter which directory it was launched from."""
    path = os.path.expanduser(path.strip())
    if os.path.isabs(path) or os.path.exists(path):
        return path
    return str(REPO_ROOT / path)


def _load_trades(path: str) -> pd.DataFrame:
    path = _resolve(path)
    if path in _CACHE:
        return _CACHE[path]
    print(f"loading trades from {path} ...")
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    print(f"  loaded {len(df)} rows covering {df['ticker'].nunique()} tickers")
    _CACHE[path] = df
    return df


def _bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[chart_server] {self.address_string()} - {fmt % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/chart":
            self.send_response(404)
            self._cors()
            self.end_headers()
            self.wfile.write(b"not found - use /chart?ticker=...")
            return

        q = parse_qs(parsed.query)
        ticker = (q.get("ticker") or [""])[0].upper().strip()
        if not ticker:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b"missing ?ticker=")
            return

        source = (q.get("source") or [None])[0] or DEFAULT_TRADES_PATH
        if not source:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b"missing ?source= (path to the feature/ledger file) and no --trades default was set")
            return

        start = (q.get("start") or [None])[0]
        end = (q.get("end") or [None])[0]
        recency = (q.get("recency_window_days") or [None])[0]
        label_trades = _bool((q.get("label_trades") or [None])[0], True)

        try:
            trades = _load_trades(source)
            with _LOCK:
                buf = io.BytesIO()
                viz.plot_ticker(
                    ticker, trades, start=start, end=end,
                    recency_window_days=int(recency) if recency else None,
                    label_trades=label_trades, out=buf,
                )
            png = buf.getvalue()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
        except Exception as e:
            traceback.print_exc()
            msg = f"chart generation failed for {ticker}: {e}".encode()
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)


def main():
    global DEFAULT_TRADES_PATH
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--trades", default=None,
        help="override the default feature file (default: build_trade_book.SETUPS_PATH). "
             "A request's own ?source= still wins.",
    )
    args = ap.parse_args()

    # No prompt, no flag needed: default to the feature file build_trade_book.py reads.
    # --trades overrides it; a per-request ?source= overrides both.
    trades_path = args.trades or SETUPS_PATH
    _load_trades(trades_path)   # fail fast + warm the cache before serving
    DEFAULT_TRADES_PATH = trades_path
    print(f"default feature file: {trades_path}")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"chart server listening on http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    print("open tradebook_viewer.html in your browser now — its modal will pick this up automatically")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping.")


if __name__ == "__main__":
    main()
