"""Compatibility wrapper for :mod:`src.python.mt5_account.mt5_market_watch`."""

from src.python.mt5_account.mt5_market_watch import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.mt5_account.mt5_market_watch", run_name="__main__")
