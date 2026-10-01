"""Compatibility wrapper for :mod:`src.python.indicators.ichimoku`."""

from src.python.indicators.ichimoku import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.ichimoku", run_name="__main__")
