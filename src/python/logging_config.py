"""Centralized, structured and safety-aware logging for AI Smart Tred."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_SECRET_KEYS = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|authorization|credential)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b[\w-]*(?:password|passwd|token|secret|api[_-]?key|authorization|credential)"
    r"\b)\s*([=:])\s*(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def _redact_text(value: str) -> str:
    """Redact bearer tokens and key/value credentials from log text."""

    value = _SENSITIVE_TEXT.sub(r"\1[REDACTED]", value)
    return _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)


def _safe_value(value: Any) -> Any:
    """Recursively remove credentials and normalize values for JSON logs."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SECRET_KEYS.search(str(key))
            else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return (
            _SENSITIVE_TEXT.sub(r"\1[REDACTED]", value)
            if isinstance(value, str)
            else value
        )
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def new_correlation_id() -> str:
    """Create and bind an identifier used to trace one operation end-to-end."""
    value = uuid.uuid4().hex[:16]
    _correlation_id.set(value)
    return value


def get_correlation_id() -> str:
    """Return the current operation correlation identifier."""
    return _correlation_id.get()


def bind_request_context(request_id: str, correlation_id: str) -> None:
    """Bind request and correlation identifiers to the current execution context."""
    _request_id.set(request_id)
    _correlation_id.set(correlation_id)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    message: str | None = None,
    **fields: Any,
) -> None:
    """Write a structured domain event without leaking sensitive values."""
    payload = {"event": event, **fields}
    logger.log(level, message or event, extra={"smart_event": _safe_value(payload)})


class _StructuredFormatter(logging.Formatter):
    """Emit machine-readable JSON while retaining useful exception details."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
            "correlation_id": get_correlation_id() or None,
            "request_id": _request_id.get() or None,
        }
        event = getattr(record, "smart_event", None)
        if event:
            payload.update(event)
        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=True, default=str)


class _RedactingFormatter(logging.Formatter):
    """Keep legacy plain-text handlers safe without changing their layout."""

    def format(self, record: logging.LogRecord) -> str:
        return _redact_text(super().format(record))


def setup_logging(
    log_dir: str = "logs",
    level: int = logging.INFO,
    console_output: bool = True,
) -> logging.Logger:
    """Set up idempotent rotating text and structured event logs.

    Args:
        log_dir: Directory for log files
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_output: Whether to output logs to console

    Returns:
        Configured logger instance
    """
    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Avoid duplicate handlers when Streamlit reruns import the module.
    logger = logging.getLogger("ai_smart_tred")
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = _RedactingFormatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    structured_formatter = _StructuredFormatter()

    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    log_file = log_path / f"trading_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # This file is shared by the dashboard, collectors, and training jobs.
    # Windows cannot rotate an open file held by another process.
    event_handler = logging.FileHandler(
        log_path / "smart_events.json",
        encoding="utf-8",
    )
    event_handler.setLevel(level)
    event_handler.setFormatter(structured_formatter)
    logger.addHandler(event_handler)

    error_file = log_path / f"errors_{datetime.now().strftime('%Y%m%d')}.log"
    error_handler = RotatingFileHandler(
        error_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    logger.info("Logging system initialized successfully")

    return logger


# Create default logger instance
logger = setup_logging()


def get_logger(name: str) -> logging.Logger:
    """Get a child logger that participates in centralized structured logging."""
    return logging.getLogger(f"ai_smart_tred.{name}")
