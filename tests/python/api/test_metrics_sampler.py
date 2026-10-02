from __future__ import annotations

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from src.python.data.database import AsyncDatabase
from src.python.data.models import (
    DataStatus,
    OHLCVBar,
    OrderStatus,
    Symbol,
    TradeSide,
    TradingOrder,
)
from src.python.observability import MetricsRegistry

MODULE = "src.python.api.metrics_sampler"


def _parser():
    assert importlib.util.find_spec(MODULE) is not None, (
        f"{MODULE} must provide candle age limit parsing"
    )
    return importlib.import_module(MODULE).parse_candle_age_limits


def _sampler_type():
    sampler = getattr(
        importlib.import_module(MODULE), "OperationalMetricsSampler", None
    )
    assert sampler is not None, f"{MODULE} must provide OperationalMetricsSampler"
    return sampler


def _candle(symbol: str, timeframe: str, timestamp: datetime, status: DataStatus):
    from src.python.data.config import Timeframe

    return OHLCVBar(
        symbol=symbol,
        timeframe=Timeframe(timeframe),
        timestamp=timestamp,
        open=1,
        high=2,
        low=0.5,
        close=1.5,
        status=status,
    )


def _order(order_id: str, status: OrderStatus):
    return TradingOrder(
        order_id=order_id,
        symbol="EURUSD",
        side=TradeSide.BUY,
        quantity=1,
        status=status,
    )


def test_parser_normalizes_symbols_and_timeframes():
    parse = _parser()

    assert parse('{"eurusd:m5": 300}') == {("EURUSD", "M5"): 300}


def test_parser_accepts_empty_configuration():
    assert _parser()("") == {}
    assert _parser()(None) == {}
    assert _parser()(" \t") == {}


def test_parser_accepts_positive_fractional_limits():
    assert _parser()('{"EURUSD:M5": 1.5}') == {("EURUSD", "M5"): 1.5}


def test_parser_rejects_integer_limits_outside_finite_float_range():
    raw = '{"EURUSD:M5": ' + ("9" * 400) + "}"

    with pytest.raises(ValueError, match="finite positive"):
        _parser()(raw)


def test_parser_rejects_exact_duplicate_json_keys():
    with pytest.raises(ValueError, match="duplicate"):
        _parser()('{"EURUSD:M5": 300, "EURUSD:M5": 600}')


@pytest.mark.parametrize("value", ["not-json", "[]", '{"EURUSD:M5": 300,}'])
def test_parser_rejects_invalid_json_or_top_level_shape(value: str):
    with pytest.raises(ValueError):
        _parser()(value)


@pytest.mark.parametrize(
    "value",
    [
        '{"EURUSD:INVALID": 300}',
        '{"EURUSD:M5": true}',
        '{"EURUSD:M5": NaN}',
        '{"EURUSD:M5": Infinity}',
        '{"EURUSD:M5": 0}',
        '{"EURUSD:M5": -1}',
        '{"EURUSD:M5": 300, "eurusd:m5": 600}',
        '{"EURUSD": 300}',
        '{" EURUSD:M5": 300}',
    ],
)
def test_parser_rejects_invalid_or_ambiguous_entries(value: str):
    with pytest.raises(ValueError):
        _parser()(value)


