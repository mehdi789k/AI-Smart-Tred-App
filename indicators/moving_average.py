"""Compatibility wrapper for :mod:`src.python.indicators.moving_average`."""

from src.python.indicators.moving_average import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.moving_average", run_name="__main__")
