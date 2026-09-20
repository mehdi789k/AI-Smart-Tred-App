from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from src.python.backtest import calculate_metrics


def test_metrics_calculate_returns_drawdown_and_trades():
    result = calculate_metrics(
        np.array([100.0, 110.0, 99.0, 120.0]),
        [SimpleNamespace(pnl=10), {"pnl": -5}, SimpleNamespace(pnl=20)],
        initial_balance=100,
        periods_per_year=4,
    )

    assert result.total_return == pytest.approx(0.2)
    assert result.max_drawdown == pytest.approx(11)
    assert result.max_drawdown_percent == pytest.approx(0.1)
    assert result.win_rate == pytest.approx(2 / 3)
    assert result.total_trades == 3
    assert result.profit_factor == pytest.approx(6)
    assert result.sortino_ratio > 0


def test_metrics_empty_curve_is_safe_but_invalid_inputs_fail():
    result = calculate_metrics([], [], initial_balance=100)
    assert result.total_return == 0
    assert result.total_trades == 0

    with pytest.raises(ValueError):
        calculate_metrics([], [], initial_balance=0)
    with pytest.raises(ValueError):
        calculate_metrics([100, 0], [], initial_balance=100)
