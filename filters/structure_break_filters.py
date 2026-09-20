"""Compatibility wrapper for :mod:`src.python.filters.structure_break_filters`."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.python.filters.structure_break_filters import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.filters.structure_break_filters", run_name="__main__")
