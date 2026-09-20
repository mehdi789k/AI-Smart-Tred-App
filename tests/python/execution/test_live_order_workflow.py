import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from src.python.data.database import ConcurrencyConflict
from src.python.data.models import ExecutionControlState
from src.python.execution.live_order_workflow import (
    AmbiguousOrderOutcome,
    DemoActivationConfig,
    LiveOrderConfig,
    LiveOrderRejected,
    LiveOrderWorkflow,
)


class FakeMT5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    ORDER_FILLING_RETURN = 2
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self, order_send_result=None, order_send_unknown=False):
        self.sent = []
        self.order_send_result = order_send_result
        self.order_send_unknown = order_send_unknown

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=100.0, ask=100.2)

    def order_check(self, request):
        return SimpleNamespace(retcode=0)

    def order_send(self, request):
        self.sent.append(request)
        if self.order_send_unknown:
            return None
        if self.order_send_result is not None:
            return self.order_send_result
        return SimpleNamespace(retcode=10009, order=42, deal=43)

    def positions_get(self):
        return []

    def last_error(self):
        return (1, "error")


def test_malformed_broker_response_is_ambiguous_and_not_retried():
    connector = FakeConnector(
        order_send_result=SimpleNamespace(retcode="not-a-retcode", order=42)
    )
    adapter = workflow()
    adapter.connector = connector
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(AmbiguousOrderOutcome) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )

    assert error.value.code == "execution_result_unknown"
    assert len(connector._mt5.sent) == 1


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(retcode=10009, order=0, deal=43),
        SimpleNamespace(retcode=10009, order=42, deal=None),
    ],
)
def test_accepted_broker_result_without_positive_identifiers_is_ambiguous(result):
    connector = FakeConnector(order_send_result=result)
    adapter = workflow()
    adapter.connector = connector
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(AmbiguousOrderOutcome) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )

    assert error.value.code == "execution_result_unknown"
    assert len(connector._mt5.sent) == 1


def test_explicit_broker_rejection_exposes_retcode():
    adapter = LiveOrderWorkflow(
        FakeConnector(order_send_result=SimpleNamespace(retcode=10030, order=0)),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
        ),
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )

    assert error.value.code == "order_rejected"
    assert error.value.retcode == 10030


class FakeConnector:
    def __init__(self, history=None, order_send_result=None, order_send_unknown=False):
        self._mt5 = FakeMT5(
            order_send_result=order_send_result,
            order_send_unknown=order_send_unknown,
        )
        self.history = history if history is not None else pd.DataFrame()

    def is_connected(self):
        return True

    def get_symbols_list(self, visible_only=True):
        return [{"symbol": "EURUSD"}]

    def get_history(self, days=1):
        return self.history

    def get_symbol_info(self, symbol):
        return {"spread": 0.2}

    def get_historical_candles(self, symbol, timeframe, count):
        return [{"high": 101.0, "low": 100.0, "close": 100.5} for _ in range(count)]


class ControlStore:
    def __init__(self, state=None):
        self.state = state
        self.loads = 0
        self.saves = []

    def load_execution_control(self, scope):
        self.loads += 1
        if self.state is None:
            return None
        assert self.state.scope == scope
        return self.state

    def save_execution_control(self, scope, *, expected_version, **changes):
        self.saves.append((scope, expected_version, changes))
        if self.state is None:
            if expected_version != 0:
                raise ConcurrencyConflict("missing execution control")
            self.state = ExecutionControlState(scope=scope, **changes)
            return self.state
        if expected_version != self.state.version:
            raise ConcurrencyConflict("stale execution control")
        values = {
            "emergency_stop": self.state.emergency_stop,
            "emergency_stop_reason": self.state.emergency_stop_reason,
            "demo_active": self.state.demo_active,
            "session_expires_at": self.state.session_expires_at,
            "demo_trade_count": self.state.demo_trade_count,
            "daily_loss": self.state.daily_loss,
            "actor": self.state.actor,
            "demo_owner_approval": self.state.demo_owner_approval,
            "demo_second_approval": self.state.demo_second_approval,
            "demo_selected_symbols": self.state.demo_selected_symbols,
            "demo_limits": self.state.demo_limits,
            "demo_configuration_hash": self.state.demo_configuration_hash,
            **changes,
        }
        self.state = ExecutionControlState(
            scope=scope, version=self.state.version + 1, **values
        )
        return self.state


def durable_state(scope="live:EURUSD", **overrides):
    values = {
        "scope": scope,
        "session_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        **overrides,
    }
    return ExecutionControlState(**values)


