"""Automated Trading System - Main orchestrator for AI-powered trading."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ..logging_config import get_logger
from ..ml import MLConfig, MLInferenceService, Prediction
from ..risk.break_even import BreakEvenConfig
from ..strategies import BaseStrategy, Signal
from .position import PositionManager, TradeResult
from .scoring import ScoringSystem, SignalScore
from .shadow import ShadowOrderLedger
from .trading import Order, OrderType, TradingExecution

logger = get_logger("execution.auto_trader")


@dataclass
class TradingConfig:
    """Configuration for automated trading."""

    account_balance: float = 10000.0
    risk_per_trade: float = 0.01  # 1% of balance
    max_positions: int = 3
    daily_loss_limit: float = 0.03  # 3% of balance
    min_signal_score: float = 0.65
    enabled_strategies: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=lambda: ["EURUSD"])
    timeframes: List[str] = field(default_factory=lambda: ["H1"])
    symbol_timeframes: Dict[str, str] = field(default_factory=dict)
    execution_mode: str = "simulated"  # 'simulated', 'shadow', or 'live'
    shadow_ledger_path: str = ""
    max_position_volume: float = 1.0
    require_protection: bool = True
    break_even: BreakEvenConfig = field(default_factory=BreakEvenConfig)
    partial_close_enabled: bool = False
    partial_close_trigger_r: float = 1.5
    partial_close_percent: float = 50.0
    sl_tp_mode: str = "signal"
    stop_loss_value: float = 0.0
    take_profit_value: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_balance": self.account_balance,
            "risk_per_trade": self.risk_per_trade,
            "max_positions": self.max_positions,
            "daily_loss_limit": self.daily_loss_limit,
            "min_signal_score": self.min_signal_score,
            "enabled_strategies": self.enabled_strategies,
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "symbol_timeframes": self.symbol_timeframes,
            "execution_mode": self.execution_mode,
            "shadow_ledger_path": self.shadow_ledger_path,
            "max_position_volume": self.max_position_volume,
            "require_protection": self.require_protection,
            "break_even": {
                "enabled": self.break_even.enabled,
                "trigger_r": self.break_even.trigger_r,
                "entry_offset": self.break_even.entry_offset,
            },
            "partial_close_enabled": self.partial_close_enabled,
            "partial_close_trigger_r": self.partial_close_trigger_r,
            "partial_close_percent": self.partial_close_percent,
            "sl_tp_mode": self.sl_tp_mode,
            "stop_loss_value": self.stop_loss_value,
            "take_profit_value": self.take_profit_value,
        }


@dataclass
class TradingCycle:
    """Results from a single trading cycle."""

    timestamp: datetime
    signals_generated: int = 0
    orders_executed: int = 0
    positions_opened: int = 0
    positions_closed: int = 0
    total_pnl: float = 0.0
    open_positions: int = 0
    scores: List[SignalScore] = field(default_factory=list)
    signal_details: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "signals_generated": self.signals_generated,
            "orders_executed": self.orders_executed,
            "positions_opened": self.positions_opened,
            "positions_closed": self.positions_closed,
            "total_pnl": round(self.total_pnl, 2),
            "open_positions": self.open_positions,
            "scores": [s.to_dict() for s in self.scores],
            "signal_details": self.signal_details,
            "errors": self.errors,
        }


class AutoTrader:
    """
    Main automated trading system orchestrator.

    Coordinates all components:
    - Strategies (signal generation)
    - ML models (prediction and confidence)
    - Scoring system (signal evaluation)
    - Execution engine (order management)
    - Position manager (trade tracking)

    Usage:
        trader = AutoTrader(config)
        trader.add_strategy(strategy)
        trader.add_ml_artifact("ensemble", "models/ensemble_latest.pkl")

        # Run trading loop
        while True:
            cycle = trader.run_cycle(market_data)
            if cycle.positions_opened > 0:
                logger.info(f"Opened {cycle.positions_opened} positions")
    """

    def __init__(self, config: TradingConfig):
        """
        Initialize automated trader.

        Args:
            config: Trading configuration
        """
        self.config = config
        self.logger = get_logger("execution.auto_trader")

        # Initialize components
        self.position_manager = PositionManager(account_balance=config.account_balance)
        self.position_manager.max_positions = config.max_positions
        self.position_manager.daily_loss_limit = (
            config.account_balance * config.daily_loss_limit
        )

        if config.execution_mode not in {"simulated", "shadow", "live"}:
            raise ValueError("execution_mode must be simulated, shadow, or live")
        self.execution_engine = TradingExecution(
            mode=config.execution_mode,
            shadow_ledger=(
                ShadowOrderLedger(config.shadow_ledger_path)
                if config.execution_mode == "shadow"
                else None
            ),
        )

        self.scoring_system = ScoringSystem(
            weights={
                "strategy": 0.25,
                "ml_confidence": 0.25,
                "trend_alignment": 0.15,
                "risk_reward": 0.15,
                "volatility": 0.10,
                "time": 0.05,
                "intermarket": 0.05,
            }
        )

        # Strategy and ML storage
        self.strategies: Dict[str, BaseStrategy] = {}
        self.ml_services: Dict[str, MLInferenceService] = {}

        # State
        self.is_running = False
        self.cycle_history: List[TradingCycle] = []
        self.last_cycle_time: Optional[datetime] = None

        self.logger.info(
            f"AutoTrader initialized with balance: ${config.account_balance}"
        )
        self.logger.info(f"Execution mode: {config.execution_mode}")

    def configure_live_execution(
        self, connector: Any, workflow: Any, token_provider: Any
    ) -> None:
        """Attach the only supported live execution adapter."""
        if self.config.execution_mode != "live":
            raise ValueError("live execution requires execution_mode='live'")
        self.execution_engine = TradingExecution(
            mode="live",
            mt5_connector=connector,
            live_workflow=workflow,
            confirmation_token_provider=token_provider,
        )

    def add_strategy(self, strategy: BaseStrategy) -> None:
        """Add a trading strategy."""
        if not strategy.config.enabled:
            self.logger.warning(f"Strategy {strategy.name} is disabled")
            return

        self.strategies[strategy.name] = strategy
        self.logger.info(f"Added strategy: {strategy.name}")

    def add_ml_artifact(
        self,
        name: str,
        artifact_path: str,
        config: MLConfig | None = None,
    ) -> MLInferenceService:
        """Load and register a canonical artifact-backed inference service."""

        service = MLInferenceService(artifact_path, config)
        self.ml_services[name] = service
        self.logger.info("Added ML artifact service: %s", name)
        return service

    def run_cycle(
        self,
        market_data: Dict[str, Any],
        current_prices: Dict[str, float],
    ) -> TradingCycle:
        """
        Run a single trading cycle.

        Args:
            market_data: Market data including OHLCV, indicators
            current_prices: Current prices for all symbols

        Returns:
            TradingCycle results
        """
        start_time = time.time()
        cycle = TradingCycle(timestamp=datetime.now())

        try:
            # Step 1: Generate signals from all strategies
            signals = self._generate_signals(market_data)
            cycle.signals_generated = len(signals)
            cycle.signal_details = [
                {
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "strategy": signal.strategy_name or "Unknown",
                    "order_type": str(
                        signal.metadata.get(
                            "execution_type",
                            signal.metadata.get("order_type", "MARKET"),
                        )
                    ).upper(),
                    "strength": round(float(signal.strength), 3),
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "timestamp": signal.timestamp.isoformat(),
                    "reason": str(signal.metadata.get("reason", "")).strip()
                    if signal.metadata
                    else "",
                }
                for signal in signals
            ]

            if not signals:
                self.logger.debug("No signals generated")
                self.cycle_history.append(cycle)
                return cycle

            # Step 2: Get ML predictions
            ml_predictions = self._get_ml_predictions(market_data)

            # Step 3: Score signals
            market_context = market_data.get("context", {})
            scored_signals = self.scoring_system.filter_signals_by_score(
                signals=signals,
                ml_predictions=ml_predictions,
                market_context=market_context,
                min_score=self.config.min_signal_score,
            )
            cycle.scores = scored_signals

            if not scored_signals:
                self.logger.debug("No signals passed score threshold")
                self.cycle_history.append(cycle)
                return cycle

            # Step 4: Execute trades for high-scoring signals
            for signal_score in scored_signals[: self.config.max_positions]:
                signal = signal_score.signal
                if (
                    self.config.execution_mode == "live"
                    and self.config.require_protection
                    and (not signal.stop_loss or not signal.take_profit)
                ):
                    cycle.errors.append(f"Protected SL/TP required for {signal.symbol}")
                    self.logger.warning(
                        "Skipping unprotected live signal for %s", signal.symbol
                    )
                    continue

                # Market entries are unique per symbol; pending limits may coexist.
                requested_type = str(
                    signal.metadata.get(
                        "execution_type", signal.metadata.get("order_type", "MARKET")
                    )
                ).upper()
                order_type = (
                    OrderType.LIMIT
                    if requested_type in {"LIMIT", "BUY_LIMIT", "SELL_LIMIT"}
                    and signal.entry_price is not None
                    else OrderType.MARKET
                )
                if not self.position_manager.can_open_new_position(
                    signal.symbol,
                    volume=0.1,  # Will be calculated properly
                    price=signal.entry_price or current_prices.get(signal.symbol, 0),
                    order_type=order_type,
                    direction=signal.direction,
                ):
                    block_reason = self.position_manager.position_open_block_reason(
                        signal.symbol,
                        volume=0.1,
                        price=signal.entry_price
                        or current_prices.get(signal.symbol, 0),
                        order_type=order_type,
                        direction=signal.direction,
                    )
                    self.logger.info(
                        "Cannot open position for %s: %s",
                        signal.symbol,
                        block_reason or "risk_gate",
                    )
                    cycle.errors.append(
                        f"Order blocked for {signal.symbol}: {block_reason or 'risk_gate'}"
                    )
                    continue

                # Calculate position size
                volume = self._calculate_position_size(
                    signal=signal,
                    account_balance=self.config.account_balance,
                )

                if volume <= 0:
                    self.logger.warning(f"Invalid volume for {signal.symbol}")
                    continue
                if volume > self.config.max_position_volume:
                    self.logger.warning(
                        "Capping volume for %s at configured maximum", signal.symbol
                    )
                    volume = self.config.max_position_volume

                # Create order
                ml_pred = ml_predictions.get(signal.symbol)
                current_price = current_prices.get(signal.symbol, signal.entry_price)
                signal = self._apply_sl_tp_settings(signal, current_price)
                order = self.execution_engine.create_order_from_signal(
                    signal=signal,
                    volume=volume,
                    ml_prediction=ml_pred,
                )

                if not order:
                    continue

                # Execute order
                report = self.execution_engine.execute_order(order, current_price)

                if report.success:
                    cycle.orders_executed += 1

                    if report.order.status.value == "FILLED":
                        if report.order.filled_price is None:
                            self.logger.error(
                                "Filled order has no execution price; position creation blocked"
                            )
                            continue
                        position = self.position_manager.open_position(
                            order=order,
                            filled_price=report.order.filled_price,
                        )
                        if position:
                            cycle.positions_opened += 1
                            self.logger.info(
                                f"Position opened: {position.direction} {position.volume} {position.symbol} "
                                f"@ {position.entry_price}"
                            )
                    else:
                        pending_key = order.order_id or (
                            f"{order.symbol}:{order.direction}:{order.price}"
                        )
                        self.position_manager.pending_orders[pending_key] = order
                        self.logger.info(
                            "Pending order accepted: %s %s @ %s",
                            order.direction,
                            order.symbol,
                            order.price,
                        )
                else:
                    cycle.errors.append(
                        f"Order failed for {signal.symbol}: {report.message}"
                    )

            # Step 5: Update existing positions
            self.position_manager.update_prices(current_prices)

            # Step 6: Check for SL/TP triggers and close positions
            closed_count = self._check_and_close_positions(current_prices)
            cycle.positions_closed = closed_count

            # Update cycle stats
            cycle.open_positions = len(self.position_manager.positions)
            cycle.total_pnl = self.position_manager.get_total_unrealized_pnl()

            self.last_cycle_time = datetime.now()

        except Exception as e:
            self.logger.error(f"Trading cycle error: {e}", exc_info=True)
            cycle.errors.append(str(e))

        self.cycle_history.append(cycle)

        cycle_duration = time.time() - start_time
        self.logger.debug(
            f"Trading cycle completed in {cycle_duration:.3f}s - "
            f"Signals: {cycle.signals_generated}, Orders: {cycle.orders_executed}, "
            f"Opened: {cycle.positions_opened}, Closed: {cycle.positions_closed}"
        )

        return cycle

    def _generate_signals(self, market_data: Dict[str, Any]) -> List[Signal]:
        """Generate signals from all strategies."""
        signals = []

        for name, strategy in self.strategies.items():
            try:
                if not strategy.enabled:
                    continue

                # Get data for each symbol
                for symbol in self.config.symbols:
                    symbol_data = market_data.get(symbol, {})

                    if not symbol_data:
                        continue

                    signal = strategy.generate_signal(
                        data=symbol_data,
                        current_position=self.position_manager.positions.get(symbol),
                    )

                    if signal and signal.direction in ["BUY", "SELL"]:
                        signals.append(signal)
                        self.logger.debug(
                            f"Signal from {name}: {signal.direction} {signal.symbol} "
                            f"(strength: {signal.strength})"
                        )

            except Exception as e:
                self.logger.error(f"Strategy {name} error: {e}")
                signals.append(None)

        return [s for s in signals if s is not None]

    def _apply_sl_tp_settings(
        self, signal: Signal, market_price: Optional[float] = None
    ) -> Signal:
        """Apply validated user SL/TP distances without weakening signal protection."""
        mode = self.config.sl_tp_mode
        if mode not in {"signal", "fixed", "percent"}:
            return signal
        if mode == "signal":
            return signal
        entry = float(signal.entry_price or market_price or 0.0)
        if entry <= 0:
            return signal
        sl_distance = float(self.config.stop_loss_value)
        tp_distance = float(self.config.take_profit_value)
        if mode == "percent":
            sl_distance = entry * sl_distance / 100.0
            tp_distance = entry * tp_distance / 100.0
        if sl_distance <= 0 or tp_distance <= 0:
            return signal
        reference = float(market_price or entry)
        if reference <= 0:
            return signal
        if signal.direction == "BUY":
            signal.stop_loss = min(entry - sl_distance, reference - sl_distance)
            signal.take_profit = max(entry + tp_distance, reference + tp_distance)
        else:
            signal.stop_loss = max(entry + sl_distance, reference + sl_distance)
            signal.take_profit = min(entry - tp_distance, reference - tp_distance)
        return signal

    def _get_ml_predictions(
        self,
        market_data: Dict[str, Any],
    ) -> Dict[str, Prediction]:
        """Get ML predictions for all symbols."""
        predictions: Dict[str, Prediction] = {}

        if not self.ml_services:
            self.logger.debug("No ML inference service available")
            return predictions

        for symbol in self.config.symbols:
            try:
                symbol_data = market_data.get(symbol, {})
                candles = symbol_data.get("candles", [])
                if self.ml_services and candles:
                    frame = pd.DataFrame(candles)
                    service_predictions = [
                        service.predict(frame, symbol=symbol)
                        for service in self.ml_services.values()
                    ]
                    prediction = self._combine_predictions(service_predictions, symbol)
                    predictions[symbol] = prediction

                if symbol in predictions:
                    self.logger.debug(
                        f"ML prediction for {symbol}: {prediction.direction} "
                        f"(confidence: {prediction.confidence})"
                    )

            except Exception as e:
                self.logger.warning(f"ML prediction error for {symbol}: {e}")

        return predictions

    @staticmethod
    def _combine_predictions(
        predictions: list[Prediction],
        symbol: str,
    ) -> Prediction:
        """Average canonical service probabilities without retraining models."""

        if not predictions:
            raise ValueError("at least one prediction is required")
        labels = ("BUY", "HOLD", "SELL")
        probabilities = {
            label: float(np.mean([item.probabilities[label] for item in predictions]))
            for label in labels
        }
        direction = max(labels, key=lambda label: probabilities[label])
        return Prediction(
            direction=direction,
            confidence=probabilities[direction],
            probabilities=probabilities,
            feature_names=predictions[0].feature_names,
            model_name=",".join(item.model_name for item in predictions),
            model_version=",".join(item.model_version for item in predictions),
        )

    def _calculate_position_size(
        self,
        signal: Signal,
        account_balance: float,
    ) -> float:
        """Calculate position size based on risk parameters."""
        if not signal.entry_price or not signal.stop_loss:
            return 0.1  # Default minimum

        risk_amount = account_balance * self.config.risk_per_trade

        if signal.direction == "BUY":
            risk_per_unit = signal.entry_price - signal.stop_loss
        else:
            risk_per_unit = signal.stop_loss - signal.entry_price

        if risk_per_unit <= 0:
            return 0.0

        position_size = risk_amount / risk_per_unit
        return max(0.01, round(position_size, 2))

    def _check_and_close_positions(self, current_prices: Dict[str, float]) -> int:
        """Check and close positions that hit SL/TP."""
        closed_count = 0

        for symbol, position in list(self.position_manager.positions.items()):
            current_price = current_prices.get(symbol, position.current_price)

            # Check if SL or TP triggered
            trigger = self.position_manager._check_stop_loss_take_profit(
                position, current_price
            )

            if trigger:
                # Close position
                result = self.position_manager.close_position(
                    symbol=symbol,
                    exit_price=current_price,
                    exit_reason=trigger,
                )

                if result:
                    closed_count += 1

                    # Execute close order
                    close_direction = "SELL" if position.direction == "BUY" else "BUY"
                    close_order = Order(
                        symbol=symbol,
                        direction=close_direction,
                        order_type=OrderType.MARKET,
                        volume=position.volume,
                        price=current_price,
                        metadata={
                            "action": "close_position",
                            "reason": trigger,
                            "shadow_order_id": position.order_id,
                        },
                    )

                    self.execution_engine.execute_order(close_order, current_price)

                    self.logger.info(
                        f"Position closed: {symbol} {trigger} - PnL: ${result.pnl:.2f}"
                    )

        return closed_count

    def get_status(self) -> Dict[str, Any]:
        """Get current trader status."""
        position_stats = self.position_manager.get_position_stats()
        performance = self.position_manager.get_performance_metrics()
        execution_stats = self.execution_engine.get_execution_stats()

        return {
            "is_running": self.is_running,
            "account_balance": self.config.account_balance,
            "daily_pnl": self.position_manager.daily_pnl,
            "positions": position_stats,
            "performance": performance,
            "execution": execution_stats,
            "strategies": list(self.strategies.keys()),
            "ml_services": list(self.ml_services.keys()),
            "last_cycle": self.last_cycle_time.isoformat()
            if self.last_cycle_time
            else None,
            "total_cycles": len(self.cycle_history),
        }

    def start(self) -> None:
        """Start the automated trader."""
        self.is_running = True
        self.logger.info("AutoTrader started")

    def stop(self) -> None:
        """Stop the automated trader."""
        self.is_running = False
        self.logger.info("AutoTrader stopped")

        # Optionally close all positions
        # self.close_all_positions()

    def close_all_positions(self) -> List[TradeResult]:
        """Close all open positions."""
        results = []

        for symbol in list(self.position_manager.positions.keys()):
            position = self.position_manager.positions[symbol]

            # Get current price (in production, fetch from market)
            current_price = position.current_price

            result = self.position_manager.close_position(
                symbol=symbol,
                exit_price=current_price,
                exit_reason="MANUAL_CLOSE_ALL",
            )

            if result:
                results.append(result)

        self.logger.info(f"Closed {len(results)} positions")
        return results
