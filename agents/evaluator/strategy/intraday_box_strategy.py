"""Intraday box-break buy — the decision is made ON the breakout day, after 10:30.

The daily `RangeBreakoutStrategy` trigger is "the box widened AND the day closed
>= expansion_pct" and it fills at the NEXT open. There is no level test in it at
all: direction comes entirely from the day-over-day % (a big DOWN day satisfies
the box-widening test identically). This strategy replaces that with a level
test evaluated intraday:

    trigger level = max(first-hour high, box_high * (1 + require_box_clear_pct/100))
    ENTER  the first time price trades at/through that level after 10:30
    STOP   the session low established BEFORE the entry hour ("low of the day")

No magnitude gate: the confirmation is the level being taken out, not how far
price travelled. `require_box_clear_pct` is how much clearance above the box
counts as a real break (default 1%), NOT a momentum filter.

**No lookahead.** At 10:30 on day T the last COMPLETED daily bar is T-1, so:
  * the box is the maximal box ending at T-1 (state before T is appended);
  * every context feature is snapshotted as of T-1 — providers are advanced with
    bar T only AFTER the entry decision (this reverses the parent's ordering,
    which calls `_pre_decision` first because it decides on T's close);
  * the stop is the day's low up to the START of the entry hour — established
    before the fill;
  * the entry bar's own low counts AGAINST the position (it may print after the
    fill) — see `entry_day_post_low`.

The one place T's daily bar is read before the decision is a pure skip filter:
if `bar.high` never reached the trigger level, no intraday trigger can have
fired, so the (expensive) feature snapshot is skipped. Excluding days the
trigger provably missed cannot leak information into the days it kept.

Scan mode only (`collect_intraday_setups`); the single-position broker fills at
the next open by construction and cannot represent an intraday fill.
"""
from __future__ import annotations

from datetime import time as _time
from typing import Optional

import pandas as pd

from ..engine.records import Bar
from .base import StrategyContext
from .stage_range_strategy import StageRangeStrategy

OPEN_TIME = _time(9, 30)


