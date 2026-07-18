"""A 'tight day' — a bar whose open and close are within ``tight_pct`` of each
other (small real body), signalling intraday indecision / consolidation.

Used to count how many tight days sit inside a range box before a breakout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..engine.records import Bar


@dataclass
class TightDay:
    date: pd.Timestamp
    open: float
    close: float
    body_pct: float          # abs(close - open) / open * 100

    @staticmethod
    def body_pct_of(bar: Bar) -> Optional[float]:
        if bar.open <= 0:
            return None
        return abs(bar.close - bar.open) / bar.open * 100

    @classmethod
    def is_tight(cls, bar: Bar, tight_pct: float = 2.0) -> bool:
        b = cls.body_pct_of(bar)
        return b is not None and b <= tight_pct

    @classmethod
    def classify(cls, bar: Bar, tight_pct: float = 2.0) -> Optional["TightDay"]:
        b = cls.body_pct_of(bar)
        if b is None or b > tight_pct:
            return None
        return cls(date=bar.date, open=bar.open, close=bar.close, body_pct=round(b, 2))
