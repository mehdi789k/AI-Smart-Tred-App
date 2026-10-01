"""
MT5 Connector for Dashboard - Real-time MetaTrader 5 Data Connection
Provides live account info, positions, orders, and market data to the dashboard
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Add parent path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def normalize_mt5_trade_mode(value: Any, *, mt5_module: Any = None) -> str | None:
    """Return a redacted account mode only when MT5 identifies it explicitly."""

    if mt5_module is not None:
        for name, normalized in (
            ("ACCOUNT_TRADE_MODE_DEMO", "demo"),
            ("TRADE_MODE_DEMO", "demo"),
            ("ACCOUNT_TRADE_MODE_CONTEST", "contest"),
            ("TRADE_MODE_CONTEST", "contest"),
            ("ACCOUNT_TRADE_MODE_REAL", "real"),
            ("TRADE_MODE_REAL", "real"),
        ):
            constant = getattr(mt5_module, name, None)
            if constant is not None and value == constant:
                return normalized
    if isinstance(value, str):
        normalized_value = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "demo": "demo",
            "trade_mode_demo": "demo",
            "account_trade_mode_demo": "demo",
            "contest": "contest",
            "trade_mode_contest": "contest",
            "account_trade_mode_contest": "contest",
            "real": "real",
            "trade_mode_real": "real",
            "account_trade_mode_real": "real",
        }
        return aliases.get(normalized_value)
    return None


def _serialize_mt5_call(method):
    """Serialize native MT5 calls because the package is not thread-safe."""

    def wrapped(self, *args, **kwargs):
        with self._api_lock:
            return method(self, *args, **kwargs)

    wrapped.__name__ = method.__name__
    wrapped.__doc__ = method.__doc__
    return wrapped


def _load_dotenv_if_present() -> None:
    """Load missing MT5 settings from the project dotenv file."""

    dotenv_path = _PROJECT_ROOT / ".env"
    if not dotenv_path.is_file():
        return
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        logger.warning("Unable to read dashboard environment file: %s", error)
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


class MT5Connection:
    """Handles real-time connection to MetaTrader 5."""

    def __init__(self):
        _load_dotenv_if_present()
        self._api_lock = threading.RLock()
        self._mt5 = None
        self.connected = False
        self.account_info_cache = None
        self.last_update = None
        self.last_connection_error: str | None = None
        self._last_health_check = 0.0
        # Navigation can trigger several reruns in quick succession. A
        # terminal_info IPC probe on every rerun can stall the Streamlit
        # script runner while MT5 is handling another request.
        self._health_check_interval = 30.0
        self._initialize()

    def _initialize(self):
        """Initialize MT5 library."""
        try:
            import MetaTrader5 as mt5

            self._mt5 = mt5
            logger.info("MetaTrader5 library loaded successfully")
        except ImportError as error:
            if platform.system() != "Windows":
                self.last_connection_error = (
                    "MetaTrader5 is unavailable in this Linux/container environment. "
                    "Run the dashboard/collector MT5 connector on the Windows host "
                    "with Python 3.12, or keep MT5_ENABLED=false and use the API "
                    f"boundary (platform={platform.system()}, "
                    f"python={platform.python_version()})."
                )
            else:
                self.last_connection_error = (
                    "MetaTrader5 is unavailable in the active Windows Python "
                    "environment. Install it with the same Python 3.12 interpreter: "
                    f'`"{sys.executable}" -m pip install -r requirements/mt5.in` '
                    f"(platform={platform.system()}, python={platform.python_version()})."
                )
            logger.error("%s Import error: %s", self.last_connection_error, error)
            self._mt5 = None

    @_serialize_mt5_call
    def connect(
        self,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout_ms: int | None = None,
    ) -> bool:
        """Connect to the configured MT5 demo account or a running terminal."""
        if self._mt5 is None:
            logger.warning(self.last_connection_error or "MT5 library not available")
            if not self.last_connection_error:
                self.last_connection_error = (
                    "MetaTrader5 is unavailable; install requirements/mt5.in "
                    "with the same Python interpreter that runs the dashboard"
                )
            return False

        try:
            login = login or os.getenv("MT5_LOGIN")
            password = password or os.getenv("MT5_PASSWORD")
            server = server or os.getenv("MT5_SERVER")
            timeout_ms = int(
                timeout_ms
                if timeout_ms is not None
                else os.getenv("MT5_CONNECT_TIMEOUT_MS", "60000")
            )
            if timeout_ms <= 0:
                raise ValueError("MT5_CONNECT_TIMEOUT_MS must be positive")
            terminal_path = os.getenv("MT5_TERMINAL_PATH", "").strip()
            if terminal_path and not Path(terminal_path).is_file():
                logger.error("Configured MT5 terminal was not found: %s", terminal_path)
                self.last_connection_error = "configured terminal path was not found"
                return False
            if any((login, password, server)) and not all((login, password, server)):
                logger.error("MT5 credentials are incomplete; connection refused")
                self.last_connection_error = "MT5 credentials are incomplete"
                return False
            if login and password and server:
                # Prefer the already logged-in terminal. This is the same
                # process-safe path used by the market-data collector.
                result = self._mt5.initialize(
                    path=terminal_path or None,
                    timeout=timeout_ms,
                )
                account = self._mt5.account_info() if result else None
                if result and account is not None:
                    if int(account.login) != int(login) or account.server != server:
                        self._mt5.shutdown()
                        result = self._mt5.initialize(
                            path=terminal_path or None,
                            login=int(login),
                            password=password,
                            server=server,
                            timeout=timeout_ms,
                        )
            else:
                # Connect to running terminal
                result = self._mt5.initialize(
                    path=terminal_path or None,
                    timeout=timeout_ms,
                )

            if result:
                self.connected = True
                self._last_health_check = time.monotonic()
                self.last_update = datetime.now()
                self.last_connection_error = None
                logger.info("Connected to MT5")
                return True
            else:
                error = self._mt5.last_error()
                self.last_connection_error = str(error)
                logger.warning(f"MT5 connection failed: {error}")
                return False
        except Exception as e:
            self.last_connection_error = str(e)
            logger.error(f"MT5 connection error: {e}")
            return False

    @_serialize_mt5_call
    def disconnect(self):
        """Disconnect from MT5."""
        if self._mt5 and self.connected:
            self._mt5.shutdown()
            self.connected = False
            self._last_health_check = 0.0
            logger.info("Disconnected from MT5")

    @_serialize_mt5_call
    def is_connected(self) -> bool:
        """Check if connected to MT5."""
        if not self._mt5 or not self.connected:
            return False

        now = time.monotonic()
        if now - self._last_health_check < self._health_check_interval:
            return True

        # Verify connection is still alive
        try:
            terminal = self._mt5.terminal_info()
            self._last_health_check = now
            if terminal is None or not terminal.connected:
                self.connected = False
                return False
            return True
        except Exception:
            self.connected = False
            self._last_health_check = 0.0
            return False

    @_serialize_mt5_call
    def get_account_summary(self) -> Dict[str, Any]:
        """Get real account information."""
        if not self.is_connected():
            return {
                "login": None,
                "server": None,
                "balance": None,
                "equity": None,
                "margin": None,
                "margin_free": None,
                "margin_level": None,
                "profit": None,
                "currency": None,
                "leverage": None,
                "connected": False,
                "error": "MT5 is not connected",
            }

        try:
            account = self._mt5.account_info()
            if account is None:
                return {
                    "connected": False,
                    "error": "MT5 account information is unavailable",
                }
            terminal = self._mt5.terminal_info()

            return {
                "login": account.login,
                "server": account.server,
                "trade_mode": normalize_mt5_trade_mode(
                    getattr(account, "trade_mode", None),
                    mt5_module=self._mt5,
                ),
                "account_trade_allowed": bool(
                    getattr(account, "trade_allowed", False)
                ),
                "account_trade_expert": bool(
                    getattr(account, "trade_expert", False)
                ),
                "account_trade_status": (
                    "enabled"
                    if bool(getattr(account, "trade_allowed", False))
                    else "disabled_or_investor_mode"
                ),
                "terminal_trade_allowed": bool(
                    getattr(terminal, "trade_allowed", False)
                ),
                "terminal_tradeapi_disabled": bool(
                    getattr(terminal, "tradeapi_disabled", False)
                ),
                "balance": float(account.balance),
                "equity": float(account.equity),
                "margin": float(account.margin),
                "margin_free": float(account.margin_free),
                "margin_level": float(account.margin_level),
                "profit": float(account.profit),
                "currency": account.currency,
                "leverage": account.leverage,
                "connected": True,
                "last_update": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return {
                "connected": False,
                "error": "MT5 account information could not be read",
            }

    @_serialize_mt5_call
    def is_demo_account(self) -> bool:
        """Return true only when the connected account has explicit demo mode."""

        try:
            summary = self.get_account_summary()
            return (
                normalize_mt5_trade_mode(
                    summary.get("trade_mode"),
                    mt5_module=self._mt5,
                )
                == "demo"
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False

    @_serialize_mt5_call
    def get_positions(self) -> pd.DataFrame:
        """Get real open positions as DataFrame."""
        if not self.is_connected():
            return pd.DataFrame()

        try:
            positions = self._mt5.positions_get()
            if positions is None or len(positions) == 0:
                return pd.DataFrame()

            data = []
            for pos in positions:
                data.append(
                    {
                        "ticket": pos.ticket,
                        "symbol": pos.symbol,
                        "type": pos.type,  # 0=BUY, 1=SELL
                        "volume": float(pos.volume),
                        "price_open": float(pos.price_open),
                        "price_current": float(pos.price_current),
                        "sl": float(pos.sl) if pos.sl > 0 else 0.0,
                        "tp": float(pos.tp) if pos.tp > 0 else 0.0,
                        "profit": float(pos.profit),
                        "swap": float(pos.swap),
                        "commission": float(getattr(pos, "commission", 0.0)),
                        "time": datetime.fromtimestamp(pos.time, timezone.utc),
                    }
                )

            return pd.DataFrame(data)
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return pd.DataFrame()

    @_serialize_mt5_call
    def get_orders(self) -> pd.DataFrame:
        """Get real pending orders as DataFrame."""
        if not self.is_connected():
            return pd.DataFrame()

        try:
            orders = self._mt5.orders_get()
            if orders is None or len(orders) == 0:
                return pd.DataFrame()

            data = []
            for order in orders:
                tick = self._mt5.symbol_info_tick(order.symbol)
                order_type = int(order.type)
                buy_order_types = {2, 4, 6}
                current_price = None
                if tick is not None:
                    if order_type in buy_order_types and tick.ask > 0:
                        current_price = float(tick.ask)
                    elif order_type not in buy_order_types and tick.bid > 0:
                        current_price = float(tick.bid)
                data.append(
                    {
                        "ticket": order.ticket,
                        "symbol": order.symbol,
                        "type": order_type,
                        "volume_initial": float(order.volume_initial),
                        "price_open": float(order.price_open),
                        "price_current": current_price,
                        "sl": float(order.sl) if order.sl > 0 else 0.0,
                        "tp": float(order.tp) if order.tp > 0 else 0.0,
                        "time_setup": datetime.fromtimestamp(
                            order.time_setup, timezone.utc
                        ),
                        "time_expiration": datetime.fromtimestamp(
                            order.time_expiration, timezone.utc
                        )
                        if order.time_expiration > 0
                        else None,
                    }
                )

            return pd.DataFrame(data)
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return pd.DataFrame()

    @_serialize_mt5_call
    def get_history(self, days: int = 30) -> pd.DataFrame:
        """Get all account deals directly from MT5 using UTC chunks."""
        if self.is_connected():
            try:
                requested_to = datetime.now(timezone.utc)
                date_from = requested_to - timedelta(days=days, hours=12)
                date_to = requested_to + timedelta(hours=12)
                deals = []
                chunk_start = date_from
                while chunk_start < date_to:
                    chunk_end = min(chunk_start + timedelta(days=7), date_to)
                    chunk = self._mt5.history_deals_get(chunk_start, chunk_end)
                    if chunk is None:
                        raise RuntimeError(
                            f"MT5 history query failed: {self._mt5.last_error()}"
                        )
                    deals.extend(chunk)
                    chunk_start = chunk_end
                if not deals:
                    return pd.DataFrame()

                data = []
                for deal in deals:
                    data.append(
                        {
                            "ticket": deal.ticket,
                            "order": deal.order,
                            "position_id": deal.position_id,
                            "symbol": deal.symbol,
                            "type": deal.type,
                            "entry": deal.entry,
                            "reason": deal.reason,
                            "comment": deal.comment,
                            "volume": float(deal.volume),
                            "price": float(deal.price),
                            "profit": float(deal.profit),
                            "commission": float(deal.commission),
                            "swap": float(deal.swap),
                            "fee": float(getattr(deal, "fee", 0.0)),
                            "time": datetime.fromtimestamp(deal.time, timezone.utc),
                        }
                    )
                return pd.DataFrame(data)
            except Exception as e:
                logger.error(f"Error loading MT5 deal history: {e}")
                return pd.DataFrame()

        # Preserve offline/demo behavior only when MT5 is not connected.
        try:
            from pathlib import Path

            data_dir = (
                Path(__file__).resolve().parent.parent
                / "mt5_account"
                / "account_history"
            )
            history_files = list(data_dir.glob("account_*_history.json"))

            if history_files:
                latest_file = max(history_files, key=lambda f: f.stat().st_mtime)
                import json

                with open(latest_file, "r", encoding="utf-8") as f:
                    history = json.load(f)

                deals = history.get("deals", [])
                if deals:
                    data = []
                    for deal in deals[-100:]:  # Last 100 deals
                        data.append(
                            {
                                "ticket": deal.get("ticket"),
                                "position_id": deal.get("position_id"),
                                "symbol": deal.get("symbol"),
                                "type": deal.get("type", 0),
                                "volume": deal.get("volume", 0),
                                "price": deal.get("price", 0),
                                "profit": deal.get("profit", 0),
                                "commission": deal.get("commission", 0),
                                "swap": deal.get("swap", 0),
                                "time": datetime.fromisoformat(
                                    deal.get("time_iso", deal.get("time", ""))
                                )
                                if deal.get("time")
                                else datetime.now(),
                            }
                        )
                    return pd.DataFrame(data)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
        return pd.DataFrame()

    @_serialize_mt5_call
    def get_historical_candles(
        self,
        symbol: str,
        timeframe: str = "H1",
        count: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return live MT5 OHLC candles for read-only backtesting."""
        if not self.is_connected():
            return []

        timeframe_map = {
            "M1": self._mt5.TIMEFRAME_M1,
            "M5": self._mt5.TIMEFRAME_M5,
            "M15": self._mt5.TIMEFRAME_M15,
            "M30": self._mt5.TIMEFRAME_M30,
            "H1": self._mt5.TIMEFRAME_H1,
            "H4": self._mt5.TIMEFRAME_H4,
            "D1": self._mt5.TIMEFRAME_D1,
            "W1": self._mt5.TIMEFRAME_W1,
            "MN1": self._mt5.TIMEFRAME_MN1,
        }
        mt5_timeframe = timeframe_map.get(timeframe.upper())
        if mt5_timeframe is None:
            raise ValueError(f"Unsupported MT5 timeframe: {timeframe}")

        symbols_to_try = [symbol]
        available_symbols = self._mt5.symbols_get(group=f"{symbol}*")
        if available_symbols:
            symbols_to_try.extend(
                item.name
                for item in available_symbols
                if item.name not in symbols_to_try
            )

        rates = None
        for symbol_name in symbols_to_try:
            self._mt5.symbol_select(symbol_name, True)
            rates = self._mt5.copy_rates_from_pos(symbol_name, mt5_timeframe, 0, count)
            if rates is not None and len(rates) >= 100:
                break

        if rates is None or len(rates) < 100:
            logger.error(
                "MT5 candle query failed for %s: %s", symbol, self._mt5.last_error()
            )
            return []

        return [
            {
                "time": int(rate["time"]),
                "open": float(rate["open"]),
                "high": float(rate["high"]),
                "low": float(rate["low"]),
                "close": float(rate["close"]),
                "tick_volume": int(rate["tick_volume"]),
            }
            for rate in rates
        ]

    @_serialize_mt5_call
    def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get real-time symbol information."""
        if not self.is_connected():
            return None

        try:
            info = self._mt5.symbol_info(symbol)
            if info is None:
                return None

            tick = self._mt5.symbol_info_tick(symbol)

            return {
                "name": info.name,
                "description": info.description,
                "trade_mode": getattr(info, "trade_mode", None),
                "order_mode": getattr(info, "order_mode", None),
                "visible": bool(getattr(info, "visible", False)),
                "select": bool(getattr(info, "select", False)),
                "volume_min": getattr(info, "volume_min", None),
                "volume_max": getattr(info, "volume_max", None),
                "volume_step": getattr(info, "volume_step", None),
                "trade_stops_level": getattr(info, "trade_stops_level", None),
                "bid": tick.bid if tick else info.bid,
                "ask": tick.ask if tick else info.ask,
                "spread": (tick.ask - tick.bid) if tick else (info.ask - info.bid),
                "volume": tick.volume if tick else 0,
                "last_update": (
                    datetime.fromtimestamp(tick.time, tz=timezone.utc)
                    if tick
                    else None
                ),
            }
        except Exception as e:
            logger.error(f"Error getting symbol info for {symbol}: {e}")
            return None

    @_serialize_mt5_call
    def symbol_info_tick(self, symbol: str) -> Any:
        """Return the current MT5 tick for a symbol when the terminal is connected."""
        if not self.is_connected():
            return None

        try:
            return self._mt5.symbol_info_tick(symbol)
        except Exception as e:
            logger.error(f"Error getting live tick for {symbol}: {e}")
            return None

    @_serialize_mt5_call
    def get_symbols_list(self, visible_only: bool = True) -> List[Dict[str, Any]]:
        """Get list of available symbols."""
        if not self.is_connected():
            return []

        try:
            symbols = self._mt5.symbols_get()
            if symbols is None:
                return []

            result = []
            # Filter first so the result reflects all symbols currently shown
            # in Market Watch, not just the first 50 broker symbols.
            visible_symbols = [
                sym for sym in symbols if not visible_only or sym.visible
            ]
            for sym in visible_symbols[:200]:
                if visible_only and not sym.visible:
                    continue

                # ``symbols_get`` already returns the latest broker quote.
                # Avoid one IPC request per symbol while rendering Settings.
                bid = getattr(sym, "bid", 0.0) or 0.0
                ask = getattr(sym, "ask", 0.0) or 0.0
                result.append(
                    {
                        "name": sym.name,
                        "description": sym.description,
                        "bid": bid,
                        "ask": ask,
                        "spread": ask - bid,
                        "visible": sym.visible,
                    }
                )

            return result
        except Exception as e:
            logger.error(f"Error getting symbols list: {e}")
            return []


# Singleton instance
_mt5_instance: Optional[MT5Connection] = None


def get_mt5_instance() -> MT5Connection:
    """Get singleton MT5Connection instance."""
    global _mt5_instance
    if _mt5_instance is None:
        _mt5_instance = MT5Connection()
    return _mt5_instance


def refresh_mt5_connection():
    """Refresh the MT5 connection."""
    global _mt5_instance
    if _mt5_instance:
        _mt5_instance.disconnect()
    _mt5_instance = MT5Connection()
    return _mt5_instance
