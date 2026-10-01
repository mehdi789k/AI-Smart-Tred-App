"""Compatibility wrapper for :mod:`src.python.indicators.macd`."""

from src.python.indicators.macd import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.macd", run_name="__main__")
