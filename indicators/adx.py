"""Compatibility wrapper for :mod:`src.python.indicators.adx`."""

from src.python.indicators.adx import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.adx", run_name="__main__")
