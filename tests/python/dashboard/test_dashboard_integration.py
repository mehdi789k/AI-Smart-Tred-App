"""
Dashboard Integration Tests - Test Dashboard Components with Real Data and Strategies
Tests all dashboard sections ensuring connection to project modules and real data usage.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.python.backtest import BacktestEngine
from src.python.dashboard import app as dashboard_app
from src.python.execution import TradingConfig

# Add project root to path
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestDashboardImports(unittest.TestCase):
    """Test that all required dashboard imports work correctly."""

    def test_dashboard_data_manager_import(self):
        """Test data manager module imports."""
        from src.python.dashboard.data_manager import (
            DashboardDataManager,
            get_dashboard_data,
        )

        self.assertIsNotNone(get_dashboard_data)
        self.assertIsNotNone(DashboardDataManager)

    def test_dashboard_mt5_connector_import(self):
        """Test MT5 connector module imports."""
        from src.python.dashboard.mt5_connector import MT5Connection, get_mt5_instance

        self.assertIsNotNone(get_mt5_instance)
        self.assertIsNotNone(MT5Connection)

    def test_strategy_imports(self):
        """Test that all strategy modules can be imported."""
        from src.python.strategies.mean_reversion import MeanReversionStrategy
        from src.python.strategies.smc import SMCStrategy
        from src.python.strategies.trend_following import TrendFollowingStrategy

        self.assertIsNotNone(TrendFollowingStrategy)
        self.assertIsNotNone(MeanReversionStrategy)
        self.assertIsNotNone(SMCStrategy)

    def test_backtest_engine_import(self):
        """Test backtest engine import."""
        from src.python.backtest import BacktestEngine

        self.assertIsNotNone(BacktestEngine)

    def test_ml_models_import(self):
        """Test canonical artifact inference imports."""
        from src.python.ml import MLInferenceService, ProbabilityEnsemble, save_artifact

        self.assertIsNotNone(MLInferenceService)
        self.assertIsNotNone(ProbabilityEnsemble)
        self.assertIsNotNone(save_artifact)

    def test_execution_modules_import(self):
        """Test execution module imports."""
        from src.python.execution import (
            AutoTrader,
            PositionManager,
            ScoringSystem,
            TradingConfig,
        )

        self.assertIsNotNone(AutoTrader)
        self.assertIsNotNone(TradingConfig)
        self.assertIsNotNone(PositionManager)
        self.assertIsNotNone(ScoringSystem)

    def test_overview_refresh_does_not_use_busy_rerun_loop(self):
        """The overview must not schedule a no-op automatic rerun loop."""
        self.assertIsNone(dashboard_app.OVERVIEW_REFRESH_INTERVAL_SECONDS)

    def test_live_monitor_refresh_interval_is_not_subsecond(self):
        """Live monitor refreshes must leave MT5 history calls time to complete."""
        interval = dashboard_app.LIVE_MONITOR_REFRESH_INTERVAL_SECONDS
        self.assertTrue(interval is None or interval >= 5)

    def test_dashboard_resources_are_reused_across_reruns(self):
        """MT5 resources must not be recreated for every Streamlit rerun."""
        self.assertIs(
            dashboard_app._get_dashboard_data_manager(),
            dashboard_app._get_dashboard_data_manager(),
        )
        self.assertIs(
            dashboard_app._get_mt5_connector(),
            dashboard_app._get_mt5_connector(),
        )

    def test_live_account_values_allow_missing_numeric_data(self):
        """Disconnected accounts must render when a broker field is unavailable."""
        self.assertEqual(dashboard_app._format_account_value(None), "—")
        self.assertEqual(dashboard_app._format_account_value(1234.5), "1,234.50")

    def test_spread_monitor_degrades_when_atr_limit_is_unavailable(self):
        """An unavailable ATR limit must not crash the dashboard render."""

        class FakeConnector:
            def is_connected(self):
                return True

            def get_symbol_info(self, symbol):
                return {"spread": 0.25}

        class FakeConfig:
            spread_mode = "atr"

        class FakeWorkflow:
            config = FakeConfig()

            def spread_limit(self, symbol):
                raise dashboard_app.LiveOrderRejected(
                    "spread_unavailable", "ATR spread value is unavailable"
                )

        with (
            patch.object(dashboard_app, "mt5_connector", FakeConnector()),
            patch.object(
                dashboard_app, "get_live_order_workflow", return_value=FakeWorkflow()
            ),
            patch.object(
                dashboard_app.st.session_state,
                "saved_settings",
                {"symbol": "XAUUSD", "max_spread": 0.5},
                create=True,
            ),
        ):
            dashboard_app._render_live_spread_monitor()


class TestDashboardDataManager(unittest.TestCase):
    """Test Dashboard Data Manager functionality."""

    def setUp(self):
        """Set up test fixtures."""
        from src.python.dashboard.data_manager import get_dashboard_data

        self.data_manager = get_dashboard_data()

    def test_data_manager_initialization(self):
        """Test data manager initializes correctly."""
        self.assertIsNotNone(self.data_manager)
        self.assertTrue(hasattr(self.data_manager, "mt5"))
        self.assertTrue(hasattr(self.data_manager, "cache"))

    def test_is_connected_method(self):
        """Test connection status check."""
        connected = self.data_manager.is_connected()
        self.assertIsInstance(connected, bool)

    def test_get_account_data_structure(self):
        """Test account data returns correct structure."""
        account_data = self.data_manager.get_account_data()
        self.assertIsInstance(account_data, dict)

        # Check required fields (may be mock data if MT5 not connected)
        self.assertIn("balance", account_data)
        self.assertIn("equity", account_data)
        self.assertIn("connected", account_data)

    def test_get_positions_data_structure(self):
        """Test positions data returns DataFrame."""
        positions = self.data_manager.get_positions_data()
        self.assertIsInstance(positions, pd.DataFrame)

    def test_get_history_data_structure(self):
        """Test history data returns DataFrame."""
        history = self.data_manager.get_history_data(days=7)
        self.assertIsInstance(history, pd.DataFrame)

    def test_get_equity_curve_structure(self):
        """Test equity curve returns DataFrame with correct columns."""
        equity = self.data_manager.get_equity_curve(days=30)
        self.assertIsInstance(equity, pd.DataFrame)
        self.assertIn("time", equity.columns)
        self.assertIn("equity", equity.columns)

    def test_get_performance_metrics_structure(self):
        """Test performance metrics returns correct structure."""
        metrics = self.data_manager.get_performance_metrics()
        self.assertIsInstance(metrics, dict)

        # Check key metrics exist
        self.assertIn("win_rate", metrics)
        self.assertIn("total_trades", metrics)
        self.assertIn("profit_factor", metrics)

    def test_get_market_data_structure(self):
        """Test market data returns correct structure."""
        symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
        market_data = self.data_manager.get_market_data(symbols)
        self.assertIsInstance(market_data, dict)

    def test_refresh_all_method(self):
        """Test cache refresh method."""
        self.data_manager.refresh_all()
        # Should clear caches
        self.assertEqual(len(self.data_manager.cache), 0)
        self.assertEqual(len(self.data_manager.cache_timestamp), 0)

    def test_equity_curve_reuses_cached_account_snapshot(self):
        """Equity calculations must not bypass the account-data cache."""
        from src.python.dashboard.data_manager import DashboardDataManager

        class FakeMT5:
            def __init__(self):
                self.account_calls = 0

            def get_account_summary(self):
                self.account_calls += 1
                return {
                    "connected": True,
                    "balance": 1000.0,
                    "equity": 1000.0,
                    "profit": 0.0,
                }

            def get_history(self, days):
                return pd.DataFrame(
                    [{"type": 0, "profit": 10.0, "time": datetime.now()}]
                )

            def get_positions(self):
                return pd.DataFrame()

        fake_mt5 = FakeMT5()
        manager = DashboardDataManager()
        manager.mt5 = fake_mt5

        manager.get_account_data()
        manager.get_equity_curve(30)

        self.assertEqual(fake_mt5.account_calls, 1)

    def test_market_data_does_not_query_positions_for_each_symbol(self):
        """Market prices must use direct symbol lookup, not an expensive positions query."""
        from src.python.dashboard.data_manager import DashboardDataManager

        class FakeMT5:
            def __init__(self):
                self.position_calls = 0

            def get_positions(self):
                self.position_calls += 1
                return pd.DataFrame()

            def get_symbol_info(self, symbol):
                return {
                    "name": symbol,
                    "bid": 1.0,
                    "ask": 1.1,
                }

        fake_mt5 = FakeMT5()
        manager = DashboardDataManager()
        manager.mt5 = fake_mt5

        result = manager.get_market_data(["EURUSD_l", "XAUUSD_l"])

        self.assertEqual(set(result), {"EURUSD_l", "XAUUSD_l"})
        self.assertEqual(fake_mt5.position_calls, 0)


class TestStrategyIntegration(unittest.TestCase):
    """Test Strategy integration with dashboard components."""

    def setUp(self):
        """Set up test fixtures."""
        from src.python.strategies.mean_reversion import MeanReversionStrategy
        from src.python.strategies.smc import SMCStrategy
        from src.python.strategies.trend_following import TrendFollowingStrategy

        self.tf_strategy = TrendFollowingStrategy()
        self.mr_strategy = MeanReversionStrategy()
        self.smc_strategy = SMCStrategy()

    def test_trend_following_signal_generation(self):
        """Test Trend Following strategy generates valid signals."""
        # Create sample market data
        n_periods = 100
        prices = 100 + np.cumsum(np.random.randn(n_periods) * 0.5)

        candles = []
        for i in range(n_periods):
            open_price = prices[i]
            close_price = prices[i] + np.random.randn() * 0.2
            high = max(open_price, close_price) + abs(np.random.randn() * 0.1)
            low = min(open_price, close_price) - abs(np.random.randn() * 0.1)
            candles.append(
                {
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close_price,
                    "volume": np.random.randint(100, 1000),
                }
            )

        data = {"symbol": "EURUSD", "timeframe": "H1", "candles": candles}

        signal = self.tf_strategy.generate_signal(data)

        # Validate signal structure
        self.assertIn(signal.direction, ["BUY", "SELL", "HOLD"])
        self.assertGreaterEqual(signal.strength, 0.0)
        self.assertLessEqual(signal.strength, 1.0)
        self.assertEqual(signal.strategy_name, "TrendFollowing")

    def test_mean_reversion_signal_generation(self):
        """Test Mean Reversion strategy generates valid signals."""
        n_periods = 100
        prices = 100 + np.cumsum(np.random.randn(n_periods) * 0.5)

        candles = []
        for i in range(n_periods):
            open_price = prices[i]
            close_price = prices[i] + np.random.randn() * 0.2
            high = max(open_price, close_price) + abs(np.random.randn() * 0.1)
            low = min(open_price, close_price) - abs(np.random.randn() * 0.1)
            candles.append(
                {
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close_price,
                    "volume": np.random.randint(100, 1000),
                }
            )

        data = {"symbol": "EURUSD", "timeframe": "H1", "candles": candles}

        signal = self.mr_strategy.generate_signal(data)

        # Validate signal structure
        self.assertIn(signal.direction, ["BUY", "SELL", "HOLD"])
        self.assertGreaterEqual(signal.strength, 0.0)
        self.assertLessEqual(signal.strength, 1.0)
        self.assertEqual(signal.strategy_name, "MeanReversion")

    def test_smc_signal_generation(self):
        """Test SMC strategy generates valid signals."""
        n_periods = 100
        prices = 100 + np.cumsum(np.random.randn(n_periods) * 0.5)

        candles = []
        for i in range(n_periods):
            open_price = prices[i]
            close_price = prices[i] + np.random.randn() * 0.2
            high = max(open_price, close_price) + abs(np.random.randn() * 0.1)
            low = min(open_price, close_price) - abs(np.random.randn() * 0.1)
            candles.append(
                {
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close_price,
                    "volume": np.random.randint(100, 1000),
                }
            )

        data = {"symbol": "EURUSD", "timeframe": "H1", "candles": candles}

        signal = self.smc_strategy.generate_signal(data)

        # Validate signal structure
        self.assertIn(signal.direction, ["BUY", "SELL", "HOLD"])
        self.assertGreaterEqual(signal.strength, 0.0)
        self.assertLessEqual(signal.strength, 1.0)
        self.assertEqual(signal.strategy_name, "SMC")


class TestBacktestIntegration(unittest.TestCase):
    """Test Backtest Engine integration with strategies."""

    def setUp(self):
        """Set up test fixtures."""
        from src.python.strategies.mean_reversion import MeanReversionStrategy
        from src.python.strategies.trend_following import TrendFollowingStrategy

        self.engine = BacktestEngine(initial_balance=10000)
        self.tf_strategy = TrendFollowingStrategy()
        self.mr_strategy = MeanReversionStrategy()

    def _generate_sample_data(self, n_periods=500):
        """Generate sample OHLCV data for backtesting."""
        np.random.seed(42)
        prices = 100 + np.cumsum(np.random.randn(n_periods) * 0.5)

        df = pd.DataFrame(
            {
                "timestamp": pd.date_range(
                    end=datetime.now(), periods=n_periods, freq="h"
                ),
                "open": prices,
                "high": prices + np.abs(np.random.randn(n_periods) * 0.3),
                "low": prices - np.abs(np.random.randn(n_periods) * 0.3),
                "close": prices + np.random.randn(n_periods) * 0.2,
                "volume": np.random.randint(100, 1000, n_periods),
            }
        )
        df["high"] = np.maximum(df["high"], np.maximum(df["open"], df["close"]))
        df["low"] = np.minimum(df["low"], np.minimum(df["open"], df["close"]))
        return df

    def test_backtest_with_trend_following(self):
        """Test backtest engine with Trend Following strategy."""
        df = self._generate_sample_data()
        results = self.engine.run(df, self.tf_strategy)

        # Validate results structure
        self.assertIsInstance(results, dict)
        self.assertIn("final_balance", results)
        self.assertIn("total_return", results)
        self.assertIn("win_rate", results)
        self.assertIn("max_drawdown", results)
        self.assertIn("total_trades", results)

    def test_backtest_with_mean_reversion(self):
        """Test backtest engine with Mean Reversion strategy."""
        df = self._generate_sample_data()
        results = self.engine.run(df, self.mr_strategy)

        # Validate results structure
        self.assertIsInstance(results, dict)
        self.assertIn("final_balance", results)
        self.assertIn("total_return", results)
        self.assertIn("win_rate", results)
        self.assertIn("max_drawdown", results)
        self.assertIn("total_trades", results)

    def test_backtest_initial_balance_preserved(self):
        """Test that backtest respects initial balance."""
        initial_balance = 50000
        engine = BacktestEngine(initial_balance=initial_balance)
        df = self._generate_sample_data()
        _results = engine.run(df, self.tf_strategy)

        # Initial balance should be set correctly
        self.assertEqual(engine.initial_balance, initial_balance)


class TestMLIntegration(unittest.TestCase):
    """Test ML Model integration with dashboard."""

    def setUp(self):
        """Set up test fixtures."""
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier

        self.rf_model = RandomForestClassifier(random_state=42)
        self.xgb_model = GradientBoostingClassifier(random_state=42)
        self.ensemble = None

    def _generate_sample_features(self, n_samples=100):
        """Generate sample feature data."""
        np.random.seed(42)
        features = pd.DataFrame(
            {
                "rsi": np.random.uniform(0, 100, n_samples),
                "macd": np.random.randn(n_samples),
                "volume": np.random.randint(100, 1000, n_samples),
                "atr": np.random.uniform(0.1, 2.0, n_samples),
                "bb_position": np.random.uniform(0, 1, n_samples),
            }
        )
        target = np.random.randint(0, 2, n_samples)  # Binary classification
        return features, target

    def test_random_forest_training(self):
        """Test RandomForest model training."""
        features, _ = self._generate_sample_features()
        target = np.random.randint(0, 3, len(features))

        # Train model
        self.rf_model.fit(features, target)

        # Test prediction
        prediction = self.rf_model.predict(features.iloc[:5])
        self.assertIsNotNone(prediction)
        self.assertEqual(len(prediction), 5)

    def test_xgboost_training(self):
        """Test XGBoost model training."""
        features, target = self._generate_sample_features()

        # Train model
        self.xgb_model.fit(features, target)

        # Test prediction
        prediction = self.xgb_model.predict(features.iloc[:5])
        self.assertIsNotNone(prediction)
        self.assertEqual(len(prediction), 5)

    def test_ensemble_voting(self):
        """Test Ensemble Voting mechanism."""
        from src.python.ml import ProbabilityEnsemble

        features, _ = self._generate_sample_features()
        target = np.random.randint(0, 3, len(features))

        # Train base models
        self.rf_model.fit(features, target)
        self.xgb_model.fit(features, target)

        # Get predictions from base models
        self.rf_model.predict_proba(features.iloc[:10])
        self.xgb_model.predict_proba(features.iloc[:10])

        # Test ensemble voting
        self.ensemble = ProbabilityEnsemble([self.rf_model, self.xgb_model])
        ensemble_pred = self.ensemble.predict(features.iloc[:10])
        self.assertIsNotNone(ensemble_pred)
        self.assertEqual(len(ensemble_pred), 10)


class TestExecutionIntegration(unittest.TestCase):
    """Test Execution module integration with dashboard."""

    def setUp(self):
        """Set up test fixtures."""
        from src.python.execution import (
            AutoTrader,
            PositionManager,
            ScoringSystem,
            TradingConfig,
        )

        config = TradingConfig(
            risk_per_trade=0.01, max_positions=3, daily_loss_limit=500
        )

        self.trader = AutoTrader(config=config)
        self.position_manager = PositionManager()
        self.scoring_system = ScoringSystem()

    def test_trading_config_initialization(self):
        """Test TradingConfig initializes correctly."""
        config = TradingConfig(
            risk_per_trade=0.02, max_positions=5, daily_loss_limit=1000
        )

        self.assertEqual(config.risk_per_trade, 0.02)
        self.assertEqual(config.max_positions, 5)
        self.assertEqual(config.daily_loss_limit, 1000)

    def test_position_manager_operations(self):
        """Test PositionManager add/remove operations."""
        # Add a position
        position = {
            "ticket": 12345,
            "symbol": "EURUSD",
            "type": "BUY",
            "volume": 0.1,
            "entry_price": 1.0850,
            "sl": 1.0800,
            "tp": 1.0950,
        }

        self.position_manager.add_position(position)
        positions = self.position_manager.get_all_positions()

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["ticket"], 12345)

    def test_scoring_system_calculation(self):
        """Test ScoringSystem calculates scores correctly."""
        signal = {"direction": "BUY", "strength": 0.75, "confidence": 0.80}

        score = self.scoring_system.calculate_score(signal)
        self.assertIsNotNone(score)
        self.assertIsInstance(score, (int, float))


class TestDashboardAppStructure(unittest.TestCase):
    """Test Dashboard App structure and page navigation."""

    def test_persist_settings_retries_transient_windows_permission_error(self):
        """Settings writes recover from a transient Windows rename lock."""
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "dashboard_settings.json"
            settings_file.write_text("{}", encoding="utf-8")
            original_file = dashboard_app.SETTINGS_FILE
            dashboard_app.SETTINGS_FILE = settings_file
            original_replace = Path.replace
            attempts = 0

            def flaky_replace(path, target):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError(5, "Access is denied")
                return original_replace(path, target)

            try:
                with patch.object(Path, "replace", flaky_replace):
                    dashboard_app._persist_saved_settings({"symbol": "ZECUSD_l"})
            finally:
                dashboard_app.SETTINGS_FILE = original_file

            self.assertEqual(attempts, 2)
            self.assertEqual(
                __import__("json").loads(settings_file.read_text(encoding="utf-8"))[
                    "symbol"
                ],
                "ZECUSD_l",
            )

    def test_ml_operation_symbols_fall_back_to_local_data(self):
        """Training can use collected local candles when MT5 is unavailable."""
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, "market_data")
            os.makedirs(data_dir)
            open(
                os.path.join(data_dir, "ZECUSD_l_M5.json"),
                "w",
                encoding="utf-8",
            ).close()

            symbols = dashboard_app._get_ml_operation_symbols([], data_dir)

        self.assertEqual(symbols, ["ZECUSD_l"])

    def test_ml_operation_symbols_prefer_market_watch(self):
        """Training keeps the terminal Market Watch as the authoritative source."""
        symbols = dashboard_app._get_ml_operation_symbols(
            [{"name": "XAUUSD_l", "description": "Gold"}],
            os.path.join(PROJECT_ROOT, "market_data"),
        )

        self.assertEqual(symbols, ["XAUUSD_l"])

    def test_latest_training_gate_reads_top_level_quality_report(self):
        """The training page reports rejection details from the newest gate report."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            models_dir = root / "models"
            quality_dir = root / "reports" / "data_quality"
            models_dir.mkdir(parents=True)
            quality_dir.mkdir(parents=True)
            (quality_dir / "newer_raw_report.json").write_text(
                '{"symbol": "UKBRENT_l", "status": "rejected"}',
                encoding="utf-8",
            )
            (models_dir / "training_summary_DSHUSD_l_M5.json").write_text(
                (
                    '{"symbol": "DSHUSD_l", "timeframe": "M5", '
                    '"training_date": "2026-09-20T04:00:00+00:00", '
                    '"data_manifest": {"quality": {"status": "rejected"}}}'
                ),
                encoding="utf-8",
            )

            status = dashboard_app._latest_training_gate_status(models_dir)

        self.assertEqual(status["status"], "rejected")
        self.assertEqual(status["symbol"], "DSHUSD_l")

    def test_app_file_exists(self):
        """Test that app.py exists."""
        app_path = os.path.join(PROJECT_ROOT, "src", "python", "dashboard", "app.py")
        self.assertTrue(os.path.exists(app_path))

    def test_app_pages_defined(self):
        """Test that all required pages are defined in app.py."""
        app_path = os.path.join(PROJECT_ROOT, "src", "python", "dashboard", "app.py")

        with open(app_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check for page definitions
        required_pages = [
            "Overview",
            "Strategies",
            "Backtest Results",
            "ML Predictions",
            "Live Trading",
            "Settings",
        ]

        for page in required_pages:
            self.assertIn(page, content, f"Page '{page}' not found in app.py")

    def test_app_imports_strategies(self):
        """Test that app.py imports strategy modules."""
        app_path = os.path.join(PROJECT_ROOT, "src", "python", "dashboard", "app.py")

        with open(app_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check strategy imports
        self.assertIn("TrendFollowingStrategy", content)
        self.assertIn("MeanReversionStrategy", content)

    def test_app_imports_backtest(self):
        """Test that app.py imports backtest engine."""
        app_path = os.path.join(PROJECT_ROOT, "src", "python", "dashboard", "app.py")

        with open(app_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("BacktestEngine", content)

    def test_app_imports_ml_models(self):
        """Test that app.py imports ML models."""
        app_path = os.path.join(PROJECT_ROOT, "src", "python", "dashboard", "app.py")

        with open(app_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("MLInferenceService", content)
        self.assertNotIn("RandomForestModel", content)
        self.assertNotIn("XGBoostModel", content)
        self.assertNotIn("EnsembleVoting", content)

    def test_app_uses_real_data_functions(self):
        """Test that app.py uses real data functions from data_manager."""
        app_path = os.path.join(PROJECT_ROOT, "src", "python", "dashboard", "app.py")

        with open(app_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check for real data function calls
        real_data_functions = [
            "get_real_account_data",
            "get_real_positions_data",
            "get_real_history_data",
            "get_real_equity_curve",
            "get_real_performance_metrics",
            "get_real_market_data",
        ]

        for func in real_data_functions:
            self.assertIn(
                func, content, f"Real data function '{func}' not found in app.py"
            )


class TestEndToEndIntegration(unittest.TestCase):
    """Test end-to-end integration of dashboard with all components."""

    def test_full_workflow_simulation(self):
        """Simulate complete dashboard workflow."""
        # 1. Initialize data manager
        from src.python.dashboard.data_manager import get_dashboard_data

        data_manager = get_dashboard_data()
        self.assertIsNotNone(data_manager)

        # 2. Get account data
        account_data = data_manager.get_account_data()
        self.assertIsInstance(account_data, dict)

        # 3. Initialize strategies
        from src.python.strategies.trend_following import TrendFollowingStrategy

        strategy = TrendFollowingStrategy()
        self.assertIsNotNone(strategy)

        # 4. Generate sample data and signal
        n_periods = 100
        prices = 100 + np.cumsum(np.random.randn(n_periods) * 0.5)
        candles = [
            {
                "open": prices[i],
                "high": prices[i] + 0.3,
                "low": prices[i] - 0.3,
                "close": prices[i] + np.random.randn() * 0.2,
                "volume": np.random.randint(100, 1000),
            }
            for i in range(n_periods)
        ]

        data = {"symbol": "EURUSD", "timeframe": "H1", "candles": candles}

        signal = strategy.generate_signal(data)
        self.assertIn(signal.direction, ["BUY", "SELL", "HOLD"])

        # 5. Run backtest
        from src.python.backtest import BacktestEngine

        df = pd.DataFrame(
            {
                "timestamp": pd.date_range(
                    end=datetime.now(), periods=n_periods, freq="h"
                ),
                "open": [c["open"] for c in candles],
                "high": [c["high"] for c in candles],
                "low": [c["low"] for c in candles],
                "close": [c["close"] for c in candles],
                "volume": [c["volume"] for c in candles],
            }
        )
        df["high"] = np.maximum(df["high"], np.maximum(df["open"], df["close"]))
        df["low"] = np.minimum(df["low"], np.minimum(df["open"], df["close"]))

        engine = BacktestEngine(initial_balance=10000)
        results = engine.run(df, strategy)
        self.assertIn("final_balance", results)

        # 6. Test ML model
        from sklearn.ensemble import RandomForestClassifier

        rf_model = RandomForestClassifier(random_state=42)
        features = pd.DataFrame(
            {
                "rsi": np.random.uniform(0, 100, 50),
                "macd": np.random.randn(50),
                "volume": np.random.randint(100, 1000, 50),
            }
        )
        target = np.random.randint(0, 2, 50)
        rf_model.fit(features, target)
        prediction = rf_model.predict(features.iloc[:5])
        self.assertIsNotNone(prediction)

        print("✅ End-to-end integration test passed!")


def run_tests():
    """Run all tests and generate report."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    test_classes = [
        TestDashboardImports,
        TestDashboardDataManager,
        TestStrategyIntegration,
        TestBacktestIntegration,
        TestMLIntegration,
        TestExecutionIntegration,
        TestDashboardAppStructure,
        TestEndToEndIntegration,
    ]

    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Success: {result.wasSuccessful()}")
    print("=" * 70)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
