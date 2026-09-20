"""Position Management module for tracking and managing trading positions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..logging_config import get_logger
from ..risk.break_even import BreakEvenConfig, BreakEvenDecision, apply_break_even
from .trading import Order

logger = get_logger("execution.position")


@dataclass
class Position:
    """Represents an open trading position."""

    symbol: str
    direction: str  # 'BUY' or 'SELL'
    volume: float
    entry_price: float
    current_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    strategy_name: str = ""
    order_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def update_price(self, price: float) -> None:
        """Update current price and recalculate PnL."""
        self.current_price = price
        self.updated_at = datetime.now()

        if self.direction == "BUY":
            self.unrealized_pnl = (price - self.entry_price) * self.volume
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.volume

    def to_dict(self) -> Dict[str, Any]:
        """Convert position to dictionary."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "opened_at": self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "strategy_name": self.strategy_name,
            "order_id": self.order_id,
        }


@dataclass
class TradeResult:
    """Result of a closed trade."""

    symbol: str
    direction: str
    volume: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_percent: float
    exit_reason: str  # 'TP', 'SL', 'MANUAL', 'STOPPED_OUT'
    opened_at: datetime
    closed_at: datetime
    strategy_name: str = ""
    duration_seconds: float = 0.0
    max_drawdown: float = 0.0
    max_profit: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert trade result to dictionary."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "exit_reason": self.exit_reason,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "strategy_name": self.strategy_name,
            "duration_seconds": self.duration_seconds,
            "max_drawdown": self.max_drawdown,
            "max_profit": self.max_profit,
        }


