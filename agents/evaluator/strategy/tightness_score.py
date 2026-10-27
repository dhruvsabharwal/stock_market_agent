"""VCP Tightness Score — the ADR/ATR-adjusted percentile compression measure
Jeff Sun references and uses (joshdm_94 / @ETFbreakouts' TradingView indicator).

Unlike a flat threshold or a two-window before/after delta, this measures
whether the CURRENT short-window price spread is unusually TIGHT for THIS
STOCK specifically, by ranking a volatility-normalized ratio against its own
trailing history:

    ratio_t = (spread over the last `tight_window` bars) / (ADR% or ATR%, over
              a SEPARATE, longer `vol_window` — not the same window as the
              spread, avoiding the near-circularity of a window compared to
              itself; ADR%/ATR% come from the existing ``Volatility`` provider)
    score_t = 100 - percentile_rank(ratio_t among the last `baseline_window`
              daily ratio_t readings, INCLUDING today) — 100 = the tightest
              this stock's ratio has been in `baseline_window` bars, 0 = the
              loosest. Computed fresh every bar; can land anywhere depending
              on the stock's own recent behaviour, never a fixed absolute cutoff.

Two spread flavors (matching the reference tool):
  * "closing" — (highest close - lowest close) / lowest close * 100
  * "swing"   — (highest high  - lowest low)   / lowest low   * 100 (full
                intraday extremes, not just closes)

Both against BOTH volatility measures — ADR% (the tool's own default) and
ATR% (gap-inclusive; Jeff Sun's own extension/LoD rules explicitly prefer ATR
"because it includes gaps") — four scores total, so which combination actually
separates outcomes can be decided empirically rather than assumed upfront.

All causal: the percentile only ever looks at past bars + the current one.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from ..engine.records import Bar


def _rnd(x: Optional[float], n: int = 2) -> Optional[float]:
    return round(float(x), n) if x is not None else None


class VCPTightnessScore:
    def __init__(self, *, tight_window: int = 5, baseline_window: int = 50,
                 key_suffix: str = ""):
        self.tight_window = tight_window
        self.baseline_window = baseline_window
        self.key_suffix = key_suffix
        span = tight_window + 1
        self._h: deque = deque(maxlen=span)
        self._l: deque = deque(maxlen=span)
        self._c: deque = deque(maxlen=span)
        self._hist = {
            ("closing", "adr"): deque(maxlen=baseline_window),
            ("closing", "atr"): deque(maxlen=baseline_window),
            ("swing", "adr"): deque(maxlen=baseline_window),
            ("swing", "atr"): deque(maxlen=baseline_window),
        }

    def update(self, bar: Bar) -> None:
        self._h.append(float(bar.high))
        self._l.append(float(bar.low))
        self._c.append(float(bar.close))

    def _spread(self, kind: str) -> Optional[float]:
        n = self.tight_window
        if len(self._c) < n:
            return None
        if kind == "closing":
            window = list(self._c)[-n:]
            hi, lo = max(window), min(window)
        else:                       # "swing"
            hi, lo = max(list(self._h)[-n:]), min(list(self._l)[-n:])
        return (hi - lo) / lo * 100.0 if lo > 0 else None

    @staticmethod
    def _percentile_tightness(hist: deque, val: float) -> Optional[float]:
        """100 = the tightest reading in ``hist`` (incl. this one), 0 = loosest."""
        if len(hist) < 5:            # need a minimally meaningful baseline
            return None
        pct_rank = sum(1 for v in hist if v <= val) / len(hist) * 100.0
        return 100.0 - pct_rank

    def features(self, bar: Bar, *, adr_pct: Optional[float],
                atr_pct: Optional[float]) -> dict:
        out = {}
        vol = {"adr": adr_pct, "atr": atr_pct}
        for kind in ("closing", "swing"):
            spread = self._spread(kind)
            for vm in ("adr", "atr"):
                key = f"vcp_tight_{kind}_{vm}{self.key_suffix}"
                v = vol[vm]
                ratio = spread / v if (spread is not None and v and v > 0) else None
                if ratio is not None:
                    self._hist[(kind, vm)].append(ratio)
                score = (self._percentile_tightness(self._hist[(kind, vm)], ratio)
                         if ratio is not None else None)
                out[key] = _rnd(score, 1)
                out[f"{key}_ratio"] = _rnd(ratio, 3)
        return out
