from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from src.python.execution import mt5_connector

_DEFAULT = object()


class FakeMT5:
    def __init__(
        self,
        *,
        initialize_result=True,
        account=_DEFAULT,
        terminal=_DEFAULT,
        positions=(),
        deals=(),
        orders=(),
        symbol=None,
        tick=None,
        symbols=(),
        rates=(),
        terminal_error=None,
    ):
        self.initialize_result = initialize_result
        self.account = (
            SimpleNamespace(
                login=123,
                server="Demo",
                balance=1000.0,
                equity=1000.0,
                margin=0.0,
                margin_free=1000.0,
                margin_level=0.0,
                profit=0.0,
                leverage=100,
                currency="USD",
            )
            if account is _DEFAULT
            else account
        )
        self.terminal = (
            SimpleNamespace(connected=True) if terminal is _DEFAULT else terminal
        )
        self.positions = positions
        self.deals = deals
        self.orders = orders
        self.symbol = symbol
        self.tick = tick
        self.symbols = symbols
        self.rates = rates
        self.terminal_error = terminal_error
        self.initialize_calls = []
        self.shutdown_calls = 0

    def initialize(self, **kwargs):
        self.initialize_calls.append(kwargs)
        return self.initialize_result

    def last_error(self):
        return (1, "initialization failed")

    def account_info(self):
        return self.account

    def terminal_info(self):
        if self.terminal_error:
            raise self.terminal_error
        return self.terminal

    def shutdown(self):
        self.shutdown_calls += 1

    def positions_get(self):
        return self.positions

    def history_deals_get(self, *_args):
        return self.deals

    def history_orders_get(self, *_args):
        return self.orders

    def symbol_info(self, _symbol):
        return self.symbol

    def symbol_info_tick(self, _symbol):
        return self.tick

    def symbols_get(self):
        return self.symbols

    def copy_rates_from_pos(self, *_args):
        return self.rates


def attach_fake(monkeypatch, fake):
    monkeypatch.setattr(mt5_connector, "mt5", fake)
    return mt5_connector.MT5Connection()


def test_connect_fails_closed_when_terminal_initialization_fails(monkeypatch):
    fake = FakeMT5(initialize_result=False)
    connector = attach_fake(monkeypatch, fake)

    assert connector.connect() is False
    assert connector.connected is False
    assert fake.initialize_calls == [
        {"path": None, "login": None, "password": None, "server": None}
    ]


def test_connect_and_account_summary_use_fake_terminal(monkeypatch):
    fake = FakeMT5()
    connector = attach_fake(monkeypatch, fake)

    assert connector.connect(login=123, server="Demo") is True

    summary = connector.get_account_summary()

    assert summary == {
        "login": 123,
        "server": "Demo",
        "balance": 1000.0,
        "equity": 1000.0,
        "margin": 0.0,
        "margin_free": 1000.0,
        "margin_level": 0.0,
        "profit": 0.0,
        "leverage": 100,
        "currency": "USD",
        "connected": True,
    }
    assert connector.is_connected() is True
    connector.disconnect()
    connector.disconnect()
    assert fake.shutdown_calls == 1
    assert connector.connected is False


def test_connect_shuts_down_when_account_information_is_unavailable(monkeypatch):
    fake = FakeMT5(account=None)
    connector = attach_fake(monkeypatch, fake)

    assert connector.connect() is False
    assert connector.connected is False
    assert connector.is_connected() is False
    assert fake.shutdown_calls == 1


@pytest.mark.parametrize(
    ("terminal", "terminal_error"),
    [
        (None, None),
        (SimpleNamespace(connected=False), None),
        (None, RuntimeError("terminal unavailable")),
    ],
)
def test_connection_is_invalidated_when_terminal_is_unavailable(
    monkeypatch, terminal, terminal_error
):
    fake = FakeMT5(terminal=terminal, terminal_error=terminal_error)
    connector = attach_fake(monkeypatch, fake)
    connector.connected = True

    assert connector.is_connected() is False
    assert connector.connected is False
    assert connector.get_positions().empty
    with pytest.raises(RuntimeError, match="MT5 is not connected"):
        connector.get_order_history(datetime(2025, 1, 1), datetime(2025, 1, 2))


