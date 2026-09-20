"""Compatibility wrapper for :mod:`src.python.mt5_account.mt5_account_history`."""

from src.python.mt5_account.mt5_account_history import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.mt5_account.mt5_account_history", run_name="__main__")
