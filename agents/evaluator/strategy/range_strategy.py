"""On-bar port of the range-expansion trade (no stage).

Faithful streaming equivalent of ``base_identification.find_ranges`` +
``build_range_expansion_trade`` for ``max_expansion_days == 1`` (the default):

  * **Box** — at every bar we maintain the *maximal* consolidation box ending at
    the previous bar: the longest suffix whose height ((hi-lo)/lo*100) ≤
    ``box_pct``. (Height is monotone in window length, so a two-pointer shrink
    from the left keeps it maximal.)
  * **Expansion (up)** — bar k triggers an entry when it *breaks* that box
    (adding it pushes height > box_pct), the box had ≥ ``min_days`` bars, and the
    day-over-day change (close/prev_close-1) ≥ ``expansion_pct``. This is exactly
    the batch trigger: the breakout bar is the box-breaking bar, and the
    threshold is day-over-day % change.
  * **Entry** — open of the bar AFTER the expansion (the engine fills the queued
    order at next open).
  * **Stop** — ``build_stop_loss`` logic: criteria level computed at the
    expansion bar (known), max-loss cap applied at fill by the broker, so the
    stop is live on the entry bar (matches the batch buy-day stop check).
  * **Exit** — sell-in-weakness: two consecutive closes below the MA → sell at
    next open (with the optional rising-close veto), OR the stop fires intrabar.
    The engine's per-bar ordering (fill queued sell at open → then check stop)
    matches the batch ordering exactly.

No future data is ever consulted: the box, MA, and day-change all use only bars
already delivered. While holding a position the strategy keeps its box/MA state
current but does not enter (one position at a time).
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import pandas as pd

from ..engine.records import Bar
from ..logging_util import logger
from .base import Strategy, StrategyContext
from ._lab_imports import RangeExpansion


class RangeBreakoutStrategy(Strategy):
    name = "range_breakout"
    # Feed full history before the sim window so the MA matches the batch series.
    warmup_bars = 300

    def __init__(
        self,
        *,
        box_pct: float = 3.0,
        min_days: int = 3,
        expansion_pct: float = 4.0,
        max_expansion_days: int = 1,
        close_threshold_pct: float = 10.0,
        price_mode: str = "high_low",
        trade_ma_type: str = "21ema",
        allow_rising_close_exception: bool = True,
        stop_type: str = "expansion_open",
        stop_buffer_pct: float = 0.02,
        stop_constant_pct: float = 0.03,
        max_loss_pct: float = 0.04,
        tight_pcts=(1.0, 2.0),
        range_recent_days: int = 3,
        linearity_window: int = 30,
        min_dollar_volume: Optional[float] = None,
        dollar_volume_window: int = 20,
        vol_avg_window: int = 50,
    ):
        if max_expansion_days != 1:
            raise NotImplementedError(
                "The on-bar port currently supports max_expansion_days=1 "
                "(expansion on the bar immediately after the box)."
            )
        super().__init__(
            box_pct=box_pct, min_days=min_days, expansion_pct=expansion_pct,
            close_threshold_pct=close_threshold_pct, price_mode=price_mode,
            trade_ma_type=trade_ma_type,
            allow_rising_close_exception=allow_rising_close_exception,
            stop_type=stop_type, stop_buffer_pct=stop_buffer_pct,
            stop_constant_pct=stop_constant_pct, max_loss_pct=max_loss_pct,
            tight_pcts=list(tight_pcts),
            min_dollar_volume=min_dollar_volume, dollar_volume_window=dollar_volume_window,
            vol_avg_window=vol_avg_window,
        )
        self.tight_pcts = list(tight_pcts)
        self.range_recent_days = range_recent_days
        self.linearity_window = linearity_window
        self.box_pct = box_pct
        self.min_days = min_days
        self.expansion_pct = expansion_pct
        self.close_threshold_pct = close_threshold_pct
        self.price_mode = price_mode
        self.trade_ma_type = trade_ma_type
        self.allow_rising_close_exception = allow_rising_close_exception
        self.stop_type = stop_type
        self.stop_buffer_pct = stop_buffer_pct
        self.stop_constant_pct = stop_constant_pct
        self.max_loss_pct = max_loss_pct
        self.min_dollar_volume = min_dollar_volume
        self.dollar_volume_window = dollar_volume_window
        self.vol_avg_window = vol_avg_window

        # ── liquidity: trailing avg $ volume (Close*Volume), causal ─────────────
        self._dv_window: deque[float] = deque(maxlen=dollar_volume_window)
        # ── volume signature: trailing share-volume avg (ends at prior bar) +
        #    per-box-bar volume (synced to the box), for breakout/base ratios ──
        self._vol_avg: deque[float] = deque(maxlen=vol_avg_window)
        self._win_vol: deque[float] = deque()

        # ── box state: the maximal box ending at the most recent processed bar
        self._win_upper: deque[float] = deque()
        self._win_lower: deque[float] = deque()
        # per-box-bar tightness %, synced to the box: body = |close-open|/open,
        # range = |high-low|/low. Counted at any threshold at entry.
        self._win_body: deque[float] = deque()
        self._win_range: deque[float] = deque()
        # raw per-box-bar prices (synced), so we can report BOTH the high-low and
        # the open-close height of the box regardless of price_mode.
        self._win_high: deque[float] = deque()
        self._win_low: deque[float] = deque()
        self._win_ocup: deque[float] = deque()   # max(open, close)
        self._win_oclo: deque[float] = deque()   # min(open, close)
        self._win_daychg: deque[float] = deque() # per-bar day-over-day % change
        # ── series state
        self._prev_close: Optional[float] = None
        self._ema: Optional[float] = None              # 21 EMA (incremental)
        self._sma_window: deque[float] = deque(maxlen=50)
        self._ema_span = 21
        # ── trade/exit state
        self._below_prev: Optional[bool] = None        # was prior close below MA
        self._exit_queued: bool = False
        self._was_in_position: bool = False
        # ── 2LYNCH-style base features: consecutive up-days into the breakout, the
        #    day-before bar's tightness, and base cleanliness (>4% down days). All
        #    causal — updated at the end of each bar, so at a breakout they hold the
        #    PRIOR bars' values.
        self._recent_daychg: deque[float] = deque(maxlen=12)  # raw day-over-day %, last bars
        self._last_range_pct: Optional[float] = None          # prior bar (high-low)/low %
        self._last_body_pct: Optional[float] = None           # prior bar |close-open|/open %
        # closes buffer for prior-move linearity (efficiency ratio of the advance
        # into the base); big enough for a long base + the linearity window.
        self._closes: deque[float] = deque(maxlen=linearity_window + 260)

    # ── price-mode helpers ───────────────────────────────────────────────────
    def _upper(self, b: Bar) -> float:
        return max(b.open, b.close) if self.price_mode == "open_close" else b.high

    def _lower(self, b: Bar) -> float:
        return min(b.open, b.close) if self.price_mode == "open_close" else b.low

    # ── moving averages (incremental; seeded from the first bar seen) ────────
    def _update_ma(self, close: float) -> None:
        alpha = 2.0 / (self._ema_span + 1)
        self._ema = close if self._ema is None else alpha * close + (1 - alpha) * self._ema
        self._sma_window.append(close)

    def _ma_for(self, kind: str) -> Optional[float]:
        if kind == "21ema":
            return self._ema
        if kind == "50sma":
            return sum(self._sma_window) / 50 if len(self._sma_window) == 50 else None
        return None

    def _below_weakness_ma(self, close: float) -> Optional[bool]:
        ma = self._ma_for(self.trade_ma_type)
        return None if ma is None else close < ma

    # ── liquidity gate (trailing avg $ volume, ending at the PRIOR bar) ──────
    def avg_dollar_volume(self) -> Optional[float]:
        return sum(self._dv_window) / len(self._dv_window) if self._dv_window else None

    def _liquidity_ok(self) -> bool:
        if self.min_dollar_volume is None:
            return True
        adv = self.avg_dollar_volume()
        return adv is not None and adv >= self.min_dollar_volume

    # ── stop criteria at the expansion bar (mirrors build_stop_loss) ─────────-
    def _stop_criteria(self, exp_bar: Bar) -> tuple[Optional[float], Optional[float]]:
        """Return (criteria_price, constant_pct) for the order's stop spec.

        criteria_price is the buffered reference level (known at the expansion
        bar). For ``constant_pct`` the level depends on the fill, so we hand the
        broker the fraction instead. The max-loss cap is applied at fill."""
        b = self.stop_buffer_pct
        if self.stop_type == "expansion_open":
            return exp_bar.open * (1 - b), None
        if self.stop_type == "range_low":
            return exp_bar.low * (1 - b), None
        if self.stop_type == "constant_pct":
            return None, self.stop_constant_pct
        if self.stop_type in ("21ema", "50sma"):
            ref = self._ma_for(self.stop_type)
            return (ref * (1 - b) if ref is not None else None), None
        raise ValueError(f"unsupported stop_type {self.stop_type!r}")

    # ── main loop ─────────────────────────────────────────────────────────────
    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        up, lo, close = self._upper(bar), self._lower(bar), bar.close

        # Subclass hook: update any extra indicators (e.g. a Stage-2 filter)
        # with this bar BEFORE the entry decision. Runs every bar, warm-up too.
        self._pre_decision(bar, ctx)

        if not ctx.in_position:
            self._exit_queued = False           # cleared once flat again
        just_entered = ctx.in_position and not self._was_in_position

        # Maximal box ending at the PREVIOUS bar (state before adding this bar).
        pre_len = len(self._win_upper)
        pre_high = max(self._win_upper) if pre_len else None
        pre_low = min(self._win_lower) if pre_len else None

        day_chg = ((close / self._prev_close - 1) * 100
                   if self._prev_close not in (None, 0) else 0.0)

        # Does this bar break the box ending at the previous bar?
        broke = False
        if pre_len:
            nh, nl = max(pre_high, up), min(pre_low, lo)
            broke = nl > 0 and (nh - nl) / nl * 100 > self.box_pct

        if (ctx.enabled and not ctx.in_position
                and broke and pre_len >= self.min_days
                and day_chg >= self.expansion_pct
                and self._liquidity_ok()
                and self._entry_allowed(bar, ctx)):
            # ── entry: only when flat (and any subclass gate passes) ───────--
            self._enter(bar, ctx, day_chg, pre_high, pre_low, pre_len)
        elif ctx.in_position and not just_entered and not self._exit_queued:
            # ── exit: sell-in-weakness (skip the entry bar; pairing starts the
            #    bar after the buy, matching the batch) ─────────────────────--
            self._manage_exit(close, ctx)

        # ── advance state (always — warm-up, holding, or flat) ────────────────
        self._advance_box(up, lo, bar, day_chg)
        self._update_ma(close)
        self._dv_window.append(bar.close * bar.volume)
        self._vol_avg.append(bar.volume)   # trailing avg ends at the PRIOR bar at entry
        self._below_prev = self._below_weakness_ma(close)
        self._prev_close = close
        self._was_in_position = ctx.in_position
        # record this bar's day-change + tightness for the NEXT bar's breakout snapshot
        self._recent_daychg.append(day_chg)
        self._last_range_pct = (round((bar.high - bar.low) / bar.low * 100, 2)
                                if bar.low > 0 else None)
        self._last_body_pct = (round(abs(bar.close - bar.open) / bar.open * 100, 2)
                               if bar.open > 0 else None)
        self._closes.append(close)

    # ── helpers ────────────────────────────────────────────────────────────--
    def _enter(self, bar, ctx, day_chg, box_high, box_low, box_len) -> None:
        d_range = bar.high - bar.low
        closing_range = (bar.close - bar.low) / d_range * 100 if d_range > 0 else 50.0
        exp = RangeExpansion(
            date=bar.date, direction="up", close=round(bar.close, 2),
            day_chg_pct=round(day_chg, 2),
            move_pct=round((bar.close / box_high - 1) * 100, 2) if box_high else 0.0,
            day_high=round(bar.high, 2), day_low=round(bar.low, 2),
            day_range_pct=round(d_range / bar.low * 100, 2) if bar.low > 0 else 0.0,
            closing_range_pct=round(closing_range, 1),
            strong_close=closing_range >= (100 - self.close_threshold_pct),
        )
        logger.info(
            "%-6s SIGNAL %s range_expansion  day_chg=%+.2f%%  "
            "box=%dd/%.2f%%  close_range=%.0f%%%s -> buy next open",
            ctx.ticker, bar.date.date(), day_chg, box_len,
            (box_high - box_low) / box_low * 100 if box_low else 0.0,
            closing_range, "  STRONG" if exp.strong_close else "",
        )
        criteria_price, constant_pct = self._stop_criteria(bar)
        features = {
            # the breakout/signal bar — ALL context features (MAs, Kell, overhead,
            # EPS) are snapshotted here; the trade fills at the NEXT open, so
            # entry_date is one trading day after signal_date.
            "signal_date": bar.date.strftime("%Y-%m-%d"),
            "range_length_days": box_len,
            "range_height_pct": round((box_high - box_low) / box_low * 100, 2)
                                if box_low else None,
            # both height measures of the same box, regardless of price_mode:
            "range_height_hl_pct": self._box_height(self._win_high, self._win_low),
            "range_height_oc_pct": self._box_height(self._win_ocup, self._win_oclo),
            # biggest single-day move inside the box (day-over-day %); down is negative
            "range_max_up_day_pct": round(max(self._win_daychg), 2) if self._win_daychg else None,
            "range_max_down_day_pct": round(min(self._win_daychg), 2) if self._win_daychg else None,
            # 2LYNCH: consecutive up-days ending AT the breakout (1 = only the
            # breakout day is up; higher = extended into the move, Bonde's "2").
            "up_days_in_a_row": self._up_days_run(),
            # 2LYNCH "N": the day BEFORE the breakout — narrow/quiet is better.
            "day_before_range_pct": self._last_range_pct,
            "day_before_body_pct": self._last_body_pct,
            # 2LYNCH "C" cleanliness: # of >4% down days inside the box (Bonde: <=1).
            "range_down_days_gt4": sum(1 for dc in self._win_daychg if dc < -4.0),
            # 2LYNCH "L": linearity of the advance INTO the base — Kaufman efficiency
            # ratio (|net move| / total path, 0=choppy, 1=straight) + its net % move
            # (sign = direction). Measured over the window just before the box.
            **self._linearity_features(box_len),
            # same metrics over just the LAST N days of the box (common tail read)
            **self._recent_range_features(),
            "price_mode": self.price_mode,
            "expansion_move_pct": exp.day_chg_pct,
            "expansion_closing_range": exp.closing_range_pct,
            "strong_close": exp.strong_close,
            # tight days inside the box (body + range variants, per threshold)
            **self._tight_features(box_len),
            "stop_type": self.stop_type,
            "ma_type": self.trade_ma_type,
            f"avg_dollar_vol_{self.dollar_volume_window}d": self.avg_dollar_volume(),
            **self._volume_features(bar),
        }
        features.update(self._extra_features(bar))   # subclass feature snapshot
        if self._scan:
            # scan mode: record EVERY setup independently (overlaps allowed); the
            # scan runner simulates each trade with fixed sizing. No live position.
            self.scan_signals.append({
                "signal_date": bar.date,
                "features": features,
                "criteria_price": criteria_price,
                "constant_pct": constant_pct,
            })
            return
        ctx.buy(
            reason="range_expansion", features=features,
            stop_criteria_price=criteria_price, stop_constant_pct=constant_pct,
            stop_max_loss_pct=self.max_loss_pct,
        )

    def _manage_exit(self, close: float, ctx: StrategyContext) -> None:
        below_today = self._below_weakness_ma(close)
        if self._below_prev and below_today:
            rising = (self.allow_rising_close_exception
                      and self._prev_close is not None and close > self._prev_close)
            if not rising:
                ctx.sell(reason="weakness")
                self._exit_queued = True

    # ── extension hooks (overridden by StageRangeStrategy etc.) ──────────────
    def _pre_decision(self, bar: Bar, ctx: StrategyContext) -> None:
        """Update extra per-bar indicators before the entry decision."""

    def _entry_allowed(self, bar: Bar, ctx: StrategyContext) -> bool:
        """Extra gate on top of the range-expansion trigger. Default: open."""
        return True

    def _extra_features(self, bar: Bar) -> dict:
        """Extra entry-time features to record on the trade. Default: none."""
        return {}

    def _linearity_features(self, box_len: int) -> dict:
        """Kaufman efficiency ratio of the advance in the ``linearity_window`` bars
        ENDING just before the box (i.e. the run-up into the consolidation). ~1 =
        smooth/straight, ~0 = choppy. Also the net % move over that window (sign =
        up/down). None until there's enough pre-box history."""
        W = self.linearity_window
        closes = list(self._closes)                 # up to the prior bar
        end = len(closes) - box_len                  # exclusive end = box start
        out = {"prior_move_linearity": None, "prior_move_net_pct": None}
        if end <= 1:
            return out
        w = closes[max(0, end - W):end]
        if len(w) < 10 or w[0] <= 0:
            return out
        path = sum(abs(w[i] - w[i - 1]) for i in range(1, len(w)))
        if path <= 0:
            return out
        out["prior_move_linearity"] = round(abs(w[-1] - w[0]) / path, 3)
        out["prior_move_net_pct"] = round((w[-1] / w[0] - 1) * 100, 2)
        return out

    def _up_days_run(self) -> int:
        """Consecutive up-close days ending at (and including) the breakout bar.
        The breakout bar is an up day by construction (day_chg >= expansion_pct);
        we then walk back through the raw recent day-changes while they're > 0."""
        run = 1
        for dc in reversed(self._recent_daychg):
            if dc is not None and dc > 0:
                run += 1
            else:
                break
        return run

    @staticmethod
    def _box_height(uppers, lowers) -> Optional[float]:
        """Height % of the box from a pair of upper/lower deques."""
        if not lowers:
            return None
        lo = min(lowers)
        return round((max(uppers) - lo) / lo * 100, 2) if lo > 0 else None

    def _recent_range_features(self) -> dict:
        """Height (hl/oc) and max up/down day over the LAST ``range_recent_days``
        bars of the box — a common tail read regardless of total box length."""
        n = self.range_recent_days
        tag = f"last{n}"
        hi, lo = list(self._win_high)[-n:], list(self._win_low)[-n:]
        ocu, ocl = list(self._win_ocup)[-n:], list(self._win_oclo)[-n:]
        dc = list(self._win_daychg)[-n:]
        body, rng = list(self._win_body)[-n:], list(self._win_range)[-n:]
        out = {
            f"range_{tag}_height_hl_pct": self._box_height(hi, lo),
            f"range_{tag}_height_oc_pct": self._box_height(ocu, ocl),
            f"range_{tag}_max_up_day_pct": round(max(dc), 2) if dc else None,
            f"range_{tag}_max_down_day_pct": round(min(dc), 2) if dc else None,
        }
        out.update(self._tight_counts(body, rng, len(body), suffix=f"_{tag}"))
        return out

    def _tight_counts(self, body_vals, range_vals, denom: int, suffix: str = "") -> dict:
        """Tight-day counts at each threshold (body + range) over the given bars."""
        out: dict = {}
        for t in self.tight_pcts:
            tag = f"{t:g}pct"
            body = sum(1 for b in body_vals if b <= t)
            rng = sum(1 for r in range_vals if r <= t)
            out[f"tight_body_days{suffix}_{tag}"] = body
            out[f"tight_body_pct{suffix}_{tag}"] = round(body / denom * 100, 1) if denom else None
            out[f"tight_range_days{suffix}_{tag}"] = rng
            out[f"tight_range_pct{suffix}_{tag}"] = round(rng / denom * 100, 1) if denom else None
        return out

    def _tight_features(self, box_len: int) -> dict:
        """Tight-day counts over the whole box."""
        return self._tight_counts(self._win_body, self._win_range, box_len)

    def _volume_features(self, bar: Bar) -> dict:
        """Volume signature (O'Neil/Minervini): the breakout-day surge vs the
        trailing average, and volume dry-up in the base. Causal — ``_vol_avg``
        ends at the prior bar and ``_win_vol`` is the box (both exclude the
        breakout bar). All ratios (cross-ticker comparable)."""
        v_avg = (sum(self._vol_avg) / len(self._vol_avg)) if self._vol_avg else None
        box = list(self._win_vol)
        base_avg = (sum(box) / len(box)) if box else None
        out = {
            # breakout-day volume vs the trailing 50-day average (>1 = surge)
            "breakout_vol_ratio": round(bar.volume / v_avg, 2) if v_avg else None,
            # base volume vs the trailing average (<1 = quiet/dried-up base)
            "base_vol_ratio": round(base_avg / v_avg, 2)
                              if (v_avg and base_avg is not None) else None,
        }
        # dry-up THROUGH the base: last third vs first third of box volume (<1 = drying)
        n = len(box)
        if n >= 6:
            t = n // 3
            first = sum(box[:t]) / t
            last = sum(box[-t:]) / t
            out["base_vol_dryup"] = round(last / first, 2) if first > 0 else None
        else:
            out["base_vol_dryup"] = None
        return out

    def _advance_box(self, up: float, lo: float, bar: Bar, day_chg: float) -> None:
        """Append this bar and shrink from the left so the window is the maximal
        box ending at this bar. All per-bar deques are kept in lock-step so the
        tight-day counts, both box heights, and max up/down day match the box."""
        self._win_upper.append(up)
        self._win_lower.append(lo)
        self._win_body.append(abs(bar.close - bar.open) / bar.open * 100 if bar.open > 0 else 999.0)
        self._win_range.append((bar.high - bar.low) / bar.low * 100 if bar.low > 0 else 999.0)
        self._win_high.append(bar.high)
        self._win_low.append(bar.low)
        self._win_ocup.append(max(bar.open, bar.close))
        self._win_oclo.append(min(bar.open, bar.close))
        self._win_daychg.append(day_chg)
        self._win_vol.append(bar.volume)
        while len(self._win_upper) > 1:
            nh, nl = max(self._win_upper), min(self._win_lower)
            if nl > 0 and (nh - nl) / nl * 100 <= self.box_pct:
                break
            for dq in (self._win_upper, self._win_lower, self._win_body, self._win_range,
                       self._win_high, self._win_low, self._win_ocup, self._win_oclo,
                       self._win_daychg, self._win_vol):
                dq.popleft()
