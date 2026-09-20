"""Stage D event-driven backtesting and walk-forward evaluation."""

from .engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    BacktestResult,
    Bar,
    Fill,
    Position,
    Trade,
    WalkForwardResult,
)
from .metrics import PerformanceMetrics, calculate_metrics, performance_metrics
from .optimizer import (
    GridOptimizer,
    OptimizationResult,
    optimize,
    parameter_combinations,
)
from .report import generate_html_report, generate_report, trades_to_csv
from .strategy import OrderIntent, Strategy, normalize_intents

__all__ = [
    "BacktestConfig",
    "BacktestError",
    "BacktestEngine",
    "BacktestResult",
    "Bar",
    "Fill",
    "Position",
    "Trade",
    "WalkForwardResult",
    "PerformanceMetrics",
    "calculate_metrics",
    "performance_metrics",
    "GridOptimizer",
    "OptimizationResult",
    "optimize",
    "parameter_combinations",
    "generate_html_report",
    "generate_report",
    "trades_to_csv",
    "OrderIntent",
    "Strategy",
    "normalize_intents",
]
