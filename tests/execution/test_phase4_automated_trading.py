"""Test suite for Phase 4 - Automated Trading."""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# Add src to path
sys.path.insert(0, "/workspace/src/python")

from src.python.execution import (
    AutoTrader,
    Order,
    OrderStatus,
    OrderType,
    PositionManager,
    ScoringSystem,
    TradingConfig,
    TradingExecution,
)
from src.python.ml import Prediction
from src.python.strategies import (
    MeanReversionStrategy,
    Signal,
    StrategyConfig,
    TrendFollowingStrategy,
)


def test_trading_execution():
    """Test TradingExecution module."""
    print("\n" + "=" * 60)
    print("TEST: Trading Execution")
    print("=" * 60)

    # Initialize execution engine
    executor = TradingExecution(mode="simulated", default_slippage=0.0001)

    # Create a test signal
    signal = Signal(
        symbol="EURUSD",
        direction="BUY",
        strength=0.8,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        strategy_name="TrendFollowing",
    )

    # Create order from signal
    order = executor.create_order_from_signal(signal, volume=0.5)
    assert order is not None, "Order should be created"
    assert order.direction == "BUY"
    assert order.volume == 0.5
    assert order.symbol == "EURUSD"
    print(f"✓ Order created: {order.direction} {order.volume} {order.symbol}")

    # Execute order
    report = executor.execute_order(order, current_price=1.1000)
    assert report.success, "Order execution should succeed"
    assert order.status == OrderStatus.FILLED
    print(f"✓ Order executed: {report.message}")
    print(f"  - Filled price: {order.filled_price:.5f}")
    print(f"  - Slippage: {report.slippage:.6f}")
    print(f"  - Commission: ${report.commission:.4f}")

    # Get stats
    stats = executor.get_execution_stats()
    assert stats["total_orders"] == 1
    assert stats["successful_executions"] == 1
    print(f"✓ Execution stats: {stats['success_rate'] * 100:.1f}% success rate")

    print("\n✅ Trading Execution tests PASSED\n")


def test_position_manager():
    """Test PositionManager module."""
    print("\n" + "=" * 60)
    print("TEST: Position Manager")
    print("=" * 60)

    # Initialize position manager
    pm = PositionManager(account_balance=10000.0)

    # Create and open a position
    order = Order(
        symbol="EURUSD",
        direction="BUY",
        order_type=OrderType.MARKET,
        volume=0.5,
        stop_loss=1.0950,
        take_profit=1.1100,
        strategy_name="TrendFollowing",
    )

    position = pm.open_position(order, filled_price=1.1000)
    assert position is not None, "Position should be opened"
    assert position.entry_price == 1.1000
    print(
        f"✓ Position opened: {position.direction} {position.volume} {position.symbol} @ {position.entry_price}"
    )

    # Update price
    pm.update_prices({"EURUSD": 1.1050})
    assert position.unrealized_pnl > 0, "PnL should be positive"
    print(f"✓ Price updated to 1.1050, Unrealized PnL: ${position.unrealized_pnl:.2f}")

    # Test closing at TP
    trigger = pm._check_stop_loss_take_profit(position, 1.1100)
    assert trigger == "TP", "Take profit should be triggered"
    print("✓ Take Profit triggered at 1.1100")

    # Close position
    result = pm.close_position("EURUSD", exit_price=1.1100, exit_reason="TP")
    assert result is not None
    assert result.pnl > 0
    assert len(pm.positions) == 0
    print(f"✓ Position closed - PnL: ${result.pnl:.2f} ({result.pnl_percent:.2f}%)")

    # Get performance metrics
    metrics = pm.get_performance_metrics()
    assert metrics["total_trades"] == 1
    assert metrics["win_rate"] == 1.0
    print(
        f"✓ Performance: Win Rate={metrics['win_rate'] * 100:.1f}%, Total PnL=${metrics['total_pnl']:.2f}"
    )

    print("\n✅ Position Manager tests PASSED\n")