def workflow(history=None, daily_loss_reset_at=0.0, max_spread=1):
    return LiveOrderWorkflow(
        FakeConnector(history),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            max_spread=max_spread,
            daily_loss_reset_at=daily_loss_reset_at,
        ),
    )


def activation_hash(symbols, limits):
    return hashlib.sha256(
        json.dumps(
            {"symbols": tuple(sorted(symbols)), "limits": limits},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def activate_valid_demo(adapter):
    symbol = next(iter(adapter.config.allowed_symbols))
    limits = {
        "max_trade_volume": adapter.config.demo_activation.max_trade_volume,
        "max_trades_per_session": adapter.config.demo_activation.max_trades_per_session,
        "max_daily_loss": adapter.config.demo_activation.max_daily_loss,
    }
    adapter.activate_demo(
        manual_confirmation=True,
        owner_approval="owner",
        second_approval="reviewer",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        selected_symbols=[symbol],
        limits=limits,
        configuration_hash=activation_hash([symbol], limits),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"owner_approval": None},
        {"second_approval": None},
        {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)},
        {"selected_symbols": ["GBPUSD"]},
        {"configuration_hash": "wrong"},
    ],
)
def test_demo_activation_rejects_incomplete_or_out_of_scope_record(overrides):
    store = ControlStore(durable_state())
    adapter = workflow()
    adapter._control_store = store
    adapter._control_store_missing = False
    adapter._refresh_control_state_sync()
    limits = {
        "max_trade_volume": 0.01,
        "max_trades_per_session": 3,
        "max_daily_loss": 10,
    }
    args = {
        "manual_confirmation": True,
        "owner_approval": "owner",
        "second_approval": "reviewer",
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "selected_symbols": ["EURUSD"],
        "limits": limits,
        "configuration_hash": activation_hash(["EURUSD"], limits),
    }
    args.update(overrides)
    with pytest.raises(LiveOrderRejected):
        adapter.activate_demo(**args)


def test_demo_activation_persists_two_person_record_across_workflow_restart():
    store = ControlStore(durable_state("demo:EURUSD"))
    limits = {
        "max_trade_volume": 0.01,
        "max_trades_per_session": 3,
        "max_daily_loss": 10,
    }
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    config = LiveOrderConfig(
        frozenset({"EURUSD"}),
        magic=7,
        max_position_volume=1.0,
        max_daily_loss=100,
        demo_activation=DemoActivationConfig(enabled=True),
    )
    adapter = LiveOrderWorkflow(FakeConnector(), config, control_store=store)
    adapter._control_store = store
    adapter._control_store_missing = False
    adapter._refresh_control_state_sync()
    adapter.activate_demo(
        manual_confirmation=True,
        owner_approval="owner",
        second_approval="reviewer",
        expires_at=expires,
        selected_symbols=["EURUSD"],
        limits=limits,
        configuration_hash=activation_hash(["EURUSD"], limits),
    )

    restarted = LiveOrderWorkflow(FakeConnector(), config, control_store=store)
    restarted._control_store = store
    restarted._control_store_missing = False
    restarted._refresh_control_state_sync()
    assert restarted.demo_active is True
    assert store.state.demo_owner_approval == "owner"
    assert store.state.demo_second_approval == "reviewer"
    assert store.state.demo_selected_symbols == ["EURUSD"]
    assert store.state.demo_limits == limits


def test_missing_durable_control_state_rejects_live_order_without_gateway_call():
    connector = FakeConnector()
    adapter = LiveOrderWorkflow(
        connector,
        LiveOrderConfig(
            frozenset({"EURUSD"}), magic=7, max_position_volume=1.0, max_daily_loss=100
        ),
        control_store=None,
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(LiveOrderRejected, match="durable"):
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )

    assert connector._mt5.sent == []


def test_each_live_order_reads_durable_state_and_blocks_persisted_emergency_stop():
    connector = FakeConnector()
    store = ControlStore(durable_state(emergency_stop=True))
    adapter = LiveOrderWorkflow(
        connector,
        LiveOrderConfig(
            frozenset({"EURUSD"}), magic=7, max_position_volume=1.0, max_daily_loss=100
        ),
        control_store=store,
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )

    assert error.value.code == "emergency_stop_active"
    assert store.loads == 1
    assert connector._mt5.sent == []


