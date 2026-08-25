"""Meaningful-resistance / Stage-1 "stuck below resistance" detector.

This is **not** a Stage-2 definition — Kell's weekly uptrend logic (see
``weekly_kell.py`` / ``trend_state``) defines Stage 2 and we keep it. This flags
the *dead zone*: price **stuck under a meaningful overhead resistance** it has not
cleared, so we don't want to participate there.

Detection is **swing-high driven, with NO dependence on the trend state** — a
rejected rally fails *below* a high, so it rarely registers as an "uptrend"; using
confirmed swing-high pivots catches every rally-and-fail regardless of how the
trend machine labels it.

  * A **swing high** = a confirmed ±``pivot_window``-bar pivot (a local top, which
    by construction has already failed — there are lower bars after it). Causal:
    a peak isn't counted until ``pivot_window`` later bars confirm it.
  * A **resistance level** is a price **zone**: swing highs within ``band_pct`` of
    each other (symmetric) are the SAME level — e.g. a top at 25.8 and a retest to
    26.0 are one resistance, and the 26.0 is a *test*, not a new level. The zone's
    price tracks the highest swing high in it.
  * **Rejections**: the swing high that first *forms* a zone does not count. Every
    *subsequent* swing high landing in the zone is a rejection (+1):
      - ``>= 1`` rejection            -> a **resistance**
      - ``>= min_rejections`` (def 2) -> a **significant** resistance
  * **Cleared** = a daily close above the zone's price **by more than
    ``clear_buffer_pct``** — a real break of the top. A marginal poke above (e.g. a
    $50.41 close over a $50.3 zone) is still a *test*, not a break, so a double-top
    at ~$50.3 / ~$50.5 stays ONE zone and accrues rejections instead of resetting.
    (The clear buffer is deliberately small vs ``band_pct``: a stock trading well
    above a level has genuinely broken it.)

If ``recency_window_days`` is set, only rejections within that many trailing bars
count toward ``min_rejections`` — so "tested >= 2 times **in the last year**"
(≈252 bars). A zone tested heavily long ago but quiet since ages out. Default
``None`` = all-time.

The **meaningful resistance** at a bar = the uncleared zone above price with
``>= min_rejections`` (recent) rejections, most-tested (ties -> highest). **No fallback** to
the highest swing high: below a never-retested high is just "below the 52-week
high", tracked elsewhere. The far-overhead flaw is handled by the rejection rule
itself — a clean Stage-4 decline prints *lower* swing highs (each a fresh zone),
so no single far level is retested twice; a genuine range hammers one zone
repeatedly and flags however deep price dips inside it. ``max_dist_pct`` (default
``None`` = off) is an optional guard to ignore zones farther than that % above.

No lookahead: zones are built from *confirmed* pivots (past bars) and clearing
uses the current close. Cache-only.
"""
from __future__ import annotations

from collections import deque

from ..engine.records import Bar


class _Level:
    __slots__ = ("price", "reject_idxs", "cleared", "cleared_idx", "last_idx", "born_idx")

    def __init__(self, price: float, idx: int):
        self.price = price
        self.reject_idxs: list[int] = []   # bar indices of retests (formation excluded)
        self.cleared = False
        self.cleared_idx = None            # bar index at which the zone was broken
        self.last_idx = idx
        self.born_idx = idx                # bar index of the swing high that set it


