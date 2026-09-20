"""Scoring system for evaluating trading signals and positions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..logging_config import get_logger
from ..ml import Prediction
from ..strategies import Signal

logger = get_logger("execution.scoring")


@dataclass
class SignalScore:
    """Score for a trading signal."""

    signal: Signal
    total_score: float
    strategy_score: float = 0.0
    ml_confidence_score: float = 0.0
    trend_alignment_score: float = 0.0
    risk_reward_score: float = 0.0
    volatility_score: float = 0.0
    time_score: float = 0.0
    intermarket_score: float = 0.0
    recommendation: str = "HOLD"  # 'STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL'
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert score to dictionary."""
        return {
            "symbol": self.signal.symbol,
            "direction": self.signal.direction,
            "total_score": round(self.total_score, 3),
            "strategy_score": round(self.strategy_score, 3),
            "ml_confidence_score": round(self.ml_confidence_score, 3),
            "trend_alignment_score": round(self.trend_alignment_score, 3),
            "risk_reward_score": round(self.risk_reward_score, 3),
            "volatility_score": round(self.volatility_score, 3),
            "time_score": round(self.time_score, 3),
            "intermarket_score": round(self.intermarket_score, 3),
            "recommendation": self.recommendation,
            "timestamp": self.signal.timestamp.isoformat(),
        }


