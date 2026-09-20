"""Compatibility wrapper for :mod:`src.python.indicators.atr`."""

from src.python.indicators.atr import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.atr", run_name="__main__")
