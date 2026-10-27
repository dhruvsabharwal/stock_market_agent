"""Volatility-contraction scan — decoupled from the box/breakout strategy.

Walks every cached ticker's full daily history through the SAME tightness and
VCP providers the box strategy uses (``VCPTightnessScore``, ``VCPFootprint``),
but with no box/range/breakout requirement anywhere: a row is emitted purely
because the stock was IN a contraction, on any day, whether or not it ever
broke out afterward.

Two independent contraction flags, per the session's design:

  * ``tight_pctile_flag``  — the SHORT ``VCPTightnessScore`` (5d spread over
    the 20d ADR%/ATR%, percentile-ranked against its OWN trailing 50-day
    history) crosses into its top decile (>= ``TIGHT_PCTILE_THRESHOLD``) on
    ANY of its 4 flavors (closing/swing x adr/atr). Self-relative, not a
    fixed cutoff — this is the same mechanism already used elsewhere, just
    run unconditionally instead of only on box-strategy signal days.
  * ``vcp_structural_flag`` — ``VCPFootprint.vcp_contracting``: a genuine
    multi-leg, progressively-shallower pullback pattern off a swing high,
    still unresolved (not yet broken out of).

Only the FIRST day of each contraction episode is kept per (ticker, flag) —
not every day of a multi-week contraction — so the output is one row per
distinct episode, not a stack of near-duplicates.

For each flagged event, records the stock's own trailing returns
(``ret_{1,3,6,12}m``), relative strength vs SPY (``rs_vs_spy_{1,3,6,12}m``),
and volatility-adjusted RS (``vars_spy_{1,3,6,12}m`` / ``vars_{1,3,6,12}m`` vs
the resolved group benchmark) as of that date — all point-in-time, no lookahead.

Usage:
    .venv/bin/python -m agents.evaluator.scan_volatility_contraction
"""
from __future__ import annotations

import pandas as pd

from .data import store
from .engine.records import Bar
from .strategy.momentum import MarketReturns, TrailingReturns
from .strategy.tightness_score import VCPTightnessScore
from .strategy.vars import VolAdjRelativeStrength
from .strategy.vcp import VCPFootprint
from .strategy.volatility import Volatility

# ── CONFIG ──────────────────────────────────────────────────────────────
OUT_PATH = "agents/evaluator/runs/volatility_contraction_events.parquet"
YEARS_BACK = 3
FEED_BUFFER_DAYS = 400          # extra calendar days fed before the window, for provider warmup
TIGHT_PCTILE_THRESHOLD = 90.0    # top-decile-tightest (self-relative) to flag
MIN_DOLLAR_VOL_20D = 500_000
MIN_PRICE = 5.0
# ─────────────────────────────────────────────────────────────────────────