class ScoringSystem:
    """
    Intelligent scoring system for trading signals.

    Combines multiple factors to generate a comprehensive score:
    - Strategy signal strength
    - ML model confidence
    - Trend alignment (multi-timeframe)
    - Risk/Reward ratio
    - Volatility conditions
    - Time of day
    - Intermarket correlations
    """

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
    ):
        """
        Initialize scoring system.

        Args:
            weights: Custom weights for scoring components
        """
        self.logger = get_logger("execution.scoring")

        # Default weights (must sum to 1.0)
        self.weights = weights or {
            "strategy": 0.25,
            "ml_confidence": 0.25,
            "trend_alignment": 0.15,
            "risk_reward": 0.15,
            "volatility": 0.10,
            "time": 0.05,
            "intermarket": 0.05,
        }

        # Normalize weights
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

        # Thresholds for recommendations
        self.thresholds = {
            "strong_buy": 0.85,
            "buy": 0.65,
            "hold_upper": 0.45,
            "hold_lower": 0.35,
            "sell": 0.15,
        }

        self.logger.info(f"ScoringSystem initialized with weights: {self.weights}")

    def calculate_signal_score(
        self,
        signal: Signal,
        ml_prediction: Optional[Prediction] = None,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> SignalScore:
        """
        Calculate comprehensive score for a trading signal.

        Args:
            signal: Trading signal from strategy
            ml_prediction: ML model prediction
            market_context: Additional market context

        Returns:
            SignalScore with detailed breakdown
        """
        scores = {}

        # 1. Strategy Score (from signal strength)
        scores["strategy"] = self._calculate_strategy_score(signal)

        # 2. ML Confidence Score
        scores["ml_confidence"] = self._calculate_ml_score(ml_prediction, signal)

        # 3. Trend Alignment Score
        scores["trend_alignment"] = self._calculate_trend_score(signal, market_context)

        # 4. Risk/Reward Score
        scores["risk_reward"] = self._calculate_risk_reward_score(signal)

        # 5. Volatility Score
        scores["volatility"] = self._calculate_volatility_score(signal, market_context)

        # 6. Time Score
        scores["time"] = self._calculate_time_score(signal)

        # 7. Intermarket Score
        scores["intermarket"] = self._calculate_intermarket_score(
            signal, market_context
        )

        # Calculate weighted total
        total_score = sum(
            scores.get(key, 0.0) * weight for key, weight in self.weights.items()
        )

        # Determine recommendation
        recommendation = self._get_recommendation(total_score, signal.direction)

        score = SignalScore(
            signal=signal,
            total_score=total_score,
            strategy_score=scores.get("strategy", 0.0),
            ml_confidence_score=scores.get("ml_confidence", 0.0),
            trend_alignment_score=scores.get("trend_alignment", 0.0),
            risk_reward_score=scores.get("risk_reward", 0.0),
            volatility_score=scores.get("volatility", 0.0),
            time_score=scores.get("time", 0.0),
            intermarket_score=scores.get("intermarket", 0.0),
            recommendation=recommendation,
            metadata={
                "component_scores": {k: round(v, 3) for k, v in scores.items()},
                "weights_used": self.weights,
            },
        )

        self.logger.info(
            f"Signal scored: {signal.symbol} {signal.direction} - "
            f"Total: {total_score:.3f}, Recommendation: {recommendation}"
        )

        return score

    def calculate_score(self, signal: Dict[str, Any]) -> float:
        """Return a scalar score for the compact dashboard signal payload."""
        if not isinstance(signal, dict):
            raise TypeError("signal must be a mapping")
        strength_value = signal.get("strength", signal.get("confidence", 0.0))
        normalized = Signal(
            symbol=str(signal.get("symbol", "UNKNOWN")),
            direction=str(signal.get("direction", "HOLD")).upper(),
            strength=float(strength_value if strength_value is not None else 0.0),
        )
        return self.calculate_signal_score(normalized).total_score

    def _calculate_strategy_score(self, signal: Signal) -> float:
        """Calculate score based on strategy signal strength."""
        # Direct mapping: strength (0-1) -> score (0-1)
        base_score = signal.strength

        # Bonus for clear direction
        if signal.direction in ["BUY", "SELL"] and signal.strength > 0.7:
            base_score += 0.1

        return min(1.0, base_score)

    def _calculate_ml_score(
        self,
        ml_prediction: Optional[Prediction],
        signal: Signal,
    ) -> float:
        """Calculate score based on ML prediction confidence."""
        if not ml_prediction:
            return 0.5  # Neutral if no ML prediction

        # Base confidence
        confidence = ml_prediction.confidence

        # Check if ML agrees with signal
        if ml_prediction.direction == signal.direction:
            agreement_bonus = 0.2
        elif ml_prediction.direction == "HOLD":
            agreement_bonus = 0.0
        else:
            agreement_bonus = -0.3  # Penalty for disagreement

        score = confidence + agreement_bonus
        return max(0.0, min(1.0, score))

    def _calculate_trend_score(
        self,
        signal: Signal,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Calculate score based on multi-timeframe trend alignment."""
        if not market_context:
            return 0.5  # Neutral

        trend_data = market_context.get("trend", {})

        # Check trend alignment across timeframes
        timeframes = trend_data.get("timeframes", {})
        if not timeframes:
            return 0.5

        aligned_count = 0
        total_count = len(timeframes)

        for tf, trend_direction in timeframes.items():
            if trend_direction == signal.direction:
                aligned_count += 1

        alignment_ratio = aligned_count / total_count if total_count > 0 else 0.5

        # Higher score for better alignment
        return alignment_ratio

    def _calculate_risk_reward_score(self, signal: Signal) -> float:
        """Calculate score based on risk/reward ratio."""
        if not signal.entry_price or not signal.stop_loss or not signal.take_profit:
            return 0.5  # Neutral if incomplete data

        if signal.direction == "BUY":
            risk = signal.entry_price - signal.stop_loss
            reward = signal.take_profit - signal.entry_price
        else:
            risk = signal.stop_loss - signal.entry_price
            reward = signal.entry_price - signal.take_profit

        if risk <= 0:
            return 0.0

        rr_ratio = reward / risk

        # Score based on RR ratio
        if rr_ratio >= 3.0:
            return 1.0
        elif rr_ratio >= 2.0:
            return 0.8
        elif rr_ratio >= 1.5:
            return 0.6
        elif rr_ratio >= 1.0:
            return 0.4
        else:
            return 0.2

    def _calculate_volatility_score(
        self,
        signal: Signal,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Calculate score based on volatility conditions."""
        if not market_context:
            return 0.5

        volatility_data = market_context.get("volatility", {})
        current_vol = volatility_data.get("current", 0)
        avg_vol = volatility_data.get("average", current_vol)

        if avg_vol == 0:
            return 0.5

        vol_ratio = current_vol / avg_vol

        # Optimal volatility is slightly above average
        if 1.0 <= vol_ratio <= 2.0:
            return 1.0
        elif 0.8 <= vol_ratio < 1.0:
            return 0.8
        elif 2.0 < vol_ratio <= 3.0:
            return 0.6
        else:
            return 0.4

    def _calculate_time_score(self, signal: Signal) -> float:
        """Calculate score based on time of day."""
        if not signal.timestamp:
            return 0.5

        hour = signal.timestamp.hour

        # Forex optimal hours (London/NY overlap)
        if 13 <= hour <= 17:  # 1 PM - 5 PM UTC
            return 1.0
        elif 8 <= hour < 13 or 17 <= hour < 20:  # London or NY session
            return 0.8
        elif 5 <= hour < 8 or 20 <= hour < 23:
            return 0.5
        else:  # Asian session or late night
            return 0.3

    def _calculate_intermarket_score(
        self,
        signal: Signal,
        market_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Calculate score based on intermarket correlations."""
        if not market_context:
            return 0.5

        intermarket_data = market_context.get("intermarket", {})

        if not intermarket_data:
            return 0.5

        # Check correlation alignment
        correlations = intermarket_data.get("correlations", {})
        aligned = 0
        total = len(correlations)

        for asset, corr_data in correlations.items():
            correlation = corr_data.get("value", 0)
            direction = corr_data.get("direction", "")

            if correlation > 0.5 and direction == signal.direction:
                aligned += 1
            elif correlation < -0.5 and direction != signal.direction:
                aligned += 1

        return aligned / total if total > 0 else 0.5

    def _get_recommendation(self, total_score: float, direction: str) -> str:
        """Get recommendation based on total score."""
        if direction == "BUY":
            if total_score >= self.thresholds["strong_buy"]:
                return "STRONG_BUY"
            elif total_score >= self.thresholds["buy"]:
                return "BUY"
            elif total_score >= self.thresholds["hold_upper"]:
                return "HOLD"
            elif total_score >= self.thresholds["hold_lower"]:
                return "WEAK_HOLD"
            else:
                return "SELL"
        elif direction == "SELL":
            if total_score >= self.thresholds["strong_buy"]:
                return "STRONG_SELL"
            elif total_score >= self.thresholds["buy"]:
                return "SELL"
            elif total_score >= self.thresholds["hold_upper"]:
                return "HOLD"
            elif total_score >= self.thresholds["hold_lower"]:
                return "WEAK_HOLD"
            else:
                return "BUY"
        else:
            return "HOLD"

    def filter_signals_by_score(
        self,
        signals: List[Signal],
        ml_predictions: Optional[Dict[str, Prediction]] = None,
        market_context: Optional[Dict[str, Any]] = None,
        min_score: float = 0.6,
    ) -> List[SignalScore]:
        """
        Filter and score multiple signals.

        Args:
            signals: List of signals to evaluate
            ml_predictions: ML predictions by symbol
            market_context: Market context
            min_score: Minimum score threshold

        Returns:
            List of SignalScore objects above threshold
        """
        scored_signals = []

        for signal in signals:
            ml_pred = ml_predictions.get(signal.symbol) if ml_predictions else None

            score = self.calculate_signal_score(signal, ml_pred, market_context)

            if score.total_score >= min_score:
                scored_signals.append(score)

        # Sort by total score descending
        scored_signals.sort(key=lambda x: x.total_score, reverse=True)

        return scored_signals
