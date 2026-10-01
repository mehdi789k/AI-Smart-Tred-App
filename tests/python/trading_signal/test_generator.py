import pytest
from trading_signal.generator import SignalGenerator


def prediction(direction, confidence=0.95):
    return {"direction": direction, "confidence": confidence}


def test_buy_uses_ask_entry_and_bid_for_safe_sl():
    signal = SignalGenerator().generate(
        symbol="EURUSD",
        ml_prediction=prediction("BUY"),
        indicators={"trend": True},
        quote={"bid": 100, "ask": 101},
        stop_loss=98,
        take_profit=104,
    )
    assert signal["entry"] == 101
    assert signal["action"] == "buy"


def test_sell_stop_loss_must_be_above_ask():
    with pytest.raises(ValueError, match="invalid"):
        SignalGenerator().generate(
            symbol="EURUSD",
            ml_prediction=prediction("SELL"),
            indicators={"trend": True},
            quote={"bid": 100, "ask": 101},
            stop_loss=100.5,
            take_profit=98,
        )


def test_low_score_or_unconfirmed_timeframe_is_not_emitted():
    args = dict(
        symbol="EURUSD",
        ml_prediction=prediction("BUY", 0.5),
        indicators={},
        quote={"bid": 100, "ask": 101},
        stop_loss=98,
        take_profit=104,
    )
    assert SignalGenerator().generate(**args) is None
    assert SignalGenerator().generate(**args, timeframe_confirmed=False) is None


def test_sell_auto_correction_prevents_executor_failure():
    signal = SignalGenerator(auto_correct_protection=True).generate(
        symbol="EURUSD",
        ml_prediction=prediction("SELL"),
        indicators={"trend": True},
        quote={"bid": 100, "ask": 101},
        stop_loss=100.5,
        take_profit=102,
    )
    assert signal["stop_loss"] > 101
    assert signal["take_profit"] < 100
