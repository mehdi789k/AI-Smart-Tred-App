"""Mean Reversion Strategy implementation."""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from . import BaseStrategy, Signal, StrategyConfig


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy using RSI and Bollinger Bands.

    Enters long when price is below lower BB and RSI is oversold.
    Enters short when price is above upper BB and RSI is overbought.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="MeanReversion",
                parameters={
                    "bb_period": 20,
                    "bb_std_dev": 2.0,
                    "rsi_period": 14,
                    "rsi_oversold": 30,
                    "rsi_overbought": 70,
                },
            )
        super().__init__(config)

        self.bb_period = int(self.config.parameters.get("bb_period", 20))
        self.bb_std_dev = float(self.config.parameters.get("bb_std_dev", 2.0))
        self.rsi_period = int(self.config.parameters.get("rsi_period", 14))
        self.rsi_oversold = float(self.config.parameters.get("rsi_oversold", 30))
        self.rsi_overbought = float(self.config.parameters.get("rsi_overbought", 70))

    def _initialize(self, historical_data: Optional[Dict[str, Any]] = None) -> None:
        """Initialize with historical data."""
        if historical_data:
            self.logger.info(
                f"Initialized with {len(historical_data.get('candles', []))} candles"
            )

    def _calculate_bollinger_bands(
        self, prices: np.ndarray
    ) -> tuple[float, float, float]:
        """Calculate Bollinger Bands (middle, upper, lower)."""
        if len(prices) < self.bb_period:
            mean = np.mean(prices)
            std = np.std(prices)
            return (
                float(mean),
                float(mean + self.bb_std_dev * std),
                float(mean - self.bb_std_dev * std),
            )

        recent_prices = prices[-self.bb_period :]
        middle = np.mean(recent_prices)
        std = np.std(recent_prices)
        upper = middle + self.bb_std_dev * std
        lower = middle - self.bb_std_dev * std

        return float(middle), float(upper), float(lower)

    def _calculate_rsi(self, prices: np.ndarray) -> float:
        """Calculate RSI (Relative Strength Index)."""
        if len(prices) < self.rsi_period + 1:
            return 50.0  # Neutral

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-self.rsi_period :])
        avg_loss = np.mean(losses[-self.rsi_period :])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi)

    def generate_signal(
        self,
        data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        """Generate trading signal based on mean reversion logic."""

        if not self.validate_data(data):
            return Signal(
                symbol=data.get("symbol", "UNKNOWN"),
                direction="HOLD",
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "Invalid data"},
            )

        candles = data["candles"]
        if len(candles) < max(self.bb_period, self.rsi_period + 1):
            self.logger.warning("Insufficient data for mean reversion analysis")
            return Signal(
                symbol=data["symbol"],
                direction="HOLD",
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "Insufficient data"},
            )

        # Extract OHLC data
        closes = np.array([c["close"] for c in candles])
        highs = np.array([c["high"] for c in candles])
        lows = np.array([c["low"] for c in candles])

        # Calculate indicators
        bb_middle, bb_upper, bb_lower = self._calculate_bollinger_bands(closes)
        rsi = self._calculate_rsi(closes)

        current_price = closes[-1]

        # Determine signal
        direction = "HOLD"
        strength = 0.0
        reason = ""

        # Check for oversold condition (BUY signal)
        if current_price <= bb_lower and rsi <= self.rsi_oversold:
            direction = "BUY"
            # Strength based on how far below BB and how oversold RSI is
            price_distance = (
                (bb_lower - current_price) / bb_lower if bb_lower > 0 else 0
            )
            rsi_distance = (self.rsi_oversold - rsi) / 100
            strength = min(1.0, 0.5 + price_distance + rsi_distance)
            reason = f"Price below lower BB, RSI={rsi:.2f} (oversold)"

        # Check for overbought condition (SELL signal)
        elif current_price >= bb_upper and rsi >= self.rsi_overbought:
            direction = "SELL"
            # Strength based on how far above BB and how overbought RSI is
            price_distance = (
                (current_price - bb_upper) / bb_upper if bb_upper > 0 else 0
            )
            rsi_distance = (rsi - self.rsi_overbought) / 100
            strength = min(1.0, 0.5 + price_distance + rsi_distance)
            reason = f"Price above upper BB, RSI={rsi:.2f} (overbought)"

        else:
            reason = f"No mean reversion signal (RSI={rsi:.2f}, Price vs BB: {current_price:.5f})"

        # Calculate SL/TP based on Bollinger Bands
        atr = (
            np.mean(np.abs(highs[-14:] - lows[-14:]))
            if len(highs) >= 14
            else (bb_upper - bb_lower) / 4
        )

        if direction == "BUY":
            stop_loss = current_price - atr * 1.5
            take_profit = bb_middle  # Target is the mean
        elif direction == "SELL":
            stop_loss = current_price + atr * 1.5
            take_profit = bb_middle  # Target is the mean
        else:
            stop_loss = current_price
            take_profit = current_price

        self.logger.info(
            f"{self.name}: {direction} | Strength: {strength:.2f} | {reason}"
        )

        return Signal(
            symbol=data["symbol"],
            direction=direction,
            strength=round(strength, 3),
            entry_price=current_price,
            stop_loss=round(stop_loss, 5),
            take_profit=round(take_profit, 5),
            strategy_name=self.name,
            metadata={
                "bb_upper": round(bb_upper, 5),
                "bb_middle": round(bb_middle, 5),
                "bb_lower": round(bb_lower, 5),
                "rsi": round(rsi, 2),
                "reason": reason,
            },
        )
