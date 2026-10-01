"""Persist read-only MetaTrader 5 account state and history."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import MetaTrader5 as mt5
from data.config import DataConfig
from data.database import AsyncDatabase


def _record_dict(record: Any) -> dict[str, Any]:
    """Convert an MT5 namedtuple into JSON-safe primitive values."""

    values = record._asdict() if hasattr(record, "_asdict") else vars(record)
    result: dict[str, Any] = {}
    for key, value in values.items():
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, datetime):
            value = value.astimezone(timezone.utc).isoformat()
        result[str(key)] = value
    return result


def _timestamp(value: Any, fallback: datetime | None = None) -> datetime:
    if value is None:
        return fallback or datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _side(value: Any) -> str:
    return (
        "buy"
        if int(value or 0)
        in {getattr(mt5, "ORDER_TYPE_BUY", 0), getattr(mt5, "DEAL_TYPE_BUY", 0)}
        else "sell"
    )


def _order_status(value: Any) -> str:
    mapping = {
        getattr(mt5, "ORDER_STATE_PLACED", -1): "accepted",
        getattr(mt5, "ORDER_STATE_PARTIAL", -2): "partial",
        getattr(mt5, "ORDER_STATE_FILLED", -3): "filled",
        getattr(mt5, "ORDER_STATE_CANCELED", -4): "cancelled",
        getattr(mt5, "ORDER_STATE_REJECTED", -5): "rejected",
        getattr(mt5, "ORDER_STATE_EXPIRED", -6): "expired",
    }
    return mapping.get(int(value or -1), "pending")


async def sync_account_database(
    database: AsyncDatabase,
    *,
    history_days: int = 30,
    account_module: Any = mt5,
) -> dict[str, int | str]:
    """Persist account snapshot, open positions, orders, and historical deals."""

    if history_days <= 0:
        raise ValueError("history_days must be greater than zero")
    account = account_module.account_info()
    if account is None:
        raise RuntimeError(f"MT5 account_info failed: {account_module.last_error()}")
    account_data = _record_dict(account)
    account_id = str(account_data["login"])
    snapshot_time = datetime.now(timezone.utc)
    await database.repository.insert_account_snapshot(
        {
            "account_id": account_id,
            "timestamp": snapshot_time,
            "balance": float(account_data.get("balance", 0)),
            "equity": float(account_data.get("equity", 0)),
            "margin": float(account_data.get("margin", 0)),
            "free_margin": float(account_data.get("margin_free", 0)),
            "currency": account_data.get("currency"),
            "status": "active",
            "payload": account_data,
        }
    )

    positions = account_module.positions_get() or ()
    position_rows = []
    position_ids: set[str] = set()
    for position in positions:
        data = _record_dict(position)
        position_id = str(data["ticket"])
        position_ids.add(position_id)
        position_rows.append(
            {
                "position_id": position_id,
                "account_id": account_id,
                "symbol": str(data["symbol"]),
                "side": _side(data.get("type")),
                "quantity": float(data.get("volume", 0)),
                "average_price": float(data.get("price_open", 0)),
                "unrealized_pnl": float(data.get("profit", 0)),
                "opened_at": _timestamp(data.get("time"), snapshot_time),
                "updated_at": snapshot_time,
                "payload": data,
            }
        )
    if position_rows:
        await database.repository.bulk_upsert_positions(position_rows)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=history_days)
    orders = list(account_module.orders_get() or ())
    history_orders = account_module.history_orders_get(start, end) or ()
    orders.extend(history_orders)
    order_rows = []
    order_ids: set[str] = set()
    for order in orders:
        data = _record_dict(order)
        if not data.get("symbol"):
            continue
        order_id = str(data["ticket"])
        order_ids.add(order_id)
        order_rows.append(
            {
                "order_id": order_id,
                "account_id": account_id,
                "symbol": str(data["symbol"]),
                "side": _side(data.get("type")),
                "quantity": float(
                    data.get("volume_current", data.get("volume_initial", 0))
                ),
                "price": data.get("price_open"),
                "stop_loss": data.get("sl"),
                "take_profit": data.get("tp"),
                "status": _order_status(data.get("state")),
                "created_at": _timestamp(
                    data.get("time_setup", data.get("time_done")), snapshot_time
                ),
                "updated_at": snapshot_time,
                "payload": data,
            }
        )
    if order_rows:
        await database.repository.bulk_upsert_orders(order_rows)

    deals = account_module.history_deals_get(start, end) or ()
    execution_rows = []
    for deal in deals:
        data = _record_dict(deal)
        if not data.get("symbol"):
            continue
        order_id = (
            str(data["order"])
            if data.get("order") and str(data["order"]) in order_ids
            else None
        )
        position_id = (
            str(data["position_id"])
            if data.get("position_id") and str(data["position_id"]) in position_ids
            else None
        )
        execution_rows.append(
            {
                "execution_id": str(data["ticket"]),
                "order_id": order_id,
                "position_id": position_id,
                "symbol": str(data["symbol"]),
                "timestamp": _timestamp(data.get("time"), snapshot_time),
                "side": _side(data.get("type")),
                "quantity": float(data.get("volume", 0)),
                "price": float(data.get("price", 0)),
                "fee": float(data.get("fee", 0)),
                "realized_pnl": float(data.get("profit", 0)),
                "payload": data,
            }
        )
    if execution_rows:
        await database.repository.bulk_insert_trade_executions(execution_rows)

    return {
        "account_id": account_id,
        "snapshot_count": 1,
        "positions_count": len(position_rows),
        "orders_count": len(order_rows),
        "deals_count": len(execution_rows),
    }


async def _run(history_days: int) -> None:
    config = DataConfig.from_env()
    database = AsyncDatabase(config.database_url)
    initialized = False
    try:
        await database.initialize(hypertables=True)
        initialized = True
        if not mt5.initialize(timeout=60_000):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        print(await sync_account_database(database, history_days=history_days))
    finally:
        if initialized:
            mt5.shutdown()
        await database.dispose()


def main() -> None:
    """Synchronize live account data into PostgreSQL."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-days", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(_run(args.history_days))


if __name__ == "__main__":
    main()
