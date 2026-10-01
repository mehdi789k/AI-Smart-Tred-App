import numpy as np
import pandas as pd
import pytest

from scripts.stage_b_validation import (
    _oos_trade_metrics,
    expanding_splits,
    validate_frame,
)


def test_expanding_splits_are_ordered_and_disjoint():
    splits = expanding_splits(100, n_splits=3, test_size=20)
    assert len(splits) == 3
    for train, test in splits:
        assert train[-1] < test[0]
        assert not set(train).intersection(test)


def test_expanding_splits_reject_insufficient_data():
    with pytest.raises(ValueError, match="not enough"):
        expanding_splits(20, n_splits=3, test_size=10)


def test_validate_frame_rejects_non_chronological_input():
    frame = pd.DataFrame(
        {
            "open": [1.0, 1.0],
            "high": [2.0, 2.0],
            "low": [0.0, 0.0],
            "close": [1.0, 1.0],
        },
        index=pd.to_datetime(["2026-01-01 00:05", "2026-01-01 00:00"]),
    )
    with pytest.raises(ValueError, match="chronological"):
        validate_frame(frame)


def test_oos_trade_metrics_include_costs_and_risk_metrics():
    frame = pd.DataFrame({"open": [100.0, 100.0, 100.0], "close": [100.0, 101.0, 99.0]})
    result = _oos_trade_metrics(
        frame,
        np.array([0, 1]),
        np.array([2, 0]),
        initial_capital=1000.0,
        spread=0.1,
        slippage=0.05,
        commission=0.05,
    )
    assert result["trades"] == 2
    assert result["buy_signals"] == 1
    assert result["sell_signals"] == 1
    assert result["expectancy"] == pytest.approx(0.75)
    assert result["profit_factor"] == 0.0
