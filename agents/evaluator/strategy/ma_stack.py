"""Moving-average stack — is price above each MA, and are the MAs coiled?

Incremental, no lookahead.
  * ``above_{p}ema`` / ``above_{n}sma`` — price > that MA at the entry bar.
  * ``above_all_mas`` — all of them true.
  * ``coiled_up`` — the coil MAs (default 10-EMA, 20-EMA, 50-SMA) are all within
    ``coil_pct`` of each other (default 3%), i.e. tightly clustered / squeezing.
    ``ma_coil_spread_pct`` records the actual spread so the threshold is tunable.

A value is ``None`` until its MAs have enough history.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from ..engine.records import Bar


class MovingAverageStack:
    def __init__(
        self,
        *,
        ema_periods=(10, 20),
        sma_periods=(50, 200),
        coil_ema_periods=(10, 20),
        coil_sma_periods=(50,),
        coil_pct: float = 3.0,
    ):
        self.ema_periods = list(ema_periods)
        self.sma_periods = list(sma_periods)
        self.coil_ema_periods = list(coil_ema_periods)
        self.coil_sma_periods = list(coil_sma_periods)
        self.coil_pct = coil_pct
        # compute every EMA/SMA needed for both the "above" flags AND the coil
        all_ema = sorted(set(self.ema_periods) | set(self.coil_ema_periods))
        all_sma = sorted(set(self.sma_periods) | set(self.coil_sma_periods))
        self._ema = {p: None for p in all_ema}
        self._sma_win = {n: deque(maxlen=n) for n in all_sma}

    def update(self, bar: Bar) -> None:
        for p in self._ema:
            alpha = 2.0 / (p + 1)
            prev = self._ema[p]
            self._ema[p] = bar.close if prev is None else alpha * bar.close + (1 - alpha) * prev
        for n in self._sma_win:
            self._sma_win[n].append(bar.close)

    def _sma(self, n: int) -> Optional[float]:
        w = self._sma_win[n]
        return sum(w) / n if len(w) == n else None

    def features(self, bar: Bar) -> dict:
        out: dict = {}
        c = bar.close
        flags = []
        for p in self.ema_periods:
            v = self._ema[p]
            ab = (c > v) if v is not None else None
            out[f"above_{p}ema"] = ab
            # how far the entry sits above/below the MA (+ = extended above it)
            out[f"close_ext_{p}ema_pct"] = round((c / v - 1) * 100, 2) if v else None
            flags.append(ab)
        for n in self.sma_periods:
            v = self._sma(n)
            ab = (c > v) if v is not None else None
            out[f"above_{n}sma"] = ab
            out[f"close_ext_{n}sma_pct"] = round((c / v - 1) * 100, 2) if v else None
            flags.append(ab)
        out["above_all_mas"] = all(f is True for f in flags) if all(f is not None for f in flags) else None

        # ── coil: are the coil MAs all within coil_pct of each other? ─────────
        coil_vals = [self._ema[p] for p in self.coil_ema_periods] \
            + [self._sma(n) for n in self.coil_sma_periods]
        if coil_vals and all(v is not None for v in coil_vals):
            lo, hi = min(coil_vals), max(coil_vals)
            spread = (hi - lo) / lo * 100 if lo > 0 else None
            out["ma_coil_spread_pct"] = round(spread, 2) if spread is not None else None
            out["coiled_up"] = spread is not None and spread <= self.coil_pct
        else:
            out["ma_coil_spread_pct"] = None
            out["coiled_up"] = None
        return out