def test_scoring_system():
    """Test ScoringSystem module."""
    print("\n" + "=" * 60)
    print("TEST: Scoring System")
    print("=" * 60)

    # Initialize scoring system
    scorer = ScoringSystem()

    # Create test signal
    signal = Signal(
        symbol="EURUSD",
        direction="BUY",
        strength=0.85,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        strategy_name="TrendFollowing",
    )

    # Create ML prediction
    ml_pred = Prediction(
        direction="BUY",
        confidence=0.78,
        probabilities={"BUY": 0.78, "HOLD": 0.12, "SELL": 0.10},
        feature_names=(),
        model_name="Ensemble",
    )

    # Create market context
    market_context = {
        "trend": {"timeframes": {"H1": "BUY", "H4": "BUY", "D1": "BUY"}},
        "volatility": {
            "current": 0.0008,
            "average": 0.0007,
        },
        "intermarket": {
            "correlations": {
                "DXY": {"value": -0.7, "direction": "SELL"},
                "Gold": {"value": 0.5, "direction": "BUY"},
            }
        },
    }

    # Calculate score
    score = scorer.calculate_signal_score(signal, ml_pred, market_context)

    assert score.total_score > 0.6, "Score should be above threshold"
    print(f"✓ Signal scored: {score.recommendation}")
    print(f"  - Total Score: {score.total_score:.3f}")
    print(f"  - Strategy Score: {score.strategy_score:.3f}")
    print(f"  - ML Confidence Score: {score.ml_confidence_score:.3f}")
    print(f"  - Risk/Reward Score: {score.risk_reward_score:.3f}")
    print(f"  - Trend Alignment Score: {score.trend_alignment_score:.3f}")

    # Test recommendation thresholds
    if score.total_score >= 0.85:
        assert score.recommendation == "STRONG_BUY"
        print("✓ Strong BUY recommendation confirmed")
    elif score.total_score >= 0.65:
        assert score.recommendation in ["BUY", "STRONG_BUY"]
        print("✓ BUY recommendation confirmed")

    print("\n✅ Scoring System tests PASSED\n")


