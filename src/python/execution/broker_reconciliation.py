"""Conservative reconciliation of ambiguous MT5 submissions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class BrokerMatch:
    """A unique broker record that provides evidence for one local order."""

    order_id: str
    resolution: str
    broker_order_id: str | None
    broker_deal_id: str | None
    evidence: dict[str, Any]


def _value(record: Any, name: str, default: Any = None) -> Any:
    """Read both MT5 namedtuples and test dictionaries."""

    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _timestamp(record: Any, *names: str) -> datetime | None:
    for name in names:
        value = _value(record, name)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
    return None


def match_order_history(
    order: Any,
    broker_orders: Iterable[Any],
    broker_deals: Iterable[Any],
    *,
    magic: int | None = None,
    now: datetime | None = None,
    window_seconds: int = 900,
) -> BrokerMatch | None:
    """Return a match only when broker evidence is unique and consistent.

    Missing or ambiguous evidence deliberately returns ``None`` so an
    operator can investigate without risking a duplicate live submission.
    """

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    created_at = _timestamp(order, "created_at")
    if created_at is None:
        return None
    lower = created_at - timedelta(seconds=window_seconds)
    upper = now + timedelta(seconds=30)
    symbol = str(_value(order, "symbol", "")).upper()
    quantity = float(_value(order, "quantity", 0) or 0)
    order_hint = str(_value(order, "order_id", ""))[:8]

    candidates: list[tuple[Any, int]] = []
    for record in broker_orders:
        record_symbol = str(_value(record, "symbol", "")).upper()
        record_time = _timestamp(record, "time_setup", "time_done", "time")
        record_volume = float(
            _value(record, "volume_initial", _value(record, "volume_current", 0)) or 0
        )
        comment = str(_value(record, "comment", ""))
        if record_symbol != symbol or record_time is None:
            continue
        if not lower <= record_time <= upper or abs(record_volume - quantity) > 1e-9:
            continue
        if magic is not None and int(_value(record, "magic", -1) or -1) != int(magic):
            continue
        score = 2 if order_hint and order_hint in comment else 0
        candidates.append((record, score))

    if not candidates:
        return None
    best_score = max(score for _, score in candidates)
    best = [record for record, score in candidates if score == best_score]
    if len(best) != 1:
        return None

    matched_order = best[0]
    order_ticket = _value(matched_order, "ticket")
    state = str(_value(matched_order, "state", "")).lower()
    rejected_states = {"rejected", "canceled", "cancelled", "expired"}
    resolution = (
        "rejected" if any(item in state for item in rejected_states) else "accepted"
    )

    matching_deals = [
        deal
        for deal in broker_deals
        if str(_value(deal, "order", "")) == str(order_ticket)
    ]
    deal_ticket = (
        _value(matching_deals[0], "ticket") if len(matching_deals) == 1 else None
    )
    return BrokerMatch(
        order_id=str(_value(order, "order_id")),
        resolution=resolution,
        broker_order_id=str(order_ticket) if order_ticket is not None else None,
        broker_deal_id=str(deal_ticket) if deal_ticket is not None else None,
        evidence={
            "source": "mt5_history",
            "broker_order": dict(matched_order._asdict())
            if hasattr(matched_order, "_asdict")
            else {"ticket": order_ticket, "state": state},
            "deal_count": len(matching_deals),
        },
    )


async def reconcile_unknown_orders(
    repository: Any,
    connector: Any,
    *,
    magic: int | None = None,
    limit: int = 100,
    lookback_seconds: int = 900,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve uniquely matched unknown orders using read-only MT5 history."""

    if limit < 1 or limit > 1_000:
        raise ValueError("limit must be between 1 and 1000")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    orders = await repository.get_unknown_orders(limit=limit)
    if not orders:
        return {"scanned": 0, "reconciled": 0, "unresolved": 0, "results": []}
    start = min(_timestamp(order, "created_at") or now for order in orders)
    start = min(start, now - timedelta(seconds=lookback_seconds))
    broker_orders = connector.get_order_history(start, now + timedelta(seconds=30))
    broker_deals = connector.get_deal_history(start, now + timedelta(seconds=30))
    results: list[dict[str, Any]] = []
    for order in orders:
        match = match_order_history(
            order,
            broker_orders,
            broker_deals,
            magic=magic,
            now=now,
            window_seconds=lookback_seconds,
        )
        if match is None:
            continue
        results.append(
            await repository.reconcile_unknown_order(
                match.order_id,
                match.resolution,
                evidence=match.evidence,
                broker_order_id=match.broker_order_id,
                broker_deal_id=match.broker_deal_id,
            )
        )
    return {
        "scanned": len(orders),
        "reconciled": len(results),
        "unresolved": len(orders) - len(results),
        "results": results,
    }
