"""Execution module initialization."""

from .auto_trader import AutoTrader, TradingConfig, TradingCycle
from .automated_loop import LiveTradingLoop, ShadowTradingLoop
from .live_order_workflow import LiveOrderConfig, LiveOrderRejected, LiveOrderWorkflow
from .position import Position, PositionManager, TradeResult
from .scoring import ScoringSystem, SignalScore
from .shadow import ShadowOrderLedger, ShadowOrderRecord
from .trading import ExecutionReport, Order, OrderStatus, OrderType, TradingExecution

__all__ = [
    # Trading Execution
    "TradingExecution",
    "Order",
    "OrderType",
    "OrderStatus",
    "ExecutionReport",
    # Position Management
    "PositionManager",
    "Position",
    "TradeResult",
    # Scoring System
    "ScoringSystem",
    "SignalScore",
    # Auto Trader
    "AutoTrader",
    "TradingConfig",
    "TradingCycle",
    "LiveOrderWorkflow",
    "LiveOrderConfig",
    "LiveOrderRejected",
    "LiveTradingLoop",
    "ShadowTradingLoop",
    "ShadowOrderLedger",
    "ShadowOrderRecord",
]