def test_version_conflict_during_execution_control_reservation_rejects_before_gateway():
    class ConflictingStore(ControlStore):
        def save_execution_control(self, *args, **kwargs):
            raise ConcurrencyConflict("stale execution control")

    connector = FakeConnector()
    adapter = LiveOrderWorkflow(
        connector,
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
        ),
        control_store=ConflictingStore(durable_state()),
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )

    assert error.value.code == "execution_control_conflict"
    assert connector._mt5.sent == []


def test_recreated_workflow_preserves_emergency_stop_without_reactivating_demo(
    tmp_path,
):
    store = ControlStore(durable_state(scope="demo:EURUSD"))
    config = LiveOrderConfig(
        frozenset({"EURUSD"}),
        magic=7,
        max_position_volume=1.0,
        max_daily_loss=100,
        demo_activation=DemoActivationConfig(
            enabled=True,
            require_manual_confirmation=True,
            audit_log_path=str(tmp_path / "test-live-order-workflow.jsonl"),
        ),
    )
    first = LiveOrderWorkflow(FakeConnector(), config, control_store=store)
    activate_valid_demo(first)
    first.emergency_stop(reason="operator stop")

    second = LiveOrderWorkflow(FakeConnector(), config, control_store=store)

    assert second.emergency_stop_active is True
    assert second.demo_active is False


def test_live_order_refreshes_control_state_after_workflow_construction():
    store = ControlStore(durable_state())
    adapter = LiveOrderWorkflow(
        FakeConnector(),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
        ),
        control_store=store,
    )
    store.state.emergency_stop = True
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )

    assert error.value.code == "emergency_stop_active"


def test_control_transitions_persist_versioned_activation_lease_and_trade_count(
    tmp_path,
):
    store = ControlStore(durable_state(scope="demo:EURUSD"))
    config = LiveOrderConfig(
        frozenset({"EURUSD"}),
        magic=7,
        max_position_volume=1.0,
        max_daily_loss=100,
        automation_session_seconds=60,
        demo_activation=DemoActivationConfig(
            enabled=True,
            max_trade_volume=0.1,
            max_trades_per_session=2,
            max_daily_loss=100,
            audit_log_path=str(tmp_path / "audit.jsonl"),
        ),
    )
    adapter = LiveOrderWorkflow(
        FakeConnector(), config, control_store=store, control_scope="demo:EURUSD"
    )

    activate_valid_demo(adapter)
    start_token = adapter.request_confirmation("start_auto_trading")
    adapter.confirm_automation(start_token)
    order_token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    adapter.execute_market_order(
        "EURUSD", "BUY", 0.1, magic=7, confirmation_token=order_token
    )

    assert store.state.demo_active is True
    assert store.state.session_expires_at is not None
    assert store.state.demo_trade_count == 1
    assert store.state.version == 4


def test_successful_order_writes_audited_correlation_id(tmp_path):
    audit_path = tmp_path / "trading-audit.jsonl"
    adapter = LiveOrderWorkflow(
        FakeConnector(),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            demo_activation=DemoActivationConfig(
                enabled=True,
                require_manual_confirmation=False,
                audit_log_path=str(audit_path),
            ),
        ),
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.01:0:0")

    result = adapter.execute_market_order(
        "EURUSD",
        "BUY",
        0.01,
        magic=7,
        confirmation_token=token,
    )

    assert result.retcode == 10009
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    accepted = [event for event in events if event["event"] == "order_accepted"]
    assert accepted
    assert accepted[-1]["payload"]["correlation_id"]


def test_demo_activation_enforces_single_symbol_and_emergency_stop():
    with pytest.raises(ValueError, match="exactly one allowed symbol"):
        LiveOrderWorkflow(
            FakeConnector(),
            LiveOrderConfig(
                frozenset({"XAUUSD", "EURUSD"}),
                magic=7,
                max_position_volume=1.0,
                max_daily_loss=100,
                demo_activation=DemoActivationConfig(enabled=True),
            ),
        )

    adapter = LiveOrderWorkflow(
        FakeConnector(),
        LiveOrderConfig(
            frozenset({"XAUUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            demo_activation=DemoActivationConfig(
                enabled=True,
                max_trade_volume=0.01,
                max_trades_per_session=1,
                max_daily_loss=10,
            ),
        ),
    )
    activate_valid_demo(adapter)

    adapter.emergency_stop(reason="operator_stop")
    assert adapter.demo_active is False
    assert adapter.emergency_stop_active is True
    with pytest.raises(LiveOrderRejected) as stopped_error:
        adapter._apply_demo_gate("XAUUSD", 0.01)
    assert stopped_error.value.code == "emergency_stop_active"

    with pytest.raises(LiveOrderRejected) as reset_error:
        adapter.reset_emergency_stop(manual_confirmation=False, reason="not approved")
    assert reset_error.value.code == "manual_confirmation_required"
    adapter.reset_emergency_stop(manual_confirmation=True, reason="operator reviewed")
    assert adapter.emergency_stop_active is False


def test_order_requires_one_time_confirmation_and_sends_after_gates():
    adapter = workflow()
    with pytest.raises(LiveOrderRejected, match="confirmation"):
        adapter.execute_market_order("EURUSD", "BUY", 0.1)
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    result = adapter.execute_market_order(
        "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
    )
    assert result.retcode == 10009
    assert len(adapter.connector._mt5.sent) == 1
    with pytest.raises(LiveOrderRejected, match="confirmation"):
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )


