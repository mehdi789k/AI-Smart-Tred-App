"""Compatibility wrapper for :mod:`src.python.indicators.rsi`."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.python.indicators.rsi import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.indicators.rsi", run_name="__main__")
