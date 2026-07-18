"""Pluggable exit rules — the SELL, decoupled from the setup (BUY).

An ``ExitRule`` takes an entry and the forward price path and decides when/where
to exit. Swap the rule to test different sells on the *same* setups (same 139
features), which is the clean way to ask "what does the exit change?".

``StopAndWeaknessExit`` is the default and reproduces the current behaviour
(initial stop + 2-closes-below-the-MA). ``TrailingStopExit`` is an example of a
different sell. Write your own by subclassing ``ExitRule``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class ExitResult:
    sell_idx: int
    sell_price: float
    reason: str
    peak_close: float          # running peak close over the hold (for peak_return)
    trough_low: float          # running trough low over the hold (for MAE)
    days_to_peak: int
    trough_before_peak: float  # lowest low reached up to the peak bar (heat before reward)


class PriceData:
    """Price arrays + lazily-cached indicators for one ticker (computed once,
    reused across every trade and every exit rule)."""

    def __init__(self, df: pd.DataFrame):
        self.dates = df.index
        self.open = df["Open"].values
        self.high = df["High"].values
        self.low = df["Low"].values
        self.close = df["Close"].values
        self._df = df
        self._cache: dict = {}

    def __len__(self) -> int:
        return len(self.close)

    def ema(self, span: int):
        return self._cache.setdefault(
            ("ema", span), self._df["Close"].ewm(span=span, adjust=False).mean().values)

    def sma(self, n: int):
        return self._cache.setdefault(("sma", n), self._df["Close"].rolling(n).mean().values)

    def below_ma(self, ma_type: str):
        key = ("below", ma_type)
        if key not in self._cache:
            ma = self.sma(50) if ma_type == "50sma" else self.ema(21)
            self._cache[key] = [(not np.isnan(ma[i])) and (self.close[i] < ma[i])
                                for i in range(len(self.close))]
        return self._cache[key]

    def atr(self, n: int = 14):
        key = ("atr", n)
        if key not in self._cache:
            h, l, c = self.high, self.low, self.close
            tr = np.maximum(h[1:] - l[1:],
                            np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
            tr = np.concatenate([[h[0] - l[0]], tr])
            self._cache[key] = pd.Series(tr).rolling(n).mean().values
        return self._cache[key]


class ExitRule(ABC):
    """Given an entry (``buy_idx``/``buy_price``) and the setup's stop hints,
    scan forward and return where the trade exits."""

    name = "exit"

    @abstractmethod
    def simulate(self, pd_: PriceData, buy_idx: int, buy_price: float, setup: dict) -> ExitResult:
        ...


class StopAndWeaknessExit(ExitRule):
    """Initial stop (setup's stop reference, capped by ``max_loss_pct``) OR
    sell-in-weakness (2 consecutive closes below the MA → next open). The
    original / default sell."""

    name = "stop_weakness"

    def __init__(self, *, ma_type: str = "21ema",
                 allow_rising_close_exception: bool = True, max_loss_pct: float = 0.04):
        self.ma_type = ma_type
        self.allow_veto = allow_rising_close_exception
        self.max_loss_pct = max_loss_pct

    def _stop(self, buy_price: float, setup: dict) -> Optional[float]:
        levels = []
        if setup.get("criteria_price") is not None:
            levels.append(setup["criteria_price"])
        elif setup.get("constant_pct") is not None:
            levels.append(buy_price * (1 - setup["constant_pct"]))
        if self.max_loss_pct is not None:
            levels.append(buy_price * (1 - self.max_loss_pct))
        # A stop must be BELOW entry. Drop any candidate >= entry (e.g. a
        # gap-up-fade breakout whose expansion-open reference sits above the
        # next-day entry) and fall back to the max-loss stop.
        levels = [s for s in levels if s < buy_price]
        return max(levels) if levels else None

    def simulate(self, pd_, buy_idx, buy_price, setup) -> ExitResult:
        stop = self._stop(buy_price, setup)
        below = pd_.below_ma(self.ma_type)
        o, l, c, n = pd_.open, pd_.low, pd_.close, len(pd_)
        peak, trough, d2p, tbp, pending = c[buy_idx], l[buy_idx], 0, l[buy_idx], False
        k = buy_idx
        while k < n:
            if pending:
                return ExitResult(k, float(o[k]), "weakness", peak, trough, d2p, tbp)
            if stop is not None and l[k] <= stop:
                return ExitResult(k, min(float(o[k]), stop), "stop_loss", peak, trough, d2p, tbp)
            dh = k - buy_idx
            if l[k] < trough:
                trough = l[k]
            if c[k] > peak:
                peak, d2p, tbp = c[k], dh, trough
            if k > buy_idx and below[k - 1] and below[k]:
                if not (self.allow_veto and c[k] > c[k - 1]):
                    pending = True
            k += 1
        return ExitResult(n - 1, float(c[n - 1]), "end_of_data", peak, trough, d2p, tbp)


class TrailingStopExit(ExitRule):
    """Example alternative sell: trail ``trail_pct`` below the running peak close,
    with a hard ``max_loss_pct`` floor. Shows how a different exit plugs in."""

    name = "trailing"

    def __init__(self, *, trail_pct: float = 8.0, max_loss_pct: float = 0.04):
        self.trail = trail_pct / 100.0
        self.max_loss_pct = max_loss_pct

    def simulate(self, pd_, buy_idx, buy_price, setup) -> ExitResult:
        o, l, c, n = pd_.open, pd_.low, pd_.close, len(pd_)
        hard = buy_price * (1 - self.max_loss_pct) if self.max_loss_pct is not None else None
        peak, trough, d2p, tbp = c[buy_idx], l[buy_idx], 0, l[buy_idx]
        k = buy_idx
        while k < n:
            stop = peak * (1 - self.trail)
            if hard is not None:
                stop = max(stop, hard)
            if l[k] <= stop:
                return ExitResult(k, min(float(o[k]), stop), "trailing_stop", peak, trough, d2p, tbp)
            dh = k - buy_idx
            if l[k] < trough:
                trough = l[k]
            if c[k] > peak:
                peak, d2p, tbp = c[k], dh, trough
            k += 1
        return ExitResult(n - 1, float(c[n - 1]), "end_of_data", peak, trough, d2p, tbp)


class EmaTrailExit(ExitRule):
    """Stop just below a moving average, trailed up as the MA rises.

    Initial stop = ``buffer_pct`` below the ``ema_period``-EMA at entry (clamped
    below entry). Risk is therefore the distance to the EMA — small when you
    enter close to it, larger when extended — not a fixed %. ``max_loss_pct``
    (default None = uncapped) is an optional hard floor. As the EMA rises the
    stop ratchets up under it; you exit when price loses the EMA.
    """

    name = "ema_trail"

    def __init__(self, *, ema_period: int = 20, buffer_pct: float = 1.0,
                 max_loss_pct: Optional[float] = None):
        self.p = ema_period
        self.buf = buffer_pct / 100.0
        self.max_loss_pct = max_loss_pct

    def simulate(self, pd_, buy_idx, buy_price, setup) -> ExitResult:
        ema = pd_.ema(self.p)
        o, l, c, n = pd_.open, pd_.low, pd_.close, len(pd_)
        cands = []
        if not np.isnan(ema[buy_idx]):
            cands.append(ema[buy_idx] * (1 - self.buf))
        if self.max_loss_pct is not None:
            cands.append(buy_price * (1 - self.max_loss_pct))
        cands = [s for s in cands if s < buy_price]
        stop = max(cands) if cands else None
        peak, trough, d2p, tbp = c[buy_idx], l[buy_idx], 0, l[buy_idx]
        k = buy_idx
        while k < n:
            if stop is not None and l[k] <= stop:
                return ExitResult(k, min(float(o[k]), stop), "ema_trail_stop",
                                  peak, trough, d2p, tbp)
            dh = k - buy_idx
            if l[k] < trough:
                trough = l[k]
            if c[k] > peak:
                peak, d2p, tbp = c[k], dh, trough
            if not np.isnan(ema[k]):
                cand = ema[k] * (1 - self.buf)
                if stop is None or cand > stop:      # ratchet up only
                    stop = cand
            k += 1
        return ExitResult(n - 1, float(c[n - 1]), "end_of_data", peak, trough, d2p, tbp)


class SwingLowTrailExit(ExitRule):
    """Ratchet the stop up under each new confirmed swing low.

    Start with a below-entry stop (setup criteria, capped by ``max_loss_pct``).
    A swing low = a bar whose low is the minimum of the ``swing_window`` bars on
    each side (confirmed ``swing_window`` bars later, so it's causal). When one
    confirms, raise the stop to ``trail_buffer_pct`` below it — never lower it —
    and let the trade run. Exits when the ratcheting stop is hit.
    """

    name = "swing_low_trail"

    def __init__(self, *, swing_window: int = 3, trail_buffer_pct: float = 1.0,
                 max_loss_pct: float = 0.04):
        self.w = swing_window
        self.buf = trail_buffer_pct / 100.0
        self.max_loss_pct = max_loss_pct

    def simulate(self, pd_, buy_idx, buy_price, setup) -> ExitResult:
        o, l, c, n, w = pd_.open, pd_.low, pd_.close, len(pd_), self.w
        levels = [buy_price * (1 - self.max_loss_pct)] if self.max_loss_pct is not None else []
        if setup.get("criteria_price") is not None and setup["criteria_price"] < buy_price:
            levels.append(setup["criteria_price"])
        stop = max(levels) if levels else None
        peak, trough, d2p, tbp = c[buy_idx], l[buy_idx], 0, l[buy_idx]
        k = buy_idx
        while k < n:
            if stop is not None and l[k] <= stop:
                return ExitResult(k, min(float(o[k]), stop), "swing_trail_stop",
                                  peak, trough, d2p, tbp)
            dh = k - buy_idx
            if l[k] < trough:
                trough = l[k]
            if c[k] > peak:
                peak, d2p, tbp = c[k], dh, trough
            # confirm a swing low at j = k - w (full window inside the trade)
            j = k - w
            if j - w >= buy_idx and l[j] == l[j - w:j + w + 1].min():
                cand = l[j] * (1 - self.buf)
                if stop is None or cand > stop:
                    stop = cand
            k += 1
        return ExitResult(n - 1, float(c[n - 1]), "end_of_data", peak, trough, d2p, tbp)


class ScaleOutSwingTrailExit(ExitRule):
    """Swing-low trail + a hard %-giveback-from-peak cap + a sell-into-strength
    scale-out.

    Loss/giveback side (governs the whole remaining position): the stop is the
    HIGHEST (tightest) of — the ``max_loss_pct`` initial floor, the swing-low
    ratchet (``trail_buffer_pct`` under each confirmed ±``swing_window`` low), and
    a ``max_giveback_pct`` trail below the running peak close ("can't hold it all
    the way down"). Never lowered.

    Sell-into-strength: the FIRST time the trade reaches its ``scaleout_at``-th
    *exhaustion extension* (a distinct episode of close ≥ ``ext_pct`` above the
    ``ext_ema``-EMA, counted from entry; an episode ends only when close drops back
    below that EMA), sell ``scaleout_frac`` of the position at that bar's close.
    The remaining fraction keeps trailing. Realized return is the size-weighted
    blend of the scale-out fill and the final exit (returned as a synthetic
    ``sell_price`` so downstream return math is unchanged). Peak/MAE are
    position-independent, so they reflect the full hold.
    """

    name = "scaleout_swing_trail"

    def __init__(self, *, swing_window: int = 5, trail_buffer_pct: float = 1.0,
                 max_loss_pct: float = 0.04, max_giveback_pct: float = 0.15,
                 scaleout_at: int = 2, scaleout_frac: float = 0.40,
                 ext_ema: int = 20, ext_pct: float = 0.10):
        self.w = swing_window
        self.buf = trail_buffer_pct / 100.0
        self.max_loss_pct = max_loss_pct
        self.giveback = max_giveback_pct
        self.scaleout_at = scaleout_at
        self.scaleout_frac = scaleout_frac
        self.ext_ema = ext_ema
        self.ext_pct = ext_pct

    def simulate(self, pd_, buy_idx, buy_price, setup) -> ExitResult:
        o, l, c, n, w = pd_.open, pd_.low, pd_.close, len(pd_), self.w
        ema = pd_.ema(self.ext_ema)
        levels = [buy_price * (1 - self.max_loss_pct)] if self.max_loss_pct is not None else []
        if setup.get("criteria_price") is not None and setup["criteria_price"] < buy_price:
            levels.append(setup["criteria_price"])
        stop = max(levels) if levels else None
        peak, trough, d2p, tbp = c[buy_idx], l[buy_idx], 0, l[buy_idx]
        ext_count, in_ext, scaled_ret, frac_left = 0, False, None, 1.0
        k = buy_idx
        while k < n:
            if stop is not None and l[k] <= stop:                    # remaining position stopped
                fill = min(float(o[k]), stop)
                return self._blend(buy_price, k, fill, "trail_stop", peak, trough, d2p, tbp,
                                   scaled_ret, frac_left)
            dh = k - buy_idx
            if l[k] < trough:
                trough = l[k]
            if c[k] > peak:
                peak, d2p, tbp = c[k], dh, trough
            # sell-into-strength: the scaleout_at-th extension episode (from entry)
            if scaled_ret is None and ema[k] and not np.isnan(ema[k]):
                if c[k] >= ema[k] * (1 + self.ext_pct):
                    if not in_ext:
                        in_ext = True
                        ext_count += 1
                        if ext_count == self.scaleout_at:
                            scaled_ret = c[k] / buy_price - 1.0
                            frac_left = 1.0 - self.scaleout_frac
                elif c[k] < ema[k]:
                    in_ext = False
            # raise stops (never lower): swing-low ratchet AND giveback-from-peak
            j = k - w
            if j - w >= buy_idx and l[j] == l[j - w:j + w + 1].min():
                cand = l[j] * (1 - self.buf)
                if stop is None or cand > stop:
                    stop = cand
            gb = peak * (1 - self.giveback)
            if stop is None or gb > stop:
                stop = gb
            k += 1
        return self._blend(buy_price, n - 1, float(c[n - 1]), "end_of_data",
                           peak, trough, d2p, tbp, scaled_ret, frac_left)

    def _blend(self, buy_price, sell_idx, fill, reason, peak, trough, d2p, tbp,
               scaled_ret, frac_left) -> ExitResult:
        final_ret = fill / buy_price - 1.0
        if scaled_ret is not None:
            blended = self.scaleout_frac * scaled_ret + frac_left * final_ret
            reason = "scaleout_" + reason
        else:
            blended = final_ret
        return ExitResult(sell_idx, buy_price * (1 + blended), reason, peak, trough, d2p, tbp)


class ReactiveMomentumExit(ExitRule):
    """React to failed follow-through instead of waiting for the full stop.

    Four independent legs; whichever fires first wins (checked in this order
    each bar: pending fill -> stop hit -> queue new pending triggers):

    1. **No-follow-through kill** — if the close has never exceeded entry by
       the close of day ``no_follow_through_days``, exit (next open). Data:
       ~50% of all losing setups never close green even once, and ~87% of
       those are decided within 2 days, vs <1% of >8% winners ever showing
       this pattern — a cheap, high-precision early kill.
    2. **Fading-momentum reaction** — a burst DID show up (close return at
       ``momentum_ref_day`` was positive) but has already given back ground by
       ``momentum_check_day`` (return there is lower). Distinct from #1: this
       is "momentum was there but didn't materialize" rather than "never
       moved at all". ``fading_momentum_action`` controls the response:
       ``"exit"`` (next open) or ``"tighten_to_breakeven"`` (raise the stop to
       entry, never lower it, and keep riding — trades away the downside
       instead of the position itself).
    3. **Pivot-low trailing stop** — identical mechanic to
       ``SwingLowTrailExit``: ratchet the stop up under each newly confirmed
       swing low (never down), buffered by ``trail_buffer_pct``. This is the
       loss-side floor the whole trade trails on once no early kill fires.
    4. **Confirmed weakness sell** — two consecutive closes below the
       ``ema_period``-EMA (the live sell-in-weakness trigger), but ONLY when
       the 2nd close is at least ``weakness_min_deterioration_pct`` below the
       1st below-EMA close — guards against selling on a shallow double-dip
       that isn't real deterioration. Same rising-close veto as
       ``StopAndWeaknessExit``.
    """

    name = "reactive_momentum"

    def __init__(self, *,
                 no_follow_through_days: int = 2,
                 momentum_ref_day: int = 1,
                 momentum_check_day: int = 3,
                 fading_momentum_action: str = "exit",
                 swing_window: int = 8,
                 trail_buffer_pct: float = 1.0,
                 ema_period: int = 20,
                 weakness_min_deterioration_pct: float = 1.0,
                 allow_rising_close_exception: bool = True,
                 max_loss_pct: float = 0.04):
        if fading_momentum_action not in ("exit", "tighten_to_breakeven"):
            raise ValueError(
                f"fading_momentum_action must be 'exit' or 'tighten_to_breakeven', "
                f"got {fading_momentum_action!r}"
            )
        self.nft_days = no_follow_through_days
        self.ref_day = momentum_ref_day
        self.check_day = momentum_check_day
        self.fading_action = fading_momentum_action
        self.w = swing_window
        self.buf = trail_buffer_pct / 100.0
        self.ema_p = ema_period
        self.weak_min = weakness_min_deterioration_pct / 100.0
        self.allow_veto = allow_rising_close_exception
        self.max_loss_pct = max_loss_pct

    def simulate(self, pd_, buy_idx, buy_price, setup) -> ExitResult:
        o, l, c, n, w = pd_.open, pd_.low, pd_.close, len(pd_), self.w
        ema = pd_.ema(self.ema_p)
        below = [(not np.isnan(ema[i])) and c[i] < ema[i] for i in range(n)]

        levels = [buy_price * (1 - self.max_loss_pct)] if self.max_loss_pct is not None else []
        if setup.get("criteria_price") is not None and setup["criteria_price"] < buy_price:
            levels.append(setup["criteria_price"])
        stop = max(levels) if levels else None

        peak, trough, d2p, tbp = c[buy_idx], l[buy_idx], 0, l[buy_idx]
        ret_at_ref: Optional[float] = None
        pending = False
        pending_reason = ""
        k = buy_idx
        while k < n:
            if pending:
                return ExitResult(k, float(o[k]), pending_reason, peak, trough, d2p, tbp)
            if stop is not None and l[k] <= stop:
                return ExitResult(k, min(float(o[k]), stop), "pivot_trail_stop",
                                  peak, trough, d2p, tbp)

            dh = k - buy_idx
            if l[k] < trough:
                trough = l[k]
            if c[k] > peak:
                peak, d2p, tbp = c[k], dh, trough

            if dh == self.ref_day:
                ret_at_ref = (c[k] / buy_price - 1) * 100 if buy_price else None

            # ── 1. no-follow-through: never closed above entry by this day ──
            if dh == self.nft_days and peak <= buy_price:
                pending, pending_reason = True, "no_follow_through"

            # ── 2. fading momentum: burst showed, then already gave it back ──
            if (not pending and dh == self.check_day and ret_at_ref is not None
                    and ret_at_ref > 0):
                ret_now = (c[k] / buy_price - 1) * 100 if buy_price else None
                if ret_now is not None and ret_now < ret_at_ref:
                    if self.fading_action == "exit":
                        pending, pending_reason = True, "fading_momentum"
                    else:  # tighten_to_breakeven: protect entry, keep riding
                        if stop is None or buy_price > stop:
                            stop = buy_price

            # ── 3. pivot-low trail: confirm a swing low at j = k - w ─────────
            j = k - w
            if j - w >= buy_idx and l[j] == l[j - w:j + w + 1].min():
                cand = l[j] * (1 - self.buf)
                if stop is None or cand > stop:
                    stop = cand

            # ── 4. confirmed weakness: 2nd below-EMA close notably lower ─────
            if not pending and k > buy_idx and below[k - 1] and below[k]:
                deterioration = ((c[k - 1] - c[k]) / c[k - 1]) if c[k - 1] else 0.0
                rising = self.allow_veto and c[k] > c[k - 1]
                if deterioration >= self.weak_min and not rising:
                    pending, pending_reason = True, "weakness"

            k += 1
        return ExitResult(n - 1, float(c[n - 1]), "end_of_data", peak, trough, d2p, tbp)
