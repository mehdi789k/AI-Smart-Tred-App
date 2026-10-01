from src.python.execution.live_order_workflow import LiveOrderConfig
from src.python.execution.policy import DEFAULT_EXECUTION_POLICY


def test_python_execution_policy_matches_live_workflow_defaults():
    policy = DEFAULT_EXECUTION_POLICY
    config = LiveOrderConfig(allowed_symbols=frozenset({policy.default_symbol}))

    assert config.magic == policy.magic_number
    assert config.max_position_volume == policy.max_lot_size
    assert config.max_daily_loss == policy.daily_loss_limit
    assert policy.live_enabled is False


def test_environment_overrides_remain_explicit(monkeypatch):
    monkeypatch.delenv("MT5_LIVE_MAGIC", raising=False)
    monkeypatch.delenv("MT5_MAX_POSITION_VOLUME", raising=False)
    monkeypatch.delenv("MT5_MAX_DAILY_LOSS", raising=False)

    config = LiveOrderConfig.from_environment()

    assert config.magic == DEFAULT_EXECUTION_POLICY.magic_number
    assert config.max_position_volume == DEFAULT_EXECUTION_POLICY.max_lot_size
    assert config.max_daily_loss == DEFAULT_EXECUTION_POLICY.daily_loss_limit
