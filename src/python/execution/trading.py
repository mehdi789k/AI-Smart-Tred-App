"""Trading Execution module for automated trading."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ..logging_config import get_logger
from ..ml import Prediction
from ..strategies import Signal
from .shadow import ShadowOrderLedger

logger = get_logger("execution.trading")


class OrderType(Enum):
    """Order types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(Enum):
    """Order status."""

    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    """Represents a trading order."""

    symbol: str
    direction: str  # 'BUY' or 'SELL'
    order_type: OrderType
    volume: float
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_volume: float = 0.0
    order_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    strategy_name: str = ""
    signal_strength: float = 0.0
    ml_confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.direction not in ["BUY", "SELL"]:
            raise ValueError(f"Invalid direction: {self.direction}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert order to dictionary."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "order_type": self.order_type.value,
            "volume": self.volume,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "status": self.status.value,
            "filled_price": self.filled_price,
            "filled_volume": self.filled_volume,
            "order_id": self.order_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "strategy_name": self.strategy_name,
            "signal_strength": self.signal_strength,
            "ml_confidence": self.ml_confidence,
        }


@dataclass
class ExecutionReport:
    """Report after order execution."""

    order: Order
    success: bool
    message: str
    execution_time_ms: float
    slippage: float = 0.0
    commission: float = 0.0
    mt5_response: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "success": self.success,
            "message": self.message,
            "execution_time_ms": self.execution_time_ms,
            "slippage": self.slippage,
            "commission": self.commission,
            "order": self.order.to_dict(),
        }


