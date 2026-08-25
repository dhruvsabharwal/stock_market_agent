"""Range-expansion trades annotated with Minervini Stage-2 + EPS features.

By default this takes **every** range breakout ``RangeBreakoutStrategy`` would
(``min_criteria=0`` = gate off) and records the six Stage-2 sub-criteria, their
pass-count, and point-in-time EPS YoY growth on each trade. That gives the full
trade population with features attached, so you can evaluate which conditions
actually matter (you keep the trades that failed a criterion, not just a
filtered subset).

Set ``min_criteria`` > 0 to also *gate* entries on that many Stage-2 checks —
useful once the analysis tells you which threshold helps.

Reuses ``RangeBreakoutStrategy`` wholesale (box detection, expansion trigger,
stop, sell-in-weakness exit) via the base hooks:
  * ``_pre_decision`` — feed each bar to the Stage-2 template + lazy-load EPS.
  * ``_entry_allowed`` — gate (always True when ``min_criteria=0``).
  * ``_extra_features`` — the recorded Stage-2 + EPS snapshot.

No lookahead: the template and EPS lookup use only bars/reports dated on or
before the current bar.
"""
from __future__ import annotations

from typing import Optional

from ..data.fundamentals import EpsHistory, RevenueHistory
from ..engine.records import Bar
from .base import StrategyContext
from .higher_timeframe import HigherTimeframeExtension
from .index_regime import IndexRegime
from .kell import KellCycle
from .ma_stack import MovingAverageStack
from .trend_state import TrendState
from .weekly_kell import WeeklyKellContext
from .momentum import MarketRegime, MarketReturns, RelativeStrength, TrailingReturns
from .overhead import OverheadSupply
from .range_strategy import RangeBreakoutStrategy
from .resistance import MeaningfulResistance
from .stage2 import Stage2TrendTemplate


