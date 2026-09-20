import pytest

from scripts.verify_strategy_tester_inputs import validate_inputs


def test_strategy_tester_inputs_are_fail_closed(tmp_path):
    ea = tmp_path / "SmartTraderEA.mq5"
    settings = tmp_path / "SmartTraderEA.set"
    ea.write_text("input bool InpAllowLiveTrading=false;", encoding="utf-8")
    settings.write_text(
        "InpAllowLiveTrading=false\nInpEnableZmq=false\n",
        encoding="utf-8",
    )

    validate_inputs(ea, settings)


def test_strategy_tester_inputs_reject_live_trading(tmp_path):
    ea = tmp_path / "SmartTraderEA.mq5"
    settings = tmp_path / "SmartTraderEA.set"
    ea.write_text("input bool InpAllowLiveTrading=true;", encoding="utf-8")
    settings.write_text(
        "InpAllowLiveTrading=true\nInpEnableZmq=false\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="disable live trading"):
        validate_inputs(ea, settings)