def _scan_ticker(ticker: str, window_start: pd.Timestamp) -> list[dict]:
    try:
        px = store.load(ticker)
    except Exception:
        return []
    if px is None or px.empty:
        return []
    feed_start = window_start - pd.Timedelta(days=FEED_BUFFER_DAYS)
    px = px[px.index >= feed_start]
    if len(px) < 60:
        return []

    px = px.copy()
    px["_ema10"] = px["Close"].ewm(span=10, adjust=False).mean()
    px["_ema20"] = px["Close"].ewm(span=20, adjust=False).mean()
    px["_sma50"] = px["Close"].rolling(50).mean()
    px["_sma200"] = px["Close"].rolling(200).mean()

    vol = Volatility()
    tight = VCPTightnessScore(tight_window=5, baseline_window=50)
    vcp = VCPFootprint()
    trailing = TrailingReturns()
    mkt = MarketReturns(benchmark="SPY")
    vars_provider = VolAdjRelativeStrength()

    prev_tight_flag = False
    prev_vcp_flag = False
    rows: list[dict] = []

    for date, row in px.iterrows():
        bar = Bar.from_row(date, row)

        vol.update(bar)
        vol_feats = vol.features(bar)
        tight.update(bar)
        tight_feats = tight.features(bar, adr_pct=vol_feats.get("adr_pct"), atr_pct=vol_feats.get("atr_pct"))
        vcp.update(bar)
        vcp_feats = vcp.features(bar)
        trailing.update(bar)
        stock_ret = trailing.features(bar)

        tight_scores = [
            tight_feats.get("vcp_tight_closing_atr"), tight_feats.get("vcp_tight_swing_atr"),
            tight_feats.get("vcp_tight_closing_adr"), tight_feats.get("vcp_tight_swing_adr"),
        ]
        tight_max = max((s for s in tight_scores if s is not None), default=None)
        tight_flag = tight_max is not None and tight_max >= TIGHT_PCTILE_THRESHOLD
        vcp_flag = bool(vcp_feats.get("vcp_contracting"))

        is_tight_event = tight_flag and not prev_tight_flag
        is_vcp_event = vcp_flag and not prev_vcp_flag
        prev_tight_flag, prev_vcp_flag = tight_flag, vcp_flag

        if date < window_start or not (is_tight_event or is_vcp_event):
            continue

        mkt_ret = mkt.features(bar)
        vars_feats = vars_provider.features(bar, stock_ret, vol_feats.get("atr_pct"), ticker)

        def _above(ma_val):
            return None if pd.isna(ma_val) else bool(bar.close > ma_val)

        ma_flags = {
            "above_10ema": _above(row["_ema10"]), "above_20ema": _above(row["_ema20"]),
            "above_50sma": _above(row["_sma50"]), "above_200sma": _above(row["_sma200"]),
        }

        rs_vs_spy = {}
        for m in (1, 3, 6, 12):
            s, k = stock_ret.get(f"ret_{m}m"), mkt_ret.get(f"mkt_ret_{m}m")
            rs_vs_spy[f"rs_vs_spy_{m}m"] = round(s - k, 2) if (s is not None and k is not None) else None

        rows.append({
            "ticker": ticker, "date": date,
            "close": float(bar.close),
            "avg_dollar_vol_20d": None,   # filled in bulk pass, see main()
            **ma_flags,
            "tight_pctile_flag": is_tight_event,
            "tight_pctile_max_score": tight_max,
            "vcp_tight_closing_atr": tight_feats.get("vcp_tight_closing_atr"),
            "vcp_tight_swing_atr": tight_feats.get("vcp_tight_swing_atr"),
            "vcp_tight_closing_adr": tight_feats.get("vcp_tight_closing_adr"),
            "vcp_tight_swing_adr": tight_feats.get("vcp_tight_swing_adr"),
            "vcp_structural_flag": is_vcp_event,
            "vcp_num_contractions": vcp_feats.get("vcp_num_contractions"),
            "vcp_tightest_pct": vcp_feats.get("vcp_tightest_pct"),
            "vcp_depth_ratio": vcp_feats.get("vcp_depth_ratio"),
            **{f"ret_{m}m": stock_ret.get(f"ret_{m}m") for m in (1, 3, 6, 12)},
            **rs_vs_spy,
            **vars_feats,
        })

    return rows


def main():
    window_start = pd.Timestamp.today().normalize() - pd.DateOffset(years=YEARS_BACK)
    tickers = [t for t in store.cached_tickers() if t in set(store.us_universe())]
    print(f"scanning {len(tickers)} tickers from {window_start.date()} onward "
          f"(fed from {(window_start - pd.Timedelta(days=FEED_BUFFER_DAYS)).date()} for warmup) ...")

    all_rows: list[dict] = []
    for i, t in enumerate(tickers, 1):
        all_rows.extend(_scan_ticker(t, window_start))
        if i % 100 == 0:
            print(f"  {i}/{len(tickers)} tickers scanned, {len(all_rows)} events so far ...")

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("no contraction events found.")
        return

    # 20d avg dollar volume, computed in one pass per ticker over the raw cache (cheap)
    adv_by_ticker: dict[str, pd.Series] = {}
    for t in df["ticker"].unique():
        try:
            px = store.load(t)
            adv_by_ticker[t] = (px["Close"] * px["Volume"]).rolling(20).mean()
        except Exception:
            continue
    df["avg_dollar_vol_20d"] = df.apply(
        lambda r: adv_by_ticker[r["ticker"]].asof(r["date"]) if r["ticker"] in adv_by_ticker else None,
        axis=1,
    )
    df = df[(df["avg_dollar_vol_20d"] > MIN_DOLLAR_VOL_20D) & (df["close"] > MIN_PRICE)]
    df = df.sort_values(["date", "ticker"]).reset_index(drop=True)

    df.to_parquet(OUT_PATH, index=False)
    df.to_csv(OUT_PATH.replace(".parquet", ".csv"), index=False)
    print(f"\nwrote {len(df)} contraction events "
          f"({df['tight_pctile_flag'].sum()} tight-pctile, {df['vcp_structural_flag'].sum()} vcp-structural, "
          f"{(df['tight_pctile_flag'] & df['vcp_structural_flag']).sum()} both) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
