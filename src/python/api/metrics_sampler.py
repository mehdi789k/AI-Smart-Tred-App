"""Read-only operational metrics collection for API replicas."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ..data.config import Timeframe
from ..data.models import DataStatus, OHLCVBar, OrderStatus, TradingOrder
from ..logging_config import get_logger, log_event
from ..observability import MetricsRegistry

logger = get_logger(__name__)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Candle age limits contain duplicate JSON keys")
        result[key] = value
    return result


def parse_candle_age_limits(raw: str | None) -> dict[tuple[str, str], float]:
    """Parse explicit positive age limits keyed by ``SYMBOL:TIMEFRAME``."""
    if raw is None or not raw.strip():
        return {}

    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Invalid candle age limits configuration") from None

    if not isinstance(parsed, dict):
        raise ValueError("Candle age limits configuration must be a JSON object")

    limits: dict[tuple[str, str], float] = {}
    valid_timeframes = {timeframe.value for timeframe in Timeframe}

    for key, value in parsed.items():
        if not isinstance(key, str) or key.count(":") != 1:
            raise ValueError("Candle age limit keys must use SYMBOL:TIMEFRAME")

        symbol, timeframe = key.split(":")
        normalized_symbol = symbol.upper()
        normalized_timeframe = timeframe.upper()
        if (
            not symbol
            or not timeframe
            or symbol != symbol.strip()
            or timeframe != timeframe.strip()
            or normalized_timeframe not in valid_timeframes
        ):
            raise ValueError("Candle age limit key has an invalid symbol or timeframe")

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Candle age limits must be finite positive numbers")
        try:
            limit = float(value)
        except OverflowError:
            raise ValueError(
                "Candle age limits must be finite positive numbers"
            ) from None
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("Candle age limits must be finite positive numbers")

        pair = (normalized_symbol, normalized_timeframe)
        if pair in limits:
            raise ValueError("Candle age limits contain duplicate normalized keys")
        limits[pair] = limit

    return limits


class OperationalMetricsSampler:
    """Sample shared database state for one API replica without writing to it."""

    def __init__(
        self,
        engine: AsyncEngine | None,
        metrics: MetricsRegistry,
        candle_age_limits: Mapping[tuple[str, str], float],
    ) -> None:
        self.engine = engine
        self.metrics = metrics
        self.candle_age_limits = dict(candle_age_limits)

    async def collect(self, *, now: datetime | None = None) -> None:
        """Refresh source availability and configured operational state gauges."""
        sample_time = now or datetime.now(timezone.utc)
        if sample_time.tzinfo is None or sample_time.utcoffset() is None:
            raise ValueError("Sampling time must be timezone-aware")
        sample_time = sample_time.astimezone(timezone.utc)

        self.metrics.set_gauge(
            "candle_freshness_configured", float(bool(self.candle_age_limits))
        )
        self.metrics.set_gauge(
            "observability_source_available",
            0,
            labels={"source": "market_data"},
        )
        self.metrics.set_gauge(
            "observability_source_available", 0, labels={"source": "order_state"}
        )
        for symbol, timeframe in self.candle_age_limits:
            labels = {"symbol": symbol, "timeframe": timeframe}
            self.metrics.set_gauge("candle_data_available", 0, labels=labels)
            self.metrics.set_gauge(
                "candle_stale_after_seconds",
                self.candle_age_limits[(symbol, timeframe)],
                labels=labels,
            )

        engine = self.engine
        if engine is None:
            return

        await self._collect_candles(engine, sample_time)
        await self._collect_unknown_orders(engine)

    async def _collect_candles(
        self, engine: AsyncEngine, sample_time: datetime
    ) -> None:
        try:
            async with engine.connect() as connection:
                for (symbol, timeframe), _limit in self.candle_age_limits.items():
                    statement = select(func.max(OHLCVBar.timestamp)).where(
                        OHLCVBar.symbol == symbol,
                        OHLCVBar.timeframe == Timeframe(timeframe),
                        OHLCVBar.status == DataStatus.PERSISTED,
                    )
                    timestamp = (
                        await connection.execute(statement)
                    ).scalar_one_or_none()
                    if timestamp is None:
                        continue

                    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    timestamp = timestamp.astimezone(timezone.utc)
                    if timestamp > sample_time:
                        log_event(
                            logger,
                            30,
                            "observability_future_candle_timestamp",
                            source="market_data",
                        )
                        continue

                    labels = {"symbol": symbol, "timeframe": timeframe}
                    self.metrics.set_gauge(
                        "candle_data_age_seconds",
                        (sample_time - timestamp).total_seconds(),
                        labels=labels,
                    )
                    self.metrics.set_gauge("candle_data_available", 1, labels=labels)
        except SQLAlchemyError as error:
            for symbol, timeframe in self.candle_age_limits:
                self.metrics.set_gauge(
                    "candle_data_available",
                    0,
                    labels={"symbol": symbol, "timeframe": timeframe},
                )
            self._record_source_failure("market_data", error)
            return

        self.metrics.set_gauge(
            "observability_source_available", 1, labels={"source": "market_data"}
        )

    async def _collect_unknown_orders(self, engine: AsyncEngine) -> None:
        try:
            async with engine.connect() as connection:
                statement = (
                    select(func.count())
                    .select_from(TradingOrder)
                    .where(TradingOrder.status == OrderStatus.UNKNOWN)
                )
                count = (await connection.execute(statement)).scalar_one()
        except SQLAlchemyError as error:
            self._record_source_failure("order_state", error)
            return

        self.metrics.set_gauge("unknown_orders_count", count)
        self.metrics.set_gauge(
            "observability_source_available", 1, labels={"source": "order_state"}
        )

    def _record_source_failure(self, source: str, error: SQLAlchemyError) -> None:
        log_event(
            logger,
            40,
            "observability_collection_failed",
            source=source,
            error_type=type(error).__name__,
        )
        self.metrics.inc(
            "observability_collection_errors_total", labels={"source": source}
        )
