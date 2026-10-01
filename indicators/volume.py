"""Compatibility wrapper for :mod:`src.python.indicators.volume`."""

from src.python.indicators.volume import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.volume", run_name="__main__")
