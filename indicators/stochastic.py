"""Compatibility wrapper for :mod:`src.python.indicators.stochastic`."""

from src.python.indicators.stochastic import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.stochastic", run_name="__main__")
