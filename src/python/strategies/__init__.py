"""Base strategy class for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from ..logging_config import get_logger
except ImportError:  # Support legacy PYTHONPATH=src/python entry points.
    from logging_config import get_logger

logger = get_logger("strategies.base")


@dataclass
class Signal:
    """Represents a trading signal."""

    symbol: str
    direction: str  # 'BUY', 'SELL', or 'HOLD'
    strength: float  # 0.0 to 1.0
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)
    strategy_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.direction not in ["BUY", "SELL", "HOLD"]:
            raise ValueError(f"Invalid direction: {self.direction}")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"Strength must be between 0 and 1, got {self.strength}")


@dataclass
class StrategyConfig:
    """Configuration for a strategy."""

    name: str
    enabled: bool = True
    risk_per_trade: float = 0.01  # 1% of account
    max_positions: int = 1
    timeframes: List[str] = field(default_factory=lambda: ["H1"])
    symbols: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.

    All strategies must inherit from this class and implement
    the generate_signal method.
    """

    def __init__(self, config: StrategyConfig):
        """
        Initialize the strategy.

        Args:
            config: Strategy configuration
        """
        self.config = config
        self.name = config.name
        self.enabled = config.enabled
        self.logger = get_logger(f"strategies.{self.name.lower()}")
        self._initialized = False

    @abstractmethod
    def generate_signal(
        self,
        data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        """
        Generate a trading signal based on market data.

        Args:
            data: Market data including OHLCV, indicators, etc.
            current_position: Current position info if any

        Returns:
            Signal object with trading recommendation
        """
        pass

    def initialize(self, historical_data: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize the strategy with historical data.

        Args:
            historical_data: Optional historical data for warm-up
        """
        self.logger.info(f"Initializing strategy: {self.name}")
        self._initialize(historical_data)
        self._initialized = True

    def _initialize(self, historical_data: Optional[Dict[str, Any]] = None) -> None:
        """Override this method for strategy-specific initialization."""
        pass

    def validate_data(self, data: Dict[str, Any]) -> bool:
        """Validate that required data is present."""
        required_fields = ["symbol", "timeframe", "candles"]
        for required_field in required_fields:
            if required_field not in data:
                self.logger.warning(f"Missing required field: {required_field}")
                return False
        return True

    def calculate_position_size(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss: float,
        direction: str,
    ) -> float:
        """
        Calculate position size based on risk parameters.

        Args:
            account_balance: Total account balance
            entry_price: Entry price
            stop_loss: Stop loss price
            direction: 'BUY' or 'SELL'

        Returns:
            Position size in lots/units
        """
        risk_amount = account_balance * self.config.risk_per_trade

        if direction == "BUY":
            risk_per_unit = entry_price - stop_loss
        else:
            risk_per_unit = stop_loss - entry_price

        if risk_per_unit <= 0:
            self.logger.warning("Invalid risk calculation")
            return 0.0

        position_size = risk_amount / risk_per_unit
        return round(position_size, 2)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, enabled={self.enabled})"


from .mean_reversion import MeanReversionStrategy  # noqa: E402
from .smc import SMCStrategy  # noqa: E402
from .trend_following import TrendFollowingStrategy  # noqa: E402

__all__ = [
    "BaseStrategy",
    "MeanReversionStrategy",
    "Signal",
    "SMCStrategy",
    "StrategyConfig",
    "TrendFollowingStrategy",
]
