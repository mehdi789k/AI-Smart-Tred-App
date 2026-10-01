"""Read-only readiness checks for the guarded live MT5 workflow."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class LiveReadinessReport:
    """Redacted result of live account and market-data validation."""

    ready: bool
    reasons: tuple[str, ...]
    account: dict[str, object]
    symbols: dict[str, dict[str, object]]


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def validate_live_readiness(
    connector: Any,
    *,
    expected_login: int,
    expected_server: str,
    allowed_symbols: frozenset[str],
    max_tick_age_seconds: float = 30.0,
    now: datetime | None = None,
) -> LiveReadinessReport:
    """Validate MT5 identity and fresh market data without submitting orders."""

    reasons: list[str] = []
    account: dict[str, object] = {}
    symbols: dict[str, dict[str, object]] = {}
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if not allowed_symbols:
        reasons.append("symbol_whitelist_empty")
    if max_tick_age_seconds <= 0 or not math.isfinite(max_tick_age_seconds):
        reasons.append("invalid_tick_age_limit")

    try:
        connected = bool(connector.is_connected())
    except (AttributeError, OSError, RuntimeError, TypeError):
        connected = False
    if not connected:
        reasons.append("mt5_unavailable")
        return LiveReadinessReport(False, tuple(dict.fromkeys(reasons)), account, symbols)

    try:
        raw_account = connector.get_account_summary()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raw_account = {}
        reasons.append("account_unavailable")
    if not isinstance(raw_account, dict) or raw_account.get("connected") is False:
        reasons.append("account_unavailable")
    else:
        trade_mode = str(raw_account.get("trade_mode", "")).strip().lower()
        account = {
            key: raw_account[key]
            for key in (
                "login",
                "server",
                "currency",
                "leverage",
                "balance",
                "equity",
                "account_trade_allowed",
                "account_trade_expert",
                "terminal_trade_allowed",
                "terminal_tradeapi_disabled",
            )
            if key in raw_account
        }
        account["trade_mode"] = trade_mode
        if trade_mode not in {"demo", "real"}:
            reasons.append("account_mode_unsupported")
        if raw_account.get("account_trade_allowed") is not True:
            reasons.append("account_trade_disabled")
        if raw_account.get("terminal_trade_allowed") is not True:
            reasons.append("terminal_trade_disabled")
        if raw_account.get("terminal_tradeapi_disabled") is True:
            reasons.append("terminal_tradeapi_disabled")
        if raw_account.get("login") != expected_login:
            reasons.append("account_login_mismatch")
        if str(raw_account.get("server", "")).strip() != expected_server.strip():
            reasons.append("account_server_mismatch")

    try:
        visible_symbols = {
            str(item.get("symbol", item.get("name", ""))).upper()
            for item in connector.get_symbols_list(visible_only=True)
            if isinstance(item, dict) and item.get("symbol", item.get("name"))
        }
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        visible_symbols = set()
        reasons.append("symbol_watch_unavailable")

    for symbol in sorted(allowed_symbols):
        normalized = symbol.upper()
        try:
            info = connector.get_symbol_info(normalized)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            info = {}
            reasons.append(f"{normalized}:symbol_data_unavailable")
        if normalized not in visible_symbols:
            reasons.append(f"{normalized}:symbol_not_visible")
        if not isinstance(info, dict):
            reasons.append(f"{normalized}:symbol_data_unavailable")
            continue
        symbols[normalized] = {
            key: info[key]
            for key in ("bid", "ask", "spread", "timestamp", "last_update")
            if key in info
        }
        bid, ask = info.get("bid"), info.get("ask")
        if (
            not isinstance(bid, (int, float))
            or not isinstance(ask, (int, float))
            or not math.isfinite(float(bid))
            or not math.isfinite(float(ask))
            or bid <= 0
            or ask <= 0
            or ask < bid
        ):
            reasons.append(f"{normalized}:invalid_tick")
        timestamp = _as_utc(info.get("timestamp", info.get("last_update")))
        if timestamp is None:
            reasons.append(f"{normalized}:tick_timestamp_unavailable")
        elif (
            timestamp > current_time
            or (current_time - timestamp).total_seconds() > max_tick_age_seconds
        ):
            reasons.append(f"{normalized}:stale_tick")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return LiveReadinessReport(
        ready=not unique_reasons,
        reasons=unique_reasons,
        account=account,
        symbols=symbols,
    )
