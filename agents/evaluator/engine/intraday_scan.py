"""Scan for the intraday box-break buy — the T0-fill twin of ``engine/scan.py``.

``engine/scan.py`` assumes the daily convention: decide on bar T, fill at T+1's
open. The intraday strategy decides AND fills on day T, at a recorded price, so
both phases need their own version:

  * ``collect_intraday_setups`` — like ``collect_setups``, but wires the hourly
    cache into the strategy first (cache-only; a ticker with no hourly data
    yields nothing rather than silently falling back to a daily fill).
  * ``apply_intraday_exit`` — like ``apply_exit``, but buys at the setup's own
    ``entry_price`` on ``entry_date`` (buy_idx = the signal day + 1 calendar
    trading bar is NOT used) and hands the rule the entry-day post-fill low.

Setups are exit-independent, so the collect/apply split is preserved: compute
the ~240 features once, try many sells cheaply.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .broker import Broker
from .exits import ExitRule, LodStopExit, PriceData
from .records import TradeRecord
from ..data import intraday
from ..strategy.base import StrategyContext


def collect_intraday_setups(df, strategy, *, ticker, start=None, end=None,
                            intraday_df: Optional[pd.DataFrame] = None) -> list[dict]:
    """Run ``strategy`` (an ``IntradayBoxBreakStrategy``) in scan mode over the
    daily frame, with the hourly cache attached. Returns every intraday-triggered
    setup. Empty list when no hourly data is cached for ``ticker``."""
    if intraday_df is None:
        intraday_df = intraday.load(ticker)          # cache-only, never fetches
    if intraday_df is None or intraday_df.empty:
        return []
    strategy.set_intraday(intraday_df)

    df = df.sort_index()
    sim_start = pd.Timestamp(start) if start is not None else None
    if sim_start is not None and strategy.warmup_bars > 0:
        pre = df[df.index < sim_start]
        warm = pre.iloc[-strategy.warmup_bars:] if len(pre) else pre
        feed_df = pd.concat([warm, df[df.index >= sim_start]])
        feed_df = feed_df[~feed_df.index.duplicated(keep="first")]
    else:
        feed_df = df
    if end is not None:
        feed_df = feed_df[feed_df.index <= pd.Timestamp(end)]

    from .feed import BarFeed
    strategy._scan = True
    strategy.scan_signals = []
    ctx = StrategyContext(Broker(ticker), ticker)    # no fills in scan mode
    for bar in BarFeed(feed_df, ticker=ticker):
        strategy._ingest(bar)
        ctx.bar = bar
        ctx.enabled = sim_start is None or bar.date >= sim_start
        strategy.on_bar(bar, ctx)
    return list(strategy.scan_signals)


def apply_intraday_exit(setups, df, exit_rule: ExitRule, *, ticker,
                        notional: float = 1000.0) -> list[TradeRecord]:
    """Simulate each intraday setup forward — buy on ``entry_date`` at the
    recorded intraday ``entry_price`` (NOT the next open)."""
    pd_ = PriceData(df.sort_index())
    idx_of = {d: i for i, d in enumerate(pd_.dates)}
    trades = []
    for sig in setups:
        bi = idx_of.get(pd.Timestamp(sig["entry_date"]))
        if bi is None:
            continue
        buy_price = float(sig["entry_price"])
        if buy_price <= 0:
            continue
        er = exit_rule.simulate(pd_, bi, buy_price, sig)
        trades.append(TradeRecord(
            ticker=ticker, entry_date=pd_.dates[bi], entry_price=buy_price,
            exit_date=pd_.dates[er.sell_idx], exit_price=er.sell_price,
            qty=notional / buy_price, exit_reason=er.reason,
            return_pct=round((er.sell_price / buy_price - 1) * 100, 2),
            days_held=er.sell_idx - bi,
            peak_return_pct=round((er.peak_close / buy_price - 1) * 100, 2),
            days_to_peak=er.days_to_peak,
            mae_pct=round((er.trough_low / buy_price - 1) * 100, 2),
            mae_before_peak_pct=round((er.trough_before_peak / buy_price - 1) * 100, 2),
            features=sig["features"],
        ))
    return trades


def run_intraday_scan(df, strategy, *, ticker, start=None, end=None,
                      notional: float = 1000.0, exit_rule: Optional[ExitRule] = None,
                      intraday_df: Optional[pd.DataFrame] = None) -> list[TradeRecord]:
    """collect + apply in one call. Default sell = ``LodStopExit`` (low-of-day
    stop + the unchanged 2-closes-below-21EMA weakness leg)."""
    setups = collect_intraday_setups(df, strategy, ticker=ticker, start=start,
                                     end=end, intraday_df=intraday_df)
    rule = exit_rule or LodStopExit()
    return apply_intraday_exit(setups, df, rule, ticker=ticker, notional=notional)
