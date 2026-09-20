"""
ماژول مدیریت داده‌های واقعی برای داشبورد
اتصال به MT5 و ارائه داده‌های زنده به داشبورد
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from .mt5_connector import get_mt5_instance

logger = logging.getLogger(__name__)


class DashboardDataManager:
    """مدیریت داده‌های واقعی داشبورد از متاتریدر"""

    def __init__(self):
        self.mt5 = get_mt5_instance()
        self.cache = {}
        self.cache_timestamp = {}
        self.cache_durations = {
            "account": 2.0,
            "positions": 2.0,
            "orders": 3.0,
            "history": 15.0,
            "equity": 15.0,
            "performance": 15.0,
            "market": 2.0,
        }

    def _is_cache_valid(self, key: str) -> bool:
        """بررسی اعتبار کش"""
        if key not in self.cache_timestamp:
            return False

        age = (datetime.now() - self.cache_timestamp[key]).total_seconds()
        duration = self.cache_durations.get(key.split("_", 1)[0], 2.0)
        return age < duration

    def _get_cached(self, key: str) -> Optional[Any]:
        """دریافت از کش اگر معتبر باشد"""
        if self._is_cache_valid(key):
            return self.cache.get(key)
        return None

    def _set_cache(self, key: str, data: Any):
        """ذخیره در کش"""
        self.cache[key] = data
        self.cache_timestamp[key] = datetime.now()

    def connect_mt5(
        self, login: int = None, password: str = None, server: str = None
    ) -> bool:
        """اتصال به MT5 با اطلاعات کاربر"""
        return self.mt5.connect(login=login, password=password, server=server)

    def is_connected(self) -> bool:
        """بررسی وضعیت اتصال"""
        return self.mt5.is_connected()

    def get_account_data(self) -> Dict[str, Any]:
        """دریافت داده‌های حساب واقعی"""
        cached = self._get_cached("account")
        if cached is not None:
            return cached

        data = self.mt5.get_account_summary()
        self._set_cache("account", data)

        # محاسبات اضافی
        if data.get("connected"):
            data["profit_percent"] = 0.0
            if data["balance"] > 0:
                data["profit_percent"] = (
                    (data["equity"] - data["balance"]) / data["balance"]
                ) * 100

            # تعیین وضعیت
            if data["profit"] > 0:
                data["status"] = "profit"
            elif data["profit"] < 0:
                data["status"] = "loss"
            else:
                data["status"] = "neutral"

        return data

    def get_positions_data(self) -> pd.DataFrame:
        """دریافت پوزیشن‌های باز واقعی"""
        cached = self._get_cached("positions")
        if cached is not None:
            return cached

        df = self.mt5.get_positions()

        if not df.empty:
            # Convert type to text
            df["Type"] = df["type"].apply(lambda x: "BUY" if x == 0 else "SELL")

            # Calculate profit percentage - use correct column names
            df["Profit %"] = df.apply(
                lambda row: (
                    (
                        (row["price_current"] - row["price_open"])
                        / row["price_open"]
                        * 100
                    )
                    if row["type"] == 0  # BUY
                    else (
                        (row["price_open"] - row["price_current"])
                        / row["price_open"]
                        * 100
                    )
                ),
                axis=1,
            )

        self._set_cache("positions", df)
        return df

    def get_orders_data(self) -> pd.DataFrame:
        """Return pending orders reported by the connected MT5 terminal."""
        cached = self._get_cached("orders")
        if cached is not None:
            return cached
        orders = self.mt5.get_orders()
        self._set_cache("orders", orders)
        return orders

    def get_history_data(self, days: int = 7) -> pd.DataFrame:
        """دریافت تاریخچه معاملات"""
        cache_key = f"history_{days}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        df = self.mt5.get_history(days)

        if not df.empty:
            trade_deals = df[df["type"].isin([0, 1])].copy()
            if (
                not trade_deals.empty
                and "position_id" in trade_deals.columns
                and "entry" in trade_deals.columns
            ):
                trade_deals = self._combine_position_deals(trade_deals)
            df = trade_deals
            if not df.empty:
                df["profit_cumulative"] = df["profit"].cumsum()

        self._set_cache(cache_key, df)
        return df

    @staticmethod
    def _combine_position_deals(deals: pd.DataFrame) -> pd.DataFrame:
        """Combine entry and exit deals into one row per MT5 position."""
        rows = []
        for position_id, position_deals in deals.groupby("position_id", dropna=False):
            position_deals = position_deals.sort_values("time")
            entries = position_deals[position_deals["entry"] == 0]
            exits = position_deals[position_deals["entry"].isin([1, 2])]
            entry = entries.iloc[0] if not entries.empty else position_deals.iloc[0]
            exit_deal = exits.iloc[-1] if not exits.empty else None
            last = exit_deal if exit_deal is not None else entry

            row = {
                "ticket": int(last["ticket"]),
                "entry_ticket": int(entry["ticket"]),
                "exit_ticket": int(exit_deal["ticket"])
                if exit_deal is not None
                else None,
                "position_id": position_id,
                "order": int(last["order"]),
                "symbol": entry["symbol"],
                "direction": "BUY" if entry["type"] == 0 else "SELL",
                "type": int(entry["type"]),
                "volume": float(entry["volume"]),
                "entry_price": float(entry["price"]),
                "exit_price": float(exit_deal["price"])
                if exit_deal is not None
                else None,
                "entry_time": entry["time"],
                "exit_time": exit_deal["time"] if exit_deal is not None else None,
                "time": last["time"],
                "status": "CLOSED" if exit_deal is not None else "OPEN",
                "profit": float(position_deals["profit"].sum()),
                "commission": float(position_deals["commission"].sum()),
                "swap": float(position_deals["swap"].sum()),
                "fee": float(position_deals["fee"].sum())
                if "fee" in position_deals
                else 0.0,
            }
            rows.append(row)

        return (
            pd.DataFrame(rows)
            .sort_values("time", ascending=False)
            .reset_index(drop=True)
        )

    def get_equity_curve(self, days: int = 30) -> pd.DataFrame:
        """Build a realized balance curve from MT5 deals and current balance."""
        cache_key = f"equity_{days}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        history = self.get_history_data(days)
        account = self.get_account_data()

        if not account.get("connected"):
            result = pd.DataFrame(columns=["time", "equity"])
            self._set_cache(cache_key, result)
            return result

        current_balance = float(account.get("balance", 0.0) or 0.0)
        if history.empty:
            result = pd.DataFrame(
                {"time": [datetime.now(timezone.utc)], "equity": [current_balance]}
            )
            self._set_cache(cache_key, result)
            return result

        history = history.copy()
        # MT5 returns UTC-aware timestamps; normalize all curve points to UTC
        # before sorting/grouping so mixed broker timestamp formats cannot fail.
        history["time"] = pd.to_datetime(history["time"], errors="coerce", utc=True)
        history = history.dropna(subset=["time"]).sort_values("time")
        history["net_profit"] = (
            history["profit"].fillna(0)
            + history.get("commission", 0)
            + history.get("swap", 0)
            + history.get("fee", 0)
        )
        history["date"] = history["time"].dt.floor("D")
        daily_profit = history.groupby("date")["net_profit"].sum()
        cumulative_profit = daily_profit.cumsum()
        ending_profit = float(cumulative_profit.iloc[-1])
        equity_curve = current_balance - ending_profit + cumulative_profit

        result = pd.DataFrame(
            {"time": equity_curve.index, "equity": equity_curve.values}
        )
        result.loc[len(result)] = [datetime.now(timezone.utc), current_balance]

        result = result.sort_values("time").reset_index(drop=True)
        self._set_cache(cache_key, result)
        return result

    def get_market_data(self, symbols: List[str] = None) -> Dict[str, Dict]:
        """دریافت قیمت‌های لحظه‌ای بازار"""
        if symbols is None:
            symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD"]
        cache_key = f"market_{','.join(symbols)}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        result = {}
        for symbol in symbols:
            # The dashboard selector already supplies the broker symbol,
            # including suffixes such as ``_l``. Avoid an unrelated positions
            # query for every symbol; it is slow and cannot resolve symbols
            # that have no open position.
            info = self.mt5.get_symbol_info(symbol)
            if info:
                # تعیین روند
                if "bid" in info and "ask" in info:
                    spread_pct = (info["ask"] - info["bid"]) / info["bid"] * 10000
                    info["spread_pips"] = round(spread_pct, 2)

                # تغییرات قیمت (ساده‌سازی شده)
                info["change_24h"] = 0.0  # نیاز به داده تاریخی دارد
                info["trend"] = "neutral"

                result[symbol] = info

        self._set_cache(cache_key, result)
        return result

    def get_performance_metrics(self) -> Dict[str, Any]:
        """محاسبه معیارهای عملکرد واقعی"""
        cached = self._get_cached("performance")
        if cached is not None:
            return cached
        history = self.get_history_data(30)
        positions = self.get_positions_data()

        metrics = {
            "total_trades": len(history),
            "open_positions": len(positions),
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "consecutive_wins": 0,
            "consecutive_losses": 0,
        }

        if not history.empty:
            wins = history[history["profit"] > 0]
            losses = history[history["profit"] < 0]

            metrics["winning_trades"] = len(wins)
            metrics["losing_trades"] = len(losses)

            if len(history) > 0:
                metrics["win_rate"] = (len(wins) / len(history)) * 100

            gross_profit = wins["profit"].sum() if not wins.empty else 0
            gross_loss = abs(losses["profit"].sum()) if not losses.empty else 0

            if gross_loss > 0:
                metrics["profit_factor"] = gross_profit / gross_loss
            elif gross_profit > 0:
                metrics["profit_factor"] = float("inf")

            metrics["avg_win"] = wins["profit"].mean() if not wins.empty else 0
            metrics["avg_loss"] = losses["profit"].mean() if not losses.empty else 0
            metrics["largest_win"] = wins["profit"].max() if not wins.empty else 0
            metrics["largest_loss"] = losses["profit"].min() if not losses.empty else 0

        # محاسبه برد/باخت متوالی
        if not history.empty:
            history_sorted = history.sort_values("time")
            current_streak = 0
            max_win_streak = 0
            max_loss_streak = 0

            for _, row in history_sorted.iterrows():
                if row["profit"] > 0:
                    current_streak += 1
                    max_win_streak = max(max_win_streak, current_streak)
                elif row["profit"] < 0:
                    current_streak -= 1
                    max_loss_streak = min(max_loss_streak, current_streak)
                else:
                    current_streak = 0

            metrics["consecutive_wins"] = max_win_streak
            metrics["consecutive_losses"] = abs(max_loss_streak)

        self._set_cache("performance", metrics)
        return metrics

    def get_strategy_signals(self) -> List[Dict]:
        """دریافت سیگنال‌های فعلی از استراتژی‌ها (شبیه‌سازی تا زمان یکپارچگی کامل)"""
        # این بخش باید به موتور استراتژی واقعی متصل شود
        # فعلاً ساختار را آماده می‌کنیم
        return []

    def refresh_all(self):
        """تازه‌سازی تمام داده‌ها"""
        self.cache.clear()
        self.cache_timestamp.clear()
        logger.info("تمام داده‌های داشبورد تازه‌سازی شد")


# نمونه Singleton
dashboard_data = DashboardDataManager()


def get_dashboard_data() -> DashboardDataManager:
    """دریافت نمونه مدیریت داده داشبورد"""
    return dashboard_data
