from src.python.execution.position import PositionManager
from src.python.execution.trading import Order, OrderType


def test_pending_limit_is_allowed_when_symbol_has_open_position():
    manager = PositionManager()
    manager.add_position(
        {
            "symbol": "ZECUSD_l",
            "direction": "SELL",
            "volume": 0.01,
            "entry_price": 100.0,
        }
    )

    order = Order(
        symbol="ZECUSD_l",
        direction="BUY",
        order_type=OrderType.LIMIT,
        volume=0.01,
        price=99.0,
    )

    assert manager.can_open_new_position(
        order.symbol,
        order.volume,
        order.price or 0.0,
        order_type=order.order_type,
        direction=order.direction,
    )


def test_duplicate_pending_limit_is_blocked_with_specific_reason():
    manager = PositionManager()
    manager.pending_orders["existing-limit"] = Order(
        symbol="ZECUSD_l",
        direction="BUY",
        order_type=OrderType.LIMIT,
        volume=0.01,
        price=99.0,
    )

    order = Order(
        symbol="ZECUSD_l",
        direction="BUY",
        order_type=OrderType.LIMIT,
        volume=0.01,
        price=99.0,
    )

    assert not manager.can_open_new_position(
        order.symbol,
        order.volume,
        order.price or 0.0,
        order_type=order.order_type,
        direction=order.direction,
    )
    assert (
        manager.position_open_block_reason(
            order.symbol,
            order.volume,
            order.price or 0.0,
            order_type=order.order_type,
            direction=order.direction,
        )
        == "pending_order_already_exists"
    )