def test_missing_account_information_is_reported_as_unavailable(monkeypatch):
    connector = attach_fake(monkeypatch, FakeMT5(account=None))
    connector.connected = True

    summary = connector.get_account_summary()

    assert summary == {
        "connected": False,
        "error": "MT5 account information is unavailable",
    }


def test_positions_are_empty_when_mt5_returns_no_data(monkeypatch):
    connector = attach_fake(monkeypatch, FakeMT5(positions=None))
    connector.connected = True

    positions = connector.get_positions()

    assert isinstance(positions, pd.DataFrame)
    assert positions.empty


def test_positions_are_normalized_from_fake_terminal_data(monkeypatch):
    connector = attach_fake(
        monkeypatch,
        FakeMT5(
            positions=[
                {
                    "time": 1_700_000_000,
                    "symbol": "EURUSD",
                    "type": 0,
                    "volume": 0.1,
                    "price_open": 1.1,
                    "sl": 1.0,
                    "tp": 1.2,
                    "price_current": 1.15,
                    "swap": 0.0,
                    "profit": 5.0,
                    "magic": 7,
                    "comment": "test",
                }
            ]
        ),
    )
    connector.connected = True

    positions = connector.get_positions()

    assert positions.loc[0, "Symbol"] == "EURUSD"
    assert positions.loc[0, "Volume"] == 0.1
    assert pd.api.types.is_datetime64_any_dtype(positions["time"])


def test_history_and_order_deal_queries_handle_empty_responses(monkeypatch):
    connector = attach_fake(
        monkeypatch,
        FakeMT5(deals=None, orders=None),
    )
    connector.connected = True
    start = datetime(2025, 1, 1)
    end = datetime(2025, 1, 2)

    assert connector.get_history().empty
    assert connector.get_order_history(start, end) == []
    assert connector.get_deal_history(start, end) == []


@pytest.mark.parametrize(
    ("symbol_info", "tick"),
    [
        (None, SimpleNamespace(bid=1.1, ask=1.2, last=1.15, volume=10)),
        (
            SimpleNamespace(high=1.3, low=1.0, digits=5),
            None,
        ),
    ],
)
def test_symbol_info_fails_closed_when_metadata_or_tick_is_missing(
    monkeypatch, symbol_info, tick
):
    connector = attach_fake(
        monkeypatch,
        FakeMT5(symbol=symbol_info, tick=tick),
    )
    connector.connected = True

    assert connector.get_symbol_info("EURUSD") == {}


def test_symbol_list_filters_hidden_symbols_and_handles_no_response(monkeypatch):
    connector = attach_fake(
        monkeypatch,
        FakeMT5(
            symbols=[
                SimpleNamespace(name="EURUSD", visible=True),
                SimpleNamespace(name="USDJPY", visible=False),
                SimpleNamespace(name=None, visible=True),
            ]
        ),
    )
    connector.connected = True

    assert connector.get_symbols_list() == [{"symbol": "EURUSD", "visible": True}]
    assert connector.get_symbols_list(visible_only=False) == [
        {"symbol": "EURUSD", "visible": True},
        {"symbol": "USDJPY", "visible": False},
    ]

    no_symbols = attach_fake(monkeypatch, FakeMT5(symbols=None))
    no_symbols.connected = True
    assert no_symbols.get_symbols_list() == []


@pytest.mark.parametrize("rates", [None, []])
def test_candles_are_empty_when_mt5_returns_no_rates(monkeypatch, rates):
    connector = attach_fake(monkeypatch, FakeMT5(rates=rates))
    connector.connected = True

    assert connector.get_candles("EURUSD", 1).empty


def test_candles_use_time_as_index(monkeypatch):
    connector = attach_fake(
        monkeypatch,
        FakeMT5(rates=[{"time": 1_700_000_000, "close": 1.25}]),
    )
    connector.connected = True

    candles = connector.get_candles("EURUSD", 1, count=1)

    assert candles.index.name == "time"
    assert candles.iloc[0]["close"] == 1.25
