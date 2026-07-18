"""Oliver Kell 'Cycle of Price Action' features — incremental, no lookahead.

>>> The authoritative, up-to-date definition of EVERY metric here lives in
>>> ``KELL_DEFINITIONS.md`` (same folder). When you change a definition below,
>>> update that file in the same edit — it is the single source of truth.

Per Kell's actual definitions (see TraderLion), a **Wedge Pop** is price
*reclaiming* the EMA after a **downside extension** — there is no fixed
"N days below" rule. So a pop fires on the reclaim (close crosses from below to
above the p-EMA) provided the preceding down-move reached at least
``min_ext_below_pct`` **below the reference EMA** (``ext_ref_ema``, default 20).
Wedge Drop is the mirror (lose the EMA after an upside extension).

**Reversal Extension** is Kell's capitulation: the point of maximum extension
**below** the reference EMA during the most recent down-move that got extended
past the threshold — *not* the lowest low over a fixed window (which can be
years stale). It updates each time a fresh qualified capitulation occurs, so it
stays anchored to the current cycle. **Exhaustion Extension** is the mirror
(max extension above the EMA — the climax top).

EMA Crossback counts rising-EMA support tests since the latest pop. Everything
uses only bars already delivered.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import pandas as pd

from ..engine.records import Bar


@dataclass
class _Rec:
    idx: int
    date: pd.Timestamp
    emas: dict          # period -> EMA value at this bar (for the rising check)


class KellCycle:
    def __init__(
        self,
        *,
        ema_periods=(10, 20),
        min_ext_below_pct: float = 5.0,       # wedge POP: down-move this far below ref EMA
        min_ext_above_pct: float = 5.0,       # wedge DROP: up-move this far above ref EMA
        reversal_min_ext_pct: float = 10.0,   # reversal EXTENSION (capitulation): much deeper
        exhaustion_min_ext_pct: float = 10.0, # exhaustion EXTENSION (climax): much higher
        ext_ref_ema: int = 20,                # reference EMA for extension + reversal/exhaustion
        crossback_low_tol: float = 0.005,
        crossback_reset_pct: float = 0.0,
        crossback_ema_rising_lookback: int = 5,
        cycle_count_months: int = 12,
        both_within_bars: int = 10,
        vol_avg_window: int = 20,
        reversal_window_bars: int = 40,   # a reversal "recently" precedes the pop
        # ── 3-state trend classification (uptrend / basing / downtrend) ──────
        trend_slope_window: int = 5,      # bars over which the ref-EMA slope is measured
        trend_flat_band_pct: float = 1.5, # |slope| within this = flat = basing
        trend_pivot_window: int = 5,      # ±bars for a confirmed swing-high pivot
        buffer_bars: int = 40,
    ):
        self.ema_periods = list(ema_periods)
        self.min_ext_below = min_ext_below_pct
        self.min_ext_above = min_ext_above_pct
        self.rev_min_ext = reversal_min_ext_pct
        self.exh_min_ext = exhaustion_min_ext_pct
        self.ext_ref_ema = ext_ref_ema
        self.cb_tol = crossback_low_tol
        self.cb_reset = crossback_reset_pct
        self.cb_rising_lb = crossback_ema_rising_lookback
        self.reversal_window = reversal_window_bars
        self.count_months = cycle_count_months
        self.both_within = both_within_bars
        self.trend_slope_window = trend_slope_window
        self.trend_flat_band = trend_flat_band_pct
        self.trend_pivot_w = trend_pivot_window
        # swing-high pivots (±trend_pivot_w) for the "consolidating below the last
        # confirmed high" leg of the 3-state trend classification
        self._hi_buf: deque[float] = deque(maxlen=2 * trend_pivot_window + 1)
        self._last_swing_high = None
        # per-run exhaustion anchors (based on the 3-state trend):
        #   last DOWNTREND bar → count exhaustions since (accumulates through bases)
        #   last NON-UPTREND bar (basing or downtrend) → count since the last base
        self._last_downtrend_idx = -1
        self._last_nonuptrend_idx = -1
        # transition tracking: current 3-state label + the PREVIOUS distinct one
        # (so a base shows whether it followed an uptrend or a downtrend).
        self._trend_cur = None
        self._trend_prev = None

        self._all_periods = sorted(set(self.ema_periods) | {ext_ref_ema})
        self._buf: deque[_Rec] = deque(maxlen=max(buffer_bars, crossback_ema_rising_lookback + 2,
                                                  trend_slope_window + 2))
        self._vol_hist: deque[float] = deque(maxlen=vol_avg_window)
        self._i = -1
        self._ema = {p: None for p in self._all_periods}
        # wedge pop/drop + crossback state (per tracked EMA)
        self._below_run = {p: 0 for p in self.ema_periods}
        self._above_run = {p: 0 for p in self.ema_periods}
        self._max_ext_below = {p: 0.0 for p in self.ema_periods}
        self._max_ext_above = {p: 0.0 for p in self.ema_periods}
        self._pops = {p: [] for p in self.ema_periods}    # (idx, bars_below, date, max_ext, vol)
        self._drops = {p: [] for p in self.ema_periods}   # (idx, bars_above, date, max_ext, vol)
        self._cb_count = {p: 0 for p in self.ema_periods}
        self._cb_armed = {p: False for p in self.ema_periods}
        self._has_pop = {p: False for p in self.ema_periods}
        # reversal / exhaustion extension episodes (vs the reference EMA)
        self._rev_run = 0
        self._rev = None          # deepest bar of the CURRENT below-episode
        self._last_rev = None     # last COMPLETED qualified capitulation
        self._rev_episodes = []   # (idx,) of every completed qualified reversal
        self._exh_run = 0
        self._exh = None
        self._last_exh = None
        self._exh_episodes = []   # (idx,) of every completed qualified exhaustion
        # ── Kell uptrend STATE MACHINE (Cycle of Price Action) ───────────────
        # STARTS at a Wedge Pop (reference-EMA reclaim after a below-extension —
        # ideally after a Reversal Extension); a new Wedge Pop while already up =
        # a Base-n'-Break LEG; ENDS when price is below the ref EMA AND the ref
        # EMA has TURNED DOWN (self-calibrating — no arbitrary bar count: a base
        # is a dip while the EMA still rises, a trend-end is the EMA rolling over).
        self._up_state = "down"
        self._uptrend_start = None
        self._uptrend_had_reversal = False
        self._uptrend_legs = 0

    # ── feed one bar ──────────────────────────────────────────────────────────
    def update(self, bar: Bar) -> None:
        self._i += 1
        avg = (sum(self._vol_hist) / len(self._vol_hist)) if self._vol_hist else None
        vol_ratio = round(bar.volume / avg, 2) if avg and avg > 0 else None
        self._vol_hist.append(bar.volume)

        for p in self._all_periods:
            alpha = 2.0 / (p + 1)
            prev = self._ema[p]
            self._ema[p] = bar.close if prev is None else alpha * bar.close + (1 - alpha) * prev
        ref = self._ema[self.ext_ref_ema]
        emas_snap = {p: self._ema[p] for p in self.ema_periods}
        # strict 1-bar slope of the ref EMA (for the symmetric state machine):
        # rose = up vs the previous bar, fell = down vs it, flat = neither.
        ref_prev = self._buf[-1].emas.get(self.ext_ref_ema) if self._buf else None
        ref_rose = ref_prev is not None and ref > ref_prev
        ref_fell = ref_prev is not None and ref < ref_prev
        self._buf.append(_Rec(self._i, bar.date, emas_snap))
        # confirmed swing-high pivot (center of a 2w+1 window is the max), causal
        self._hi_buf.append(bar.high)
        w = self.trend_pivot_w
        if len(self._hi_buf) == 2 * w + 1 and self._hi_buf[w] == max(self._hi_buf):
            self._last_swing_high = self._hi_buf[w]

        ext_below_ref = (ref - bar.low) / ref * 100 if ref and ref > 0 else 0.0
        ext_above_ref = (bar.high - ref) / ref * 100 if ref and ref > 0 else 0.0
        c = bar.close

        # ── wedge pop / drop / crossback (per tracked EMA) ───────────────────
        for p in self.ema_periods:
            ema = self._ema[p]
            rising = self._ema_rising(p, ema)
            if c > ema:
                if self._below_run[p] > 0 and self._max_ext_below[p] >= self.min_ext_below:
                    self._pops[p].append((self._i, self._below_run[p], bar.date,
                                          round(self._max_ext_below[p], 2), vol_ratio))
                    self._has_pop[p] = True
                    self._cb_count[p] = 0
                    self._cb_armed[p] = False
                    # ── STATE MACHINE: a Wedge Pop on the reference EMA ──────
                    if p == self.ext_ref_ema:
                        if self._up_state == "down":          # uptrend STARTS
                            self._up_state = "up"
                            self._uptrend_start = self._i
                            self._uptrend_legs = 1
                            self._uptrend_had_reversal = bool(
                                self._rev_episodes
                                and self._i - self._rev_episodes[-1] <= self.reversal_window)
                        else:                                  # Base-n'-Break LEG
                            self._uptrend_legs += 1
                self._below_run[p] = 0
                self._max_ext_below[p] = 0.0
                self._above_run[p] += 1
                self._max_ext_above[p] = max(self._max_ext_above[p], ext_above_ref)
            elif c < ema:
                if self._above_run[p] > 0 and self._max_ext_above[p] >= self.min_ext_above:
                    self._drops[p].append((self._i, self._above_run[p], bar.date,
                                           round(self._max_ext_above[p], 2), vol_ratio))
                self._above_run[p] = 0
                self._max_ext_above[p] = 0.0
                self._below_run[p] += 1
                self._max_ext_below[p] = max(self._max_ext_below[p], ext_below_ref)

            if self._has_pop[p]:
                if bar.low > ema * (1 + self.cb_reset):
                    self._cb_armed[p] = True
                touched = ema * (1 - self.cb_tol) <= bar.low <= ema and c > ema
                if rising and self._cb_armed[p] and touched:
                    self._cb_count[p] += 1
                    self._cb_armed[p] = False

        # ── reversal / exhaustion extension episodes (vs ref EMA) ────────────
        if ref and ref > 0:
            if c < ref:                                   # in a below-episode
                if self._exh_run > 0 and self._exh and self._exh["ext"] >= self.exh_min_ext:
                    self._last_exh = self._exh            # up-episode ended -> lock if qualified
                    self._exh_episodes.append(self._exh["idx"])
                self._exh_run, self._exh = 0, None
                if self._rev is None or ext_below_ref > self._rev["ext"]:
                    self._rev = dict(px=bar.low, idx=self._i, date=bar.date,
                                     vol=vol_ratio, emas=emas_snap, ext=round(ext_below_ref, 2))
                self._rev_run += 1
            elif c > ref:                                 # in an above-episode
                if self._rev_run > 0 and self._rev and self._rev["ext"] >= self.rev_min_ext:
                    self._last_rev = self._rev            # down-episode ended -> lock if qualified
                    self._rev_episodes.append(self._rev["idx"])
                self._rev_run, self._rev = 0, None
                if self._exh is None or ext_above_ref > self._exh["ext"]:
                    self._exh = dict(px=bar.high, idx=self._i, date=bar.date,
                                     vol=vol_ratio, emas=emas_snap, ext=round(ext_above_ref, 2))
                self._exh_run += 1

        # ── 2-state wedge-pop machine (kept for legs/from_reversal features) ──
        # UP when price is ABOVE the ref EMA and it strictly ROSE; DOWN when below
        # and it strictly FELL; a FLAT EMA persists (symmetric hysteresis).
        if ref and ref > 0:
            if self._up_state == "down" and c > ref and ref_rose:
                self._up_state = "up"                 # uptrend STARTS/resumes
                self._uptrend_start = self._i
                self._uptrend_legs = 1
                self._uptrend_had_reversal = bool(
                    self._rev_episodes
                    and self._i - self._rev_episodes[-1] <= self.reversal_window)
            elif self._up_state == "up" and c < ref and ref_fell:
                self._up_state = "down"               # uptrend ENDS
                self._uptrend_start = None

        # ── per-run exhaustion anchors, from the 3-STATE trend at this bar ───
        st, _ = self.trend_state(bar.close)
        if st == "downtrend":
            self._last_downtrend_idx = self._i
        if st is not None and st != "uptrend":        # basing OR downtrend
            self._last_nonuptrend_idx = self._i
        # previous DISTINCT trend state (point-in-time): update on a real change
        if st is not None and st != self._trend_cur:
            if self._trend_cur is not None:
                self._trend_prev = self._trend_cur
            self._trend_cur = st

    def trend_state(self, close: float) -> tuple:
        """3-state trend at the current bar: 'uptrend' / 'basing' / 'downtrend'.

        Slope = % change of the ref EMA over ``trend_slope_window`` bars. A slope
        inside ±``trend_flat_band`` is FLAT (basing). Above the band it's an
        uptrend only if price is at/above the last confirmed swing high; a rising
        EMA with price pulled back BELOW that high is a base (consolidating).
        Returns (state, slope_pct). state is None until seasoned."""
        n = self.trend_slope_window
        ref = self._ema.get(self.ext_ref_ema)
        if ref is None or len(self._buf) < n + 1:
            return None, None
        ref_n_ago = self._buf[-1 - n].emas.get(self.ext_ref_ema)
        if not ref_n_ago:
            return None, None
        slope = (ref / ref_n_ago - 1) * 100
        if slope < -self.trend_flat_band:
            return "downtrend", round(slope, 2)
        if slope > self.trend_flat_band:
            above_ema = close > ref
            at_high = self._last_swing_high is None or close >= self._last_swing_high
            if above_ema and at_high:             # rising EMA + above it + at new highs
                return "uptrend", round(slope, 2)
            return "basing", round(slope, 2)      # rising EMA but pulled back (below EMA or below last high)
        return "basing", round(slope, 2)          # flat EMA

    def prev_trend_state(self):
        """The previous DISTINCT 3-state trend label (None until a change)."""
        return self._trend_prev

    def uptrend_snapshot(self) -> dict:
        """Public read of the uptrend state machine — used to expose the SAME
        cycle logic on a higher timeframe (e.g. a weekly-bar KellCycle)."""
        up = self._up_state == "up"
        return {
            "in_uptrend": up,
            "legs": self._uptrend_legs if up else None,
            "from_reversal": self._uptrend_had_reversal if up else None,
            "bars_since_start": (self._i - self._uptrend_start)
                                if (up and self._uptrend_start is not None) else None,
            "exh_since_uptrend": (self._count_exh_after(self._uptrend_start)
                                  if (up and self._uptrend_start is not None) else None),
            "wedge_pop_bars_since": (self._i - self._pops[self.ext_ref_ema][-1][0])
                                    if self._pops[self.ext_ref_ema] else None,
            "wedge_drop_bars_since": (self._i - self._drops[self.ext_ref_ema][-1][0])
                                     if self._drops[self.ext_ref_ema] else None,
            "exh_since_downtrend": self._count_exh_after(self._last_downtrend_idx),
            "exh_since_base": self._count_exh_after(self._last_nonuptrend_idx),
        }

    def _ema_rising(self, p: int, ema_now: float) -> bool:
        """Up vs the previous bar OR vs ``crossback_ema_rising_lookback`` bars ago
        (union tolerates 1-day wiggles). 0 disables (always True)."""
        k = self.cb_rising_lb
        if k <= 0:
            return True
        if len(self._buf) < 2:
            return False
        up_1 = ema_now > self._buf[-2].emas.get(p, ema_now)
        up_k = len(self._buf) > k and (self._buf[-1 - k].emas.get(p) is not None) \
            and ema_now > self._buf[-1 - k].emas[p]
        return up_1 or up_k

    # ── exhaustion / reversal episode counting ──────────────────────────────
    # Kell counts a still-EXTENDING move as the current 1st/2nd/3rd extension —
    # it is NOT zero until price closes back across the ref EMA. So every count
    # below includes the live in-progress episode (when it already qualifies),
    # not only the completed/locked ones. (An open episode is never in
    # ``_exh_episodes``/``_rev_episodes`` yet, so there is no double-count.)
    def _open_exh_idx(self):
        if self._exh_run > 0 and self._exh and self._exh["ext"] >= self.exh_min_ext:
            return self._exh["idx"]
        return None

    def _open_rev_idx(self):
        if self._rev_run > 0 and self._rev and self._rev["ext"] >= self.rev_min_ext:
            return self._rev["idx"]
        return None

    def _count_exh_after(self, anchor) -> int:
        """# qualified exhaustion episodes with idx > anchor, incl. the live one."""
        c = sum(1 for i in self._exh_episodes if i > anchor)
        oi = self._open_exh_idx()
        return c + 1 if (oi is not None and oi > anchor) else c

    # ── snapshot at entry ──────────────────────────────────────────────────--
    def features(self, entry: Bar) -> dict:
        out: dict = {}
        idx, cur = self._i, entry.close
        count_cutoff = idx - int(self.count_months * 21)

        for p in self.ema_periods:
            pops, drops = self._pops[p], self._drops[p]
            lp = pops[-1] if pops else None
            out[f"wedge_pop_date_{p}ema"] = lp[2].strftime("%Y-%m-%d") if lp else None
            out[f"wedge_pop_bars_since_{p}ema"] = (idx - lp[0]) if lp else None
            out[f"wedge_pop_bars_below_{p}ema"] = lp[1] if lp else None
            out[f"wedge_pop_max_ext_below_{p}ema"] = lp[3] if lp else None
            out[f"wedge_pop_vol_ratio_{p}ema"] = lp[4] if lp else None
            out[f"wedge_pop_count_{p}ema"] = sum(1 for e in pops if e[0] >= count_cutoff)
            ld = drops[-1] if drops else None
            out[f"wedge_drop_date_{p}ema"] = ld[2].strftime("%Y-%m-%d") if ld else None
            out[f"wedge_drop_bars_since_{p}ema"] = (idx - ld[0]) if ld else None
            out[f"wedge_drop_bars_above_{p}ema"] = ld[1] if ld else None
            out[f"wedge_drop_max_ext_above_{p}ema"] = ld[3] if ld else None
            out[f"wedge_drop_vol_ratio_{p}ema"] = ld[4] if ld else None
            out[f"wedge_drop_count_{p}ema"] = sum(1 for e in drops if e[0] >= count_cutoff)
            out[f"crossback_count_{p}ema"] = self._cb_count[p] if self._has_pop[p] else 0

        out["wedge_pop_both_bars_since"] = self._both_bars_since(self._pops, idx)
        out["wedge_drop_both_bars_since"] = self._both_bars_since(self._drops, idx)

        # reversal: ongoing qualified capitulation, else the last completed one
        rev = self._rev if (self._rev_run > 0 and self._rev
                            and self._rev["ext"] >= self.rev_min_ext) else self._last_rev
        self._extreme_out(out, "reversal_low", rev, cur, low_side=True)
        exh = self._exh if (self._exh_run > 0 and self._exh
                            and self._exh["ext"] >= self.exh_min_ext) else self._last_exh
        self._extreme_out(out, "exhaustion_high", exh, cur, low_side=False)
        # Kell "how many extensions so far" — count of qualified exhaustion /
        # reversal episodes within the cycle window (1st vs 2nd/3rd extension =
        # early vs late-stage trend; the higher the count, the more likely a re-base).
        oe, orv = self._open_exh_idx(), self._open_rev_idx()
        out["exhaustion_high_count"] = (sum(1 for i in self._exh_episodes if i >= count_cutoff)
                                        + (1 if (oe is not None and oe >= count_cutoff) else 0))
        out["reversal_low_count"] = (sum(1 for i in self._rev_episodes if i >= count_cutoff)
                                     + (1 if (orv is not None and orv >= count_cutoff) else 0))

        # ── PER-RUN exhaustion counts (Kell's 1st vs 2nd/3rd, reset per anchor) ──
        # how many exhaustion extensions since each cycle anchor: the last reversal
        # extension (cycle bottom), the last wedge pop (trend confirmation on the
        # reference EMA), and the start of the current above-50-SMA uptrend run.
        last_rev = self._rev_episodes[-1] if self._rev_episodes else None
        ref_pops = self._pops.get(self.ext_ref_ema, [])
        last_pop = ref_pops[-1][0] if ref_pops else None

        def _count_since(anchor):
            if anchor is None:
                return None
            return self._count_exh_after(anchor)

        out["exh_count_since_reversal"] = _count_since(last_rev)
        out["exh_count_since_wedge_pop"] = _count_since(last_pop)
        # ── Kell uptrend STATE (wedge-pop start → 50-SMA-loss end) ───────────
        up = self._up_state == "up"
        out["kell_in_uptrend"] = up
        out["kell_uptrend_bars"] = (idx - self._uptrend_start) if (up and self._uptrend_start is not None) else None
        out["kell_uptrend_from_reversal"] = self._uptrend_had_reversal if up else None
        out["kell_uptrend_legs"] = self._uptrend_legs if up else None    # 1st leg vs later legs
        out["exh_count_since_uptrend"] = _count_since(self._uptrend_start) if up else None
        # ── 3-state trend (uptrend / basing / downtrend) — the primary read ──
        ts, slope = self.trend_state(cur)
        out["trend_state"] = ts
        out["prev_trend_state"] = self._trend_prev
        out["trend_slope_pct"] = slope
        # per-run exhaustion counts (Kell stages), anchored to the 3-state run:
        out["exh_since_downtrend"] = self._count_exh_after(self._last_downtrend_idx)
        out["exh_since_base"] = self._count_exh_after(self._last_nonuptrend_idx)
        return out

    def _both_bars_since(self, events: dict, idx: int):
        if len(self.ema_periods) < 2 or not all(events[p] for p in self.ema_periods):
            return None
        last_idxs = [events[p][-1][0] for p in self.ema_periods]
        if max(last_idxs) - min(last_idxs) <= self.both_within:
            return idx - max(last_idxs)
        return None

    def _extreme_out(self, out, tag, ev, cur, *, low_side: bool) -> None:
        out[f"{tag}_date"] = None
        out[f"{tag}_price"] = None
        out[f"{tag}_bars_since"] = None
        out[f"{tag}_vol_ratio"] = None
        out[f"{tag}_ext_pct"] = None                # extension vs the ref EMA (Kell's gap)
        out[f"{tag}_{'pct_above' if low_side else 'pct_below'}"] = None
        for p in self.ema_periods:
            out[f"{tag}_{'below' if low_side else 'above'}_{p}ema_pct"] = None
        if not ev:
            return
        px = ev["px"]
        out[f"{tag}_date"] = ev["date"].strftime("%Y-%m-%d")
        out[f"{tag}_price"] = round(px, 4)
        out[f"{tag}_bars_since"] = self._i - ev["idx"]
        out[f"{tag}_vol_ratio"] = ev["vol"]
        out[f"{tag}_ext_pct"] = ev["ext"]
        if cur > 0 and px > 0:
            out[f"{tag}_{'pct_above' if low_side else 'pct_below'}"] = (
                round((cur / px - 1) * 100, 2) if low_side else round((px / cur - 1) * 100, 2))
        for p in self.ema_periods:
            ema_at = ev["emas"].get(p)
            if ema_at:
                k = f"{tag}_{'below' if low_side else 'above'}_{p}ema_pct"
                out[k] = (round((ema_at - px) / ema_at * 100, 2) if low_side
                          else round((px - ema_at) / ema_at * 100, 2))