def test_market_order_moves_protection_beyond_live_broker_distance():
    class RulesMT5(FakeMT5):
        def symbol_info(self, symbol):
            return SimpleNamespace(
                point=0.01,
                digits=2,
                trade_stops_level=10,
                trade_freeze_level=20,
            )

    adapter = workflow()
    adapter.connector._mt5 = RulesMT5()
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:99.95:100.21")
    result = adapter.execute_market_order(
        "EURUSD",
        "BUY",
        0.1,
        stop_loss=99.95,
        take_profit=100.21,
        magic=7,
        confirmation_token=token,
    )
    assert result.retcode == 10009
    request = adapter.connector._mt5.sent[-1]
    assert request["sl"] == 99.78
    assert request["tp"] == 100.42


def test_order_rejects_volume_above_configured_position_limit():
    adapter = workflow()
    token = adapter.request_confirmation("open:EURUSD:BUY:1.01:0:0")

    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD",
            "BUY",
            1.01,
            magic=7,
            confirmation_token=token,
        )

    assert error.value.code == "position_size_exceeded"
    assert adapter.connector._mt5.sent == []


def test_order_rejects_when_smart_spread_filter_limit_is_exceeded():
    adapter = workflow(max_spread=0.1)
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD",
            "BUY",
            0.1,
            magic=7,
            confirmation_token=token,
        )
    assert error.value.code == "spread_too_high"
    assert adapter.connector._mt5.sent == []


def test_atr_spread_filter_uses_dynamic_limit():
    adapter = LiveOrderWorkflow(
        FakeConnector(),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            spread_mode="atr",
            spread_atr_period=14,
            spread_atr_multiplier=0.5,
        ),
    )
    assert adapter.spread_limit("EURUSD") == pytest.approx(0.5)
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    result = adapter.execute_market_order(
        "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
    )
    assert result.retcode == 10009


def test_atr_spread_filter_rejects_when_current_spread_exceeds_dynamic_limit():
    adapter = LiveOrderWorkflow(
        FakeConnector(),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            spread_mode="atr",
            spread_atr_period=14,
            spread_atr_multiplier=0.1,
        ),
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )
    assert error.value.code == "spread_too_high"


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_spread_filter_blocks_market_orders_in_both_directions(direction):
    adapter = workflow(max_spread=0.1)
    token = adapter.request_confirmation(f"open:EURUSD:{direction}:0.1:0:0")

    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD",
            direction,
            0.1,
            magic=7,
            confirmation_token=token,
        )

    assert error.value.code == "spread_too_high"
    assert adapter.connector._mt5.sent == []


@pytest.mark.parametrize(
    ("order_type", "price"),
    [("BUY_LIMIT", 99.0), ("SELL_LIMIT", 101.0)],
)
def test_spread_filter_blocks_pending_limit_orders(order_type, price):
    adapter = workflow(max_spread=0.1)
    token = adapter.request_confirmation(f"pending:EURUSD:{order_type}:0.1:{price:g}")

    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_pending_order(
            "EURUSD",
            order_type,
            0.1,
            price,
            magic=7,
            confirmation_token=token,
        )

    assert error.value.code == "spread_too_high"
    assert adapter.connector._mt5.sent == []


def test_whitelist_and_daily_loss_fail_closed_before_send():
    adapter = workflow(
        pd.DataFrame(
            [
                {
                    "time": pd.Timestamp.now(tz="UTC"),
                    "profit": -101,
                    "commission": 0,
                    "swap": 0,
                    "fee": 0,
                }
            ]
        )
    )
    token = adapter.request_confirmation("open:GBPUSD:BUY:0.1:0:0")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "GBPUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )
    assert error.value.code == "symbol_not_whitelisted"
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )
    assert error.value.code == "daily_loss_limit"


