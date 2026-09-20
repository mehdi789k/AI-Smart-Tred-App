"""Fail-closed live MT5 order workflow.

This module is deliberately separate from simulation and signal generation.  It
does not send anything unless the caller supplies a short-lived confirmation
token and every account/symbol/risk gate passes.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import os
import secrets
from collections.abc import Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from ..data.database import ConcurrencyConflict
from ..data.models import utc_now
from ..indicators.atr import calculate_atr
from ..logging_config import log_event, new_correlation_id
from ..observability import AuditLogger
from .policy import DEFAULT_EXECUTION_POLICY

logger = logging.getLogger(__name__)
_CONTROL_STORE_UNSET = object()


@dataclass
class _EphemeralExecutionControlState:
    """Test/local fallback state used only when no store was supplied."""

    scope: str
    emergency_stop: bool = False
    emergency_stop_reason: str | None = None
    demo_active: bool = False
    session_expires_at: datetime | None = None
    demo_owner_approval: str | None = None
    demo_second_approval: str | None = None
    demo_selected_symbols: list[str] | None = None
    demo_limits: dict[str, Any] | None = None
    demo_configuration_hash: str | None = None
    demo_trade_count: int = 0
    daily_loss: float = 0.0
    version: int = 1
    actor: str = "system"
    updated_at: datetime = field(default_factory=utc_now)


class _EphemeralExecutionControlStore:
    """Small deterministic store for legacy unit callers that omit injection."""

    def __init__(self, scope: str) -> None:
        self.state = _EphemeralExecutionControlState(scope=scope)
        self.state.updated_at = utc_now()

    def load_execution_control(self, scope: str) -> _EphemeralExecutionControlState:
        if self.state.scope != scope:
            raise ValueError("execution control scope mismatch")
        return self.state

    def save_execution_control(
        self, scope: str, *, expected_version: int, **changes: Any
    ) -> _EphemeralExecutionControlState:
        if self.state.scope != scope or expected_version != self.state.version:
            raise ConcurrencyConflict("stale execution control")
        values = self.state.__dict__.copy()
        values.update(changes)
        values["version"] = expected_version + 1
        values["updated_at"] = utc_now()
        self.state = _EphemeralExecutionControlState(**values)
        return self.state


class LiveOrderRejected(RuntimeError):
    """A live order was rejected before it reached MT5."""

    def __init__(self, code: str, message: str, *, retcode: int | None = None):
        super().__init__(message)
        self.code = code
        self.retcode = retcode


class AmbiguousOrderOutcome(LiveOrderRejected):
    """Raised when MT5 may have accepted an order but gave no definitive result."""


@dataclass(frozen=True)
class DemoActivationConfig:
    """Safety gate for a limited live/demo activation window."""

    enabled: bool = False
    max_trade_volume: float = 0.01
    max_trades_per_session: int = 3
    max_daily_loss: float = 10.0
    require_manual_confirmation: bool = True
    auto_stop_on_error: bool = True
    audit_log_path: str = "logs/demo_activation_audit.jsonl"

    @classmethod
    def from_environment(cls) -> "DemoActivationConfig":
        enabled = os.getenv("MT5_DEMO_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not enabled:
            return cls()
        return cls(
            enabled=True,
            max_trade_volume=float(os.getenv("MT5_DEMO_MAX_TRADE_VOLUME", "0.01")),
            max_trades_per_session=int(
                os.getenv("MT5_DEMO_MAX_TRADES_PER_SESSION", "3")
            ),
            max_daily_loss=float(os.getenv("MT5_DEMO_MAX_DAILY_LOSS", "10")),
            require_manual_confirmation=os.getenv(
                "MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION",
                "true",
            )
            .strip()
            .lower()
            not in {"0", "false", "no", "off"},
            auto_stop_on_error=os.getenv(
                "MT5_DEMO_AUTO_STOP_ON_ERROR",
                "true",
            )
            .strip()
            .lower()
            not in {"0", "false", "no", "off"},
            audit_log_path=os.getenv(
                "MT5_DEMO_AUDIT_LOG_PATH", "logs/demo_activation_audit.jsonl"
            ),
        )


DemoModeConfig = DemoActivationConfig


@dataclass(frozen=True)
class LiveOrderConfig:
    allowed_symbols: frozenset[str]
    magic: int = DEFAULT_EXECUTION_POLICY.magic_number
    max_position_volume: float = DEFAULT_EXECUTION_POLICY.max_lot_size
    max_daily_loss: float = DEFAULT_EXECUTION_POLICY.daily_loss_limit
    daily_loss_reset_at: float = 0.0
    max_spread: float = 0.0
    spread_mode: str = "fixed"
    spread_atr_period: int = 14
    spread_atr_multiplier: float = 1.0
    spread_atr_timeframe: str = "H1"
    confirmation_ttl_seconds: int = 120
    automation_session_seconds: int = 3600
    demo_activation: DemoActivationConfig | None = None

    @classmethod
    def from_environment(cls) -> "LiveOrderConfig":
        symbols = frozenset(
            item.strip().upper()
            for item in os.getenv("MT5_LIVE_SYMBOLS", "").split(",")
            if item.strip()
        )
        demo_activation = DemoActivationConfig.from_environment()
        return cls(
            allowed_symbols=symbols,
            magic=int(
                os.getenv("MT5_LIVE_MAGIC", str(DEFAULT_EXECUTION_POLICY.magic_number))
            ),
            max_position_volume=float(
                os.getenv(
                    "MT5_MAX_POSITION_VOLUME",
                    str(DEFAULT_EXECUTION_POLICY.max_lot_size),
                )
            ),
            max_daily_loss=float(
                os.getenv(
                    "MT5_MAX_DAILY_LOSS",
                    str(DEFAULT_EXECUTION_POLICY.daily_loss_limit),
                )
            ),
            daily_loss_reset_at=0.0,
            max_spread=float(os.getenv("MT5_MAX_SPREAD", "0")),
            spread_mode=os.getenv("MT5_SPREAD_MODE", "fixed").strip().lower(),
            spread_atr_period=int(os.getenv("MT5_SPREAD_ATR_PERIOD", "14")),
            spread_atr_multiplier=float(os.getenv("MT5_SPREAD_ATR_MULTIPLIER", "1.0")),
            spread_atr_timeframe=os.getenv("MT5_SPREAD_ATR_TIMEFRAME", "H1")
            .strip()
            .upper(),
            automation_session_seconds=int(
                os.getenv("MT5_AUTO_TRADING_SESSION_SECONDS", "3600")
            ),
            demo_activation=demo_activation,
        )


class LiveOrderWorkflow:
    """Validated, manually-confirmed adapter around an MT5 dashboard connector."""

    def __init__(
        self,
        connector: Any,
        config: LiveOrderConfig,
        control_store: Any = _CONTROL_STORE_UNSET,
        *,
        control_scope: str | None = None,
    ):
        if (
            config.magic <= 0
            or config.max_position_volume <= 0
            or config.max_daily_loss <= 0
            or config.automation_session_seconds <= 0
            or not math.isfinite(config.max_spread)
            or config.max_spread < 0
            or not math.isfinite(config.daily_loss_reset_at)
            or config.daily_loss_reset_at < 0
            or config.spread_mode not in {"fixed", "atr"}
            or config.spread_atr_period < 1
            or not math.isfinite(config.spread_atr_multiplier)
            or config.spread_atr_multiplier <= 0
            or not config.spread_atr_timeframe
        ):
            raise ValueError("live trading limits must be positive")
        if config.demo_activation is not None:
            if config.demo_activation.max_trade_volume <= 0:
                raise ValueError("demo max trade volume must be positive")
            if config.demo_activation.max_trades_per_session <= 0:
                raise ValueError("demo max trades per session must be positive")
            if config.demo_activation.max_daily_loss <= 0:
                raise ValueError("demo max daily loss must be positive")
        self.connector = connector
        self.config = config
        symbol = next(iter(sorted(config.allowed_symbols)), "GLOBAL")
        default_scope = (
            f"{'demo' if config.demo_activation is not None else 'live'}:{symbol}"
        )
        self.control_scope = control_scope or default_scope
        self._control_store_missing = control_store is None
        self._control_store = (
            _EphemeralExecutionControlStore(self.control_scope)
            if control_store is _CONTROL_STORE_UNSET
            else control_store
        )
        self._control_state: Any | None = None
        self._control_initial_load = True
        self._control_trade_reserved = False
        self._pending_confirmation: tuple[str, float] | None = None
        self._automation_authorized_until: float = 0.0
        self._ignored_daily_loss_deals: set[str] = set()
        self._demo_active = bool(
            config.demo_activation
            and config.demo_activation.enabled
            and not config.demo_activation.require_manual_confirmation
        )
        if config.demo_activation is not None and len(config.allowed_symbols) != 1:
            raise ValueError("controlled demo mode requires exactly one allowed symbol")
        self._emergency_stop_active = False
        self._demo_trade_count = 0
        self._demo_audit_path = (
            Path(config.demo_activation.audit_log_path)
            if config.demo_activation is not None
            else Path("logs/demo_activation_audit.jsonl")
        )
        self._demo_audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._audit_logger = AuditLogger(self._demo_audit_path)
        if not self._control_store_missing:
            try:
                self._refresh_control_state_sync()
            except LiveOrderRejected:
                # Async repositories cannot be loaded while an event loop is
                # running.  The async order path performs the same refresh.
                self._control_state = None

    @staticmethod
    def _run_sync(value: Any) -> Any:
        """Resolve a store result for synchronous callers without nesting loops."""
        if not inspect.isawaitable(value):
            return value
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(cast(Coroutine[Any, Any, Any], value))
        close = getattr(value, "close", None)
        if close is not None:
            close()
        raise LiveOrderRejected(
            "durable_state_unavailable",
            "durable execution-control state requires an async execution path",
        )

    async def _store_call_async(
        self, method_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        """Call either a synchronous test store or the async SQL repository."""
        if self._control_store_missing or self._control_store is None:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control store is unavailable",
            )
        method = getattr(self._control_store, method_name, None)
        if method is None:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control store is unavailable",
            )
        try:
            result = method(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        except LiveOrderRejected:
            raise
        except ConcurrencyConflict:
            raise
        except Exception as error:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control state is unavailable",
            ) from error

    def _store_call_sync(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Call a synchronous store and reject async stores in an active loop."""
        if self._control_store_missing or self._control_store is None:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control store is unavailable",
            )
        method = getattr(self._control_store, method_name, None)
        if method is None:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control store is unavailable",
            )
        try:
            return self._run_sync(method(*args, **kwargs))
        except LiveOrderRejected:
            raise
        except ConcurrencyConflict:
            raise
        except Exception as error:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control state is unavailable",
            ) from error

    def _apply_control_state(self, state: Any, *, initial: bool) -> None:
        """Apply persisted controls while keeping restart activation fail-closed."""
        if state is None:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control state is missing",
            )
        if getattr(state, "scope", self.control_scope) != self.control_scope:
            raise LiveOrderRejected(
                "durable_state_unavailable",
                "durable execution-control scope is invalid",
            )
        if getattr(state, "version", None) is None:
            state.version = 1
        self._control_state = state
        self._emergency_stop_active = bool(getattr(state, "emergency_stop", False))
        self._demo_trade_count = max(0, int(getattr(state, "demo_trade_count", 0) or 0))
        if getattr(state, "daily_loss", None) is not None:
            self._persisted_daily_loss = max(0.0, float(state.daily_loss))
        else:
            self._persisted_daily_loss = 0.0
        # A process restart must never silently re-enable demo execution or a
        # previous automation lease.  A subsequent refresh in this process is
        # allowed to observe operator-approved transitions.
        if initial:
            self._demo_active = bool(
                isinstance(self._control_store, _EphemeralExecutionControlStore)
                and self.config.demo_activation
                and self.config.demo_activation.enabled
                and not self.config.demo_activation.require_manual_confirmation
            )
            self._automation_authorized_until = 0.0
        else:
            self._demo_active = bool(getattr(state, "demo_active", False)) or bool(
                isinstance(self._control_store, _EphemeralExecutionControlStore)
                and self.config.demo_activation
                and self.config.demo_activation.enabled
                and not self.config.demo_activation.require_manual_confirmation
            )
            expires = getattr(state, "session_expires_at", None)
            self._automation_authorized_until = (
                expires.timestamp()
                if isinstance(expires, datetime)
                and expires.tzinfo is not None
                and expires.timestamp() > datetime.now(timezone.utc).timestamp()
                else 0.0
            )
            if (
                self._demo_active
                and self.config.demo_activation is not None
                and not isinstance(self._control_store, _EphemeralExecutionControlStore)
            ):
                owner = getattr(state, "demo_owner_approval", None)
                second = getattr(state, "demo_second_approval", None)
                selected = getattr(state, "demo_selected_symbols", None)
                limits = getattr(state, "demo_limits", None)
                config_hash = getattr(state, "demo_configuration_hash", None)
                if (
                    not owner
                    or not second
                    or owner == second
                    or not isinstance(expires, datetime)
                    or expires <= datetime.now(timezone.utc)
                    or not isinstance(selected, list)
                    or not selected
                    or not set(selected).issubset(self.config.allowed_symbols)
                    or not isinstance(limits, dict)
                    or not isinstance(config_hash, str)
                    or config_hash
                    != hashlib.sha256(
                        json.dumps(
                            {"symbols": tuple(sorted(selected)), "limits": limits},
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                ):
                    self._demo_active = False
                    raise LiveOrderRejected(
                        "activation_incomplete",
                        "durable Demo activation record is incomplete or expired",
                    )

    def _refresh_control_state_sync(self) -> None:
        """Refresh durable controls before a synchronous live operation."""
        state = self._store_call_sync("load_execution_control", self.control_scope)
        self._apply_control_state(state, initial=self._control_initial_load)
        self._control_initial_load = False

    async def _refresh_control_state_async(self) -> None:
        """Refresh durable controls before an async live operation."""
        state = await self._store_call_async(
            "load_execution_control", self.control_scope
        )
        self._apply_control_state(state, initial=self._control_initial_load)
        self._control_initial_load = False

    @staticmethod
    def _control_values(state: Any) -> dict[str, Any]:
        """Return the complete versioned control payload for a store update."""
        return {
            "emergency_stop": bool(getattr(state, "emergency_stop", False)),
            "emergency_stop_reason": getattr(state, "emergency_stop_reason", None),
            "demo_active": bool(getattr(state, "demo_active", False)),
            "session_expires_at": getattr(state, "session_expires_at", None),
            "demo_owner_approval": getattr(state, "demo_owner_approval", None),
            "demo_second_approval": getattr(state, "demo_second_approval", None),
            "demo_selected_symbols": getattr(state, "demo_selected_symbols", None),
            "demo_limits": getattr(state, "demo_limits", None),
            "demo_configuration_hash": getattr(state, "demo_configuration_hash", None),
            "demo_trade_count": int(getattr(state, "demo_trade_count", 0) or 0),
            "daily_loss": float(getattr(state, "daily_loss", 0.0) or 0.0),
            "actor": getattr(state, "actor", "system") or "system",
        }

    def _save_control_sync(self, **changes: Any) -> Any:
        """Persist one version-checked control transition synchronously."""
        state = self._control_state
        expected_version = int((getattr(state, "version", 1) or 1) if state else 0)
        values = (
            self._control_values(state)
            if state is not None
            else {
                "emergency_stop": False,
                "emergency_stop_reason": None,
                "demo_active": False,
                "session_expires_at": None,
                "demo_trade_count": self._demo_trade_count,
                "daily_loss": getattr(self, "_persisted_daily_loss", 0.0),
                "actor": "system",
            }
        )
        values.update(changes)
        try:
            saved = self._store_call_sync(
                "save_execution_control",
                self.control_scope,
                expected_version=expected_version,
                **values,
            )
        except ConcurrencyConflict as error:
            raise LiveOrderRejected(
                "execution_control_conflict",
                "durable execution-control state changed concurrently",
            ) from error
        self._apply_control_state(saved, initial=False)
        return saved

    async def _save_control_async(self, **changes: Any) -> Any:
        """Persist one version-checked control transition asynchronously."""
        state = self._control_state
        expected_version = int((getattr(state, "version", 1) or 1) if state else 0)
        values = (
            self._control_values(state)
            if state is not None
            else {
                "emergency_stop": False,
                "emergency_stop_reason": None,
                "demo_active": False,
                "session_expires_at": None,
                "demo_trade_count": self._demo_trade_count,
                "daily_loss": getattr(self, "_persisted_daily_loss", 0.0),
                "actor": "system",
            }
        )
        values.update(changes)
        try:
            saved = await self._store_call_async(
                "save_execution_control",
                self.control_scope,
                expected_version=expected_version,
                **values,
            )
        except ConcurrencyConflict as error:
            raise LiveOrderRejected(
                "execution_control_conflict",
                "durable execution-control state changed concurrently",
            ) from error
        self._apply_control_state(saved, initial=False)
        return saved

    def _prepare_control_sync(self) -> None:
        """Refresh and enforce the fail-closed durable gate."""
        if self._emergency_stop_active:
            raise LiveOrderRejected(
                "emergency_stop_active",
                "emergency stop is active; new live orders are blocked",
            )
        self._refresh_control_state_sync()
        if self._emergency_stop_active:
            raise LiveOrderRejected(
                "emergency_stop_active",
                "emergency stop is active; new live orders are blocked",
            )

    async def _prepare_control_async(self) -> None:
        """Async equivalent of :meth:`_prepare_control_sync`."""
        if self._emergency_stop_active:
            raise LiveOrderRejected(
                "emergency_stop_active",
                "emergency stop is active; new live orders are blocked",
            )
        await self._refresh_control_state_async()
        if self._emergency_stop_active:
            raise LiveOrderRejected(
                "emergency_stop_active",
                "emergency stop is active; new live orders are blocked",
            )

    def _reserve_control_for_order(self, daily_loss: float) -> None:
        """Reserve the order in durable state before calling the broker."""
        changes: dict[str, Any] = {
            "daily_loss": max(0.0, float(daily_loss)),
            "demo_active": self._demo_active,
            "demo_trade_count": self._demo_trade_count
            + (1 if self.config.demo_activation is not None else 0),
        }
        self._save_control_sync(**changes)
        self._control_trade_reserved = self.config.demo_activation is not None

    async def _reserve_control_for_order_async(self, daily_loss: float) -> None:
        """Async equivalent of :meth:`_reserve_control_for_order`."""
        changes: dict[str, Any] = {
            "daily_loss": max(0.0, float(daily_loss)),
            "demo_active": self._demo_active,
            "demo_trade_count": self._demo_trade_count
            + (1 if self.config.demo_activation is not None else 0),
        }
        await self._save_control_async(**changes)
        self._control_trade_reserved = self.config.demo_activation is not None

    @property
    def demo_active(self) -> bool:
        return self._demo_active

    @property
    def emergency_stop_active(self) -> bool:
        """Whether the independent emergency stop currently blocks demo orders."""
        return self._emergency_stop_active

    @property
    def demo_limits(self) -> DemoActivationConfig | None:
        return self.config.demo_activation

    @staticmethod
    def _order_comment(prefix: str, client_order_id: str | None) -> str:
        """Embed a durable order id in MT5's bounded comment field."""

        if not client_order_id:
            return prefix
        return f"{prefix}:{client_order_id}"[:31]

    def _record_demo_audit(self, event: str, status: str, **payload: Any) -> None:
        """Persist a structured JSONL audit record for demo activation decisions."""
        if self.config.demo_activation is None:
            return
        audit_payload = {
            "status": status,
            "demo_active": self._demo_active,
            "trade_count": self._demo_trade_count,
            **payload,
        }
        self._audit_logger.write(event, **audit_payload)

    def activate_demo(
        self,
        *,
        manual_confirmation: bool = False,
        owner_approval: str | None = None,
        second_approval: str | None = None,
        expires_at: datetime | None = None,
        selected_symbols: frozenset[str] | set[str] | list[str] | None = None,
        limits: dict[str, Any] | None = None,
        configuration_hash: str | None = None,
        reason: str = "manual_activation",
    ) -> None:
        """Enable Demo only with durable, independent two-person approval."""
        if self.config.demo_activation is None:
            raise LiveOrderRejected(
                "demo_not_configured", "demo activation is not configured"
            )
        if (
            self.config.demo_activation.require_manual_confirmation
            and not manual_confirmation
        ):
            raise LiveOrderRejected(
                "manual_confirmation_required",
                "manual operator confirmation is required before enabling demo trading",
            )
        symbols = tuple(
            sorted(
                {
                    item.strip().upper()
                    for item in (selected_symbols or [])
                    if item.strip()
                }
            )
        )
        if (
            not owner_approval
            or not second_approval
            or owner_approval == second_approval
        ):
            raise LiveOrderRejected(
                "activation_approvals_required",
                "distinct owner and second approvals are required",
            )
        if expires_at is None or expires_at <= datetime.now(timezone.utc):
            raise LiveOrderRejected(
                "activation_expired", "Demo activation expiry must be in the future"
            )
        if not symbols or not set(symbols).issubset(self.config.allowed_symbols):
            raise LiveOrderRejected(
                "activation_scope_invalid",
                "selected symbols must be a non-empty subset of the allowed symbols",
            )
        activation_limits = limits or {
            "max_trade_volume": self.config.demo_activation.max_trade_volume,
            "max_trades_per_session": self.config.demo_activation.max_trades_per_session,
            "max_daily_loss": self.config.demo_activation.max_daily_loss,
        }
        expected_hash = hashlib.sha256(
            json.dumps(
                {"symbols": symbols, "limits": activation_limits},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if configuration_hash != expected_hash:
            raise LiveOrderRejected(
                "activation_configuration_mismatch",
                "activation configuration hash does not match scope and limits",
            )
        self._demo_active = True
        self._save_control_sync(
            demo_active=True,
            session_expires_at=expires_at,
            demo_owner_approval=owner_approval,
            demo_second_approval=second_approval,
            demo_selected_symbols=list(symbols),
            demo_limits=activation_limits,
            demo_configuration_hash=configuration_hash,
            actor="operator",
        )
        self._record_demo_audit("demo_activated", "approved", reason=reason)

    def deactivate_demo(self, *, reason: str = "manual_stop") -> None:
        """Immediately disable automated demo execution and keep the audit trail."""
        self._demo_active = False
        self._save_control_sync(demo_active=False, actor="operator")
        self._record_demo_audit("demo_deactivated", "stopped", reason=reason)

    def emergency_stop(self, *, reason: str = "operator_emergency_stop") -> None:
        """Immediately block new demo orders and persist the stop decision."""
        self._emergency_stop_active = True
        self._demo_active = False
        self._automation_authorized_until = 0.0
        self._save_control_sync(
            emergency_stop=True,
            emergency_stop_reason=reason,
            demo_active=False,
            session_expires_at=None,
            actor="operator",
        )
        self._record_demo_audit("emergency_stop", "stopped", reason=reason)

    def reset_emergency_stop(
        self, *, manual_confirmation: bool, reason: str = "operator_reset"
    ) -> None:
        """Clear the emergency stop only after an explicit operator action."""
        if not manual_confirmation:
            raise LiveOrderRejected(
                "manual_confirmation_required",
                "manual operator confirmation is required before emergency reset",
            )
        if not reason.strip():
            raise ValueError("emergency reset reason is required")
        self._emergency_stop_active = False
        self._save_control_sync(
            emergency_stop=False,
            emergency_stop_reason=None,
            actor="operator",
        )
        self._record_demo_audit("emergency_stop_reset", "approved", reason=reason)

    def _apply_demo_gate(self, symbol: str, volume: float) -> None:
        """Enforce the limited demo constraints before the order leaves the gateway."""
        demo = self.config.demo_activation
        if demo is None:
            return
        if self._emergency_stop_active:
            raise LiveOrderRejected(
                "emergency_stop_active",
                "emergency stop is active; new demo orders are blocked",
            )
        if not self._demo_active:
            raise LiveOrderRejected(
                "demo_gate_inactive",
                "limited demo mode is not active; manual activation is required",
            )
        allowed_symbol = next(iter(self.config.allowed_symbols), "")
        if symbol.upper() != allowed_symbol:
            raise LiveOrderRejected(
                "demo_symbol_limit",
                f"controlled demo mode only allows {allowed_symbol}",
            )
        if volume <= 0 or volume > demo.max_trade_volume:
            raise LiveOrderRejected(
                "demo_volume_limit",
                f"requested volume {volume:g} exceeds the demo cap of {demo.max_trade_volume:g}",
            )
        if self._demo_trade_count >= demo.max_trades_per_session:
            raise LiveOrderRejected(
                "demo_trade_limit_exceeded",
                f"demo trade cap reached ({demo.max_trades_per_session} trades/session)",
            )
        if (
            self.config.max_daily_loss > 0
            and self._check_demo_daily_loss() >= demo.max_daily_loss
        ):
            raise LiveOrderRejected(
                "demo_daily_loss_limit",
                "daily loss circuit breaker is active in demo mode",
            )
        self._record_demo_audit(
            "demo_gate_passed",
            "allowed",
            symbol=symbol.upper(),
            volume=float(volume),
            trade_count=self._demo_trade_count,
        )

    def _check_demo_daily_loss(self) -> float:
        """Return the current cumulative daily loss for the demo session."""
        history = (
            self.connector.get_history(days=2) if self.connector is not None else None
        )
        if history is None or history.empty:
            return 0.0
        reset_at = (
            datetime.fromtimestamp(self.config.daily_loss_reset_at, tz=timezone.utc)
            if self.config.daily_loss_reset_at > 0
            else datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        )
        total = 0.0
        for row in history.to_dict("records"):
            stamp = row.get("time")
            if isinstance(stamp, datetime):
                timestamp = stamp
            else:
                try:
                    timestamp = datetime.fromisoformat(
                        str(stamp).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            else:
                timestamp = timestamp.astimezone(timezone.utc)
            if timestamp >= reset_at:
                total += float(row.get("profit", 0) or 0)
                total += float(row.get("commission", 0) or 0)
                total += float(row.get("swap", 0) or 0)
                total += float(row.get("fee", 0) or 0)
        return abs(total) if total < 0 else 0.0

    def _register_demo_trade(self, symbol: str, volume: float) -> None:
        """Increment trade counter for a successfully accepted demo execution."""
        demo = self.config.demo_activation
        if demo is None:
            return
        if not self._control_trade_reserved:
            self._demo_trade_count += 1
        self._record_demo_audit(
            "demo_trade_registered",
            "accepted",
            symbol=symbol.upper(),
            volume=float(volume),
            trade_count=self._demo_trade_count,
        )
        self._control_trade_reserved = False

    def request_confirmation(self, action: str) -> str:
        """Create a one-time confirmation code; this never sends an order."""
        token = secrets.token_urlsafe(18)
        self._pending_confirmation = (f"{action}:{token}", datetime.now().timestamp())
        logger.warning("Live order confirmation requested action=%s", action)
        return token

    def _consume_confirmation(self, token: str, action: str) -> None:
        pending = self._pending_confirmation
        self._pending_confirmation = None
        if not pending or not token:
            raise LiveOrderRejected(
                "confirmation_required", "explicit confirmation is required"
            )
        expected, created = pending
        if (
            expected != f"{action}:{token}"
            or datetime.now().timestamp() - created
            > self.config.confirmation_ttl_seconds
        ):
            raise LiveOrderRejected(
                "confirmation_invalid", "confirmation is invalid or expired"
            )

    def confirm_automation(self, token: str) -> None:
        """Consume a server-issued start token and authorize a short session."""
        self._consume_confirmation(token, "start_auto_trading")
        self._automation_authorized_until = (
            datetime.now(timezone.utc).timestamp()
            + self.config.automation_session_seconds
        )
        self._save_control_sync(
            session_expires_at=datetime.fromtimestamp(
                self._automation_authorized_until, tz=timezone.utc
            ),
            actor="operator",
        )
        logger.warning("automated trading confirmation accepted")

    def automation_enabled(self) -> bool:
        return datetime.now().timestamp() < self._automation_authorized_until

    def reset_daily_loss(self, reset_at: float | None = None) -> float:
        """Move the loss window forward and clear the active circuit-breaker window."""
        timestamp = (
            datetime.now(timezone.utc).timestamp()
            if reset_at is None
            else float(reset_at)
        )
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError(
                "daily loss reset timestamp must be finite and non-negative"
            )
        history = self.connector.get_history(days=2)
        if history is not None and not history.empty:
            self._ignored_daily_loss_deals = {
                self._deal_key(row)
                for row in history.to_dict("records")
                if self._deal_key(row)
            }
        self.config = replace(self.config, daily_loss_reset_at=timestamp)
        self._persisted_daily_loss = 0.0
        self._save_control_sync(daily_loss=0.0, actor="operator")
        logger.warning("daily loss circuit breaker reset at=%s", timestamp)
        return timestamp

    def restore_automation_authorization(self, expires_at: float) -> None:
        """Restore a still-valid dashboard lease without restoring a confirmation token."""
        now = datetime.now().timestamp()
        if not math.isfinite(float(expires_at)):
            raise LiveOrderRejected(
                "automation_lease_invalid", "automation lease is not finite"
            )
        if expires_at <= now:
            raise LiveOrderRejected(
                "automation_lease_expired", "automation lease has expired"
            )
        if expires_at > now + self.config.automation_session_seconds + 5:
            raise LiveOrderRejected(
                "automation_lease_invalid",
                "automation lease exceeds its configured duration",
            )
        self._automation_authorized_until = float(expires_at)
        self._save_control_sync(
            session_expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
            actor="operator",
        )
        logger.warning(
            "automated trading authorization restored from active dashboard lease"
        )

    def _require_connection(self) -> Any:
        if not self.connector or not self.connector.is_connected():
            raise LiveOrderRejected("mt5_disconnected", "MT5 is not connected")
        mt5 = getattr(self.connector, "_mt5", None)
        if mt5 is None:
            raise LiveOrderRejected(
                "mt5_unavailable", "MT5 trading adapter is unavailable"
            )
        return mt5

    def _validate_common(self, symbol: str, volume: float, magic: int) -> Any:
        mt5 = self._require_connection()
        symbol = symbol.upper()
        if not self.config.allowed_symbols or symbol not in self.config.allowed_symbols:
            raise LiveOrderRejected(
                "symbol_not_whitelisted", f"{symbol} is not whitelisted"
            )
        if magic != self.config.magic:
            raise LiveOrderRejected(
                "magic_mismatch", "magic number does not match configured value"
            )
        if volume <= 0 or volume > self.config.max_position_volume:
            raise LiveOrderRejected(
                "position_size_exceeded", "requested volume exceeds the live limit"
            )
        visible = {
            str(row.get("symbol") or row.get("name") or "").upper()
            for row in self.connector.get_symbols_list(visible_only=True)
        }
        if symbol not in visible:
            raise LiveOrderRejected(
                "symbol_not_in_market_watch", f"{symbol} is not visible in Market Watch"
            )
        return mt5

    def _resolve_market_watch_symbol(self, symbol: str) -> str:
        """Return the broker's exact Market Watch symbol spelling."""
        requested = symbol.upper()
        for row in self.connector.get_symbols_list(visible_only=True):
            candidate = str(row.get("symbol") or row.get("name") or "")
            if candidate.upper() == requested:
                return candidate
        raise LiveOrderRejected(
            "symbol_not_in_market_watch",
            f"{symbol} is not visible in Market Watch",
        )

    def _check_daily_loss(self) -> float:
        history = self.connector.get_history(days=2)
        if history is None or history.empty:
            return 0.0
        reset_at = (
            datetime.fromtimestamp(self.config.daily_loss_reset_at, tz=timezone.utc)
            if self.config.daily_loss_reset_at > 0
            else datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        )
        total = 0.0
        for row in history.to_dict("records"):
            if self._deal_key(row) in self._ignored_daily_loss_deals:
                continue
            stamp = row.get("time")
            if isinstance(stamp, datetime):
                timestamp = stamp
            else:
                try:
                    timestamp = datetime.fromisoformat(
                        str(stamp).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            else:
                timestamp = timestamp.astimezone(timezone.utc)
            if timestamp >= reset_at:
                total += float(row.get("profit", 0) or 0)
                total += float(row.get("commission", 0) or 0)
                total += float(row.get("swap", 0) or 0)
                total += float(row.get("fee", 0) or 0)
        if total <= -self.config.max_daily_loss:
            raise LiveOrderRejected(
                "daily_loss_limit", "daily loss circuit breaker is active"
            )
        return max(0.0, -total)

    @staticmethod
    def _deal_key(row: dict[str, Any]) -> str:
        """Return a stable identifier so a manual reset ignores existing deals only."""
        for field_name in ("ticket", "deal", "order", "position_id"):
            value = row.get(field_name)
            if value not in (None, "", 0):
                return f"{field_name}:{value}"
        return ""

    def _dynamic_spread_limit(self, symbol: str) -> float:
        """Calculate a fail-closed ATR-based spread limit from broker candles."""
        getter = getattr(self.connector, "get_historical_candles", None)
        if getter is None:
            getter = getattr(self.connector, "get_candles", None)
        if getter is None:
            raise LiveOrderRejected(
                "spread_unavailable", "ATR spread data is unavailable"
            )
        try:
            candles = getter(
                symbol,
                self.config.spread_atr_timeframe,
                max(100, self.config.spread_atr_period + 1),
            )
            if hasattr(candles, "to_dict"):
                candles = candles.reset_index().to_dict("records")
            values = calculate_atr(list(candles or []), self.config.spread_atr_period)
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as error:
            raise LiveOrderRejected(
                "spread_unavailable", "ATR spread data is invalid"
            ) from error
        atr = values[-1] if values else None
        if atr is None or not math.isfinite(float(atr)) or float(atr) <= 0:
            raise LiveOrderRejected(
                "spread_unavailable", "ATR spread value is unavailable"
            )
        return float(atr) * self.config.spread_atr_multiplier

    def spread_limit(self, symbol: str) -> float:
        """Return the currently effective spread limit for dashboard monitoring."""
        if self.config.spread_mode == "atr":
            return self._dynamic_spread_limit(symbol)
        return self.config.max_spread

    def _check_spread(self, symbol: str) -> None:
        limit = self.spread_limit(symbol)
        if limit <= 0:
            return
        info = self.connector.get_symbol_info(symbol)
        try:
            spread = float(info.get("spread", 0) or 0)
        except (TypeError, ValueError) as error:
            raise LiveOrderRejected(
                "spread_unavailable", "broker spread is invalid"
            ) from error
        if not math.isfinite(spread) or spread <= 0 or spread > limit:
            raise LiveOrderRejected(
                "spread_too_high",
                f"spread {spread:g} exceeds configured limit {limit:g}",
            )

    @staticmethod
    def _normalize_protection(
        mt5: Any,
        symbol: str,
        direction: str,
        tick: Any,
        stop_loss: float,
        take_profit: float,
    ) -> tuple[float, float]:
        """Place protection beyond the broker's live minimum distance.

        MT5 validates market BUY protection against bid/ask independently. Using
        the current spread and symbol stop/freeze levels prevents stale signal
        prices from producing retcode 10016.
        """
        info_getter = getattr(mt5, "symbol_info", None)
        info = info_getter(symbol) if info_getter else None
        point = float(getattr(info, "point", 0.00001) or 0.00001)
        digits = int(getattr(info, "digits", 5) or 5)
        stops_level = float(getattr(info, "trade_stops_level", 0) or 0)
        freeze_level = float(getattr(info, "trade_freeze_level", 0) or 0)
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        spread = ask - bid
        if point <= 0 or bid <= 0 or ask <= 0 or spread < 0:
            raise LiveOrderRejected(
                "protection_unavailable", "live symbol protection rules are unavailable"
            )
        minimum_distance = max(
            point,
            stops_level * point,
            freeze_level * point,
            spread * 1.10,
        )
        stop_loss = float(stop_loss or 0.0)
        take_profit = float(take_profit or 0.0)
        if stop_loss < 0 or take_profit < 0:
            raise LiveOrderRejected(
                "invalid_protection", "SL/TP must be zero or positive"
            )
        direction = direction.upper()
        if direction == "BUY":
            if stop_loss and stop_loss >= bid:
                raise LiveOrderRejected(
                    "invalid_stop_loss", "BUY stop loss must be below the bid price"
                )
            if take_profit and take_profit <= ask:
                raise LiveOrderRejected(
                    "invalid_take_profit", "BUY take profit must be above the ask price"
                )
            if stop_loss:
                stop_loss = min(stop_loss, bid - minimum_distance)
            if take_profit:
                take_profit = max(take_profit, ask + minimum_distance)
        elif direction == "SELL":
            if stop_loss and stop_loss <= ask:
                raise LiveOrderRejected(
                    "invalid_stop_loss", "SELL stop loss must be above the ask price"
                )
            if take_profit and take_profit >= bid:
                raise LiveOrderRejected(
                    "invalid_take_profit",
                    "SELL take profit must be below the bid price",
                )
            if stop_loss:
                stop_loss = max(stop_loss, ask + minimum_distance)
            if take_profit:
                take_profit = min(take_profit, bid - minimum_distance)
        else:
            raise LiveOrderRejected(
                "invalid_direction", "direction must be BUY or SELL"
            )
        return (
            round(stop_loss, digits) if stop_loss else 0.0,
            round(take_profit, digits) if take_profit else 0.0,
        )

    @staticmethod
    def _market_filling_mode(mt5: Any, symbol: str) -> int:
        """Select a filling mode supported by the broker for this symbol."""
        symbol_info = getattr(mt5, "symbol_info", None)
        info = symbol_info(symbol) if symbol_info else None
        supported = int(getattr(info, "filling_mode", 0) or 0) if info else 0
        for name, flag in (
            ("ORDER_FILLING_IOC", 2),
            ("ORDER_FILLING_FOK", 1),
            ("ORDER_FILLING_RETURN", 4),
        ):
            if supported & flag:
                return int(getattr(mt5, name, flag))
        return int(getattr(mt5, "ORDER_FILLING_IOC", 1))

    def execute_market_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        *,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        magic: int | None = None,
        confirmation_token: str = "",
        deviation: int = 20,
        client_order_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """Refresh durable controls, then execute one validated market order."""
        self._prepare_control_sync()
        return self._execute_market_order_unchecked(
            symbol,
            direction,
            volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            magic=magic,
            confirmation_token=confirmation_token,
            deviation=deviation,
            client_order_id=client_order_id,
            correlation_id=correlation_id,
        )

    async def execute_market_order_async(
        self,
        symbol: str,
        direction: str,
        volume: float,
        *,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        magic: int | None = None,
        confirmation_token: str = "",
        deviation: int = 20,
        client_order_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """Async API-boundary variant that can await the SQL execution store."""
        await self._prepare_control_async()
        return await asyncio.to_thread(
            self._execute_market_order_unchecked,
            symbol,
            direction,
            volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            magic=magic,
            confirmation_token=confirmation_token,
            deviation=deviation,
            client_order_id=client_order_id,
            correlation_id=correlation_id,
        )

    def _execute_market_order_unchecked(
        self,
        symbol: str,
        direction: str,
        volume: float,
        *,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        magic: int | None = None,
        confirmation_token: str = "",
        deviation: int = 20,
        client_order_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """Validate and send one market order after explicit confirmation."""
        operation_id = correlation_id or new_correlation_id()
        action = (
            f"open:{symbol.upper()}:{direction.upper()}:{volume:g}:"
            f"{float(stop_loss or 0):g}:{float(take_profit or 0):g}"
        )
        self._consume_confirmation(confirmation_token, action)
        magic = self.config.magic if magic is None else magic
        self._record_demo_audit(
            "market_order_attempt",
            "pending",
            symbol=symbol.upper(),
            direction=direction.upper(),
            volume=float(volume),
            magic=int(magic),
        )
        self._apply_demo_gate(symbol, float(volume))
        mt5 = self._validate_common(symbol, volume, magic)
        resolved_symbol = self._resolve_market_watch_symbol(symbol)
        daily_loss = self._check_daily_loss()
        self._check_spread(resolved_symbol)
        direction = direction.upper()
        if direction not in {"BUY", "SELL"}:
            raise LiveOrderRejected(
                "invalid_direction", "direction must be BUY or SELL"
            )
        tick = mt5.symbol_info_tick(resolved_symbol)
        if tick is None:
            raise LiveOrderRejected(
                "no_quote", f"no quote available for {resolved_symbol}"
            )
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        normalized_sl, normalized_tp = self._normalize_protection(
            mt5,
            resolved_symbol,
            direction,
            tick,
            stop_loss,
            take_profit,
        )
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": resolved_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(tick.ask if direction == "BUY" else tick.bid),
            "sl": normalized_sl,
            "tp": normalized_tp,
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": self._order_comment(
                "AI-Smart-Trader-confirmed", client_order_id
            ),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._market_filling_mode(mt5, resolved_symbol),
        }
        self._reserve_control_for_order(daily_loss)
        log_event(
            logger,
            logging.INFO,
            "order_submitted_for_validation",
            operation_id=operation_id,
            order_kind="market",
            symbol=resolved_symbol,
            direction=direction,
            volume=float(volume),
            stop_loss=normalized_sl,
            take_profit=normalized_tp,
        )
        try:
            result = self._send_checked(
                mt5, request, operation_id=operation_id, require_identifiers=True
            )
        except Exception:
            if (
                self.config.demo_activation is not None
                and self.config.demo_activation.auto_stop_on_error
            ):
                self.deactivate_demo(reason="order_failed")
            raise
        self._register_demo_trade(resolved_symbol, float(volume))
        return result

    def execute_pending_order(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: float,
        *,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        magic: int | None = None,
        confirmation_token: str = "",
        client_order_id: str | None = None,
    ) -> Any:
        """Refresh durable controls, then place one validated pending order."""
        self._prepare_control_sync()
        return self._execute_pending_order_unchecked(
            symbol,
            order_type,
            volume,
            price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            magic=magic,
            confirmation_token=confirmation_token,
            client_order_id=client_order_id,
        )

    async def execute_pending_order_async(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: float,
        *,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        magic: int | None = None,
        confirmation_token: str = "",
        client_order_id: str | None = None,
    ) -> Any:
        """Async API-boundary variant for pending orders."""
        await self._prepare_control_async()
        return await asyncio.to_thread(
            self._execute_pending_order_unchecked,
            symbol,
            order_type,
            volume,
            price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            magic=magic,
            confirmation_token=confirmation_token,
            client_order_id=client_order_id,
        )

    def _execute_pending_order_unchecked(
        self,
        symbol: str,
        order_type: str,
        volume: float,
        price: float,
        *,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        magic: int | None = None,
        confirmation_token: str = "",
        client_order_id: str | None = None,
    ) -> Any:
        """Validate and place one manually confirmed pending MT5 order."""
        operation_id = new_correlation_id()
        normalized_type = order_type.upper().replace(" ", "_")
        action = f"pending:{symbol.upper()}:{normalized_type}:{volume:g}:{price:g}"
        self._consume_confirmation(confirmation_token, action)
        magic = self.config.magic if magic is None else magic
        self._record_demo_audit(
            "pending_order_attempt",
            "pending",
            symbol=symbol.upper(),
            order_type=normalized_type,
            volume=float(volume),
            price=float(price),
            magic=int(magic),
        )
        self._apply_demo_gate(symbol, float(volume))
        mt5 = self._validate_common(symbol, volume, magic)
        resolved_symbol = self._resolve_market_watch_symbol(symbol)
        daily_loss = self._check_daily_loss()
        self._check_spread(resolved_symbol)
        type_names = {
            "BUY_LIMIT": "ORDER_TYPE_BUY_LIMIT",
            "SELL_LIMIT": "ORDER_TYPE_SELL_LIMIT",
            "BUY_STOP": "ORDER_TYPE_BUY_STOP",
            "SELL_STOP": "ORDER_TYPE_SELL_STOP",
        }
        if normalized_type not in type_names:
            raise LiveOrderRejected(
                "invalid_pending_type", "unsupported pending order type"
            )
        if price <= 0:
            raise LiveOrderRejected(
                "invalid_price", "pending order price must be positive"
            )
        tick = mt5.symbol_info_tick(resolved_symbol)
        if tick is None:
            raise LiveOrderRejected(
                "no_quote", f"no quote available for {resolved_symbol}"
            )
        if normalized_type == "BUY_LIMIT" and price >= float(tick.ask):
            raise LiveOrderRejected(
                "invalid_pending_price",
                "BUY LIMIT price must be below the current ask",
            )
        if normalized_type == "SELL_LIMIT" and price <= float(tick.bid):
            raise LiveOrderRejected(
                "invalid_pending_price",
                "SELL LIMIT price must be above the current bid",
            )
        if stop_loss < 0 or take_profit < 0:
            raise LiveOrderRejected(
                "invalid_protection", "SL/TP must be zero or positive"
            )
        if normalized_type in {"BUY_LIMIT", "BUY_STOP"}:
            if stop_loss and stop_loss >= price:
                raise LiveOrderRejected(
                    "invalid_stop_loss", "BUY stop loss must be below entry price"
                )
            if take_profit and take_profit <= price:
                raise LiveOrderRejected(
                    "invalid_take_profit", "BUY take profit must be above entry price"
                )
        else:
            if stop_loss and stop_loss <= price:
                raise LiveOrderRejected(
                    "invalid_stop_loss", "SELL stop loss must be above entry price"
                )
            if take_profit and take_profit >= price:
                raise LiveOrderRejected(
                    "invalid_take_profit", "SELL take profit must be below entry price"
                )
        mt5_type = getattr(mt5, type_names[normalized_type], None)
        if mt5_type is None:
            raise LiveOrderRejected(
                "unsupported_pending_type", "MT5 does not support this order type"
            )
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": resolved_symbol,
            "volume": float(volume),
            "type": mt5_type,
            "price": float(price),
            "sl": float(stop_loss or 0),
            "tp": float(take_profit or 0),
            "magic": int(magic),
            "comment": self._order_comment("AITraderPending", client_order_id),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": getattr(mt5, "ORDER_FILLING_RETURN", 2),
        }
        self._reserve_control_for_order(daily_loss)
        log_event(
            logger,
            logging.INFO,
            "order_submitted_for_validation",
            operation_id=operation_id,
            order_kind="pending",
            symbol=resolved_symbol,
            order_type=normalized_type,
            volume=float(volume),
            price=float(price),
            stop_loss=float(stop_loss or 0),
            take_profit=float(take_profit or 0),
        )
        try:
            result = self._send_checked(
                mt5, request, operation_id=operation_id, require_identifiers=True
            )
        except Exception:
            if (
                self.config.demo_activation is not None
                and self.config.demo_activation.auto_stop_on_error
            ):
                self.deactivate_demo(reason="pending_order_failed")
            raise
        self._register_demo_trade(resolved_symbol, float(volume))
        return result

    def close_all_positions(self, confirmation_token: str) -> list[Any]:
        """Close only positions that pass the configured magic-number gate."""
        action = "close_all"
        self._consume_confirmation(confirmation_token, action)
        mt5 = self._require_connection()
        positions = mt5.positions_get()
        if positions is None:
            raise LiveOrderRejected(
                "positions_unavailable", "MT5 positions could not be read"
            )
        results = []
        for position in positions:
            if int(getattr(position, "magic", -1)) != self.config.magic:
                logger.warning(
                    "Skipping position with unexpected magic ticket=%s",
                    getattr(position, "ticket", "?"),
                )
                continue
            symbol = str(position.symbol).upper()
            self._validate_common(symbol, float(position.volume), self.config.magic)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise LiveOrderRejected("no_quote", f"no quote available for {symbol}")
            is_buy = int(position.type) == int(mt5.POSITION_TYPE_BUY)
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(position.volume),
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "position": int(position.ticket),
                "price": float(tick.bid if is_buy else tick.ask),
                "deviation": 20,
                "magic": self.config.magic,
                "comment": "AI-Smart-Trader-confirmed-close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
            }
            results.append(self._send_checked(mt5, request))
        return results

    def close_position_partial(
        self, position: Any, percent: float, confirmation_token: str
    ) -> Any:
        """Safely close a percentage of one managed position."""
        if int(getattr(position, "magic", -1)) != self.config.magic:
            raise LiveOrderRejected(
                "magic_mismatch",
                "position magic number does not match configured value",
            )
        if not math.isfinite(float(percent)) or not 0 < float(percent) < 100:
            raise LiveOrderRejected(
                "invalid_partial_close_percent",
                "partial close percent must be between 0 and 100",
            )
        original_volume = float(getattr(position, "volume", 0.0) or 0.0)
        if original_volume <= 0:
            raise LiveOrderRejected(
                "invalid_position_volume", "position volume must be positive"
            )
        symbol = self._resolve_market_watch_symbol(str(position.symbol))
        mt5 = self._require_connection()
        if symbol not in self.config.allowed_symbols:
            raise LiveOrderRejected(
                "symbol_not_whitelisted", f"{symbol} is not whitelisted"
            )
        info = self.connector.get_symbol_info(symbol) or {}
        step = float(info.get("volume_step", 0.01) or 0.01)
        minimum = float(info.get("volume_min", step) or step)
        volume = math.floor(original_volume * float(percent) / 100 / step) * step
        volume = round(volume, 8)
        if volume < minimum or volume >= original_volume:
            raise LiveOrderRejected(
                "partial_close_volume_invalid",
                "partial close volume is below broker minimum",
            )
        action = f"partial_close:{symbol}:{int(position.ticket)}:{volume:g}"
        self._consume_confirmation(confirmation_token, action)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise LiveOrderRejected("no_quote", f"no quote available for {symbol}")
        is_buy = int(position.type) == int(getattr(mt5, "POSITION_TYPE_BUY", 0))
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": int(position.ticket),
            "price": float(tick.bid if is_buy else tick.ask),
            "deviation": 20,
            "magic": self.config.magic,
            "comment": "AI-Smart-Trader-partial-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
        }
        return self._send_checked(mt5, request)

    def modify_position_stop(
        self,
        position: Any,
        stop_loss: float,
        confirmation_token: str,
    ) -> Any:
        """Move an existing managed position's stop loss after all safety gates."""
        if int(getattr(position, "magic", -1)) != self.config.magic:
            raise LiveOrderRejected(
                "magic_mismatch",
                "position magic number does not match configured value",
            )
        if not math.isfinite(float(stop_loss)) or float(stop_loss) <= 0:
            raise LiveOrderRejected(
                "invalid_stop_loss", "stop loss must be a positive finite price"
            )
        action = (
            f"modify_sl:{str(position.symbol).upper()}:{int(position.ticket)}:"
            f"{float(stop_loss):g}"
        )
        self._consume_confirmation(confirmation_token, action)
        mt5 = self._require_connection()
        symbol = self._resolve_market_watch_symbol(str(position.symbol))
        self._validate_common(symbol, float(position.volume), self.config.magic)
        current_sl = float(getattr(position, "sl", 0.0) or 0.0)
        direction = (
            "BUY"
            if int(getattr(position, "type", -1))
            == int(getattr(mt5, "POSITION_TYPE_BUY", 0))
            else "SELL"
        )
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise LiveOrderRejected("no_quote", f"no quote available for {symbol}")
        market_price = float(tick.bid if direction == "BUY" else tick.ask)
        if direction == "BUY" and (
            stop_loss <= current_sl or stop_loss >= market_price
        ):
            raise LiveOrderRejected(
                "invalid_stop_update", "BUY stop update is not favourable"
            )
        if (
            direction == "SELL"
            and current_sl > 0
            and (stop_loss >= current_sl or stop_loss <= market_price)
        ):
            raise LiveOrderRejected(
                "invalid_stop_update", "SELL stop update is not favourable"
            )
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": int(position.ticket),
            "sl": float(stop_loss),
            "tp": float(getattr(position, "tp", 0.0) or 0.0),
            "magic": self.config.magic,
        }
        return self._send_checked(mt5, request, operation_id=new_correlation_id())

    def _send_checked(
        self,
        mt5: Any,
        request: dict[str, Any],
        *,
        operation_id: str = "",
        require_identifiers: bool = False,
    ) -> Any:
        operation_id = operation_id or new_correlation_id()
        check = mt5.order_check(request)
        check_code = getattr(check, "retcode", "none")
        check_comment = getattr(check, "comment", "")
        if check is None or int(check_code or 0) not in {0, 10009}:
            detail = f" ({check_code})"
            if check_comment:
                detail += f": {check_comment}"
            log_event(
                logger,
                logging.ERROR,
                "order_check_rejected",
                operation_id=operation_id,
                symbol=request.get("symbol"),
                retcode=check_code,
                reason=check_comment or "broker_validation_failed",
            )
            self._audit_logger.write(
                "order_rejected",
                correlation_id=operation_id,
                symbol=request.get("symbol"),
                reason="order_check_failed",
                retcode=check_code,
            )
            raise LiveOrderRejected(
                "order_check_failed",
                f"MT5 rejected the order during validation{detail}. "
                "Review price, SL/TP distance, volume, and symbol trading rules.",
            )
        result = mt5.order_send(request)
        if result is None:
            log_event(
                logger,
                logging.ERROR,
                "order_send_result_unknown",
                operation_id=operation_id,
                symbol=request.get("symbol"),
                broker_error=str(mt5.last_error()),
            )
            self._audit_logger.write(
                "order_outcome_unknown",
                correlation_id=operation_id,
                symbol=request.get("symbol"),
                reason="execution_result_unknown",
            )
            raise AmbiguousOrderOutcome(
                "execution_result_unknown",
                f"MT5 order_send outcome is unknown; no result was returned: "
                f"{mt5.last_error()}",
            )
        retcode_value = getattr(result, "retcode", None)
        try:
            if retcode_value is None:
                raise ValueError("missing retcode")
            retcode = int(retcode_value)
        except (TypeError, ValueError) as error:
            self._audit_logger.write(
                "order_outcome_unknown",
                correlation_id=operation_id,
                symbol=request.get("symbol"),
                reason="malformed_execution_result",
            )
            raise AmbiguousOrderOutcome(
                "execution_result_unknown",
                "MT5 order_send returned a malformed result",
            ) from error
        if retcode not in {10008, 10009, 10010}:
            comment = getattr(result, "comment", "")
            detail = f" ({retcode})"
            if comment:
                detail += f": {comment}"
            log_event(
                logger,
                logging.ERROR,
                "order_rejected",
                operation_id=operation_id,
                symbol=request.get("symbol"),
                retcode=retcode,
                reason=comment or "broker_rejected",
            )
            self._audit_logger.write(
                "order_rejected",
                correlation_id=operation_id,
                symbol=request.get("symbol"),
                reason=comment or "broker_rejected",
                retcode=retcode,
            )
            raise LiveOrderRejected(
                "order_rejected", f"MT5 rejected order{detail}", retcode=retcode
            )
        order_id = getattr(result, "order", None)
        deal_id = getattr(result, "deal", None)
        if require_identifiers:
            try:
                if (
                    not isinstance(order_id, int)
                    or not isinstance(deal_id, int)
                    or order_id <= 0
                    or deal_id <= 0
                ):
                    raise ValueError("broker identifiers must be positive")
            except ValueError as error:
                self._audit_logger.write(
                    "order_outcome_unknown",
                    correlation_id=operation_id,
                    symbol=request.get("symbol"),
                    reason="missing_broker_identifiers",
                    retcode=retcode,
                )
                raise AmbiguousOrderOutcome(
                    "execution_result_unknown",
                    "MT5 accepted the order but returned invalid broker identifiers",
                ) from error
        log_event(
            logger,
            logging.INFO,
            "order_accepted",
            operation_id=operation_id,
            ticket=order_id,
            symbol=request["symbol"],
            volume=request.get("volume"),
            direction=request.get("type"),
            stop_loss=request.get("sl", 0),
            take_profit=request.get("tp", 0),
            retcode=retcode,
        )
        self._audit_logger.write(
            "order_accepted",
            correlation_id=operation_id,
            symbol=request["symbol"],
            volume=request.get("volume"),
            retcode=retcode,
            ticket=order_id,
        )
        return result
