"""Circuit breaker facade for emergency trading stops."""

from __future__ import annotations

from typing import Any

from .manager import DEFAULT_CONSECUTIVE_LOSS_LIMIT, DailyLossFilter

try:
    from ..logging_config import get_correlation_id, get_logger, log_event
    from ..observability import AuditLogger, MetricsRegistry
except ImportError:  # pragma: no cover - compatibility for legacy top-level imports
    from logging_config import get_correlation_id, get_logger, log_event
    from observability import AuditLogger, MetricsRegistry


class CircuitBreaker:
    def __init__(
        self,
        base_capital: Any,
        consecutive_loss_limit: Any = DEFAULT_CONSECUTIVE_LOSS_LIMIT,
        max_daily_loss_percent: Any = 2.0,
        reset_interval_hours: Any = None,
        loss_reset_interval_hours: Any = None,
        *,
        metrics: MetricsRegistry | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.filter = DailyLossFilter(
            base_capital,
            consecutive_loss_limit,
            max_daily_loss_percent,
            reset_interval_hours,
            loss_reset_interval_hours,
        )
        self._manual_override = False
        self._last_result: dict[str, Any] = {
            "trading_allowed": True,
            "stop_reason": None,
        }
        self.metrics = metrics or MetricsRegistry()
        self.audit_logger = audit_logger
        self.logger = get_logger("circuit_breaker")

    @property
    def trading_allowed(self) -> bool:
        return bool(self._last_result["trading_allowed"])

    @property
    def is_tripped(self) -> bool:
        return not self.trading_allowed

    def set_manual_override(self, allowed: bool) -> None:
        if not isinstance(allowed, bool):
            raise ValueError("manual override must be a boolean")
        self._manual_override = allowed
        event = (
            "circuit_breaker_override_enabled"
            if allowed
            else "circuit_breaker_override_disabled"
        )
        log_event(self.logger, 30, event)
        if self.audit_logger:
            self.audit_logger.write(event, correlation_id=get_correlation_id())

    def evaluate(self, trades: Any, current_date: Any = None) -> dict[str, Any]:
        result = self.filter.evaluate(trades, current_date=current_date)
        if self._manual_override:
            result["trading_allowed"], result["stop_reason"] = True, None
        was_tripped = self.is_tripped
        if result["trading_allowed"]:
            self.metrics.set_gauge("circuit_breaker_tripped", 0)
        else:
            self.metrics.set_gauge("circuit_breaker_tripped", 1)
            if not was_tripped:
                self.metrics.inc("circuit_breaker_trips_total")
                log_event(
                    self.logger,
                    40,
                    "circuit_breaker_tripped",
                    reason=result["stop_reason"],
                )
                if self.audit_logger:
                    self.audit_logger.write(
                        "circuit_breaker_tripped",
                        reason=result["stop_reason"],
                        correlation_id=get_correlation_id(),
                    )
        self._last_result = result
        return result

    check = evaluate
