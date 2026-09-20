"""Focused performance regressions for dashboard data access."""

from datetime import datetime

import pandas as pd

from src.python.dashboard.data_manager import DashboardDataManager


class FakeMT5:
    def __init__(self) -> None:
        self.account_calls = 0
        self.position_calls = 0

    def get_account_summary(self):
        self.account_calls += 1
        return {
            "connected": True,
            "balance": 1000.0,
            "equity": 1000.0,
            "profit": 0.0,
        }

    def get_history(self, days: int):
        return pd.DataFrame([{"type": 0, "profit": 10.0, "time": datetime.now()}])

    def get_positions(self):
        self.position_calls += 1
        return pd.DataFrame()

    def get_symbol_info(self, symbol: str):
        return {"name": symbol, "bid": 1.0, "ask": 1.1}


def test_equity_curve_reuses_cached_account_snapshot():
    """Equity calculations must not bypass the account-data cache."""
    mt5 = FakeMT5()
    manager = DashboardDataManager()
    manager.mt5 = mt5

    manager.get_account_data()
    manager.get_equity_curve(30)

    assert mt5.account_calls == 1


def test_market_data_does_not_query_positions_for_each_symbol():
    """Market prices must use direct symbol lookup."""
    mt5 = FakeMT5()
    manager = DashboardDataManager()
    manager.mt5 = mt5

    result = manager.get_market_data(["EURUSD_l", "XAUUSD_l"])

    assert set(result) == {"EURUSD_l", "XAUUSD_l"}
    assert mt5.position_calls == 0
