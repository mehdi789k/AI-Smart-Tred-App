from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.python.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestError,
    Bar,
    GridOptimizer,
    OrderIntent,
    Strategy,
    generate_report,
)


def bars(
    prices: list[float], *, symbol: str = "XAUUSD", volume: float = 0
) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "timestamp": start + timedelta(minutes=index),
            "symbol": symbol,
            "timeframe": "M1",
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": volume,
        }
        for index, price in enumerate(prices)
    ]


class BuyThenClose(Strategy):
    def __init__(self) -> None:
        self.seen = 0

    def reset(self) -> None:
        self.seen = 0

    def on_bar(self, bar, indicators=None, ml_prediction=None):
        self.seen += 1
        if self.seen == 1:
            return OrderIntent(side="BUY", quantity=2)
        if self.seen == 3:
            return OrderIntent(action="CLOSE")
        return None


class TrainingStrategy(Strategy):
    def __init__(self, threshold: float = 0) -> None:
        self.threshold = threshold
        self.trained_on = 0

    def train(self, data):
        self.trained_on = len(data)
        return self

    def on_bar(self, bar, indicators=None, ml_prediction=None):
        if self.trained_on and bar.close > self.threshold:
            return OrderIntent(side="BUY", quantity=1)
        return None


def test_engine_models_commission_spread_and_partial_fill():
    config = BacktestConfig(
        initial_balance=1_000,
        commission=0.01,
        spread=2,
        slippage=0,
        max_fill_ratio=1,
    )
    result = BacktestEngine(config).run(BuyThenClose(), bars([100, 110, 120], volume=1))

    assert result.total_trades == 1
    # Volume one caps the requested quantity two and marks the entry partial.
    assert result.trades[0].quantity == 1
    assert result.fills[0].partial is True
    assert result.fills[0].price == pytest.approx(101)
    assert result.end_balance < 1_000 + 20
    assert result.metrics is not None
    assert result.metrics.total_trades == 1


def test_engine_supports_short_positions_and_oos_walk_forward():
    class Short(Strategy):
        def on_bar(self, bar, indicators=None, ml_prediction=None):
            if bar.timestamp.minute % 2 == 0:
                return OrderIntent(side="SELL", quantity=1)
            return OrderIntent(action="CLOSE")

    engine = BacktestEngine(initial_balance=10_000)
    result = engine.run(Short(), bars([100, 95, 90, 85]))
    assert result.total_trades == 2
    assert result.trades[0].direction == "SELL"

    data = bars(list(range(100, 112)))
    walk = engine.walk_forward(
        lambda: TrainingStrategy(threshold=0),
        data,
        train_window=4,
        test_window=2,
        step=2,
    )
    assert len(walk.folds) == 4
    assert len(walk.out_of_sample.equity_curve) > 1
    assert all(start < end for start, end in walk.test_windows)


def test_engine_rejects_bad_bars_and_strategy_failures():
    with pytest.raises(ValueError):
        Bar.from_value(
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "symbol": "X",
                "timeframe": "M1",
                "open": 10,
                "high": 9,
                "low": 8,
                "close": 9,
            }
        )

    class Broken(Strategy):
        def on_bar(self, bar, indicators=None, ml_prediction=None):
            raise RuntimeError("boom")

    with pytest.raises(BacktestError):
        BacktestEngine().run(Broken(), bars([10]))

    with pytest.raises(ValueError):
        BacktestEngine().run(Strategy(), bars([10]) + bars([11]))


def test_optimizer_and_report_are_deterministic():
    engine = BacktestEngine(initial_balance=1_000)
    optimizer = GridOptimizer({"threshold": [100, 105]}, objective="total_return")
    parameters = optimizer.optimize(
        lambda params: TrainingStrategy(**params), bars([100, 101, 102, 103]), engine
    )
    assert parameters == {"threshold": 100}
    assert len(optimizer.last_trials) == 2
    result = engine.run(TrainingStrategy(), bars([100, 101, 102]))
    assert "<!doctype html>" in generate_report(result)
    assert "entry_time" in generate_report(result, format="csv")
