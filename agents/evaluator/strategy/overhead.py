"""Overhead supply — prior swing highs looming above the current price.

A 'supply' level is a confirmed swing high (a pivot high over ±``swing_window``
bars, ≈ a 20-day-span peak at the default of 10). Because a pivot is only
confirmed once ``swing_window`` later bars exist, this is leak-free — a peak in
the last ~``swing_window`` bars is not yet counted as supply.

Per lookback window ``w`` months, at entry we compare the entry price to the
swing highs formed within the window:
  * ``has_overhead_{w}m`` / ``blue_sky_{w}m`` — is any swing high above us?
  * nearest (first resistance) and highest overhead levels, each with the % up
    to it, and how many bars ago the nearest one formed.
"""
from __future__ import annotations

from collections import deque

import pandas as pd

from ..engine.records import Bar


class OverheadSupply:
    def __init__(self, *, windows_months=(6, 12, 24), swing_window: int = 10,
                 buffer_bars: int = 800):
        self.windows = list(windows_months)
        self.w = swing_window
        self._i = -1
        self._pivot_buf: deque = deque(maxlen=2 * swing_window + 1)  # (idx, date, high)
        self._swings: deque = deque(maxlen=buffer_bars)             # confirmed (idx, date, price)

    def update(self, bar: Bar) -> None:
        self._i += 1
        self._pivot_buf.append((self._i, bar.date, bar.high))
        if len(self._pivot_buf) == 2 * self.w + 1:
            center = self._pivot_buf[self.w]
            highs = [r[2] for r in self._pivot_buf]
            # center is the window max and strictly above its left neighbour
            if center[2] == max(highs) and center[2] > self._pivot_buf[self.w - 1][2]:
                self._swings.append(center)

    def features(self, entry: Bar) -> dict:
        out: dict = {}
        cur, idx = entry.close, self._i
        for w in self.windows:
            cutoff = entry.date - pd.DateOffset(months=w)
            above = [s for s in self._swings if s[1] >= cutoff and s[2] > cur]
            out[f"has_overhead_{w}m"] = bool(above)
            out[f"blue_sky_{w}m"] = not above
            out[f"overhead_nearest_price_{w}m"] = None
            out[f"overhead_nearest_pct_{w}m"] = None
            out[f"overhead_highest_price_{w}m"] = None
            out[f"overhead_highest_pct_{w}m"] = None
            out[f"overhead_nearest_bars_since_{w}m"] = None
            if above and cur > 0:
                nearest = min(above, key=lambda s: s[2])   # closest above -> first resistance
                highest = max(above, key=lambda s: s[2])
                out[f"overhead_nearest_price_{w}m"] = round(nearest[2], 4)
                out[f"overhead_nearest_pct_{w}m"] = round((nearest[2] / cur - 1) * 100, 2)
                out[f"overhead_highest_price_{w}m"] = round(highest[2], 4)
                out[f"overhead_highest_pct_{w}m"] = round((highest[2] / cur - 1) * 100, 2)
                out[f"overhead_nearest_bars_since_{w}m"] = idx - nearest[0]
        return out
