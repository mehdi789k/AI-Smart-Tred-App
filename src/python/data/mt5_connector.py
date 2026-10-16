from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover - optional dependency for non-MT5 machines.
    mt5 = None

from .config import TIMEFRAME_MAP


class MT5ConnectionError(RuntimeError):
    """Raised when the MT5 connector cannot initialize or read data."""


class MT5DataUnavailableError(MT5ConnectionError):
    """Raised when MT5 cannot provide historical data for a symbol/timeframe."""


class MetaTraderConnector:
    """Thin wrapper around the MetaTrader 5 Python API."""

    def __init__(
        self,
        *,
        path: str | None = None,
        login: int | str | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout_ms: int = 15000,
    ) -> None:
        self.path = path or os.getenv("MT5_PATH")
        self.login = login if login is not None else os.getenv("MT5_LOGIN")
        self.password = password if password is not None else os.getenv("MT5_PASSWORD")
        self.server = server if server is not None else os.getenv("MT5_SERVER")
        self.timeout_ms = timeout_ms
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        if mt5 is None:
            raise MT5ConnectionError("MetaTrader5 package is not installed or not available in this environment.")

        initialize_options: dict[str, Any] = {"timeout": self.timeout_ms}
        if self.path:
            terminal_path = Path(self.path).expanduser()
            if terminal_path.is_dir():
                terminal_path = next(
                    (
                        terminal_path / executable
                        for executable in ("terminal64.exe", "terminal.exe")
                        if (terminal_path / executable).is_file()
                    ),
                    terminal_path,
                )
            if not terminal_path.exists():
                raise MT5ConnectionError(f"MT5_PATH does not point to a valid installation: {terminal_path}")
            initialize_options["path"] = str(terminal_path)

        if all(value for value in (self.login, self.password, self.server)):
            try:
                initialize_options.update(
                    login=int(self.login),
                    password=str(self.password),
                    server=str(self.server),
                )
            except ValueError as exc:
                raise MT5ConnectionError("MT5_LOGIN must be a numeric account number.") from exc

        initialized = mt5.initialize(**initialize_options)
        if not initialized:
            raise MT5ConnectionError(f"MetaTrader5 failed to initialize: {mt5.last_error()}")
        self._connected = True

    def disconnect(self) -> None:
        if mt5 is not None:
            mt5.shutdown()
        self._connected = False

    def list_market_watch_symbols(self) -> list[str]:
        self._ensure_connected()
        symbols = mt5.symbols_get()
        if symbols is None:
            raise MT5ConnectionError(f"Unable to read symbols from the MT5 Market Watch: {mt5.last_error()}")

        visible: list[str] = []
        for item in symbols:
            symbol_name = str(getattr(item, "name", item)).strip()
            if not symbol_name:
                continue
            info = mt5.symbol_info(symbol_name)
            if info is None or not getattr(info, "visible", False):
                continue
            visible.append(symbol_name)
        if not visible:
            raise MT5ConnectionError("No symbols are visible in the MT5 Market Watch.")
        return visible

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str | int,
        start_index: int = 0,
        count: int = 1000,
    ) -> list[dict[str, Any]]:
        self._ensure_connected()
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("count must be a positive integer.")
        if (
            not isinstance(start_index, int)
            or isinstance(start_index, bool)
            or start_index < 0
        ):
            raise ValueError("start_index must not be negative.")

        normalized_timeframe = TIMEFRAME_MAP.get(str(timeframe).upper(), timeframe)
        if not mt5.symbol_select(symbol, True):
            raise MT5DataUnavailableError(
                f"Unable to select {symbol} in MetaTrader 5: {mt5.last_error()}."
            )

        rates = mt5.copy_rates_from_pos(symbol, int(normalized_timeframe), start_index, count)
        position_error = mt5.last_error()
        if rates is None or len(rates) == 0:
            if start_index == 0:
                date_rates = mt5.copy_rates_from(
                    symbol,
                    int(normalized_timeframe),
                    datetime.now(timezone.utc),
                    count,
                )
                date_error = mt5.last_error()
            else:
                date_rates = None
                date_error = "not attempted because start_index is nonzero"
            if date_rates is not None and len(date_rates) > 0:
                rates = date_rates
            else:
                position_result = "returned no rates" if rates is None else f"returned {len(rates)} bars"
                date_result = (
                    "returned no rates"
                    if date_rates is None
                    else f"returned {len(date_rates)} bars"
                )
                raise MT5DataUnavailableError(
                    f"MT5 returned no OHLCV data for {symbol} ({timeframe}); "
                    f"copy_rates_from_pos {position_result} (last_error={position_error}); "
                    f"copy_rates_from {date_result} (last_error={date_error})."
                )

        rows: list[dict[str, Any]] = []
        for rate in rates:
            def value(field: str, default: Any = None) -> Any:
                if isinstance(rate, dict):
                    return rate.get(field, default)
                try:
                    return rate[field]
                except (IndexError, KeyError, TypeError):
                    return getattr(rate, field, default)

            timestamp_value = value("time")
            if timestamp_value is None:
                continue
            dt = datetime.fromtimestamp(float(timestamp_value), tz=timezone.utc)
            rows.append(
                {
                    "time": dt,
                    "open": float(value("open", 0.0) or 0.0),
                    "high": float(value("high", 0.0) or 0.0),
                    "low": float(value("low", 0.0) or 0.0),
                    "close": float(value("close", 0.0) or 0.0),
                    "tick_volume": int(value("tick_volume", 0) or 0),
                    "spread": int(value("spread", 0) or 0),
                    "real_volume": int(value("real_volume", 0) or 0),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Trading primitives (used by the live-trading executor)
    # ------------------------------------------------------------------
    def account_info(self) -> dict[str, Any]:
        """Return the current trading account snapshot as a plain dict."""
        self._ensure_connected()
        info = mt5.account_info()
        if info is None:
            raise MT5ConnectionError(f"Unable to read MT5 account info: {mt5.last_error()}")
        return info._asdict() if hasattr(info, "_asdict") else dict(info)

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        self._ensure_connected()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5ConnectionError(f"Unknown symbol '{symbol}': {mt5.last_error()}")
        return info._asdict() if hasattr(info, "_asdict") else dict(info)

    def symbol_tick(self, symbol: str) -> dict[str, Any]:
        self._ensure_connected()
        if not mt5.symbol_select(symbol, True):
            raise MT5DataUnavailableError(
                f"Unable to select {symbol} in MetaTrader 5: {mt5.last_error()}."
            )
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5DataUnavailableError(f"No tick data for {symbol}: {mt5.last_error()}")
        return tick._asdict() if hasattr(tick, "_asdict") else dict(tick)

    def positions_get(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self._ensure_connected()
        raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if raw is None:
            raise MT5ConnectionError(f"Unable to read open positions: {mt5.last_error()}")
        return [p._asdict() if hasattr(p, "_asdict") else dict(p) for p in raw]

    def order_send(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a raw trade request through ``mt5.order_send``."""
        self._ensure_connected()
        result = mt5.order_send(dict(request))
        if result is None:
            raise MT5ConnectionError(f"order_send failed with no response: {mt5.last_error()}")
        return result._asdict() if hasattr(result, "_asdict") else dict(result)

    def _ensure_connected(self) -> None:
        if not self.connected:
            raise MT5ConnectionError("The MetaTrader connector is not connected. Call connect() first.")

    def __enter__(self) -> "MetaTraderConnector":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
