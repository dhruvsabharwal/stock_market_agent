"""The bar feed — the engine's clock and the no-lookahead chokepoint.

A ``BarFeed`` yields ``Bar`` objects strictly forward in time. This is the
single place that guarantees a strategy can never see the future: the feed only
ever emits the *next* bar, and nothing downstream is given the underlying frame.

Restricting the run to a date window (e.g. 2015-2026 for eval, 1990-2015 for
probabilities) is done here, by slicing the source frame before iteration.
"""
from __future__ import annotations

from typing import Iterator, Optional

import pandas as pd

from .records import Bar


class BarFeed:
    def __init__(
        self,
        df: pd.DataFrame,
        *,
        ticker: str,
        start: Optional[pd.Timestamp] = None,
        end: Optional[pd.Timestamp] = None,
    ):
        """``df`` is single-ticker daily OHLCV with a DatetimeIndex.

        ``start``/``end`` bound the *simulation* window. Note: a strategy that
        needs warm-up history (e.g. a 200-day MA) should be fed bars from before
        ``start`` — see ``warmup`` in the runner. Here we only restrict the
        outer bounds.
        """
        self.ticker = ticker
        df = df.sort_index()
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        self._df = df

    def __len__(self) -> int:
        return len(self._df)

    @property
    def first_date(self) -> Optional[pd.Timestamp]:
        return self._df.index[0] if len(self._df) else None

    @property
    def last_date(self) -> Optional[pd.Timestamp]:
        return self._df.index[-1] if len(self._df) else None

    def __iter__(self) -> Iterator[Bar]:
        for date, row in self._df.iterrows():
            yield Bar.from_row(date, row)
