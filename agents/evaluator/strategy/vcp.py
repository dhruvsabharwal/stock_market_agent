"""VCP footprint — Minervini's Volatility Contraction Pattern, on-bar and causal.

A VCP is a run of pullbacks each **shallower than the last** (the classic "2T/3T"
footprint): a wide correction, then a tighter one, then tighter still, coiling
toward the pivot. This measures that structure directly from **confirmed swing
pivots** — the same ±``pivot_window`` machinery used by ``overhead`` / ``resistance``
/ ``trend_state`` — so it is leak-free: a pivot is only counted ``pivot_window``
bars after it forms.

As of the signal bar it builds an alternating high/low zigzag over the last
``lookback`` bars, anchors at the highest swing high (the base ceiling), and reads
each pullback leg (peak → following trough) as a contraction depth. Emits:

  * ``vcp_num_contractions``   — number of contraction legs off the base high (≤ ``max_legs``).
  * ``vcp_first_depth_pct`` / ``vcp_last_depth_pct`` — depth of the first (widest) and
    most-recent (tightest) legs, in %.
  * ``vcp_tightest_pct``       — the shallowest leg (how tight it has coiled).
  * ``vcp_depth_ratio``        — last / first (≪1 = strong progressive contraction).
  * ``vcp_contracting_run``    — length of the TRAILING run of strictly-decreasing legs
    ending at the most recent one (an early, unrelated wiggle before the real base no
    longer poisons the read — only the recent, dominant contraction matters).
  * ``vcp_last_leg_high`` / ``vcp_last_leg_low`` — the most recent leg's own boundary prices.
  * ``vcp_in_range``           — has the base NOT yet resolved: no close since the last leg's
    low has exceeded its high, AND today's close still sits inside [low, high]. The moment
    price closes above the leg's high, the base is done — even a later pullback BACK inside
    the old range (a retest) doesn't revive it; this scans every close since the leg's low,
    not just today's, so a since-retraced breakout isn't missed.
  * ``vcp_contracting``        — True once the trailing run reaches ``min_contracting_run``
    AND ``vcp_in_range`` (still unresolved, not stale/already broken out).
  * ``vcp_base_depth_pct``     — total base depth (base high → deepest swing low since).

None until there is enough history / at least one full leg.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from ..engine.records import Bar


def _rnd(x: Optional[float], n: int = 2) -> Optional[float]:
    return round(float(x), n) if x is not None else None


class VCPFootprint:
    def __init__(self, *, pivot_window: int = 5, lookback: int = 120, max_legs: int = 4,
                 min_contracting_run: int = 2):
        self.w = pivot_window
        self.lookback = lookback
        self.max_legs = max_legs
        self.min_contracting_run = min_contracting_run
        self._i = 0
        self._hi_buf: deque = deque(maxlen=2 * pivot_window + 1)
        self._lo_buf: deque = deque(maxlen=2 * pivot_window + 1)
        # confirmed pivots, chronological: (idx, price, kind) with kind in {'H','L'}
        self._pivots: list = []
        self._last_legs: list = []   # [(h_idx, h, l_idx, l), ...] from the last features() call
        # (idx, close) history, bounded to the lookback -- lets the "expanded" check
        # scan forward from a leg's low to today without re-touching the future.
        self._closes: deque = deque(maxlen=lookback + 2 * pivot_window + 5)

    def update(self, bar: Bar) -> None:
        self._i += 1
        self._closes.append((self._i, float(bar.close)))
        self._hi_buf.append(float(bar.high))
        self._lo_buf.append(float(bar.low))
        if len(self._hi_buf) == self._hi_buf.maxlen:
            w = self.w
            ch = self._hi_buf[w]
            if ch == max(self._hi_buf) and ch > self._hi_buf[w - 1]:
                self._add(self._i - w, ch, "H")
            cl = self._lo_buf[w]
            if cl == min(self._lo_buf) and cl < self._lo_buf[w - 1]:
                self._add(self._i - w, cl, "L")

    def _add(self, idx: int, price: float, kind: str) -> None:
        self._pivots.append((idx, price, kind))
        # keep a little more than the lookback so a base high just outside still anchors
        floor = self._i - (self.lookback + 2 * self.w)
        if self._pivots and self._pivots[0][0] < floor:
            self._pivots = [p for p in self._pivots if p[0] >= floor]

    def _zigzag(self) -> list:
        """Alternating H/L pivots within the lookback (collapse consecutive
        same-kind pivots to the more extreme one)."""
        floor = self._i - self.lookback
        pts = [p for p in self._pivots if p[0] >= floor]
        zz: list = []
        for idx, price, kind in pts:
            if zz and zz[-1][2] == kind:
                # same kind in a row: keep the higher H / lower L
                if (kind == "H" and price >= zz[-1][1]) or (kind == "L" and price <= zz[-1][1]):
                    zz[-1] = (idx, price, kind)
            else:
                zz.append((idx, price, kind))
        return zz

    def features(self, bar: Bar) -> dict:
        out = {
            "vcp_num_contractions": None, "vcp_first_depth_pct": None,
            "vcp_last_depth_pct": None, "vcp_tightest_pct": None,
            "vcp_depth_ratio": None, "vcp_contracting": None, "vcp_contracting_run": None,
            "vcp_last_leg_high": None, "vcp_last_leg_low": None, "vcp_in_range": None,
            "vcp_base_depth_pct": None,
        }
        zz = self._zigzag()
        if len(zz) < 2:
            self._last_legs = []
            return out

        # anchor at the highest swing high (base ceiling); read legs from there on.
        hi_idxs = [k for k, (_, _, kind) in enumerate(zz) if kind == "H"]
        if not hi_idxs:
            self._last_legs = []
            return out
        base_k = max(hi_idxs, key=lambda k: zz[k][1])
        seq = zz[base_k:]

        # contraction legs = each (H -> following L) drop.
        depths, leg_prices, base_high, base_low = [], [], zz[base_k][1], None
        j = 0
        while j < len(seq) - 1:
            if seq[j][2] == "H" and seq[j + 1][2] == "L":
                h_idx, h = seq[j][0], seq[j][1]
                l_idx, l = seq[j + 1][0], seq[j + 1][1]
                if h > 0:
                    depths.append((h - l) / h * 100.0)
                    leg_prices.append((h_idx, h, l_idx, l))
                base_low = l if base_low is None else min(base_low, l)
                j += 2
            else:
                j += 1
        if not depths:
            self._last_legs = []
            return out

        legs = depths[-self.max_legs:]          # the recent footprint (2T/3T/4T)
        leg_prices = leg_prices[-self.max_legs:]
        self._last_legs = leg_prices            # [(h_idx, h, l_idx, l), ...] for external plotting
        out["vcp_num_contractions"] = len(legs)
        out["vcp_first_depth_pct"] = _rnd(legs[0])
        out["vcp_last_depth_pct"] = _rnd(legs[-1])
        out["vcp_tightest_pct"] = _rnd(min(legs))
        out["vcp_depth_ratio"] = _rnd(legs[-1] / legs[0], 3) if legs[0] > 0 else None
        # TRAILING run of strictly-decreasing legs ending at the last one — an
        # early anomaly (e.g. a small noise wiggle sitting before the real base)
        # no longer poisons the whole read; only the recent, dominant contraction
        # matters. `vcp_contracting` = True once that run reaches `min_contracting_run`.
        run = 1
        for i in range(len(legs) - 1, 0, -1):
            if legs[i] < legs[i - 1]:
                run += 1
            else:
                break
        out["vcp_contracting_run"] = run
        # EXPANSION GUARD: the legs are built purely from confirmed pivot history and
        # never reference what happened to price afterward — a real but OLD contraction
        # can still read True long after the base has already resolved. The moment price
        # closes above the last leg's high, that base is DONE: even if it later pulls
        # back inside the old range (a retest), it's not "still coiling" anymore, so this
        # scans every close since the leg's low confirmed, not just today's — a same-day
        # snapshot alone would miss a breakout that has since retraced back inside.
        _, last_h, last_l_idx, last_l = leg_prices[-1]
        out["vcp_last_leg_high"] = _rnd(last_h)
        out["vcp_last_leg_low"] = _rnd(last_l)
        expanded = any(c > last_h for idx, c in self._closes if idx > last_l_idx)
        out["vcp_in_range"] = bool(not expanded and last_l <= bar.close <= last_h)
        out["vcp_contracting"] = (len(legs) >= 2 and run >= self.min_contracting_run
                                  and out["vcp_in_range"])
        if base_low is not None and base_high > 0:
            out["vcp_base_depth_pct"] = _rnd((base_high - base_low) / base_high * 100.0)
        return out