@pytest.mark.asyncio
async def test_sampler_reports_fresh_and_stale_persisted_candles_and_unknown_orders():
    database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        async with database.session_factory.begin() as session:
            session.add_all(
                [
                    Symbol(name="EURUSD"),
                    Symbol(name="GBPUSD"),
                    _candle(
                        "EURUSD",
                        "M1",
                        now - timedelta(seconds=60),
                        DataStatus.PERSISTED,
                    ),
                    _candle(
                        "GBPUSD",
                        "M5",
                        now - timedelta(seconds=900),
                        DataStatus.PERSISTED,
                    ),
                    _candle("GBPUSD", "M5", now, DataStatus.RECEIVED),
                    _order("unknown-order", OrderStatus.UNKNOWN),
                    _order("accepted-order", OrderStatus.ACCEPTED),
                    _order("rejected-order", OrderStatus.REJECTED),
                ]
            )

        metrics = MetricsRegistry()
        sampler = _sampler_type()(
            database.engine,
            metrics,
            {("EURUSD", "M1"): 120, ("GBPUSD", "M5"): 300},
        )

        await sampler.collect(now=now)

        assert (
            metrics.get_gauge(
                "candle_data_age_seconds",
                labels={"symbol": "EURUSD", "timeframe": "M1"},
            )
            == 60
        )
        assert (
            metrics.get_gauge(
                "candle_data_age_seconds",
                labels={"symbol": "GBPUSD", "timeframe": "M5"},
            )
            == 900
        )
        assert (
            metrics.get_gauge(
                "candle_stale_after_seconds",
                labels={"symbol": "GBPUSD", "timeframe": "M5"},
            )
            == 300
        )
        assert (
            metrics.get_gauge(
                "candle_data_available", labels={"symbol": "EURUSD", "timeframe": "M1"}
            )
            == 1
        )
        assert metrics.get_gauge("unknown_orders_count") == 1
        assert metrics.get_gauge("candle_freshness_configured") == 1
        assert (
            metrics.get_gauge(
                "observability_source_available", labels={"source": "market_data"}
            )
            == 1
        )
        assert (
            metrics.get_gauge(
                "observability_source_available", labels={"source": "order_state"}
            )
            == 1
        )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_sampler_distinguishes_missing_persisted_candle_from_fresh_data():
    database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        metrics = MetricsRegistry()
        sampler = _sampler_type()(database.engine, metrics, {("EURUSD", "M1"): 120})

        await sampler.collect(now=datetime(2026, 10, 2, tzinfo=timezone.utc))

        labels = {"symbol": "EURUSD", "timeframe": "M1"}
        assert metrics.get_gauge("candle_data_available", labels=labels) == 0
        assert not metrics.has_gauge("candle_data_age_seconds", labels=labels)
        assert (
            metrics.get_gauge(
                "observability_source_available", labels={"source": "market_data"}
            )
            == 1
        )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_sampler_rejects_future_candle_timestamp_without_reporting_zero_age():
    database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        async with database.session_factory.begin() as session:
            session.add(Symbol(name="EURUSD"))
            session.add(
                _candle(
                    "EURUSD", "M1", now + timedelta(seconds=1), DataStatus.PERSISTED
                )
            )

        metrics = MetricsRegistry()
        sampler = _sampler_type()(database.engine, metrics, {("EURUSD", "M1"): 120})

        await sampler.collect(now=now)

        labels = {"symbol": "EURUSD", "timeframe": "M1"}
        assert metrics.get_gauge("candle_data_available", labels=labels) == 0
        assert not metrics.has_gauge("candle_data_age_seconds", labels=labels)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_sampler_without_engine_marks_sources_unavailable_without_fabricated_values():
    metrics = MetricsRegistry()
    sampler = _sampler_type()(None, metrics, {("EURUSD", "M1"): 120})

    await sampler.collect(now=datetime(2026, 10, 2, tzinfo=timezone.utc))

    assert (
        metrics.get_gauge(
            "observability_source_available", labels={"source": "market_data"}
        )
        == 0
    )
    assert (
        metrics.get_gauge(
            "observability_source_available", labels={"source": "order_state"}
        )
        == 0
    )
    assert not metrics.has_gauge("unknown_orders_count")
    assert not metrics.has_gauge(
        "candle_data_age_seconds", labels={"symbol": "EURUSD", "timeframe": "M1"}
    )


@pytest.mark.asyncio
async def test_candle_query_failure_does_not_hide_order_state():
    database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.session_factory.begin() as session:
            session.add(_order("unknown-order", OrderStatus.UNKNOWN))

        def fail_candle_query(_conn, _cursor, statement, _parameters, _context, _many):
            if "ohlcv_data" in statement.lower():
                raise SQLAlchemyError("injected candle query failure")

        event.listen(
            database.engine.sync_engine, "before_cursor_execute", fail_candle_query
        )
        try:
            metrics = MetricsRegistry()
            sampler = _sampler_type()(database.engine, metrics, {("EURUSD", "M1"): 120})

            await sampler.collect(now=datetime(2026, 10, 2, tzinfo=timezone.utc))
        finally:
            event.remove(
                database.engine.sync_engine, "before_cursor_execute", fail_candle_query
            )

        assert (
            metrics.get_gauge(
                "observability_source_available", labels={"source": "market_data"}
            )
            == 0
        )
        assert (
            metrics.get_gauge(
                "observability_source_available", labels={"source": "order_state"}
            )
            == 1
        )
        assert metrics.get_gauge("unknown_orders_count") == 1
        assert (
            metrics.get_counter(
                "observability_collection_errors_total",
                labels={"source": "market_data"},
            )
            == 1
        )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_order_query_failure_does_not_hide_candle_state():
    database = AsyncDatabase("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        async with database.session_factory.begin() as session:
            session.add(Symbol(name="EURUSD"))
            session.add(
                _candle(
                    "EURUSD", "M1", now - timedelta(seconds=30), DataStatus.PERSISTED
                )
            )

        def fail_order_query(_conn, _cursor, statement, _parameters, _context, _many):
            if (
                "orders" in statement.lower()
                and "order_transitions" not in statement.lower()
            ):
                raise SQLAlchemyError("injected order query failure")

        event.listen(
            database.engine.sync_engine, "before_cursor_execute", fail_order_query
        )
        try:
            metrics = MetricsRegistry()
            sampler = _sampler_type()(database.engine, metrics, {("EURUSD", "M1"): 120})

            await sampler.collect(now=now)
        finally:
            event.remove(
                database.engine.sync_engine, "before_cursor_execute", fail_order_query
            )

        assert (
            metrics.get_gauge(
                "observability_source_available", labels={"source": "market_data"}
            )
            == 1
        )
        assert (
            metrics.get_gauge(
                "observability_source_available", labels={"source": "order_state"}
            )
            == 0
        )
        assert (
            metrics.get_gauge(
                "candle_data_age_seconds",
                labels={"symbol": "EURUSD", "timeframe": "M1"},
            )
            == 30
        )
        assert not metrics.has_gauge("unknown_orders_count")
        assert (
            metrics.get_counter(
                "observability_collection_errors_total",
                labels={"source": "order_state"},
            )
            == 1
        )
    finally:
        await database.dispose()
