"""Compatibility wrapper for :mod:`src.python.indicators.market_regime`."""

from src.python.indicators.market_regime import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.market_regime", run_name="__main__")
