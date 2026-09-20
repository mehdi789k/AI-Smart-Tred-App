from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.python.execution.broker_reconciliation import (
    match_order_history,
    reconcile_unknown_orders,
)


def _order(order_id="order-1234"):
    return SimpleNamespace(
        order_id=order_id,
        symbol="EURUSD",
        quantity=1.0,
        side="buy",
        created_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    )


def test_matching_requires_one_unique_history_record():
    order = _order()
    record = SimpleNamespace(
        ticket=77,
        symbol="EURUSD",
        volume_initial=1.0,
        magic=42,
        comment="AI-Smart-Trader-confirmed:order-1",
        time_setup=1735732801,
        state="started",
    )
    deal = SimpleNamespace(ticket=88, order=77)

    result = match_order_history(
        order,
        [record],
        [deal],
        magic=42,
        now=datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc),
    )

    assert result is not None
    assert result.resolution == "accepted"
    assert result.broker_order_id == "77"
    assert result.broker_deal_id == "88"


def test_ambiguous_history_is_left_unresolved():
    order = _order()
    records = [
        SimpleNamespace(
            ticket=ticket,
            symbol="EURUSD",
            volume_initial=1.0,
            magic=42,
            comment="AI-Smart-Trader-confirmed",
            time_setup=1735732801,
            state="started",
        )
        for ticket in (77, 78)
    ]

    assert (
        match_order_history(
            order,
            records,
            [],
            magic=42,
            now=datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc),
        )
        is None
    )


@pytest.mark.asyncio
async def test_reconcile_service_only_resolves_unique_matches():
    repository = SimpleNamespace(
        get_unknown_orders=_unknown_orders,
        reconcile_unknown_order=_reconcile,
    )
    connector = SimpleNamespace(
        get_order_history=lambda start, end: [
            SimpleNamespace(
                ticket=77,
                symbol="EURUSD",
                volume_initial=1.0,
                magic=42,
                comment="AI-Smart-Trader-confirmed:order-1",
                time_setup=1735732801,
                state="started",
            )
        ],
        get_deal_history=lambda start, end: [],
    )

    result = await reconcile_unknown_orders(
        repository,
        connector,
        magic=42,
        now=datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc),
    )

    assert result["scanned"] == 1
    assert result["reconciled"] == 1


async def _unknown_orders(limit):
    return [_order()]


async def _reconcile(order_id, resolution, **kwargs):
    return {"order_id": order_id, "status": resolution, **kwargs}
