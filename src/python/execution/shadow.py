"""Durable audit ledger for Stage E shadow trading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ShadowOrderRecord:
    """Reconstructed would-be order and its observed outcome."""

    order_id: str
    symbol: str
    direction: str
    volume: float
    requested_price: float | None
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    status: str
    created_at: str
    closed_at: str | None = None
    exit_price: float | None = None
    pnl: float | None = None
    exit_reason: str | None = None
    commission: float = 0.0


class ShadowOrderLedger:
    """Append-only, process-safe JSONL ledger for would-be orders.

    The ledger is intentionally independent from MT5. A successful write is
    the acceptance point for a shadow order, while later close events attach
    the first real market price that triggers its protection or exit rule.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        configured = (
            path
            if path is not None
            else os.getenv("SHADOW_LEDGER_PATH") or "data/shadow_orders.jsonl"
        )
        self.path = Path(configured)
        self._lock = Lock()
        self._records: dict[str, ShadowOrderRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"invalid shadow ledger JSON at line {line_number}"
                    ) from error
                self._apply_event(event)

    def _append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        descriptor = os.open(self.path, flags, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _apply_event(self, event: dict[str, Any]) -> None:
        order_id = str(event["order_id"])
        if event["event"] == "opened":
            self._records[order_id] = ShadowOrderRecord(
                order_id=order_id,
                symbol=str(event["symbol"]),
                direction=str(event["direction"]),
                volume=float(event["volume"]),
                requested_price=event.get("requested_price"),
                entry_price=float(event["entry_price"]),
                stop_loss=event.get("stop_loss"),
                take_profit=event.get("take_profit"),
                status="would_be_filled",
                created_at=str(event["created_at"]),
                commission=float(event.get("commission", 0.0)),
            )
        elif event["event"] == "closed" and order_id in self._records:
            record = self._records[order_id]
            self._records[order_id] = ShadowOrderRecord(
                **{
                    **record.__dict__,
                    "status": "evaluated",
                    "closed_at": str(event["closed_at"]),
                    "exit_price": float(event["exit_price"]),
                    "pnl": float(event["pnl"]),
                    "exit_reason": str(event["exit_reason"]),
                }
            )

    def record_open(self, order: Any, entry_price: float, commission: float) -> str:
        """Persist a would-be fill and return its stable identifier."""

        if entry_price <= 0:
            raise ValueError("shadow entry price must be positive")
        order_id = str(order.order_id or uuid4())
        now = datetime.now(timezone.utc).isoformat()
        event = {
            "event": "opened",
            "order_id": order_id,
            "symbol": order.symbol,
            "direction": order.direction,
            "volume": order.volume,
            "requested_price": order.price,
            "entry_price": entry_price,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
            "commission": commission,
            "created_at": now,
        }
        with self._lock:
            if order_id in self._records:
                raise ValueError(f"shadow order already exists: {order_id}")
            self._append(event)
            self._apply_event(event)
        return order_id

    def record_close(
        self, order_id: str, exit_price: float, pnl: float, exit_reason: str
    ) -> None:
        """Persist the first observed exit price for an open shadow order."""

        if exit_price <= 0:
            raise ValueError("shadow exit price must be positive")
        with self._lock:
            record = self._records.get(order_id)
            if record is None:
                raise KeyError(f"unknown shadow order: {order_id}")
            if record.status == "evaluated":
                raise ValueError(f"shadow order already evaluated: {order_id}")
            event = {
                "event": "closed",
                "order_id": order_id,
                "exit_price": exit_price,
                "pnl": pnl,
                "exit_reason": exit_reason,
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }
            self._append(event)
            self._apply_event(event)

    def get(self, order_id: str) -> ShadowOrderRecord | None:
        """Return one reconstructed order without exposing mutable state."""

        with self._lock:
            return self._records.get(order_id)

    def records(self) -> list[ShadowOrderRecord]:
        """Return all records in creation order."""

        with self._lock:
            return list(self._records.values())

    def summary(self) -> dict[str, float | int]:
        """Return deterministic performance and completeness metrics for review."""
        with self._lock:
            records = list(self._records.values())
        evaluated = [record for record in records if record.status == "evaluated"]
        pnls = [float(record.pnl or 0.0) for record in evaluated]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        gross_loss = abs(sum(losses))
        return {
            "total_orders": len(records),
            "evaluated_orders": len(evaluated),
            "open_orders": len(records) - len(evaluated),
            "winning_orders": len(wins),
            "losing_orders": len(losses),
            "win_rate": len(wins) / len(evaluated) if evaluated else 0.0,
            "total_pnl": sum(pnls),
            "profit_factor": sum(wins) / gross_loss
            if gross_loss
            else float("inf")
            if wins
            else 0.0,
            "max_drawdown": max_drawdown,
        }

    def validate_for_demo(
        self,
        *,
        audit_path: str | os.PathLike[str] | None = None,
        max_drawdown: float = 0.0,
    ) -> dict[str, Any]:
        """Return a fail-closed readiness report without touching MT5."""
        summary = self.summary()
        reasons: list[str] = []
        if summary["total_orders"] == 0:
            reasons.append("empty_shadow_ledger")
        if summary["open_orders"]:
            reasons.append("open_shadow_orders")
        if summary["evaluated_orders"] == 0:
            reasons.append("no_evaluated_shadow_orders")
        if float(summary["max_drawdown"]) > max_drawdown:
            reasons.append("shadow_drawdown_exceeded")
        if audit_path is not None:
            path = Path(audit_path)
            if not path.exists():
                reasons.append("missing_audit_log")
            else:
                try:
                    with path.open("r", encoding="utf-8") as stream:
                        for line_number, line in enumerate(stream, 1):
                            if not line.strip():
                                continue
                            event = json.loads(line)
                            if not isinstance(event, dict) or not event.get("event"):
                                raise ValueError
                            if any(
                                token in json.dumps(event).lower()
                                for token in ("password", "secret", "api_key", "token")
                            ):
                                reasons.append("sensitive_audit_data")
                                break
                except (OSError, json.JSONDecodeError, ValueError):
                    reasons.append("invalid_audit_log")
        return {"ready": not reasons, "reasons": reasons, "summary": summary}
