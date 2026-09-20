"""SMC (Smart Money Concepts) Strategy implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import BaseStrategy, Signal, StrategyConfig


class SMCStrategy(BaseStrategy):
    """
    Smart Money Concepts Strategy.

    Uses Fair Value Gaps (FVG), Order Blocks, and Market Structure
    to identify high-probability entry points.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig(
                name="SMC",
                parameters={
                    "fvg_threshold": 0.0001,  # Minimum gap size
                    "lookback_period": 50,
                    "confirmation_candles": 2,
                },
            )
        super().__init__(config)

        self.fvg_threshold = self.config.parameters.get("fvg_threshold", 0.0001)
        self.lookback_period = self.config.parameters.get("lookback_period", 50)
        self.confirmation_candles = self.config.parameters.get(
            "confirmation_candles", 2
        )

    def _initialize(self, historical_data: Optional[Dict[str, Any]] = None) -> None:
        """Initialize with historical data."""
        if historical_data:
            self.logger.info(
                f"Initialized with {len(historical_data.get('candles', []))} candles"
            )

    def _find_fair_value_gaps(
        self, candles: List[Dict[str, float]]
    ) -> List[Dict[str, Any]]:
        """
        Identify Fair Value Gaps (FVG) in the price data.

        Bullish FVG: Low of candle i > High of candle i-2
        Bearish FVG: High of candle i < Low of candle i-2
        """
        fvg_list = []

        for i in range(2, len(candles)):
            current = candles[i]
            prev2 = candles[i - 2]

            # Bullish FVG
            if current["low"] > prev2["high"]:
                gap_size = current["low"] - prev2["high"]
                if gap_size >= self.fvg_threshold:
                    fvg_list.append(
                        {
                            "type": "BULLISH",
                            "index": i,
                            "top": current["low"],
                            "bottom": prev2["high"],
                            "size": gap_size,
                            "midpoint": (current["low"] + prev2["high"]) / 2,
                        }
                    )

            # Bearish FVG
            elif current["high"] < prev2["low"]:
                gap_size = prev2["low"] - current["high"]
                if gap_size >= self.fvg_threshold:
                    fvg_list.append(
                        {
                            "type": "BEARISH",
                            "index": i,
                            "top": prev2["low"],
                            "bottom": current["high"],
                            "size": gap_size,
                            "midpoint": (prev2["low"] + current["high"]) / 2,
                        }
                    )

        return fvg_list

    def _identify_order_blocks(
        self, candles: List[Dict[str, float]]
    ) -> List[Dict[str, Any]]:
        """
        Identify Order Blocks (last opposite candle before strong move).
        """
        order_blocks = []

        for i in range(3, len(candles)):
            # Bullish OB: Last bearish candle before strong bullish move
            if candles[i - 1]["close"] > candles[i - 1]["open"]:  # Current is bullish
                if (
                    candles[i - 2]["close"] < candles[i - 2]["open"]
                ):  # Previous is bearish
                    # Check if there's a strong move up
                    move_strength = (
                        candles[i - 1]["close"] - candles[i - 2]["close"]
                    ) / candles[i - 2]["close"]
                    if move_strength > 0.001:  # 0.1% move
                        order_blocks.append(
                            {
                                "type": "BULLISH",
                                "index": i - 2,
                                "high": candles[i - 2]["high"],
                                "low": candles[i - 2]["low"],
                                "open": candles[i - 2]["open"],
                                "close": candles[i - 2]["close"],
                            }
                        )

            # Bearish OB: Last bullish candle before strong bearish move
            elif candles[i - 1]["close"] < candles[i - 1]["open"]:  # Current is bearish
                if (
                    candles[i - 2]["close"] > candles[i - 2]["open"]
                ):  # Previous is bullish
                    move_strength = (
                        candles[i - 2]["close"] - candles[i - 1]["close"]
                    ) / candles[i - 2]["close"]
                    if move_strength > 0.001:
                        order_blocks.append(
                            {
                                "type": "BEARISH",
                                "index": i - 2,
                                "high": candles[i - 2]["high"],
                                "low": candles[i - 2]["low"],
                                "open": candles[i - 2]["open"],
                                "close": candles[i - 2]["close"],
                            }
                        )

        return order_blocks[-5:]  # Return last 5 order blocks

    def _detect_market_structure(
        self, candles: List[Dict[str, float]]
    ) -> Tuple[str, Optional[float], Optional[float]]:
        """
        Detect market structure (HH/HL for uptrend, LH/LL for downtrend).

        Returns: (trend, last_hh_hl or lh_ll, structure_level)
        """
        if len(candles) < 10:
            return "NEUTRAL", None, None

        # Find recent swing highs and lows
        swing_highs = []
        swing_lows = []

        for i in range(2, len(candles) - 2):
            # Swing high
            if (
                candles[i]["high"] > candles[i - 1]["high"]
                and candles[i]["high"] > candles[i - 2]["high"]
                and candles[i]["high"] > candles[i + 1]["high"]
                and candles[i]["high"] > candles[i + 2]["high"]
            ):
                swing_highs.append((i, candles[i]["high"]))

            # Swing low
            if (
                candles[i]["low"] < candles[i - 1]["low"]
                and candles[i]["low"] < candles[i - 2]["low"]
                and candles[i]["low"] < candles[i + 1]["low"]
                and candles[i]["low"] < candles[i + 2]["low"]
            ):
                swing_lows.append((i, candles[i]["low"]))

        if not swing_highs or not swing_lows:
            return "NEUTRAL", None, None

        # Analyze recent structure
        recent_highs = swing_highs[-3:]
        recent_lows = swing_lows[-3:]

        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            # Check for Higher Highs and Higher Lows (Uptrend)
            if (
                recent_highs[-1][1] > recent_highs[-2][1]
                and recent_lows[-1][1] > recent_lows[-2][1]
            ):
                return "UPTREND", recent_lows[-1][1], recent_lows[-1][1]

            # Check for Lower Highs and Lower Lows (Downtrend)
            elif (
                recent_highs[-1][1] < recent_highs[-2][1]
                and recent_lows[-1][1] < recent_lows[-2][1]
            ):
                return "DOWNTREND", recent_highs[-1][1], recent_highs[-1][1]

        return "NEUTRAL", None, None

    def generate_signal(
        self,
        data: Dict[str, Any],
        current_position: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        """Generate trading signal based on SMC analysis."""

        if not self.validate_data(data):
            return Signal(
                symbol=data.get("symbol", "UNKNOWN"),
                direction="HOLD",
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "Invalid data"},
            )

        candles = data["candles"]
        if len(candles) < max(self.lookback_period, 10):
            self.logger.warning("Insufficient data for SMC analysis")
            return Signal(
                symbol=data["symbol"],
                direction="HOLD",
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "Insufficient data"},
            )

        # Analyze SMC components
        fvgs = self._find_fair_value_gaps(candles[-self.lookback_period :])
        order_blocks = self._identify_order_blocks(candles[-self.lookback_period :])
        trend, structure_level, key_level = self._detect_market_structure(candles)

        current_price = candles[-1]["close"]

        # Determine signal based on confluence
        direction = "HOLD"
        strength = 0.0
        reason = ""

        # Look for bullish confluence
        bullish_fvgs = [f for f in fvgs if f["type"] == "BULLISH"]
        bullish_obs = [o for o in order_blocks if o["type"] == "BULLISH"]

        if bullish_fvgs and trend == "UPTREND":
            nearest_fvg = max(bullish_fvgs, key=lambda x: x["index"])
            if current_price <= nearest_fvg["top"] * 1.001:  # Price at or near FVG
                direction = "BUY"
                strength = min(
                    1.0, 0.4 + len(bullish_fvgs) * 0.1 + len(bullish_obs) * 0.1
                )
                reason = f"Bullish FVG + Uptrend, FVG size: {nearest_fvg['size']:.6f}"

        # Look for bearish confluence
        bearish_fvgs = [f for f in fvgs if f["type"] == "BEARISH"]
        bearish_obs = [o for o in order_blocks if o["type"] == "BEARISH"]

        if bearish_fvgs and trend == "DOWNTREND":
            nearest_fvg = max(bearish_fvgs, key=lambda x: x["index"])
            if current_price >= nearest_fvg["bottom"] * 0.999:  # Price at or near FVG
                direction = "SELL"
                strength = min(
                    1.0, 0.4 + len(bearish_fvgs) * 0.1 + len(bearish_obs) * 0.1
                )
                reason = f"Bearish FVG + Downtrend, FVG size: {nearest_fvg['size']:.6f}"

        if not reason:
            reason = f"No SMC confluence (Trend: {trend}, FVGs: {len(fvgs)}, OBs: {len(order_blocks)})"

        # Calculate SL/TP
        atr = (
            np.mean([candles[i]["high"] - candles[i]["low"] for i in range(-14, 0)])
            if len(candles) >= 14
            else 0
        )

        if direction == "BUY":
            stop_loss = current_price - atr * 2
            take_profit = current_price + atr * 3
        elif direction == "SELL":
            stop_loss = current_price + atr * 2
            take_profit = current_price - atr * 3
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
                "trend": trend,
                "fvg_count": len(fvgs),
                "ob_count": len(order_blocks),
                "bullish_fvgs": len(bullish_fvgs),
                "bearish_fvgs": len(bearish_fvgs),
                "reason": reason,
            },
        )