class IntradayBoxBreakStrategy(StageRangeStrategy):
    """Box break confirmed intraday. All ~240 `StageRangeStrategy` features are
    recorded as of T-1, plus the opening-range/entry block (`or_*`, `entry_*`,
    `lod_*`).

    Args (beyond the parent's):
      require_box_clear_pct: how far above `box_high` counts as cleared (%).
      max_lod_atr: optional gate — skip the entry when the stop distance exceeds
        this many ATRs (Jeff Sun's LoD < 0.6 ATR rule). None = record, don't gate.
      require_or_high: when True (default) the trigger is at least the first-hour
        high, so a name that gapped over the box still has to take out hour one.
    """

    name = "intraday_box_break"

    def __init__(self, *, require_box_clear_pct: float = 1.0,
                 max_lod_atr: Optional[float] = None,
                 require_or_high: bool = True, **kw):
        # the parent's day-over-day gate is not used here; keep it inert so any
        # inherited code path that reads it can't silently re-impose a magnitude.
        kw.setdefault("expansion_pct", 0.0)
        super().__init__(**kw)
        self.require_box_clear_pct = require_box_clear_pct
        self.max_lod_atr = max_lod_atr
        self.require_or_high = require_or_high
        self._prev_bar: Optional[Bar] = None
        self._intraday: Optional[pd.DataFrame] = None
        self._or_vol: dict = {}          # date -> trailing-mean first-hour volume
        self._sessions: dict = {}        # date -> that session's hourly frame

    # ── intraday data wiring (called once per ticker by the collector) ───────
    def set_intraday(self, df: Optional[pd.DataFrame], *, rvol_window: int = 20) -> None:
        """Attach the hourly frame for this ticker and pre-split it by session."""
        self._sessions, self._or_vol, self._intraday = {}, {}, df
        if df is None or df.empty:
            return
        d = df.sort_index()
        days = d.index.normalize()
        or_vols: list[tuple[pd.Timestamp, float]] = []
        for day, g in d.groupby(days):
            if g.index[0].time() != OPEN_TIME:   # partial/irregular session
                continue
            self._sessions[day.tz_localize(None) if day.tz else day] = g
            or_vols.append((day, float(g["Volume"].iloc[0])))
        if or_vols:
            s = pd.Series([v for _, v in or_vols],
                          index=[k.tz_localize(None) if k.tz else k for k, _ in or_vols])
            base = s.rolling(rvol_window).mean().shift(1)   # PRIOR sessions only
            self._or_vol = base.to_dict()

    # ── the intraday trigger ────────────────────────────────────────────────
    def _intraday_trigger(self, bar: Bar, box_high: float) -> Optional[dict]:
        """Return the fill dict for day ``bar.date``, or None if it never triggered."""
        g = self._sessions.get(pd.Timestamp(bar.date).normalize())
        if g is None or len(g) < 2:
            return None
        first = g.iloc[0]
        or_high, or_low, or_open = float(first["High"]), float(first["Low"]), float(first["Open"])
        clear = box_high * (1 + self.require_box_clear_pct / 100.0)
        level = max(or_high, clear) if self.require_or_high else clear

        rest = g.iloc[1:]                       # bars starting 10:30 onwards
        lows = [or_low]
        for ts, row in rest.iterrows():
            if float(row["High"]) >= level:
                # stop-buy at the level; a bar that gapped through fills worse
                fill = max(level, float(row["Open"]))
                stop = min(lows)                # low BEFORE this hour began
                post = min([float(row["Low"])] + [float(x) for x in
                                                  rest.loc[rest.index > ts, "Low"]])
                return {
                    "entry_price": fill, "entry_time": ts.strftime("%H:%M"),
                    "lod_stop": stop, "entry_day_post_low": post,
                    "or_high": or_high, "or_low": or_low, "or_open": or_open,
                    "or_volume": float(first["Volume"]),
                    "trigger_level": level, "box_clear_at_entry_pct":
                        round((fill / box_high - 1) * 100, 3),
                }
            lows.append(float(row["Low"]))
        return None

    # ── main loop (entry decided BEFORE the bar advances any state) ──────────
    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if not self._scan:
            raise RuntimeError("IntradayBoxBreakStrategy is scan-mode only "
                               "(use collect_intraday_setups); the broker fills "
                               "at the next open and cannot model an intraday fill.")
        up, lo, close = self._upper(bar), self._lower(bar), bar.close
        day_chg = ((close / self._prev_close - 1) * 100
                   if self._prev_close not in (None, 0) else 0.0)

        # Maximal box ending at the PREVIOUS bar (state before adding this bar).
        pre_len = len(self._win_upper)
        pre_high = max(self._win_upper) if pre_len else None
        pre_low = min(self._win_lower) if pre_len else None

        if (ctx.enabled and pre_len >= self.min_days and pre_high and pre_low
                and self._prev_bar is not None and self._liquidity_ok()
                # skip-only filter: if the day never reached the level, no
                # trigger can have fired -> don't pay for the feature snapshot
                and bar.high >= pre_high * (1 + self.require_box_clear_pct / 100.0)):
            hit = self._intraday_trigger(bar, pre_high)
            if hit is not None:
                self._record_intraday(bar, hit, pre_high, pre_low, pre_len)

        # ── only NOW does bar T update anything ─────────────────────────────
        self._pre_decision(bar, ctx)
        self._advance_box(up, lo, bar, day_chg)
        self._update_ma(close)
        self._dv_window.append(bar.close * bar.volume)
        self._vol_avg.append(bar.volume)
        self._below_prev = self._below_weakness_ma(close)
        self._prev_close = close
        self._recent_daychg.append(day_chg)
        self._last_range_pct = (round((bar.high - bar.low) / bar.low * 100, 2)
                                if bar.low > 0 else None)
        self._last_body_pct = (round(abs(bar.close - bar.open) / bar.open * 100, 2)
                               if bar.open > 0 else None)
        self._closes.append(close)
        self._prev_bar = bar

    # ── the setup record (features as of T-1, entry/stop from day T) ─────────
    def _record_intraday(self, bar: Bar, hit: dict, box_high: float,
                         box_low: float, box_len: int) -> None:
        prev = self._prev_bar                      # the T-1 bar: the feature anchor
        self._box_len = box_len
        self._last_criteria = self._tt.criteria()  # Stage-2 snapshot, as of T-1
        feats = {
            "signal_date": prev.date.strftime("%Y-%m-%d"),   # last completed daily bar
            "entry_day": bar.date.strftime("%Y-%m-%d"),      # the day we actually buy
            "range_length_days": box_len,
            "range_height_pct": round((box_high - box_low) / box_low * 100, 2)
                                if box_low else None,
            "range_height_hl_pct": self._box_height(self._win_high, self._win_low),
            "range_height_oc_pct": self._box_height(self._win_ocup, self._win_oclo),
            "range_max_up_day_pct": round(max(self._win_daychg), 2) if self._win_daychg else None,
            "range_max_down_day_pct": round(min(self._win_daychg), 2) if self._win_daychg else None,
            "up_days_in_a_row": self._up_days_run(),
            "day_before_range_pct": self._last_range_pct,
            "day_before_body_pct": self._last_body_pct,
            "range_down_days_gt4": sum(1 for dc in self._win_daychg if dc < -4.0),
            **self._linearity_features(box_len),
            **self._recent_range_features(),
            **self._tight_features(box_len),
            "price_mode": self.price_mode,
            "box_high": round(box_high, 4),
            "box_low": round(box_low, 4),
            "require_box_clear_pct": self.require_box_clear_pct,
            "ma_type": self.trade_ma_type,
            f"avg_dollar_vol_{self.dollar_volume_window}d": self.avg_dollar_volume(),
            # volume signature as of T-1 (day T's full-day volume is unknown at 10:30)
            **self._volume_features(prev),
        }
        feats.update(self._extra_features(prev))   # ~240 context features, as of T-1

        # ── entry / stop block (day T, intraday) ────────────────────────────
        entry, stop = hit["entry_price"], hit["lod_stop"]
        atr_pct = feats.get("atr_pct")
        stop_pct = (entry / stop - 1) * 100 if stop and stop > 0 else None
        lod_atr = (stop_pct / atr_pct) if (stop_pct is not None and atr_pct) else None
        feats.update({
            "entry_price": round(entry, 4),
            "entry_time": hit["entry_time"],
            "lod_stop": round(stop, 4),
            "stop_dist_pct": round(stop_pct, 3) if stop_pct is not None else None,
            "lod_stop_atr": round(lod_atr, 3) if lod_atr is not None else None,
            "or_high": round(hit["or_high"], 4),
            "or_low": round(hit["or_low"], 4),
            "or_open": round(hit["or_open"], 4),
            "or_range_pct": round((hit["or_high"] - hit["or_low"]) / hit["or_low"] * 100, 3)
                            if hit["or_low"] else None,
            "or_gap_pct": round((hit["or_open"] / prev.close - 1) * 100, 3)
                          if prev.close else None,
            "rvol_open_20": self._rvol(bar, hit["or_volume"]),
            "trigger_level": round(hit["trigger_level"], 4),
            "box_clear_at_entry_pct": hit["box_clear_at_entry_pct"],
        })
        if self.max_lod_atr is not None and (lod_atr is None or lod_atr > self.max_lod_atr):
            return                                  # his LoD-tightness gate
        self.scan_signals.append({
            "signal_date": prev.date,
            "entry_date": bar.date,
            "entry_price": entry,
            "features": feats,
            "lod_stop": stop,
            "entry_day_post_low": hit["entry_day_post_low"],
            "criteria_price": stop,      # so LoD-unaware exits still get a level
            "constant_pct": None,
        })

    def _volume_features(self, bar: Bar) -> dict:
        """As the parent, but the trailing average must END BEFORE ``bar``.

        The parent is called with the BREAKOUT bar while ``_vol_avg`` still ends
        at the prior bar. Here the reference bar IS T-1, whose volume has already
        been appended, so it would sit in its own denominator — pop it for the
        duration of the call."""
        stash = self._vol_avg.pop() if self._vol_avg else None
        try:
            return super()._volume_features(bar)
        finally:
            if stash is not None:
                self._vol_avg.append(stash)

    def _rvol(self, bar: Bar, or_volume: float) -> Optional[float]:
        base = self._or_vol.get(pd.Timestamp(bar.date).normalize())
        if base is None or base != base or base <= 0:
            return None
        return round(or_volume / base, 3)
