"""Bridge to the batch 'lab' code in ``agents/base_breakout_strategy``.

That folder is not a package, so we add it to ``sys.path`` once here and
re-export the dataclasses + pure helpers the on-bar strategies reuse. Keeping
the path hack in one place means strategies just do::

    from ._lab_imports import RangeExpansion, StopLoss, build_stop_loss

Only *pure, leak-free* helpers are reused for execution (``build_stop_loss``
indexes the expansion bar and earlier — never the future). The forward-scanning
batch trade builder is deliberately NOT used: the engine simulates the trade
bar-by-bar instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

_LAB_DIR = Path(__file__).resolve().parents[1].parent / "base_breakout_strategy"
if str(_LAB_DIR) not in sys.path:
    sys.path.insert(0, str(_LAB_DIR))

from base_identification import (  # noqa: E402  (path set above)
    Buy,
    RangeExpansion,
    StopLoss,
    build_stop_loss,
)

__all__ = ["Buy", "RangeExpansion", "StopLoss", "build_stop_loss"]