class MeaningfulResistance:
    def __init__(self, *, pivot_window: int = 10, band_pct: float = 12.0,
                 min_rejections: int = 2, clear_buffer_pct: float = 3.0,
                 recency_window_days=None, max_dist_pct=None):
        self.w = pivot_window
        self.band = band_pct / 100.0
        self.min_rej = min_rejections
        self.clear_buf = clear_buffer_pct / 100.0
        # only rejections within this many trailing bars (~252 = 1yr) count toward
        # 'significant'; None = all-time (no recency window).
        self.recency = int(recency_window_days) if recency_window_days is not None else None
        self.max_dist = (max_dist_pct / 100.0) if max_dist_pct is not None else None
        self._i = -1
        self._levels: list[_Level] = []
        self._pivot_buf: deque = deque(maxlen=2 * pivot_window + 1)   # trailing highs
        self._seasoned = False

    # ── feed one daily bar (no trend state needed) ─────────────────────────────
    def update(self, bar: Bar) -> None:
        self._i += 1
        # per-bar clearing: a daily close above a zone's price BY MORE THAN the
        # clear buffer breaks it (a marginal poke is a test, not a break)
        if bar.close > 0:
            for lv in self._levels:
                if not lv.cleared and bar.close > lv.price * (1 + self.clear_buf):
                    lv.cleared = True
                    lv.cleared_idx = self._i
        # confirmed swing high: the center of a (2w+1) window is its max
        self._pivot_buf.append(bar.high)
        if len(self._pivot_buf) == 2 * self.w + 1:
            self._seasoned = True
            center = self._pivot_buf[self.w]
            if center == max(self._pivot_buf) and center > self._pivot_buf[self.w - 1]:
                self._on_swing_high(center, self._i - self.w)   # peak bar = w bars back

    def _on_swing_high(self, sh: float, pidx: int) -> None:
        """A confirmed swing high (a failed local top) at bar ``pidx``. Test the
        nearest existing zone within ``band`` (symmetric) — else open a new zone."""
        cands = [lv for lv in self._levels if not lv.cleared
                 and abs(sh / lv.price - 1) <= self.band]
        if cands:
            lv = min(cands, key=lambda l: abs(l.price - sh))
            lv.reject_idxs.append(pidx)      # a retest of this zone (at the peak bar)
            lv.price = max(lv.price, sh)     # zone price tracks its highest test
            lv.last_idx = pidx
        else:
            self._levels.append(_Level(sh, pidx))   # new zone, 0 rejections

    def _rej_count(self, lv) -> int:
        """# rejections of ``lv``, restricted to the trailing recency window."""
        if self.recency is None:
            return len(lv.reject_idxs)
        lo = self._i - self.recency
        return sum(1 for j in lv.reject_idxs if j >= lo)

    def stuck_at(self, cur: float, window_days: int):
        """``stuck_below_resistance`` evaluated with a SPECIFIC trailing recency
        window (in bars), independent of the constructor's ``recency`` — reuses the
        shared zone state so many windows are cheap. None until seasoned. A
        meaningful (>= min_rej retests in-window) resistance sits ABOVE price."""
        if not self._seasoned or cur is None or cur <= 0:
            return None
        cap = cur * (1 + self.max_dist) if self.max_dist is not None else float("inf")
        lo = self._i - window_days
        for lv in self._levels:
            if lv.cleared or lv.price <= cur or lv.price > cap:
                continue
            if sum(1 for j in lv.reject_idxs if j >= lo) >= self.min_rej:
                return True
        return False

    def broke_above(self, cur: float, window_days: int):
        """The breakout is ABOVE a meaningful resistance — it escaped the dead zone
        (vs ``stuck_at`` = trapped under one). True if a level with >= min_rej
        retests within the window sits BELOW the current close (price cleared it)."""
        if not self._seasoned or cur is None or cur <= 0:
            return None
        lo = self._i - window_days
        for lv in self._levels:
            if lv.price >= cur:
                continue
            if sum(1 for j in lv.reject_idxs if j >= lo) >= self.min_rej:
                return True
        return False

    # ── snapshot at entry ───────────────────────────────────────────────────--
    def features(self, entry: Bar) -> dict:
        cur = entry.close
        cap = cur * (1 + self.max_dist) if self.max_dist is not None else float("inf")
        above = [lv for lv in self._levels if not lv.cleared and cur > 0
                 and lv.price > cur and lv.price <= cap]
        sig_above = [lv for lv in above if self._rej_count(lv) >= self.min_rej]
        # meaningful resistance = most-tested qualifying zone (ties -> highest);
        # NO fallback to the highest swing high. Rejection counts respect recency.
        R = max(sig_above, key=lambda l: (self._rej_count(l), l.price)) if sig_above else None
        nearest = min(above, key=lambda l: l.price) if above else None
        stuck = bool(sig_above) if self._seasoned else None
        return {
            "stuck_below_resistance": stuck,
            "resistance_dist_pct": round((R.price / cur - 1) * 100, 2) if (R and cur > 0) else None,
            "resistance_test_count": self._rej_count(R) if R else 0,
            "resistance_level_count": len(above),
            "significant_resistance_count": len(sig_above),
            "next_resistance_dist_pct": round((nearest.price / cur - 1) * 100, 2) if (nearest and cur > 0) else None,
        }
