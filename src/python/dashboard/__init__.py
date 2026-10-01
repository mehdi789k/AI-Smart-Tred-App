"""Dashboard module initialization."""

from .data_manager import DashboardDataManager, get_dashboard_data
from .mt5_connector import MT5Connection, get_mt5_instance, refresh_mt5_connection

__all__ = [
    "MT5Connection",
    "get_mt5_instance",
    "refresh_mt5_connection",
    "DashboardDataManager",
    "get_dashboard_data",
]