def test_auto_trader_integration():
    """Test AutoTrader integration with all components."""
    print("\n" + "=" * 60)
    print("TEST: AutoTrader Integration")
    print("=" * 60)

    # Create configuration
    config = TradingConfig(
        account_balance=10000.0,
        risk_per_trade=0.01,
        max_positions=3,
        min_signal_score=0.6,
        symbols=["EURUSD", "GBPUSD"],
        execution_mode="simulated",
    )

    # Initialize auto trader
    trader = AutoTrader(config)
    print(f"✓ AutoTrader initialized with ${config.account_balance} balance")

    # Add strategies
    trend_strategy = TrendFollowingStrategy(
        StrategyConfig(
            name="TrendFollowing",
            enabled=True,
            risk_per_trade=0.01,
        )
    )
    mean_rev_strategy = MeanReversionStrategy(
        StrategyConfig(
            name="MeanReversion",
            enabled=True,
            risk_per_trade=0.01,
        )
    )

    trader.add_strategy(trend_strategy)
    trader.add_strategy(mean_rev_strategy)
    print(f"✓ Added {len(trader.strategies)} strategies")

    # Add ML models (train with dummy data)
    try:
        # Generate dummy training data
        np.random.seed(42)
        n_samples = 100
        features = np.random.randn(n_samples, 6)
        labels = np.random.randint(0, 3, n_samples)

        from sklearn.ensemble import RandomForestClassifier

        from src.python.ml import save_artifact

        rf_model = RandomForestClassifier(n_estimators=50, max_depth=5, random_state=42)
        rf_model.fit(features, labels)
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_path = save_artifact(
                rf_model,
                ["rsi", "macd", "adx", "atr", "bb_upper", "bb_lower"],
                Path(temp_dir) / "random_forest.pkl",
                schema_version="1.0",
            )
            trader.add_ml_artifact("RandomForest", str(artifact_path))
        print("✓ RandomForest artifact trained and added")

    except Exception as e:
        print(f"⚠ ML model training skipped: {e}")

    # Create simulated market data
    base_time = datetime.now()
    market_data = {
        "EURUSD": {
            "symbol": "EURUSD",
            "timeframe": "H1",
            "candles": [
                {
                    "timestamp": base_time - timedelta(hours=i),
                    "open": 1.1000 + i * 0.0001,
                    "high": 1.1020 + i * 0.0001,
                    "low": 1.0990 + i * 0.0001,
                    "close": 1.1010 + i * 0.0001,
                    "volume": 1000,
                    "rsi": 55.0,
                    "macd": 0.0002,
                    "adx": 25.0,
                    "atr": 0.0008,
                    "bb_upper": 1.1050,
                    "bb_lower": 1.0950,
                }
                for i in range(20)
            ],
        },
        "GBPUSD": {
            "symbol": "GBPUSD",
            "timeframe": "H1",
            "candles": [
                {
                    "timestamp": base_time - timedelta(hours=i),
                    "open": 1.2700 + i * 0.0001,
                    "high": 1.2720 + i * 0.0001,
                    "low": 1.2690 + i * 0.0001,
                    "close": 1.2710 + i * 0.0001,
                    "volume": 800,
                    "rsi": 45.0,
                    "macd": -0.0001,
                    "adx": 20.0,
                    "atr": 0.0010,
                    "bb_upper": 1.2750,
                    "bb_lower": 1.2650,
                }
                for i in range(20)
            ],
        },
        "context": {
            "trend": {"timeframes": {"H1": "BUY", "H4": "BUY", "D1": "HOLD"}},
            "volatility": {
                "current": 0.0008,
                "average": 0.0007,
            },
        },
    }

    current_prices = {
        "EURUSD": 1.1010,
        "GBPUSD": 1.2710,
    }

    # Run trading cycle
    trader.start()
    cycle = trader.run_cycle(market_data, current_prices)

    print("\n✓ Trading Cycle Completed:")
    print(f"  - Timestamp: {cycle.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  - Signals Generated: {cycle.signals_generated}")
    print(f"  - Orders Executed: {cycle.orders_executed}")
    print(f"  - Positions Opened: {cycle.positions_opened}")
    print(f"  - Positions Closed: {cycle.positions_closed}")
    print(f"  - Open Positions: {cycle.open_positions}")
    print(f"  - Total PnL: ${cycle.total_pnl:.2f}")

    if cycle.errors:
        print(f"  - Errors: {cycle.errors}")

    # Get trader status
    status = trader.get_status()
    print("\n✓ Trader Status:")
    print(f"  - Running: {status['is_running']}")
    print(f"  - Strategies: {status['strategies']}")
    print(f"  - ML Services: {status['ml_services']}")
    print(f"  - Total Cycles: {status['total_cycles']}")

    trader.stop()

    print("\n✅ AutoTrader Integration tests PASSED\n")


def run_all_tests():
    """Run all Phase 4 tests."""
    print("\n" + "#" * 60)
    print("# PHASE 4: AUTOMATED TRADING - TEST SUITE")
    print("#" * 60)

    results = {}

    try:
        results["trading_execution"] = test_trading_execution()
    except Exception as e:
        print(f"\n❌ Trading Execution tests FAILED: {e}\n")
        results["trading_execution"] = False

    try:
        results["position_manager"] = test_position_manager()
    except Exception as e:
        print(f"\n❌ Position Manager tests FAILED: {e}\n")
        results["position_manager"] = False

    try:
        results["scoring_system"] = test_scoring_system()
    except Exception as e:
        print(f"\n❌ Scoring System tests FAILED: {e}\n")
        results["scoring_system"] = False

    try:
        results["auto_trader"] = test_auto_trader_integration()
    except Exception as e:
        print(f"\n❌ AutoTrader Integration tests FAILED: {e}\n")
        results["auto_trader"] = False

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    for test_name, result in results.items():
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"  {test_name}: {status}")

    print(f"\nTotal: {passed}/{total} tests passed ({passed / total * 100:.1f}%)")

    if passed == total:
        print("\n🎉 ALL PHASE 4 TESTS PASSED! 🎉\n")
        return True
    else:
        print(f"\n⚠️  {total - passed} test(s) failed\n")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