class PositionManager:
    """
    Manages all trading positions.

    Responsibilities:
    - Track open positions
    - Update PnL in real-time
    - Handle stop loss and take profit triggers
    - Close positions
    - Generate trade history
    """

    def __init__(self, account_balance: float = 10000.0):
        """
        Initialize position manager.

        Args:
            account_balance: Initial account balance
        """
        self.account_balance = account_balance
        self.logger = get_logger("execution.position")

        self.positions: Dict[str, Position] = {}
        self.trade_history: List[TradeResult] = []
        self.pending_orders: Dict[str, Order] = {}

        # Risk management settings
        self.max_positions = 5
        self.max_total_exposure = account_balance * 0.5  # 50% of balance
        self.daily_loss_limit = account_balance * 0.03  # 3% daily loss

        # Daily tracking
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.last_reset_date = datetime.now().date()

        self.logger.info(
            f"PositionManager initialized with balance: ${account_balance}"
        )

    def _check_daily_reset(self) -> None:
        """Reset daily counters if new day."""
        today = datetime.now().date()
        if today > self.last_reset_date:
            self.logger.info(
                f"Resetting daily counters. Previous daily PnL: ${self.daily_pnl:.2f}"
            )
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.last_reset_date = today

    def open_position(
        self,
        order: Order,
        filled_price: float,
    ) -> Optional[Position]:
        """
        Open a new position from a filled order.

        Args:
            order: Filled order
            filled_price: Actual fill price

        Returns:
            Position object or None if rejected
        """
        self._check_daily_reset()

        # Check if already have position for this symbol
        if order.symbol in self.positions:
            self.logger.warning(f"Position already exists for {order.symbol}")
            return None

        # Check max positions
        if len(self.positions) >= self.max_positions:
            self.logger.warning(f"Max positions ({self.max_positions}) reached")
            return None

        # Check daily loss limit
        if self.daily_pnl <= -self.daily_loss_limit:
            self.logger.error(f"Daily loss limit reached: ${self.daily_pnl:.2f}")
            return None

        # Create position
        position = Position(
            symbol=order.symbol,
            direction=order.direction,
            volume=order.volume,
            entry_price=filled_price,
            current_price=filled_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            strategy_name=order.strategy_name,
            order_id=order.order_id,
            metadata=order.metadata,
        )

        self.positions[order.symbol] = position
        self.logger.info(
            f"Position opened: {position.direction} {position.volume} {position.symbol} "
            f"@ {position.entry_price}"
        )

        return position

    def add_position(self, position: Dict[str, Any]) -> Position:
        """Import a broker-shaped position for dashboard and compatibility flows."""
        if not isinstance(position, dict):
            raise TypeError("position must be a mapping")
        symbol = str(position.get("symbol", "")).strip().upper()
        if not symbol:
            raise ValueError("position symbol is required")
        direction = str(position.get("direction", position.get("type", ""))).upper()
        if direction not in {"BUY", "SELL"}:
            raise ValueError("position type must be BUY or SELL")
        entry_price = float(position["entry_price"])
        imported = Position(
            symbol=symbol,
            direction=direction,
            volume=float(position["volume"]),
            entry_price=entry_price,
            current_price=float(position.get("current_price", entry_price)),
            stop_loss=position.get("stop_loss", position.get("sl")),
            take_profit=position.get("take_profit", position.get("tp")),
            order_id=str(position.get("ticket"))
            if position.get("ticket") is not None
            else None,
            metadata={"ticket": position.get("ticket")} if "ticket" in position else {},
        )
        self.positions[symbol] = imported
        return imported

    def get_all_positions(self) -> List[Dict[str, Any]]:
        """Return open positions in the legacy dashboard payload shape."""
        result: List[Dict[str, Any]] = []
        for position in self.positions.values():
            payload = position.to_dict()
            payload["ticket"] = position.metadata.get("ticket", position.order_id)
            payload["type"] = position.direction
            payload["sl"] = position.stop_loss
            payload["tp"] = position.take_profit
            result.append(payload)
        return result

    def update_prices(self, prices: Dict[str, float]) -> None:
        """
        Update prices for all open positions.

        Args:
            prices: Dictionary of symbol -> current price
        """
        for symbol, price in prices.items():
            if symbol in self.positions:
                position = self.positions[symbol]
                position.update_price(price)

                # Check SL/TP triggers
                trigger = self._check_stop_loss_take_profit(position, price)
                if trigger:
                    self.logger.info(f"{trigger} triggered for {symbol}")

    def apply_break_even(
        self,
        symbol: str,
        config: BreakEvenConfig | None = None,
        *,
        current_price: float | None = None,
    ) -> BreakEvenDecision:
        """Move a local/simulated position's stop only when all BE gates pass."""
        position = self.positions.get(symbol)
        if position is None:
            return BreakEvenDecision(False, reason="position_not_found")
        return apply_break_even(position, current_price, config)

    def _check_stop_loss_take_profit(
        self,
        position: Position,
        current_price: float,
    ) -> Optional[str]:
        """Check if SL or TP is hit."""
        if position.direction == "BUY":
            if position.stop_loss and current_price <= position.stop_loss:
                return "SL"
            if position.take_profit and current_price >= position.take_profit:
                return "TP"
        else:  # SELL
            if position.stop_loss and current_price >= position.stop_loss:
                return "SL"
            if position.take_profit and current_price <= position.take_profit:
                return "TP"
        return None

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "MANUAL",
    ) -> Optional[TradeResult]:
        """
        Close an existing position.

        Args:
            symbol: Symbol to close
            exit_price: Exit price
            exit_reason: Reason for closing

        Returns:
            TradeResult or None if no position found
        """
        if symbol not in self.positions:
            self.logger.warning(f"No position found for {symbol}")
            return None

        position = self.positions[symbol]

        # Calculate PnL
        if position.direction == "BUY":
            pnl = (exit_price - position.entry_price) * position.volume
        else:
            pnl = (position.entry_price - exit_price) * position.volume

        pnl_percent = (
            (pnl / (position.entry_price * position.volume)) * 100
            if position.entry_price > 0
            else 0
        )

        # Calculate duration
        duration = (datetime.now() - position.opened_at).total_seconds()

        # Create trade result
        trade_result = TradeResult(
            symbol=symbol,
            direction=position.direction,
            volume=position.volume,
            entry_price=position.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_percent=pnl_percent,
            exit_reason=exit_reason,
            opened_at=position.opened_at,
            closed_at=datetime.now(),
            strategy_name=position.strategy_name,
            duration_seconds=duration,
            max_drawdown=position.metadata.get("max_drawdown", 0.0),
            max_profit=position.metadata.get("max_profit", 0.0),
            metadata=position.metadata,
        )

        # Update daily PnL
        self.daily_pnl += pnl
        self.daily_trades += 1

        # Add to history
        self.trade_history.append(trade_result)

        # Remove from open positions
        del self.positions[symbol]

        self.logger.info(
            f"Position closed: {symbol} {exit_reason} - PnL: ${pnl:.2f} ({pnl_percent:.2f}%)"
        )

        return trade_result

    def reconcile_external_positions(
        self,
        open_symbols: set[str],
        prices: Dict[str, float],
    ) -> int:
        """Remove local positions that no longer exist at the broker."""
        closed_count = 0
        for symbol in list(self.positions):
            if symbol.upper() in open_symbols:
                continue
            position = self.positions[symbol]
            exit_price = prices.get(symbol, position.current_price)
            if not exit_price:
                continue
            if self.close_position(symbol, exit_price, "BROKER_CLOSED"):
                closed_count += 1
        return closed_count

    def get_total_exposure(self) -> float:
        """Calculate total exposure across all positions."""
        total = sum(pos.volume * pos.current_price for pos in self.positions.values())
        return total

    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized PnL."""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_position_stats(self) -> Dict[str, Any]:
        """Get position statistics."""
        if not self.positions:
            return {
                "open_positions": 0,
                "total_exposure": 0.0,
                "unrealized_pnl": 0.0,
            }

        return {
            "open_positions": len(self.positions),
            "total_exposure": self.get_total_exposure(),
            "unrealized_pnl": self.get_total_unrealized_pnl(),
            "positions": [pos.to_dict() for pos in self.positions.values()],
        }

    def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trade history."""
        sorted_history = sorted(
            self.trade_history, key=lambda x: x.closed_at, reverse=True
        )
        return [trade.to_dict() for trade in sorted_history[:limit]]

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Calculate performance metrics from trade history."""
        if not self.trade_history:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
            }

        wins = [t for t in self.trade_history if t.pnl > 0]
        losses = [t for t in self.trade_history if t.pnl < 0]

        total_pnl = sum(t.pnl for t in self.trade_history)
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "total_trades": len(self.trade_history),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(self.trade_history)
            if self.trade_history
            else 0,
            "total_pnl": total_pnl,
            "avg_win": gross_profit / len(wins) if wins else 0,
            "avg_loss": gross_loss / len(losses) if losses else 0,
            "profit_factor": profit_factor,
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
        }

    def can_open_new_position(self, symbol: str, volume: float, price: float) -> bool:
        """Check if a new position can be opened."""
        self._check_daily_reset()

        # Check if position already exists
        if symbol in self.positions:
            return False

        # Check max positions
        if len(self.positions) >= self.max_positions:
            return False

        # Check daily loss limit
        if self.daily_pnl <= -self.daily_loss_limit:
            return False

        # Check total exposure
        new_exposure = volume * price
        if self.get_total_exposure() + new_exposure > self.max_total_exposure:
            return False

        return True

    def position_open_block_reason(
        self, symbol: str, volume: float, price: float
    ) -> Optional[str]:
        """Explain why a proposed position cannot be opened."""
        self._check_daily_reset()
        if symbol in self.positions:
            return "position_already_open_for_symbol"
        if len(self.positions) >= self.max_positions:
            return "max_positions_reached"
        if self.daily_pnl <= -self.daily_loss_limit:
            return "daily_loss_limit"
        if self.get_total_exposure() + volume * price > self.max_total_exposure:
            return "max_total_exposure"
        return None