def test_missing_order_send_result_is_ambiguous_not_rejected():
    adapter = LiveOrderWorkflow(
        FakeConnector(order_send_unknown=True),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
        ),
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")

    with pytest.raises(AmbiguousOrderOutcome, match="unknown"):
        adapter.execute_market_order(
            "EURUSD",
            "BUY",
            0.1,
            magic=7,
            confirmation_token=token,
        )
    assert len(adapter.connector._mt5.sent) == 1


def test_demo_gate_requires_manual_activation_before_pending_order():
    adapter = LiveOrderWorkflow(
        FakeConnector(),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            demo_activation=DemoActivationConfig(
                enabled=True,
                max_trade_volume=0.5,
                max_trades_per_session=2,
                max_daily_loss=50.0,
            ),
        ),
    )
    token = adapter.request_confirmation("pending:EURUSD:BUY_LIMIT:0.1:99")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_pending_order(
            "EURUSD", "BUY_LIMIT", 0.1, 99.0, magic=7, confirmation_token=token
        )
    assert error.value.code == "demo_gate_inactive"
    assert adapter.connector._mt5.sent == []


def test_demo_gate_tracks_pending_trade_count_and_auto_stops_on_failure():
    adapter = LiveOrderWorkflow(
        FakeConnector(),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            demo_activation=DemoActivationConfig(
                enabled=True,
                max_trade_volume=0.5,
                max_trades_per_session=1,
                max_daily_loss=50.0,
            ),
        ),
    )
    activate_valid_demo(adapter)
    token = adapter.request_confirmation("pending:EURUSD:BUY_LIMIT:0.1:99")
    result = adapter.execute_pending_order(
        "EURUSD", "BUY_LIMIT", 0.1, 99.0, magic=7, confirmation_token=token
    )
    assert result.retcode == 10009
    assert adapter._demo_trade_count == 1
    assert adapter.demo_active is True
    token = adapter.request_confirmation("pending:EURUSD:SELL_LIMIT:0.1:101")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_pending_order(
            "EURUSD", "SELL_LIMIT", 0.1, 101.0, magic=7, confirmation_token=token
        )
    assert error.value.code == "demo_trade_limit_exceeded"


