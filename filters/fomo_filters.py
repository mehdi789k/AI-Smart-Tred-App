"""Compatibility wrapper for :mod:`src.python.filters.fomo_filters`."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.python.filters.fomo_filters import *
from src.python.filters.fomo_filters import _read_entries

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.filters.fomo_filters", run_name="__main__")
