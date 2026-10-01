import json
from pathlib import Path

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "mt5_replay_guardrails_v1.json"
)


def test_mt5_replay_guardrails_are_fail_closed():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert fixture["schema_version"] == "1.0"
    replay = fixture["replay"]
    assert replay["allow_live_trading"] is False
    assert replay["enable_zmq"] is False
    assert replay["live_execution_allowed"] is False
    assert replay["expected_total_trades"] == 0
    assert replay["expected_total_deals"] == 0
    assert "InpAllowLiveTrading=false" in replay["required_inputs"]
    assert "InpEnableZmq=false" in replay["required_inputs"]

    for case in fixture["cases"]:
        assert case["expected"]["status"] == "accepted"
        assert case["expected"]["accepted"] is True
        assert case["expected"]["requires_reconciliation"] is False
        assert case["safety_invariant"].strip()
