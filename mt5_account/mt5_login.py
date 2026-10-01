"""Compatibility wrapper for :mod:`src.python.mt5_account.mt5_login`."""

from src.python.mt5_account.mt5_login import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.mt5_account.mt5_login", run_name="__main__")
