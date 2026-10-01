"""Trend Following Strategy implementation."""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from . import BaseStrategy, Signal, StrategyConfig


class TrendFollowingStrategy(BaseStrategy):
    """
    Trend Following Strategy using moving averages and ADX.

    Enters long when fast MA crosses above slow MA and ADX shows strong trend.
    Enters short when fast MA crosses below slow MA and ADX shows strong trend.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="TrendFollowing",
                parameters={
                    "fast_ma_period": 20,
                    "slow_ma_period": 50,
                    "adx_period": 14,
                    "adx_threshold": 25,
                    "ma_type": "EMA",  # EMA or SMA
                },
            )
        super().__init__(config)

        self.fast_ma_period = int(self.config.parameters.get("fast_ma_period", 20))
        self.slow_ma_period = int(self.config.parameters.get("slow_ma_period", 50))
        self.adx_period = int(self.config.parameters.get("adx_period", 14))
        self.adx_threshold = float(self.config.parameters.get("adx_threshold", 25))
        self.ma_type = str(self.config.parameters.get("ma_type", "EMA"))

    def _initialize(self, historical_data: Optional[Dict[str, Any]] = None) -> None:
        """Initialize with historical data for indicator calculation."""
        if historical_data:
            self.logger.info(
                f"Initialized with {len(historical_data.get('candles', []))} candles"
            )

    def _calculate_ma(self, prices: np.ndarray, period: int) -> float:
        """Calculate moving average."""
        if len(prices) < period:
            return float(np.mean(prices))

        if self.ma_type == "EMA":
            # Exponential Moving Average
            multiplier = 2 / (period + 1)
            ema = np.zeros_like(prices)
            ema[0] = prices[0]
            for i in range(1, len(prices)):
                ema[i] = (prices[i] - ema[i - 1]) * multiplier + ema[i - 1]
            return float(ema[-1])
        else:
            # Simple Moving Average
            return float(np.mean(prices[-period:]))

    def _calculate_adx(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray
    ) -> float:
        """Calculate ADX (Average Directional Index)."""
        period = int(self.adx_period)

        if len(highs) < period + 1:
            return 0.0

        # Calculate True Range
        tr1 = highs[1:] - lows[1:]
        tr2 = abs(highs[1:] - closes[:-1])
        tr3 = abs(lows[1:] - closes[:-1])
        tr = np.maximum(np.maximum(tr1, tr2), tr3)

        # Calculate +DM and -DM
        plus_dm = np.where(
            (highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]),
            np.maximum(highs[1:] - highs[:-1], 0),
            0,
        )
        minus_dm = np.where(
            (lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]),
            np.maximum(lows[:-1] - lows[1:], 0),
            0,
        )

        # Smooth TR, +DM, -DM
        atr = np.mean(tr[-period:])
        atr_value = float(atr)
        plus_di = (
            100 * float(np.mean(plus_dm[-period:])) / atr_value if atr_value > 0 else 0
        )
        minus_di = (
            100 * float(np.mean(minus_dm[-period:])) / atr_value if atr_value > 0 else 0
        )

        # Calculate DX and ADX
        dx = (
            100 * abs(plus_di - minus_di) / (plus_di + minus_di)
            if (plus_di + minus_di) > 0
            else 0
        )
        adx = dx  # Simplified: using current DX as ADX approximation

        return float(adx)

    def generate_signal(
        self,
        data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        """Generate trading signal based on trend analysis."""

        if not self.validate_data(data):
            return Signal(
                symbol=data.get("symbol", "UNKNOWN"),
                direction="HOLD",
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "Invalid data"},
            )

        candles = data["candles"]
        if len(candles) < self.slow_ma_period:
            self.logger.warning(
                f"Insufficient data: {len(candles)} < {self.slow_ma_period}"
            )
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

        # Calculate MAs
        fast_ma = self._calculate_ma(closes, self.fast_ma_period)
        slow_ma = self._calculate_ma(closes, self.slow_ma_period)

        # Calculate ADX
        adx = self._calculate_adx(highs, lows, closes)

        # Current price
        current_price = closes[-1]

        # Determine signal
        direction = "HOLD"
        strength = 0.0
        reason = ""

        if adx >= self.adx_threshold:
            # Strong trend detected
            if fast_ma > slow_ma:
                direction = "BUY"
                strength = min(1.0, (fast_ma - slow_ma) / slow_ma * 100 + adx / 100)
                reason = f"Bullish crossover, ADX={adx:.2f}"
            elif fast_ma < slow_ma:
                direction = "SELL"
                strength = min(1.0, (slow_ma - fast_ma) / slow_ma * 100 + adx / 100)
                reason = f"Bearish crossover, ADX={adx:.2f}"
        else:
            reason = f"Weak trend (ADX={adx:.2f} < {self.adx_threshold})"

        # Calculate SL/TP for signal
        atr = np.mean(np.abs(highs[-14:] - lows[-14:])) if len(highs) >= 14 else 0
        stop_loss = (
            current_price - atr * 2 if direction == "BUY" else current_price + atr * 2
        )
        take_profit = (
            current_price + atr * 3 if direction == "BUY" else current_price - atr * 3
        )

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
                "fast_ma": round(fast_ma, 5),
                "slow_ma": round(slow_ma, 5),
                "adx": round(adx, 2),
                "reason": reason,
            },
        )
