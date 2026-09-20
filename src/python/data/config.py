"""Configuration for the MT5 market-data service.

Environment variables are used instead of credentials in source control.  The
small dataclass also makes the service easy to use in tests without pydantic.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Iterable


class Timeframe(str, Enum):
    """Supported MetaTrader 5 bar periods."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    @property
    def seconds(self) -> int | None:
        """Return the nominal period in seconds, or ``None`` for monthly bars."""

        return {
            self.M1: 60,
            self.M5: 300,
            self.M15: 900,
            self.M30: 1800,
            self.H1: 3600,
            self.H4: 14400,
            self.D1: 86400,
            self.W1: 604800,
            self.MN1: None,
        }[self]


def _split_csv(value: str | None, default: Iterable[str]) -> tuple[str, ...]:
    values = [item.strip() for item in (value or "").split(",") if item.strip()]
    return tuple(values) if values else tuple(default)


def _load_dotenv(path: Path) -> None:
    """Load only missing variables from a simple dotenv file."""

    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        os.environ.setdefault(key, value)


@dataclass(slots=True)
class DataConfig:
    """Runtime settings for collection, persistence, and publication."""

    mt5_login: int | None = None
    mt5_password: str | None = None
    mt5_server: str | None = None
    mt5_terminal_path: str | None = None
    mt5_xauusd_symbol: str = "XAUUSD"
    # An empty symbol list means "all symbols currently visible in Market Watch".
    symbols: tuple[str, ...] = ()
    timeframes: tuple[Timeframe, ...] = tuple(Timeframe)
    database_url: str = "postgresql+asyncpg://localhost/mt5_data"
    zmq_endpoint: str = "tcp://127.0.0.1:5556"
    poll_interval_seconds: float = 1.0
    reconnect_attempts: int = 3
    reconnect_backoff_seconds: float = 1.0
    history_bars: int = 10_000
    log_level: str = "INFO"
    log_file: str = "logs/data_collector.log"
    health_file: str = "data/collector_health.json"
    lock_file: str = "data/collector.lock"

    def __post_init__(self) -> None:
        """Normalize and validate the runtime settings after instantiation."""

        self.mt5_login = None if self.mt5_login is None else int(self.mt5_login)
        self.mt5_password = None if self.mt5_password is None else str(self.mt5_password).strip() or None
        self.mt5_server = None if self.mt5_server is None else str(self.mt5_server).strip() or None
        self.mt5_terminal_path = None if self.mt5_terminal_path is None else str(self.mt5_terminal_path).strip() or None
        self.mt5_xauusd_symbol = str(self.mt5_xauusd_symbol).strip()
        # Broker symbols can contain case-sensitive suffixes such as
        # ``XAUUSD_l``; normalize whitespace without changing the identifier.
        self.symbols = tuple(
            str(symbol).strip() for symbol in (self.symbols or ()) if str(symbol).strip()
        )
        raw_timeframes = self.timeframes or tuple(Timeframe)
        normalized_timeframes: list[Timeframe] = []
        for value in raw_timeframes:
            if value is None:
                continue
            normalized_timeframes.append(
                value if isinstance(value, Timeframe) else Timeframe(str(value).upper())
            )
        self.timeframes = tuple(dict.fromkeys(normalized_timeframes))
        self.database_url = None if self.database_url is None else str(self.database_url).strip()
        self.zmq_endpoint = None if self.zmq_endpoint is None else str(self.zmq_endpoint).strip()
        self.log_level = str(self.log_level).upper() if self.log_level is not None else "INFO"
        self.log_file = None if self.log_file is None else str(self.log_file).strip() or "logs/data_collector.log"
        self.health_file = str(self.health_file).strip() or "data/collector_health.json"
        self.lock_file = str(self.lock_file).strip() or "data/collector.lock"
        self.validate()

    def validate(self) -> "DataConfig":
        """Fail fast on environment or deployment configuration mistakes."""

        if self.mt5_login is not None and self.mt5_login <= 0:
            raise ValueError("MT5 login must be a positive integer account number")
        credential_fields = (self.mt5_login, self.mt5_password, self.mt5_server)
        if any(value is not None for value in credential_fields) and not all(
            value is not None for value in credential_fields
        ):
            raise ValueError(
                "MT5 credentials must include MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER together"
            )
        if self.mt5_password is not None and not self.mt5_password:
            raise ValueError("MT5 password must not be blank when provided")
        if self.mt5_server is not None and not self.mt5_server:
            raise ValueError("MT5 server must not be blank when provided")
        if not self.mt5_xauusd_symbol:
            raise ValueError("MT5_XAUUSD_SYMBOL must not be empty")
        if not self.database_url:
            raise ValueError("DATABASE_URL must not be empty")
        if not self.database_url.startswith(("postgresql://", "postgresql+", "postgres://", "sqlite://")):
            raise ValueError("DATABASE_URL must use a PostgreSQL or SQLite URL")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.reconnect_attempts < 1:
            raise ValueError("reconnect_attempts must be >= 1")
        if self.reconnect_backoff_seconds <= 0:
            raise ValueError("reconnect_backoff_seconds must be positive")
        if self.history_bars < 1:
            raise ValueError("history_bars must be positive")
        if self.log_level not in logging._nameToLevel:
            raise ValueError(f"Unsupported log level {self.log_level!r}")
        if self.zmq_endpoint and not self.zmq_endpoint.startswith(("tcp://", "ipc://", "inproc://")):
            raise ValueError("ZMQ endpoint must use a supported transport scheme")
        return self

    @classmethod
    def from_env(cls, dotenv_path: str | Path | None = None) -> "DataConfig":
        """Build settings from environment variables and an optional ``.env``."""

        _load_dotenv(Path(dotenv_path or ".env"))
        login_raw = os.getenv("MT5_LOGIN")
        if login_raw is not None and login_raw.strip():
            try:
                mt5_login = int(login_raw)
            except ValueError as error:
                raise ValueError("MT5_LOGIN must be a positive integer account number") from error
        else:
            mt5_login = None
        raw_timeframes = _split_csv(
            os.getenv("DATA_TIMEFRAMES") or os.getenv("MT5_TIMEFRAMES"),
            (item.value for item in cls().timeframes),
        )
        try:
            timeframes = tuple(Timeframe(item.upper()) for item in raw_timeframes)
        except ValueError as error:
            raise ValueError(
                f"Unsupported timeframe; choose from {[item.value for item in Timeframe]}"
            ) from error
        config = cls(
            mt5_login=mt5_login,
            mt5_password=os.getenv("MT5_PASSWORD"),
            mt5_server=os.getenv("MT5_SERVER"),
            mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH"),
            mt5_xauusd_symbol=os.getenv("MT5_XAUUSD_SYMBOL", "XAUUSD"),
            symbols=_split_csv(os.getenv("DATA_SYMBOLS") or os.getenv("MT5_SYMBOLS"), cls().symbols),
            timeframes=timeframes,
            database_url=os.getenv("DATABASE_URL", cls().database_url),
            zmq_endpoint=os.getenv("ZMQ_ENDPOINT", cls().zmq_endpoint),
            poll_interval_seconds=float(os.getenv("DATA_POLL_INTERVAL", "1.0")),
            reconnect_attempts=int(os.getenv("MT5_RECONNECT_ATTEMPTS", "3")),
            reconnect_backoff_seconds=float(os.getenv("MT5_RECONNECT_BACKOFF", "1.0")),
            history_bars=max(1_000, int(os.getenv("MT5_HISTORY_BARS", "10000"))),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_file=os.getenv("LOG_FILE", "logs/data_collector.log"),
            health_file=os.getenv("COLLECTOR_HEALTH_FILE", "data/collector_health.json"),
            lock_file=os.getenv("COLLECTOR_LOCK_FILE", "data/collector.lock"),
        )
        return config

    def configure_logging(self) -> None:
        """Install a rotating file handler and a console handler."""

        path = Path(self.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(getattr(logging, self.log_level.upper(), logging.INFO))
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            "%Y-%m-%dT%H:%M:%S%z",
        )
        has_file = any(
            isinstance(handler, logging.handlers.RotatingFileHandler)
            and getattr(handler, "baseFilename", None) == str(path.resolve())
            for handler in root.handlers
        )
        if not has_file:
            file_handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=10_000_000, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        has_console = any(
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
            for handler in root.handlers
        )
        if not has_console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            root.addHandler(console_handler)


Settings = DataConfig


@lru_cache(maxsize=1)
def get_settings() -> DataConfig:
    """Return process-wide settings loaded from ``.env`` and the environment."""

    return DataConfig.from_env()