def test_daily_loss_reset_ignores_losses_before_reset_time():
    now = pd.Timestamp.now(tz="UTC")
    adapter = workflow(
        pd.DataFrame(
            [
                {"time": now - pd.Timedelta(minutes=5), "profit": -101},
                {"time": now + pd.Timedelta(seconds=1), "profit": 0},
            ]
        ),
        daily_loss_reset_at=now.timestamp(),
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    result = adapter.execute_market_order(
        "EURUSD",
        "BUY",
        0.1,
        magic=7,
        confirmation_token=token,
    )
    assert result.retcode == 10009


def test_manual_daily_loss_reset_updates_existing_workflow_breaker():
    now = pd.Timestamp.now(tz="UTC")
    adapter = workflow(
        pd.DataFrame([{"ticket": 99, "time": now, "profit": -101}]),
    )
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_market_order(
            "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
        )
    assert error.value.code == "daily_loss_limit"

    reset_at = (now + pd.Timedelta(seconds=1)).timestamp()
    assert adapter.reset_daily_loss(reset_at) == reset_at
    token = adapter.request_confirmation("open:EURUSD:BUY:0.1:0:0")
    result = adapter.execute_market_order(
        "EURUSD", "BUY", 0.1, magic=7, confirmation_token=token
    )
    assert result.retcode == 10009


def test_partial_close_normalizes_volume_and_sends_opposite_deal():
    adapter = workflow()
    position = SimpleNamespace(symbol="EURUSD", ticket=42, magic=7, volume=0.2, type=0)
    token = adapter.request_confirmation("partial_close:EURUSD:42:0.1")
    result = adapter.close_position_partial(position, 50, token)
    assert result.retcode == 10009
    request = adapter.connector._mt5.sent[-1]
    assert request["position"] == 42
    assert request["volume"] == 0.1
    assert request["type"] == adapter.connector._mt5.ORDER_TYPE_SELL


def test_pending_order_carries_optional_stop_loss_and_take_profit():
    adapter = workflow()
    adapter.connector._mt5.ORDER_TYPE_BUY_LIMIT = 2
    token = adapter.request_confirmation("pending:EURUSD:BUY_LIMIT:0.1:99")
    result = adapter.execute_pending_order(
        "EURUSD",
        "BUY LIMIT",
        0.1,
        99,
        stop_loss=98,
        take_profit=101,
        magic=7,
        confirmation_token=token,
    )
    assert result.retcode == 10009
    request = adapter.connector._mt5.sent[0]
    assert request["sl"] == 98
    assert request["tp"] == 101


def test_pending_limit_rejects_price_at_or_above_current_ask():
    adapter = workflow()
    token = adapter.request_confirmation("pending:EURUSD:BUY_LIMIT:0.1:100.2")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_pending_order(
            "EURUSD",
            "BUY_LIMIT",
            0.1,
            100.2,
            magic=7,
            confirmation_token=token,
        )
    assert error.value.code == "invalid_pending_price"


def test_pending_order_rejects_protection_on_wrong_side():
    adapter = workflow()
    token = adapter.request_confirmation("pending:EURUSD:BUY_LIMIT:0.1:99")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.execute_pending_order(
            "EURUSD",
            "BUY LIMIT",
            0.1,
            99,
            stop_loss=100,
            magic=7,
            confirmation_token=token,
        )
    assert error.value.code == "invalid_stop_loss"


def test_order_check_failure_exposes_broker_reason():
    class RejectingMT5(FakeMT5):
        def order_check(self, request):
            return SimpleNamespace(retcode=10016, comment="invalid stops")

    adapter = workflow()
    adapter.connector._mt5 = RejectingMT5()
    token = adapter.request_confirmation("pending:EURUSD:BUY_LIMIT:0.1:99")

    with pytest.raises(LiveOrderRejected, match="invalid stops"):
        adapter.execute_pending_order(
            "EURUSD",
            "BUY LIMIT",
            0.1,
            99,
            stop_loss=98,
            take_profit=101,
            magic=7,
            confirmation_token=token,
        )


def test_automation_authorization_can_be_restored_only_with_active_lease():
    adapter = workflow()
    adapter.restore_automation_authorization(time.time() + 30)
    assert adapter.automation_enabled()


def test_modify_position_stop_requires_managed_position_and_sends_sltp_request():
    adapter = workflow()
    position = SimpleNamespace(
        symbol="EURUSD",
        ticket=42,
        magic=7,
        volume=0.1,
        type=0,
        price_open=99.0,
        price_current=100.0,
        sl=95.0,
        tp=110.0,
    )
    token = adapter.request_confirmation("modify_sl:EURUSD:42:99")
    result = adapter.modify_position_stop(position, 99.0, token)
    assert result.retcode == 10009
    assert adapter.connector._mt5.sent[-1]["action"] == 6
    assert adapter.connector._mt5.sent[-1]["sl"] == 99.0


def test_modify_position_stop_accepts_sltp_result_without_opening_identifiers():
    adapter = LiveOrderWorkflow(
        FakeConnector(order_send_result=SimpleNamespace(retcode=10009)),
        LiveOrderConfig(
            frozenset({"EURUSD"}),
            magic=7,
            max_position_volume=1.0,
            max_daily_loss=100,
            max_spread=1,
        ),
    )
    position = SimpleNamespace(
        symbol="EURUSD",
        ticket=42,
        magic=7,
        volume=0.1,
        type=0,
        price_open=99.0,
        price_current=100.0,
        sl=95.0,
        tp=110.0,
    )
    token = adapter.request_confirmation("modify_sl:EURUSD:42:99")

    result = adapter.modify_position_stop(position, 99.0, token)

    assert result.retcode == 10009


def test_modify_position_stop_rejects_foreign_magic_without_sending():
    adapter = workflow()
    position = SimpleNamespace(
        symbol="EURUSD", ticket=42, magic=99, volume=0.1, type=0, sl=95.0, tp=110.0
    )
    token = adapter.request_confirmation("modify_sl:EURUSD:42:99")
    with pytest.raises(LiveOrderRejected) as error:
        adapter.modify_position_stop(position, 99.0, token)
    assert error.value.code == "magic_mismatch"
    assert adapter.connector._mt5.sent == []


@pytest.mark.parametrize("expires_at", [0.0, float("nan"), float("inf")])
def test_automation_authorization_rejects_invalid_lease(expires_at):
    adapter = workflow()
    with pytest.raises(LiveOrderRejected) as error:
        adapter.restore_automation_authorization(expires_at)
    assert error.value.code in {"automation_lease_expired", "automation_lease_invalid"}
