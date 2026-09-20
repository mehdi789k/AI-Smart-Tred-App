"""Defensive MetaTrader 5 API wrapper with bounded reconnection."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DataConfig, Timeframe

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as _mt5
except ImportError:  # pragma: no cover - optional on CI/Linux
    _mt5 = None


class MT5Error(RuntimeError):
    """Base exception for connector failures."""


class MT5UnavailableError(MT5Error):
    """Raised when the MetaTrader5 Python package is not installed."""


class MT5LoginError(MT5Error):
    """Raised when initialization or account validation fails."""


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _record_to_dict(record: Any) -> dict[str, Any]:
    """Convert numpy structured rows, namedtuples, and mappings to plain dicts."""

    if isinstance(record, Mapping):
        return {str(key): _json_value(value) for key, value in record.items()}
    names = getattr(getattr(record, "dtype", None), "names", None)
    if names:
        return {name: _json_value(record[name]) for name in names}
    fields = getattr(record, "_fields", None)
    if fields:
        return {name: _json_value(getattr(record, name)) for name in fields}
    try:
        return {str(index): _json_value(value) for index, value in enumerate(record)}
    except TypeError:
        return {"value": _json_value(record)}


def _utc_epoch(value: Any) -> int | float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    return value


class MT5Connector:
    """Small, mockable wrapper around the module-level MT5 API."""

    _fallback_timeframes = {
        Timeframe.M1: 1,
        Timeframe.M5: 5,
        Timeframe.M15: 15,
        Timeframe.M30: 30,
        Timeframe.H1: 16385,
        Timeframe.H4: 16388,
        Timeframe.D1: 16408,
        Timeframe.W1: 32769,
        Timeframe.MN1: 49153,
    }

    def __init__(
        self,
        config: DataConfig,
        *,
        mt5_module: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.config = config
        self.mt5: Any = mt5_module if mt5_module is not None else _mt5
        self._sleep = sleep
        self._connected = False
        self._last_error: Any = None

    @property
    def connected(self) -> bool:
        """Return whether the terminal is initialized and reports a live link."""

        if not self._connected or self.mt5 is None:
            return False
        try:
            terminal = self.mt5.terminal_info()
        except (AttributeError, RuntimeError):
            return False
        if terminal is None:
            return False
        return bool(getattr(terminal, "connected", True))

    def connect(self) -> bool:
        """Initialize MT5 and validate the account when credentials are provided."""

        if self.mt5 is None:
            raise MT5UnavailableError(
                "MetaTrader5 is optional; install the 'MetaTrader5' package on the MT5 host"
            )
        kwargs: dict[str, Any] = {"timeout": 60_000}
        if self.config.mt5_login is not None:
            kwargs["login"] = self.config.mt5_login
        if self.config.mt5_password is not None:
            kwargs["password"] = self.config.mt5_password
        if self.config.mt5_server:
            kwargs["server"] = self.config.mt5_server
        if self.config.mt5_terminal_path:
            kwargs["path"] = str(Path(self.config.mt5_terminal_path))
        try:
            initialized = bool(self.mt5.initialize(**kwargs))
        except Exception as error:
            self._connected = False
            raise MT5LoginError(f"MetaTrader 5 initialization failed: {error}") from error
        if not initialized:
            self._connected = False
            self._last_error = self._safe_last_error()
            raise MT5LoginError(f"MetaTrader 5 login failed: {self._last_error}")
        if self.config.mt5_login is not None:
            account = self.mt5.account_info()
            if account is None:
                self._connected = False
                self._last_error = self._safe_last_error()
                self.mt5.shutdown()
                raise MT5LoginError(f"MetaTrader 5 account validation failed: {self._last_error}")
            actual_login = getattr(account, "login", self.config.mt5_login)
            if actual_login != self.config.mt5_login:
                self._connected = False
                self.mt5.shutdown()
                raise MT5LoginError(
                    f"MetaTrader 5 returned account {actual_login}, "
                    f"expected {self.config.mt5_login}"
                )
        self._connected = True
        return True

    def disconnect(self) -> None:
        """Shutdown MT5 and clear local connection state."""

        if self.mt5 is not None:
            try:
                self.mt5.shutdown()
            finally:
                self._connected = False

    def reconnect(self, attempts: int | None = None) -> bool:
        """Reconnect with exponential backoff and a clear terminal error."""

        attempts = self.config.reconnect_attempts if attempts is None else attempts
        if attempts < 1:
            raise ValueError("attempts must be greater than zero")
        self.disconnect()
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self.connect()
            except MT5Error as error:
                last_error = error
                logger.warning("MT5 reconnect attempt %d/%d failed: %s", attempt + 1, attempts, error)
                if attempt + 1 < attempts:
                    self._sleep(self.config.reconnect_backoff_seconds * (2**attempt))
        raise MT5LoginError(f"MT5 reconnect failed after {attempts} attempts: {last_error}")

    def ensure_connected(self) -> None:
        """Ensure the session is connected, reconnecting when the terminal disappeared."""

        if not self.connected:
            self.reconnect()

    def timeframe_constant(self, timeframe: Timeframe | str) -> int:
        """Resolve a domain timeframe to the installed MT5 constant."""

        value = Timeframe(timeframe)
        name = f"TIMEFRAME_{value.value}"
        return int(getattr(self.mt5, name, self._fallback_timeframes[value]))

    def visible_symbols(self) -> tuple[str, ...]:
        """Return symbols visible in the terminal's Market Watch.

        ``symbols_get`` is intentionally used instead of a hard-coded list.
        Brokers expose different instruments and the ``visible`` field is the
        authoritative Market Watch flag.  A few mock/older terminals omit that
        field; in that case the returned records are treated as visible.
        """

        records = self._retry_api_call(
            "list Market Watch symbols",
            lambda: self.mt5.symbols_get(),
        )
        if records is None:
            self._raise_api_error("list Market Watch symbols")
        names: set[str] = set()
        for record in records:
            if isinstance(record, Mapping):
                name = record.get("name", "")
                visible = record.get("visible", True)
            else:
                name = getattr(record, "name", "")
                visible = getattr(record, "visible", True)
            if visible and name:
                names.add(str(name))
        return tuple(sorted(name for name in names if name))

    def get_rates(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        count: int = 1_000,
    ) -> list[dict[str, Any]]:
        """Fetch and normalize historical OHLCV rows."""

        self.ensure_connected()
        if not symbol:
            raise ValueError("symbol must not be empty")
        if count <= 0:
            raise ValueError("count must be greater than zero")
        constant = self.timeframe_constant(timeframe)
        if start is not None or end is not None:
            if start is None or end is None:
                raise ValueError("start and end must be supplied together")
            operation = lambda: self.mt5.copy_rates_range(
                symbol, constant, _utc_epoch(start), _utc_epoch(end)
            )
        else:
            operation = lambda: self.mt5.copy_rates_from_pos(symbol, constant, 0, count)
        values = self._retry_api_call(f"copy rates for {symbol}", operation)
        if values is None:
            self._raise_api_error(f"copy rates for {symbol} {timeframe}")
        return [
            _normalize_rate(
                _record_to_dict(value), symbol, Timeframe(timeframe).value
            )
            for value in values
        ]

    def get_rates_from_pos(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        position: int,
        count: int,
        *,
        attempts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read a historical page, counting backwards from the newest bar.

        ``copy_rates_from_pos`` is the only MT5 API that supports reliable
        pagination without guessing broker session boundaries.  Keeping the
        retry loop here also means every caller gets the same reconnect
        behaviour when a terminal drops during a long backfill.
        """

        if position < 0 or count <= 0:
            raise ValueError("position must be non-negative and count must be positive")
        attempts = self.config.reconnect_attempts if attempts is None else attempts
        if attempts < 1:
            raise ValueError("attempts must be greater than zero")
        constant = self.timeframe_constant(timeframe)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                self.ensure_connected()
                values = self.mt5.copy_rates_from_pos(symbol, constant, position, count)
                if values is None:
                    self._raise_api_error(f"copy rates page for {symbol} {timeframe}")
                return [
                    _normalize_rate(_record_to_dict(value), symbol, Timeframe(timeframe).value)
                    for value in values
                ]
            except Exception as error:
                last_error = error
                self._connected = False
                if attempt + 1 >= attempts:
                    break
                self._sleep(self.config.reconnect_backoff_seconds * (2**attempt))
                try:
                    self.reconnect(attempts=1)
                except MT5Error as reconnect_error:
                    last_error = reconnect_error
        raise MT5Error(f"MT5 paged rates failed after {attempts} attempts: {last_error}")

    def get_ticks(
        self,
        symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        count: int = 1_000,
    ) -> list[dict[str, Any]]:
        """Fetch and normalize raw ticks."""

        self.ensure_connected()
        if not symbol:
            raise ValueError("symbol must not be empty")
        if count <= 0:
            raise ValueError("count must be greater than zero")
        if start is not None or end is not None:
            if start is None or end is None:
                raise ValueError("start and end must be supplied together")
            operation = lambda: self.mt5.copy_ticks_range(
                symbol, _utc_epoch(start), _utc_epoch(end), 0
            )
        else:
            operation = lambda: self.mt5.copy_ticks_from(symbol, 0, count, 0)
        values = self._retry_api_call(f"copy ticks for {symbol}", operation)
        if values is None:
            self._raise_api_error(f"copy ticks for {symbol}")
        return [_normalize_tick(_record_to_dict(value), symbol) for value in values]

    def symbol_tick(self, symbol: str) -> dict[str, Any]:
        """Read and normalize the latest tick for a symbol."""

        value = self._retry_api_call(
            f"latest tick for {symbol}",
            lambda: self.mt5.symbol_info_tick(symbol),
        )
        if value is None:
            self._raise_api_error(f"latest tick for {symbol}")
        return _normalize_tick(_record_to_dict(value), symbol)

    def _retry_api_call(
        self,
        operation: str,
        callback: Callable[[], Any],
        *,
        attempts: int | None = None,
    ) -> Any:
        """Run an MT5 read with bounded reconnect/retry handling.

        The MetaTrader5 package exposes a process-global connection and can
        return ``None`` or raise when the terminal is restarting.  Keeping the
        retry policy in one place makes range downloads, ticks, and symbol
        discovery behave consistently without ever retrying forever.
        """

        attempts = self.config.reconnect_attempts if attempts is None else attempts
        if attempts < 1:
            raise ValueError("attempts must be greater than zero")
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                self.ensure_connected()
                result = callback()
                if result is None:
                    self._raise_api_error(operation)
                return result
            except Exception as error:
                last_error = error
                self._connected = False
                if attempt + 1 >= attempts:
                    break
                delay = self.config.reconnect_backoff_seconds * (2**attempt)
                logger.warning(
                    "MT5 %s failed; reconnecting (%d/%d) in %.2fs: %s",
                    operation,
                    attempt + 1,
                    attempts,
                    delay,
                    error,
                )
                self._sleep(delay)
                try:
                    self.reconnect(attempts=1)
                except MT5Error as reconnect_error:
                    last_error = reconnect_error
        raise MT5Error(f"MT5 {operation} failed after {attempts} attempts: {last_error}")

    def iter_ticks(self, symbols: tuple[str, ...]) -> Iterator[dict[str, Any]]:
        """Yield one latest tick per symbol; callers control polling cadence."""

        for symbol in symbols:
            try:
                yield self.symbol_tick(symbol)
            except MT5Error:
                logger.exception("Unable to read tick for %s", symbol)

    def _safe_last_error(self) -> Any:
        try:
            return self.mt5.last_error()
        except (AttributeError, RuntimeError):
            return "unknown MT5 error"

    def _raise_api_error(self, operation: str) -> None:
        error = self._safe_last_error()
        self._connected = False
        raise MT5Error(f"MT5 {operation} failed: {error}")


def _normalize_rate(row: dict[str, Any], symbol: str, timeframe: str) -> dict[str, Any]:
    timestamp = row.get("time", row.get("timestamp"))
    if timestamp is None:
        raise ValueError("MT5 rate is missing time")
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if isinstance(timestamp, (int, float)):
        timestamp = datetime.fromtimestamp(timestamp, timezone.utc)
    elif timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)
    row["symbol"] = symbol
    row["timeframe"] = timeframe
    row["timestamp"] = timestamp
    row.setdefault("source", "mt5_official")
    return row


def _normalize_tick(row: dict[str, Any], symbol: str) -> dict[str, Any]:
    timestamp = row.get("time_msc", row.get("time", row.get("timestamp")))
    if timestamp is None:
        raise ValueError("MT5 tick is missing time")
    if isinstance(timestamp, (int, float)):
        if "time_msc" in row and timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        timestamp = datetime.fromtimestamp(timestamp, timezone.utc)
    elif isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    elif timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)
    row["symbol"] = symbol
    row["timestamp"] = timestamp
    return row


__all__ = [
    "MT5Connector",
    "MT5Client",
    "MT5Error",
    "MT5LoginError",
    "MT5UnavailableError",
]

MT5Client = MT5Connector