class StageRangeStrategy(RangeBreakoutStrategy):
    name = "stage_range"

    def __init__(
        self,
        *,
        # ── Stage-2 template knobs (all passable flat) ───────────────────────
        sma_fast: int = 150,
        sma_slow: int = 200,
        slope_lookback: int = 21,
        min_slope_pct: float = 0.0,
        pivot_window: int = 10,
        weeks_lookback: int = 13,
        # ── Kell cycle knobs ─────────────────────────────────────────────────
        ema_periods=(10, 20),
        min_ext_below_pct: float = 5.0,       # wedge pop: extension below ref EMA
        min_ext_above_pct: float = 5.0,       # wedge drop: extension above ref EMA
        reversal_min_ext_pct: float = 10.0,   # reversal extension (capitulation): much deeper
        exhaustion_min_ext_pct: float = 10.0, # exhaustion extension (climax): much higher
        ext_ref_ema: int = 20,                # reference EMA for the extension test
        crossback_low_tol: float = 0.005,
        crossback_reset_pct: float = 0.0,   # re-arm as soon as the low clears the EMA
        crossback_ema_rising_lookback: int = 5,
        cycle_count_months: int = 12,
        both_within_bars: int = 10,
        vol_avg_window: int = 20,
        # ── overhead supply knobs ────────────────────────────────────────────
        overhead_windows_months=(6, 12, 24),
        overhead_swing_window: int = 10,
        # ── meaningful-resistance / Stage-1 "stuck below resistance" knobs ────
        resistance_pivot_window: int = 10,      # ±bars for a confirmed swing high
        resistance_band_pct: float = 12.0,      # swing highs within this % = same zone
        resistance_min_rejections: int = 2,     # >= this = a "significant" resistance
        resistance_clear_buffer_pct: float = 3.0,  # close must exceed a zone by this % to "break" it
        resistance_recency_window_days=None,    # only count retests within this many trailing bars (~252=1yr); None=all-time
        resistance_stuck_windows: Optional[dict] = None,  # {label: bars} -> stuck_under_resistance_{label}; default 6mo/1yr/2yr
        resistance_max_dist_pct=None,           # optional guard: ignore ceilings farther than this % above (off by default)
        # ── moving-average stack (price above each MA? + coil) ───────────────
        ma_ema_periods=(10, 20),
        ma_sma_periods=(50, 200),
        coil_ema_periods=(10, 20),
        coil_sma_periods=(50,),
        coil_pct: float = 3.0,
        # ── higher-timeframe extension (Kell's weekly/monthly 10-EMA gauge) ──
        weekly_emas=(10, 20),
        monthly_emas=(10,),
        # ── weekly Kell TREND horizon (independently tunable). Defaults inherit
        #    the daily reference EMA with a 5-week slope/pivot = prior behavior.
        #    Kell's own weekly gauge is the 10-EMA → weekly_ext_ref_ema=10. ─────
        weekly_ext_ref_ema: Optional[int] = None,   # None -> use ext_ref_ema
        weekly_trend_slope_window: int = 5,
        weekly_trend_pivot_window: int = 5,
        # ── trailing returns (stock momentum + market) ───────────────────────
        return_months=(1, 3, 6, 12),
        return_days=(),
        market_benchmark: str = "SPY",
        regime_index: str = "QQQ",
        # ── gate + fundamentals ──────────────────────────────────────────────
        min_criteria: int = 0,
        record_eps: bool = True,
        # ── optional dict override for the Stage-2 knobs (takes precedence) ──
        stage2_params: Optional[dict] = None,
        # ── everything else forwards to RangeBreakoutStrategy ────────────────
        **range_params,
    ):
        """``min_criteria`` — how many of the 6 Stage-2 checks must hold to enter.
        **0 (default) = no gate**: take every range breakout and just record the
        features. Set 6 for strict Minervini, or 4/5 to loosen a gate.

        All knobs are flat. Stage-2 (``sma_fast`` …), Kell cycle (``ema_periods``,
        ``reversal_lookback_months`` …), and overhead (``overhead_windows_months``
        …) are passable directly; the list knobs expand into one column per
        entry. Remaining kwargs (``box_pct``, ``tight_pcts`` …) go to the range
        engine."""
        super().__init__(**range_params)
        stage2 = dict(sma_fast=sma_fast, sma_slow=sma_slow,
                      slope_lookback=slope_lookback, min_slope_pct=min_slope_pct,
                      pivot_window=pivot_window, weeks_lookback=weeks_lookback)
        stage2.update(stage2_params or {})
        self.params["stage2_params"] = stage2
        self.params["min_criteria"] = min_criteria
        self.params["record_eps"] = record_eps
        self._tt = Stage2TrendTemplate(**stage2)
        self._min_criteria = min_criteria
        self._record_eps = record_eps

        # ── window big enough for the longest lookback + confirmation ─────────
        max_months = max([*overhead_windows_months, cycle_count_months, 10])
        buffer_bars = int(max_months * 22) + overhead_swing_window + 40
        self.warmup_bars = max(350, buffer_bars, max(ma_sma_periods, default=0) + 50)

        self._kell = KellCycle(
            ema_periods=ema_periods,
            min_ext_below_pct=min_ext_below_pct, min_ext_above_pct=min_ext_above_pct,
            reversal_min_ext_pct=reversal_min_ext_pct,
            exhaustion_min_ext_pct=exhaustion_min_ext_pct,
            ext_ref_ema=ext_ref_ema,
            crossback_low_tol=crossback_low_tol, crossback_reset_pct=crossback_reset_pct,
            crossback_ema_rising_lookback=crossback_ema_rising_lookback,
            cycle_count_months=cycle_count_months, both_within_bars=both_within_bars,
            vol_avg_window=vol_avg_window,
        )
        self._overhead = OverheadSupply(
            windows_months=overhead_windows_months,
            swing_window=overhead_swing_window, buffer_bars=buffer_bars,
        )
        # Stage-1 dead-zone flag — stuck below a meaningful (repeatedly-tested)
        # resistance. NOT a Stage-2 definition (Kell's uptrend handles that).
        self._resistance = MeaningfulResistance(
            pivot_window=resistance_pivot_window,
            band_pct=resistance_band_pct,
            min_rejections=resistance_min_rejections,
            clear_buffer_pct=resistance_clear_buffer_pct,
            recency_window_days=resistance_recency_window_days,
            max_dist_pct=resistance_max_dist_pct,
        )
        # trailing-window "stuck under resistance" flags (bars). ~21 bars/month.
        self._stuck_windows = resistance_stuck_windows or {
            "6_mo": 126, "1_yr": 252, "2_yr": 504}
        self._ma_stack = MovingAverageStack(
            ema_periods=ma_ema_periods, sma_periods=ma_sma_periods,
            coil_ema_periods=coil_ema_periods, coil_sma_periods=coil_sma_periods,
            coil_pct=coil_pct,
        )
        self._htf = HigherTimeframeExtension(weekly_emas=weekly_emas, monthly_emas=monthly_emas)
        self._trend = TrendState()
        # Same self-calibrating cycle on WEEKLY bars — the weekly 20-EMA rolls
        # over over weeks, so the weekly trend is naturally smoother/slower (the
        # stable trend authority) with no separate parameters needed.
        self._weekly_kell = WeeklyKellContext(
            ema_periods=ema_periods,
            ext_ref_ema=weekly_ext_ref_ema or ext_ref_ema,
            trend_slope_window=weekly_trend_slope_window,
            trend_pivot_window=weekly_trend_pivot_window)
        # broad-market index regime (SPY/IWM/IJR/IJH) via the SAME weekly cycle
        self._index_regime = IndexRegime(
            ext_ref_ema=weekly_ext_ref_ema or ext_ref_ema,
            slope_window=weekly_trend_slope_window,
            pivot_window=weekly_trend_pivot_window)
        self._stock_ret = TrailingReturns(months=return_months, days=return_days)
        self._mkt_ret = MarketReturns(
            benchmark=market_benchmark, months=return_months, days=return_days,
        )
        self._regime = MarketRegime(index=regime_index, months=return_months)
        self._rs = RelativeStrength(months=return_months)
        self.warmup_bars = max(self.warmup_bars,
                               int(max(return_months, default=0) * 22) + 40,
                               max(return_days, default=0) + 5)
        self._eps: Optional[EpsHistory] = None
        self._eps_loaded = False
        self._rev: Optional[RevenueHistory] = None
        self._rev_loaded = False
        self._ticker: Optional[str] = None
        self._last_criteria: dict = {}

    def _pre_decision(self, bar: Bar, ctx: StrategyContext) -> None:
        self._ticker = ctx.ticker
        self._tt.update(bar)
        self._kell.update(bar)
        # Stage-1 stuck-below-resistance detector (swing-high driven; no trend dep).
        self._resistance.update(bar)
        self._overhead.update(bar)
        self._ma_stack.update(bar)
        self._htf.update(bar)
        self._trend.update(bar)
        self._weekly_kell.update(bar)
        self._stock_ret.update(bar)
        if self._record_eps and not self._eps_loaded:
            self._eps = EpsHistory.load(ctx.ticker)
            self._eps_loaded = True
        if self._record_eps and not self._rev_loaded:
            self._rev = RevenueHistory.load(ctx.ticker)   # cache-only; None if not ingested
            self._rev_loaded = True

    def _entry_allowed(self, bar: Bar, ctx: StrategyContext) -> bool:
        self._last_criteria = self._tt.criteria()   # snapshot for _extra_features
        return sum(self._last_criteria.values()) >= self._min_criteria

    def _extra_features(self, bar: Bar) -> dict:
        feats = {f"s2_{k}": v for k, v in self._last_criteria.items()}
        feats["stage2_pass_count"] = sum(1 for v in self._last_criteria.values() if v)
        eps_g = self._eps.growth_as_of(bar.date) if self._eps is not None else None
        feats["eps"] = eps_g.eps if eps_g else None                 # raw latest EPS
        feats["eps_yoy_growth"] = eps_g.yoy if eps_g else None      # None if sign-flip/<=0 base
        feats["eps_qoq_growth"] = eps_g.qoq if eps_g else None
        feats["eps_yoy_base"] = eps_g.yoy_base if eps_g else None   # denominator EPS (4q back)
        feats["eps_qoq_base"] = eps_g.qoq_base if eps_g else None   # denominator EPS (1q back)
        feats["eps_report_date"] = (
            eps_g.report_date.strftime("%Y-%m-%d") if eps_g else None
        )
        # Revenue (FMP, point-in-time by filing date). None when not ingested.
        rev_g = self._rev.growth_as_of(bar.date) if self._rev is not None else None
        feats["revenue_qoq_growth"] = rev_g.qoq if rev_g else None          # latest QoQ (q0 vs q-1)
        feats["revenue_qoq_growth_prev1"] = rev_g.qoq_prev[0] if rev_g else None
        feats["revenue_qoq_growth_prev2"] = rev_g.qoq_prev[1] if rev_g else None
        feats["revenue_qoq_growth_prev3"] = rev_g.qoq_prev[2] if rev_g else None
        feats["revenue_yoy_growth"] = rev_g.yoy if rev_g else None          # vs 4 quarters back
        feats["revenue"] = rev_g.revenue if rev_g else None                 # raw latest quarterly
        feats["revenue_report_date"] = (
            rev_g.report_date.strftime("%Y-%m-%d") if rev_g else None
        )
        feats.update(self._kell.features(bar))       # Kell cycle features
        feats.update(self._resistance.features(bar)) # Stage-1 stuck-below-resistance
        for _lbl, _win in self._stuck_windows.items():  # windowed stuck / broke-above flags
            feats[f"stuck_under_resistance_{_lbl}"] = self._resistance.stuck_at(bar.close, _win)
            feats[f"broke_above_resistance_{_lbl}"] = self._resistance.broke_above(bar.close, _win)
        feats.update(self._overhead.features(bar))   # overhead supply features
        feats.update(self._ma_stack.features(bar))   # price above each MA?
        feats.update(self._htf.features(bar))        # weekly/monthly 10-EMA extension (Kell)
        feats.update(self._trend.features(bar))      # base-in-uptrend vs decline gate
        feats.update(self._weekly_kell.features(bar)) # WEEKLY Kell trend context
        feats.update(self._index_regime.features(bar)) # broad-index weekly regime (SPY/IWM/IJR/IJH)
        stock_ret = self._stock_ret.features(bar)
        mkt_ret = self._mkt_ret.features(bar)
        feats.update(stock_ret)                      # stock trailing returns
        feats.update(mkt_ret)                        # market (S&P 500) trailing returns
        feats.update(self._regime.features(bar))     # QQQ regime (Kell's cash/margin switch)
        feats.update(self._rs.features(bar, self._ticker or ""))  # cross-sectional RS rank
        # S&P-relative RS (Minervini "RS line"): stock outperformance vs SPY, in
        # percentage points, per horizon. Distinct from the universe-rank above.
        for m in self._stock_ret.months:
            s, k = stock_ret.get(f"ret_{m}m"), mkt_ret.get(f"mkt_ret_{m}m")
            feats[f"rs_vs_spy_{m}m"] = round(s - k, 2) if (s is not None and k is not None) else None
        return feats
