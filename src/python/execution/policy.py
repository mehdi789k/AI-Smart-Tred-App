"""Shared execution policy defaults for the Python and MQL5 order boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Fail-closed defaults that must remain aligned with ``ExecutionPolicy.mqh``."""

    version: str = "1.0"
    magic_number: int = 26090901
    max_lot_size: float = 0.10
    daily_loss_limit: float = 100.0
    default_symbol: str = "XAUUSD"
    live_enabled: bool = False


DEFAULT_EXECUTION_POLICY = ExecutionPolicy()
