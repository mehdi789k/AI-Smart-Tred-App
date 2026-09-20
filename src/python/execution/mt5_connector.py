"""
ماژول اتصال مستقیم به متاتریدر 5 (MT5)
دریافت داده‌های واقعی حساب، قیمت‌ها و پوزیشن‌ها
"""

import logging
from datetime import datetime
from typing import Any, Dict

import MetaTrader5 as mt5
import pandas as pd

logger = logging.getLogger(__name__)


class MT5Connection:
    """مدیریت اتصال به متاتریدر 5 و دریافت داده‌های واقعی"""

    def __init__(self):
        self.connected = False
        self.account_info = None
        self.last_update = None

    def connect(
        self,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
    ) -> bool:
        """
        اتصال به متاتریدر 5
        اگر پارامترها داده نشوند، از تنظیمات پیش‌فرض ترمینال استفاده می‌کند
        """
        try:
            if not mt5.initialize(
                path=path, login=login, password=password, server=server
            ):
                error = mt5.last_error()
                logger.error(f"خطا در اتصال به MT5: {error}")
                self.connected = False
                return False

            self.connected = True
            self.account_info = mt5.account_info()
            self.last_update = datetime.now()

            if self.account_info:
                logger.info(f"اتصال موفق به MT5 - حساب: {self.account_info.login}")
                return True
            else:
                logger.error("اطلاعات حساب دریافت نشد")
                return False

        except Exception as e:
            logger.error(f"خطای غیرمنتظره در اتصال: {str(e)}")
            self.connected = False
            return False

    def disconnect(self):
        """قطع اتصال از متاتریدر"""
        if self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("اتصال به MT5 قطع شد")

    def is_connected(self) -> bool:
        """بررسی وضعیت اتصال"""
        if not self.connected:
            return False
        try:
            terminal = mt5.terminal_info()
        except Exception:
            self.connected = False
            return False
        if terminal is None or not getattr(terminal, "connected", False):
            self.connected = False
            return False
        return True

    def get_account_summary(self) -> Dict[str, Any]:
        """دریافت خلاصه اطلاعات حساب به صورت واقعی"""
        if not self.is_connected():
            return {"connected": False, "error": "MT5 is not connected"}

        info = mt5.account_info()
        if not info:
            return {
                "connected": False,
                "error": "MT5 account information is unavailable",
            }

        self.account_info = info
        self.last_update = datetime.now()

        return {
            "login": info.login,
            "server": info.server,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "margin_free": info.margin_free,
            "margin_level": info.margin_level,
            "profit": info.profit,
            "leverage": info.leverage,
            "currency": info.currency,
            "connected": True,
        }

    def get_positions(self) -> pd.DataFrame:
        """دریافت پوزیشن‌های باز واقعی"""
        if not self.is_connected():
            return pd.DataFrame()

        positions = mt5.positions_get()
        if positions is None:
            return pd.DataFrame()

        df = pd.DataFrame(list(positions))
        if not df.empty:
            df["time"] = pd.to_datetime(df["time"], unit="s")
            # نام‌گذاری ستون‌ها به فارسی/انگلیسی خوانا
            df = df.rename(
                columns={
                    "symbol": "Symbol",
                    "type": "Type",
                    "volume": "Volume",
                    "price_open": "Open Price",
                    "sl": "SL",
                    "tp": "TP",
                    "price_current": "Current Price",
                    "swap": "Swap",
                    "profit": "Profit",
                    "magic": "Magic",
                    "comment": "Comment",
                }
            )

        return df

    def get_history(self, days: int = 30) -> pd.DataFrame:
        """دریافت تاریخچه معاملات بسته شده"""
        if not self.is_connected():
            return pd.DataFrame()

        from_date = datetime.now().replace(hour=0, minute=0, second=0)
        to_date = datetime.now()

        history = mt5.history_deals_get(from_date, to_date)
        if history is None:
            return pd.DataFrame()

        df = pd.DataFrame(list(history))
        if not df.empty:
            df["time"] = pd.to_datetime(df["time"], unit="s")

        return df

    def get_order_history(self, start: datetime, end: datetime) -> list[Any]:
        """Return MT5 order history for a bounded UTC interval."""

        if not self.is_connected():
            raise RuntimeError("MT5 is not connected")
        orders = mt5.history_orders_get(start, end)
        if orders is None:
            return []
        return list(orders)

    def get_deal_history(self, start: datetime, end: datetime) -> list[Any]:
        """Return MT5 deal history for a bounded UTC interval."""

        if not self.is_connected():
            raise RuntimeError("MT5 is not connected")
        deals = mt5.history_deals_get(start, end)
        if deals is None:
            return []
        return list(deals)

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """دریافت اطلاعات نماد و قیمت لحظه‌ای"""
        if not self.is_connected():
            return {}

        info = mt5.symbol_info(symbol)
        if info is None:
            return {}

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {}

        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "spread": tick.ask - tick.bid,
            "high": info.high,
            "low": info.low,
            "digits": info.digits,
        }

    def get_candles(
        self, symbol: str, timeframe: int, count: int = 100
    ) -> pd.DataFrame:
        """دریافت کندل‌های تاریخی واقعی"""
        if not self.is_connected():
            return pd.DataFrame()

        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time")

        return df


# نمونه Singleton برای استفاده در کل پروژه
mt5_manager = MT5Connection()


def get_mt5_instance() -> MT5Connection:
    """دریافت نمونه اتصال به MT5"""
    return mt5_manager
