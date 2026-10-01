"""Compatibility wrapper for :mod:`src.python.mt5_account.mt5_manage_orders`."""

from src.python.mt5_account.mt5_manage_orders import *

if __name__ == "__main__":
    import runpy
    runpy.run_module("src.python.mt5_account.mt5_manage_orders", run_name="__main__")
