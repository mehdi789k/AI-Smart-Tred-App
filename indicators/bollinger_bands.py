"""Compatibility wrapper for :mod:`src.python.indicators.bollinger_bands`."""

from src.python.indicators.bollinger_bands import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.bollinger_bands", run_name="__main__")
