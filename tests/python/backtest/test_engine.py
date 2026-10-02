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
    assert result.fills[0].commission == pytest.approx(1.01)
    assert result.fills[1].side == "SELL"
    assert result.fills[1].quantity == pytest.approx(1)
    assert result.fills[1].price == pytest.approx(119)
    assert result.fills[1].commission == pytest.approx(1.19)
    assert result.trades[0].entry_price == pytest.approx(101)
    assert result.trades[0].exit_price == pytest.approx(119)
    assert result.trades[0].commission == pytest.approx(2.20)
    assert result.trades[0].pnl == pytest.approx(15.80)
    assert result.end_balance == pytest.approx(1_015.80)
    assert result.metrics is not None
    assert result.metrics.total_trades == 1


@pytest.mark.parametrize(
    (
        "side",
        "stop",
        "gap_open",
        "expected_exit",
        "expected_entry",
        "entry_side",
        "expected_pnl",
        "expected_balance",
    ),
    [
        ("BUY", 95, 90, 88, 102, "BUY", -15.90, 984.10),
        ("SELL", 105, 110, 112, 98, "SELL", -16.10, 983.90),
    ],
)
def test_gap_stop_fills_at_adverse_open_before_execution_costs(
    side,
    stop,
    gap_open,
    expected_exit,
    expected_entry,
    entry_side,
    expected_pnl,
    expected_balance,
):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = [
        {
            "timestamp": start,
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 2,
        },
        {
            "timestamp": start + timedelta(minutes=7),
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "open": gap_open,
            "high": 111 if side == "SELL" else 95,
            "low": 89 if side == "BUY" else 109,
            "close": 92 if side == "BUY" else 110,
            "volume": 2,
        },
    ]

    class StopAfterEntry(Strategy):
        def __init__(self):
            self.timestamps = []

        def on_bar(self, bar, indicators=None, ml_prediction=None):
            self.timestamps.append(bar.timestamp)
            if len(self.timestamps) == 1:
                return OrderIntent(side=side, quantity=1, stop_loss=stop)
            return None

    strategy = StopAfterEntry()
    result = BacktestEngine(
        initial_balance=1_000,
        commission=0.01,
        spread=2,
        slippage=1,
        slippage_is_rate=False,
    ).run(strategy, observations)

    assert strategy.timestamps == [start, start + timedelta(minutes=7)]
    assert len(result.equity_curve) == len(observations) + 1
    assert [fill.timestamp for fill in result.fills] == [
        start,
        start + timedelta(minutes=7),
    ]
    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "SL"
    assert trade.entry_price == pytest.approx(expected_entry)
    assert trade.exit_price == pytest.approx(expected_exit)
    assert result.fills[0].side == entry_side
    assert result.fills[0].commission == pytest.approx(expected_entry * 0.01)
    assert result.fills[1].price == pytest.approx(expected_exit)
    assert result.fills[1].commission == pytest.approx(expected_exit * 0.01)
    assert trade.commission == pytest.approx((expected_entry + expected_exit) * 0.01)
    assert trade.pnl == pytest.approx(expected_pnl)
    assert result.end_balance == pytest.approx(expected_balance)


def test_gap_stop_without_gap_uses_stop_price_and_stop_wins_over_target():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    observations = [
        {
            "timestamp": start,
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 2,
        },
        {
            "timestamp": start + timedelta(minutes=1),
            "symbol": "XAUUSD",
            "timeframe": "M1",
            "open": 100,
            "high": 111,
            "low": 94,
            "close": 100,
            "volume": 2,
        },
    ]

    class ProtectedLong(Strategy):
        def __init__(self):
            self.seen = 0

        def on_bar(self, bar, indicators=None, ml_prediction=None):
            self.seen += 1
            if self.seen == 1:
                return OrderIntent(
                    side="BUY", quantity=1, stop_loss=95, take_profit=105
                )
            return None

    result = BacktestEngine(spread=2).run(ProtectedLong(), observations)
    assert result.trades[0].exit_reason == "SL"
    assert result.trades[0].exit_price == pytest.approx(94)


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


def test_walk_forward_training_and_oos_inputs_are_exact_and_disjoint():
    data = bars(list(range(100, 110)))
    observed_training = []
    observed_oos = []

    class AuditedStrategy(Strategy):
        def train(self, training_data):
            observed_training.append(
                [(bar.timestamp, bar.close) for bar in training_data]
            )
            return self

        def on_bar(self, bar, indicators=None, ml_prediction=None):
            observed_oos.append((bar.timestamp, bar.close))
            return None

    walk = BacktestEngine().walk_forward(
        AuditedStrategy,
        data,
        train_window=4,
        test_window=2,
        step=2,
    )

    assert len(walk.folds) == len(observed_training) == 3
    expected = [
        (
            [(row["timestamp"], row["close"]) for row in data[start : start + 4]],
            [(row["timestamp"], row["close"]) for row in data[start + 4 : start + 6]],
        )
        for start in (0, 2, 4)
    ]
    assert observed_training == [train for train, _ in expected]
    assert observed_oos == [item for _, test in expected for item in test]
    for train, test in expected:
        train_timestamps = {timestamp for timestamp, _ in train}
        test_timestamps = {timestamp for timestamp, _ in test}
        assert train_timestamps.isdisjoint(test_timestamps)
        assert train[-1][0] < test[0][0]


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
