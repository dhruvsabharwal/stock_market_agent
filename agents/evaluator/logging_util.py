"""Toggleable event logging for the evaluator.

The engine and strategies emit human-readable trade events through the
``agents.evaluator`` logger. It is SILENT by default. Turn it on/off:

    from agents.evaluator.logging_util import set_verbose
    set_verbose(True)      # print BUY / STOP / SELL / SIGNAL events to stdout
    ...
    set_verbose(False)     # quiet again

Or scope it to a single run with the context manager:

    with verbose():
        run_backtest(df, strat, ticker="AAPL")
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager

logger = logging.getLogger("agents.evaluator")
logger.setLevel(logging.WARNING)            # quiet until asked

_handler: logging.Handler | None = None


def set_verbose(on: bool = True, *, level: int = logging.INFO) -> None:
    global _handler
    if on:
        logger.setLevel(level)
        if _handler is None:
            _handler = logging.StreamHandler(sys.stdout)
            _handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(_handler)
        logger.propagate = False
    else:
        logger.setLevel(logging.WARNING)


@contextmanager
def verbose(level: int = logging.INFO):
    prev = logger.level
    set_verbose(True, level=level)
    try:
        yield
    finally:
        logger.setLevel(prev)