class TradingExecution:
    """
    Handles order execution and trade management.

    This class is responsible for:
    - Converting signals to orders
    - Executing orders through MT5 or simulated environment
    - Tracking order status
    - Managing slippage and commissions
    """

    def __init__(
        self,
        mode: str = "simulated",
        default_slippage: float = 0.0001,
        default_commission: float = 0.02,
        mt5_connector: Optional[Any] = None,
        live_workflow: Optional[Any] = None,
        confirmation_token_provider: Optional[Any] = None,
        shadow_ledger: Optional[ShadowOrderLedger] = None,
        allowed_symbols: Optional[frozenset[str]] = None,
    ):
        """
        Initialize trading execution engine.

        Args:
            mode: 'simulated' or 'live'
            default_slippage: Default slippage rate (e.g., 0.0001 = 1 pip for EURUSD)
            default_commission: Commission per lot
            mt5_connector: MT5 connector instance for live trading
        """
        self.mode = mode
        self.default_slippage = default_slippage
        self.default_commission = default_commission
        self.mt5_connector = mt5_connector
        # Live sends must go through the fail-closed, manually-confirmed gate.
        self.live_workflow = live_workflow
        self.confirmation_token_provider = confirmation_token_provider
        self.shadow_ledger = shadow_ledger if mode == "shadow" else None
        self.allowed_symbols = (
            frozenset(symbol.upper() for symbol in allowed_symbols)
            if allowed_symbols is not None
            else None
        )
        if mode == "shadow" and self.shadow_ledger is None:
            self.shadow_ledger = ShadowOrderLedger()
        self.logger = get_logger("execution.trading")

        self.orders: List[Order] = []
        self.pending_orders: Dict[str, Order] = {}
        self.filled_orders: List[Order] = []
        self.execution_stats = {
            "total_orders": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "total_slippage": 0.0,
            "total_commission": 0.0,
        }

        self.logger.info(f"TradingExecution initialized in {mode} mode")

    def create_order_from_signal(
        self,
        signal: Signal,
        volume: float,
        ml_prediction: Optional[Prediction] = None,
    ) -> Optional[Order]:
        """
        Create an order from a trading signal.

        Args:
            signal: Trading signal from strategy
            volume: Position volume in lots
            ml_prediction: Optional ML prediction for confidence scoring

        Returns:
            Order object ready for execution
        """
        if signal.direction not in ["BUY", "SELL"]:
            self.logger.warning(f"Invalid signal direction: {signal.direction}")
            return None

        requested_type = str(
            signal.metadata.get(
                "execution_type", signal.metadata.get("order_type", "MARKET")
            )
        ).upper()
        order_type = (
            OrderType.LIMIT
            if requested_type in {"LIMIT", "BUY_LIMIT", "SELL_LIMIT"}
            else OrderType.MARKET
        )
        if order_type == OrderType.LIMIT and signal.entry_price is None:
            self.logger.warning(
                "Ignoring LIMIT execution request for %s because no entry price was supplied",
                signal.symbol,
            )
            order_type = OrderType.MARKET

        order = Order(
            symbol=signal.symbol,
            direction=signal.direction,
            order_type=order_type,
            volume=volume,
            price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            strategy_name=signal.strategy_name,
            signal_strength=signal.strength,
            ml_confidence=ml_prediction.confidence if ml_prediction else 0.0,
            metadata={
                "signal_timestamp": signal.timestamp.isoformat(),
                "signal_metadata": signal.metadata,
                "ml_prediction": ml_prediction.to_dict()
                if ml_prediction and hasattr(ml_prediction, "to_dict")
                else None,
            },
        )

        self.logger.info(
            f"Order created: {order.direction} {order.volume} {order.symbol} "
            f"@ {order.price} (SL: {order.stop_loss}, TP: {order.take_profit})"
        )

        return order

    def execute_order(
        self, order: Order, current_price: Optional[float] = None
    ) -> ExecutionReport:
        """
        Execute a trading order.

        Args:
            order: Order to execute
            current_price: Current market price (for simulation)

        Returns:
            ExecutionReport with results
        """
        start_time = time.time()
        self.execution_stats["total_orders"] += 1

        if (
            self.allowed_symbols is not None
            and order.symbol.upper() not in self.allowed_symbols
        ):
            order.status = OrderStatus.REJECTED
            self.execution_stats["failed_executions"] += 1
            return ExecutionReport(
                order=order,
                success=False,
                message=f"Symbol {order.symbol.upper()} is not allowed",
                execution_time_ms=0.0,
            )

        if self.mode == "simulated":
            report = self._execute_simulated(order, current_price)
        elif self.mode == "shadow":
            report = self._execute_shadow(order, current_price)
        elif self.mode == "live" and self.mt5_connector:
            report = self._execute_live(order)
        else:
            report = ExecutionReport(
                order=order,
                success=False,
                message="Live mode requires MT5 connector",
                execution_time_ms=0.0,
            )

        # Update statistics
        if report.success:
            self.execution_stats["successful_executions"] += 1
            if order.status == OrderStatus.FILLED:
                self.filled_orders.append(order)
        else:
            self.execution_stats["failed_executions"] += 1

        self.execution_stats["total_slippage"] += report.slippage
        self.execution_stats["total_commission"] += report.commission

        execution_time = (time.time() - start_time) * 1000
        report.execution_time_ms = round(execution_time, 2)

        self.logger.info(
            f"Order execution {'successful' if report.success else 'failed'}: "
            f"{order.symbol} {order.direction} {order.volume} - {report.message}"
        )

        return report

    def _execute_shadow(
        self, order: Order, current_price: Optional[float] = None
    ) -> ExecutionReport:
        """Record a would-be fill at the current real market price only."""

        if current_price is None or current_price <= 0:
            order.status = OrderStatus.REJECTED
            return ExecutionReport(
                order=order,
                success=False,
                message="Shadow execution requires a positive current market price",
                execution_time_ms=0.0,
            )
        ledger = self.shadow_ledger
        if ledger is None:
            raise RuntimeError("shadow ledger is unavailable")
        commission = self.default_commission * order.volume
        if order.metadata.get("action") == "close_position":
            parent_id = str(order.metadata.get("shadow_order_id", ""))
            if not parent_id:
                order.status = OrderStatus.REJECTED
                return ExecutionReport(
                    order=order,
                    success=False,
                    message="Shadow close requires the parent shadow order id",
                    execution_time_ms=0.0,
                )
            parent = ledger.get(parent_id)
            if parent is None:
                order.status = OrderStatus.REJECTED
                return ExecutionReport(
                    order=order,
                    success=False,
                    message="Shadow close references an unknown order",
                    execution_time_ms=0.0,
                )
            pnl = (
                (
                    (current_price - parent.entry_price) * parent.volume
                    if parent.direction == "BUY"
                    else (parent.entry_price - current_price) * parent.volume
                )
                - commission
                - parent.commission
            )
            ledger.record_close(
                parent_id,
                current_price,
                pnl,
                str(order.metadata.get("reason", "market_exit")),
            )
            order.order_id = str(uuid4())
        else:
            order.order_id = ledger.record_open(order, current_price, commission)
        order.status = OrderStatus.FILLED
        order.filled_price = current_price
        order.filled_volume = order.volume
        order.updated_at = datetime.now()
        return ExecutionReport(
            order=order,
            success=True,
            message=f"Shadow would-be fill @ {current_price:.5f}",
            execution_time_ms=0.0,
            commission=commission,
        )

    def _execute_simulated(
        self, order: Order, current_price: Optional[float] = None
    ) -> ExecutionReport:
        """Execute order in simulated mode."""

        if current_price is None:
            # Use entry price or generate simulated price
            current_price = order.price or 1.0

        # Simulate slippage
        import random

        slippage_rate = self.default_slippage * random.uniform(0.5, 1.5)

        if order.direction == "BUY":
            filled_price = current_price * (1 + slippage_rate)
        else:
            filled_price = current_price * (1 - slippage_rate)

        slippage = abs(filled_price - current_price)
        commission = self.default_commission * order.volume

        # Update order status
        order.status = OrderStatus.FILLED
        order.filled_price = filled_price
        order.filled_volume = order.volume
        order.updated_at = datetime.now()

        # Store in pending then move to filled
        self.pending_orders[order.symbol] = order

        return ExecutionReport(
            order=order,
            success=True,
            message=f"Simulated fill @ {filled_price:.5f}",
            execution_time_ms=0.0,
            slippage=slippage,
            commission=commission,
        )

    def _execute_live(self, order: Order) -> ExecutionReport:
        """Execute order through MT5."""

        if not self.mt5_connector or not self.live_workflow:
            return ExecutionReport(
                order=order,
                success=False,
                message="Live execution requires the confirmed risk-gated workflow",
                execution_time_ms=0.0,
            )

        try:
            token = order.metadata.get("confirmation_token", "")
            if not token and self.confirmation_token_provider:
                token = self.confirmation_token_provider(order)
            if order.order_type == OrderType.LIMIT:
                pending_type = f"{order.direction}_LIMIT"
                result = self.live_workflow.execute_pending_order(
                    order.symbol,
                    pending_type,
                    order.volume,
                    float(order.price or 0.0),
                    stop_loss=order.stop_loss or 0.0,
                    take_profit=order.take_profit or 0.0,
                    confirmation_token=token,
                    client_order_id=order.order_id,
                )
            else:
                result = self.live_workflow.execute_market_order(
                    order.symbol,
                    order.direction,
                    order.volume,
                    stop_loss=order.stop_loss or 0.0,
                    take_profit=order.take_profit or 0.0,
                    confirmation_token=token,
                    client_order_id=order.order_id,
                )

            if getattr(result, "retcode", None) in {10008, 10009, 10010}:
                is_pending = order.order_type == OrderType.LIMIT
                order.status = OrderStatus.PENDING if is_pending else OrderStatus.FILLED
                order.filled_price = (
                    None if is_pending else getattr(result, "price", order.price)
                )
                order.filled_volume = 0.0 if is_pending else order.volume
                order.order_id = str(getattr(result, "order", 0))
                order.updated_at = datetime.now()
                if is_pending:
                    self.pending_orders[order.order_id] = order

                return ExecutionReport(
                    order=order,
                    success=True,
                    message=(
                        f"Live pending order accepted @ {order.price}"
                        if is_pending
                        else f"Live fill @ {order.filled_price}"
                    ),
                    execution_time_ms=0.0,
                    mt5_response=result,
                )
            else:
                order.status = OrderStatus.REJECTED
                return ExecutionReport(
                    order=order,
                    success=False,
                    message=f"MT5 error: retcode {getattr(result, 'retcode', 'unknown')}",
                    execution_time_ms=0.0,
                    mt5_response=result,
                )

        except Exception as e:
            self.logger.error(f"Live execution error: {e}")
            order.status = OrderStatus.REJECTED
            return ExecutionReport(
                order=order,
                success=False,
                message=str(e),
                execution_time_ms=0.0,
            )

    def close_position(
        self,
        symbol: str,
        volume: float,
        direction: str,
        current_price: float,
    ) -> ExecutionReport:
        """
        Close an existing position.

        Args:
            symbol: Symbol to close
            volume: Volume to close
            direction: Opposite direction of original position
            current_price: Current market price

        Returns:
            ExecutionReport
        """
        close_order = Order(
            symbol=symbol,
            direction=direction,
            order_type=OrderType.MARKET,
            volume=volume,
            price=current_price,
            metadata={"action": "close_position"},
        )

        return self.execute_order(close_order, current_price)

    def get_execution_stats(self) -> Dict[str, Any]:
        """Get execution statistics."""
        stats = self.execution_stats.copy()
        stats["success_rate"] = (
            stats["successful_executions"] / stats["total_orders"]
            if stats["total_orders"] > 0
            else 0.0
        )
        stats["avg_slippage"] = (
            stats["total_slippage"] / stats["successful_executions"]
            if stats["successful_executions"] > 0
            else 0.0
        )
        return stats

    def cancel_pending_order(self, symbol: str) -> bool:
        """Cancel a pending order."""
        if symbol in self.pending_orders:
            order = self.pending_orders[symbol]
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
            del self.pending_orders[symbol]
            self.logger.info(f"Order cancelled: {symbol}")
            return True
        return False
