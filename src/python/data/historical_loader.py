"""Bulk historical OHLCV download and deduplication service."""

from __future__ import annotations

import logging
import math
import csv
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .config import Timeframe
from .mt5_connector import MT5Connector
from .validators import ohlcv_key, validate_ohlcv_rows

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str, str, int], None]


@dataclass(slots=True)
class HistoricalLoadResult:
    """Summary of a historical load operation."""

    symbol: str
    timeframe: str
    fetched: int = 0
    stored: int = 0
    duplicates_removed: int = 0
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    unexpected_gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    quality_valid: bool = True
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    pages: int = 0
    stopped: bool = False
    requested_bars: int = 0
    available_bars: int = 0
    exhausted: bool = False

    @property
    def coverage(self) -> tuple[datetime | None, datetime | None]:
        """Return the actual persisted coverage window as ``(start, end)``."""

        return self.coverage_start, self.coverage_end


class HistoricalDataQualityError(ValueError):
    """Raised when bars cannot safely be used for training or backtesting."""


MT5_OFFICIAL_SOURCE = "mt5_official"


def normalize_mt5_export_row(
    row: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: Timeframe | str,
    source: str = MT5_OFFICIAL_SOURCE,
    ingestion_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize an official MT5 CSV/export row to the canonical OHLCV shape.

    MT5 terminal exports use human labels (``Date``, ``Time``, ``Tick Volume``)
    while the Python API uses ``time`` and ``tick_volume``.  Both are accepted,
    without ever connecting to a terminal.
    """
    keys = {str(key).strip().lower().replace("_", " "): value for key, value in row.items()}
    timestamp = keys.get("timestamp")
    if timestamp is None and keys.get("date") is not None:
        timestamp = f"{keys['date']} {keys.get('time', '00:00:00')}"
    if timestamp is None:
        timestamp = keys.get("time")
    aliases = {
        "open": ("open",),
        "high": ("high",),
        "low": ("low",),
        "close": ("close",),
        "tick_volume": ("tick volume", "tickvolume", "volume tick"),
        "volume": ("volume", "real volume"),
        "spread": ("spread",),
    }
    normalized: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": Timeframe(timeframe).value,
        "timestamp": _timestamp({"timestamp": timestamp}),
        "source": source,
        "ingestion_metadata": dict(ingestion_metadata or {}),
    }
    for target, names in aliases.items():
        for name in names:
            if name in keys and keys[name] not in ("", None):
                normalized[target] = float(keys[name]) if target not in {"tick_volume", "volume", "spread"} else int(float(keys[name]))
                break
    normalized.setdefault("tick_volume", 0)
    normalized.setdefault("volume", 0)
    normalized.setdefault("spread", 0)
    return normalized


def normalize_mt5_export(
    rows: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: Timeframe | str,
    source: str = MT5_OFFICIAL_SOURCE,
    ingestion_id: str | None = None,
    received_at: datetime | None = None,
    collector: str = "historical_loader",
    provider: str = "MetaTrader5",
    export_format: str = "mt5_official_export",
) -> list[dict[str, Any]]:
    """Normalize, sort and deduplicate MT5 export rows deterministically.

    A single ingestion identifier and receive timestamp are shared by all rows
    in one import, making the batch traceable without exposing credentials.
    """
    metadata = {
        "ingestion_id": ingestion_id or str(uuid4()),
        "received_at": (received_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat(),
        "collector": collector,
        "format": export_format,
        "provider": provider,
    }
    unique: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        normalized = normalize_mt5_export_row(
            row,
            symbol=symbol,
            timeframe=timeframe,
            source=source,
            ingestion_metadata=metadata,
        )
        _validate_ohlcv(normalized, symbol, Timeframe(timeframe).value, require_volume=True)
        unique[normalized["timestamp"]] = normalized
    result = [unique[key] for key in sorted(unique)]
    validate_ohlcv_rows(result, reject_duplicates=True)
    return result


def validate_overlap(
    incoming: Iterable[Mapping[str, Any]],
    existing: Iterable[Mapping[str, Any]],
    *,
    price_tolerance: float = 1e-9,
) -> list[datetime]:
    """Validate overlapping bars and return matching timestamps.

    A mismatch is a hard failure: silently replacing broker history can poison
    a backtest, especially when exports use a different session or timezone.
    """
    old = {ohlcv_key(row)[2]: row for row in existing}
    matches: list[datetime] = []
    for row in incoming:
        timestamp = ohlcv_key(row)[2]
        previous = old.get(timestamp)
        if previous is None:
            continue
        for field in ("open", "high", "low", "close"):
            if abs(float(row[field]) - float(previous[field])) > price_tolerance:
                raise HistoricalDataQualityError(
                    f"overlap mismatch at {timestamp.isoformat()} ({field})"
                )
        matches.append(timestamp)
    return matches


class HistoricalLoader:
    """Fetches rates in batches and delegates durable writes to a repository."""

    def __init__(
        self,
        connector: MT5Connector,
        repository: Any,
        *,
        batch_size: int = 1_000,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        self.connector = connector
        self.repository = repository
        self.batch_size = batch_size
        self.progress_callback = progress_callback

    async def load_symbol(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        *,
        count: int = 10_000,
        start: datetime | None = None,
        end: datetime | None = None,
        storage_symbol: str | None = None,
        minimum_bars: int | None = None,
        fail_on_gaps: bool = False,
        require_volume: bool = False,
        source: str = MT5_OFFICIAL_SOURCE,
    ) -> HistoricalLoadResult:
        """Download, normalize, deduplicate, gap-check, and persist one series."""

        if count < 1_000:
            raise ValueError("count must be at least 1000 bars")
        timeframe = Timeframe(timeframe)
        ingestion_id = str(uuid4())
        try:
            rows = self.connector.get_rates(
                symbol, timeframe, start=start, end=end, count=count
            )
        except Exception:
            logger.exception("Historical download failed for %s %s", symbol, timeframe.value)
            raise
        if len(rows) < 1_000:
            logger.warning(
                "Only %d/%d bars returned for %s %s; history may contain a broker gap",
                len(rows), count, symbol, timeframe.value,
            )
        unique: dict[datetime, dict[str, Any]] = {}
        previous: datetime | None = None
        for row in rows:
            normalized = dict(row)
            timestamp = _timestamp(normalized)
            if previous is not None and timestamp < previous:
                raise HistoricalDataQualityError(
                    f"historical bars for {symbol} {timeframe.value} are not chronological"
                )
            previous = timestamp
            _validate_ohlcv(
                normalized,
                symbol,
                timeframe.value,
                require_volume=require_volume,
            )
            normalized["symbol"] = storage_symbol or symbol
            normalized["timeframe"] = timeframe.value
            normalized["timestamp"] = timestamp
            normalized["source"] = source
            normalized["ingestion_metadata"] = {
                "ingestion_id": ingestion_id,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "collector": "historical_loader",
            }
            unique[timestamp] = normalized
        ordered = [unique[key] for key in sorted(unique)]
        # Validate the canonical batch once more after normalization.  This
        # catches malformed provider timestamps and key collisions before any
        # database write while retaining the loader's deduplication contract.
        validate_ohlcv_rows(ordered, reject_duplicates=True)
        gaps = find_gaps(ordered, timeframe)
        unexpected_gaps = [
            gap for gap in gaps if not is_expected_market_gap(*gap)
        ]
        if gaps:
            logger.warning(
                "Detected %d gap(s) in %s %s; unexpected=%d first=%s..%s",
                len(gaps), symbol, timeframe.value, len(unexpected_gaps),
                gaps[0][0], gaps[0][1],
            )
        if minimum_bars is not None and len(ordered) < minimum_bars:
            raise HistoricalDataQualityError(
                f"{symbol} {timeframe.value} contains {len(ordered)} unique bars; "
                f"requires at least {minimum_bars}"
            )
        if fail_on_gaps and unexpected_gaps:
            raise HistoricalDataQualityError(
                f"{symbol} {timeframe.value} history contains "
                f"{len(unexpected_gaps)} unexpected gap(s)"
            )
        stored = 0
        for batch in _batches(ordered, self.batch_size):
            result = self.repository.bulk_upsert_candles(batch, batch_size=self.batch_size)
            stored += await result if isinstance(result, Awaitable) else result
            if self.progress_callback:
                self.progress_callback(symbol, timeframe.value, stored)
        return HistoricalLoadResult(
            symbol=symbol,
            timeframe=timeframe.value,
            requested_bars=count,
            available_bars=len(ordered),
            fetched=len(rows),
            stored=stored,
            duplicates_removed=len(rows) - len(ordered),
            gaps=gaps,
            unexpected_gaps=unexpected_gaps,
            quality_valid=not unexpected_gaps,
            coverage_start=ordered[0]["timestamp"] if ordered else None,
            coverage_end=ordered[-1]["timestamp"] if ordered else None,
            exhausted=len(rows) < count,
        )

    async def backfill_symbol(
        self,
        symbol: str,
        timeframe: Timeframe | str,
        *,
        target_bars: int,
        chunk_size: int | None = None,
        stop_event: Any | None = None,
        fail_on_gaps: bool = False,
        require_volume: bool = False,
    ) -> HistoricalLoadResult:
        """Backfill in pages from the newest bar towards the oldest bar.

        Pages are persisted as they arrive, so a process interruption loses at
        most one page.  Re-running is safe because the repository upsert key is
        ``symbol/timeframe/timestamp``.  A stop event is checked between pages
        and returns a truthful partial coverage report.
        """

        if target_bars < 1:
            raise ValueError("target_bars must be positive")
        timeframe = Timeframe(timeframe)
        page_size = chunk_size or self.batch_size
        if page_size < 1:
            raise ValueError("chunk_size must be positive")
        rows_by_time: dict[datetime, dict[str, Any]] = {}
        duplicate_rows = 0
        ingestion_id = str(uuid4())
        pages = 0
        position = 0
        stopped = False
        exhausted = False
        stagnant_pages = 0
        pending_rows: list[dict[str, Any]] = []
        stored = 0
        while len(rows_by_time) < target_bars:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            request_count = min(page_size, target_bars - len(rows_by_time))
            try:
                if hasattr(self.connector, "get_rates_from_pos"):
                    page = self.connector.get_rates_from_pos(
                        symbol, timeframe, position, request_count
                    )
                else:  # compatibility with simple/fake connectors
                    page = self.connector.get_rates(symbol, timeframe, count=request_count)
            except Exception:
                logger.exception("Backfill page failed for %s %s at %d", symbol, timeframe.value, position)
                raise
            pages += 1
            if page is None or len(page) == 0:
                exhausted = True
                break
            page_rows: list[dict[str, Any]] = []
            for raw in page:
                row = dict(raw)
                timestamp = _timestamp(row)
                _validate_ohlcv(row, symbol, timeframe.value, require_volume=require_volume)
                row.update(symbol=symbol, timeframe=timeframe.value, timestamp=timestamp)
                row["source"] = MT5_OFFICIAL_SOURCE
                row["ingestion_metadata"] = {
                    "ingestion_id": ingestion_id,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "collector": "historical_loader_backfill",
                    "page": pages,
                }
                if timestamp in rows_by_time:
                    duplicate_rows += 1
                    continue
                rows_by_time[timestamp] = row
                page_rows.append(row)
            # MT5 positions are relative to newest and pages can overlap.
            position += len(page)
            if page_rows:
                stagnant_pages = 0
                pending_rows.extend(page_rows)
                if not fail_on_gaps:
                    stored += await self._persist_rows(pending_rows)
                    pending_rows.clear()
                if self.progress_callback:
                    self.progress_callback(symbol, timeframe.value, len(rows_by_time))
            else:
                stagnant_pages += 1
                if stagnant_pages >= 3:
                    logger.warning(
                        "MT5 returned %d consecutive pages without new %s %s bars; "
                        "treating history as exhausted",
                        stagnant_pages,
                        symbol,
                        timeframe.value,
                    )
                    exhausted = True
                    break
            if len(page) < request_count:
                exhausted = True
                break
        ordered = [rows_by_time[key] for key in sorted(rows_by_time)]
        validate_ohlcv_rows(ordered, reject_duplicates=True)
        gaps = find_gaps(ordered, timeframe)
        unexpected = [gap for gap in gaps if not is_expected_market_gap(*gap)]
        if fail_on_gaps and unexpected:
            raise HistoricalDataQualityError(
                f"{symbol} {timeframe.value} history contains {len(unexpected)} unexpected gap(s)"
            )
        # When strict gap validation is requested, delay writes until the
        # complete coverage window has been checked.  Normal backfills stream
        # each page immediately, so an interruption loses at most one page.
        if fail_on_gaps and pending_rows:
            stored += await self._persist_rows(pending_rows)
        return HistoricalLoadResult(
            symbol=symbol,
            timeframe=timeframe.value,
            requested_bars=target_bars,
            available_bars=len(ordered),
            fetched=len(rows_by_time),
            stored=stored,
            duplicates_removed=duplicate_rows,
            gaps=gaps,
            unexpected_gaps=unexpected,
            quality_valid=not unexpected,
            coverage_start=ordered[0]["timestamp"] if ordered else None,
            coverage_end=ordered[-1]["timestamp"] if ordered else None,
            pages=pages,
            stopped=stopped,
            exhausted=exhausted,
        )

    async def _persist_rows(self, rows: list[dict[str, Any]]) -> int:
        """Persist a page in repository-sized transactions."""

        stored = 0
        for batch in _batches(rows, self.batch_size):
            result = self.repository.bulk_upsert_candles(
                batch, batch_size=self.batch_size
            )
            stored += await result if isinstance(result, Awaitable) else result
        return stored

    async def load_stage_a(
        self,
        *,
        symbol: str = "XAUUSD",
        source_symbol: str | None = None,
        timeframe: Timeframe | str = Timeframe.M5,
        count: int = 30_000,
        start: datetime | None = None,
        end: datetime | None = None,
        fail_on_gaps: bool = True,
        export_rows: Iterable[Mapping[str, Any]] | None = None,
    ) -> HistoricalLoadResult:
        """Load and validate the Stage A XAUUSD M5 baseline.

        Stage A deliberately has a hard minimum so a partial local terminal
        download cannot be mistaken for a usable training set.
        """

        timeframe = Timeframe(timeframe)
        if symbol.upper() != "XAUUSD" or timeframe is not Timeframe.M5:
            raise ValueError("Stage A requires symbol=XAUUSD and timeframe=M5")
        if count < 30_000:
            raise ValueError("Stage A requires at least 30000 bars")
        if export_rows is not None:
            result = await self.load_export_rows(
                export_rows, symbol=symbol, timeframe=timeframe, minimum_bars=30_000,
                fail_on_gaps=fail_on_gaps,
            )
        else:
            result = await self.load_symbol(
                source_symbol or symbol, timeframe, storage_symbol=symbol, count=count,
                start=start, end=end, minimum_bars=30_000, fail_on_gaps=fail_on_gaps,
                require_volume=True, source=MT5_OFFICIAL_SOURCE,
            )
        if source_symbol and source_symbol.upper() != symbol.upper():
            result.symbol = symbol
        return result

    async def load_export_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        symbol: str,
        timeframe: Timeframe | str,
        minimum_bars: int | None = None,
        fail_on_gaps: bool = False,
        existing_rows: Iterable[Mapping[str, Any]] = (),
        price_tolerance: float = 1e-9,
    ) -> HistoricalLoadResult:
        """Ingest an official MT5 export fixture without live credentials."""
        timeframe = Timeframe(timeframe)
        normalized = normalize_mt5_export(rows, symbol=symbol, timeframe=timeframe)
        overlaps = validate_overlap(
            normalized, existing_rows, price_tolerance=price_tolerance
        )
        gaps = find_gaps(normalized, timeframe)
        unexpected = [gap for gap in gaps if not is_expected_market_gap(*gap)]
        if minimum_bars is not None and len(normalized) < minimum_bars:
            raise HistoricalDataQualityError(
                f"{symbol} {timeframe.value} contains {len(normalized)} unique bars; "
                f"requires at least {minimum_bars}"
            )
        if fail_on_gaps and unexpected:
            raise HistoricalDataQualityError(
                f"{symbol} {timeframe.value} history contains {len(unexpected)} unexpected gap(s)"
            )
        stored = await self._persist_rows(normalized)
        return HistoricalLoadResult(
            symbol=symbol, timeframe=timeframe.value, fetched=len(normalized),
            stored=stored, available_bars=len(normalized),
            duplicates_removed=0, gaps=gaps, unexpected_gaps=unexpected,
            quality_valid=not unexpected, coverage_start=normalized[0]["timestamp"] if normalized else None,
            coverage_end=normalized[-1]["timestamp"] if normalized else None,
            requested_bars=len(normalized), pages=1,
        )

    async def load_export_file(self, path: str, **kwargs: Any) -> HistoricalLoadResult:
        """Load a UTF-8 official MT5 CSV export (no network or terminal access)."""
        with open(path, newline="", encoding="utf-8-sig") as handle:
            return await self.load_export_rows(csv.DictReader(handle), **kwargs)

    async def load(
        self,
        symbols: Iterable[str],
        timeframes: Iterable[Timeframe | str],
        *,
        count: int = 10_000,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[HistoricalLoadResult]:
        """Load all symbol/timeframe combinations sequentially for MT5 safety."""

        results = []
        for symbol in symbols:
            for timeframe in timeframes:
                results.append(
                    await self.load_symbol(
                        symbol, timeframe, count=count, start=start, end=end
                    )
                )
        return results

    async def backfill(
        self,
        symbols: Iterable[str],
        timeframes: Iterable[Timeframe | str],
        *,
        target_bars: int,
        chunk_size: int | None = None,
        stop_event: Any | None = None,
        fail_on_gaps: bool = False,
    ) -> list[HistoricalLoadResult]:
        """Run the paginated backfill sequentially (MT5 is not thread-safe)."""

        results: list[HistoricalLoadResult] = []
        for symbol in symbols:
            for timeframe in timeframes:
                if stop_event is not None and stop_event.is_set():
                    return results
                results.append(
                    await self.backfill_symbol(
                        symbol,
                        timeframe,
                        target_bars=target_bars,
                        chunk_size=chunk_size,
                        stop_event=stop_event,
                        fail_on_gaps=fail_on_gaps,
                    )
                )
        return results


async def load_historical_data(
    connector: MT5Connector,
    repository: Any,
    symbols: Iterable[str],
    timeframes: Iterable[Timeframe | str],
    **kwargs: Any,
) -> list[HistoricalLoadResult]:
    """Convenience function for applications and scripts."""

    return await HistoricalLoader(connector, repository).load(
        symbols, timeframes, **kwargs
    )


def find_gaps(
    rows: Iterable[Mapping[str, Any]],
    timeframe: Timeframe | str,
    *,
    tolerance: int = 0,
) -> list[tuple[datetime, datetime]]:
    """Return missing nominal periods, excluding monthly bars."""

    timeframe = Timeframe(timeframe)
    seconds = timeframe.seconds
    if seconds is None:
        return []
    timestamps = sorted({_timestamp(row) for row in rows})
    gaps: list[tuple[datetime, datetime]] = []
    expected = timedelta(seconds=seconds)
    for previous, current in zip(timestamps, timestamps[1:]):
        missing = int((current - previous).total_seconds() // seconds) - 1
        if missing > tolerance:
            gaps.append((previous + expected, current - expected))
    return gaps


def is_expected_market_gap(start: datetime, end: datetime) -> bool:
    """Identify broker calendar closures without fabricating replacement bars.

    Weekend closures and the common midnight maintenance pause are expected for
    many spot-metal feeds. Any other gap remains a quality failure.
    """

    cursor = start
    while cursor.date() <= end.date():
        if cursor.weekday() >= 5:
            return True
        cursor += timedelta(days=1)
    duration = end - start
    short_feed_interruption = duration <= timedelta(minutes=10)
    daily_maintenance = (
        start.hour == 0
        and end.hour == 0
        and duration <= timedelta(hours=1)
    )
    holiday_reopen = (
        start.weekday() == 0
        and start.hour >= 18
        and end.weekday() == 1
        and end.hour == 0
        and end.minute >= 50
        and duration <= timedelta(hours=6)
    )
    return short_feed_interruption or daily_maintenance or holiday_reopen


def _validate_ohlcv(
    row: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    *,
    require_volume: bool = False,
) -> None:
    """Validate the price/volume invariants before a row reaches the database."""

    required = ("open", "high", "low", "close")
    missing = [field for field in required if field not in row]
    if missing:
        raise HistoricalDataQualityError(
            f"{symbol} {timeframe} bar is missing OHLC field(s): {', '.join(missing)}"
        )
    try:
        prices = {field: float(row[field]) for field in required}
    except (TypeError, ValueError) as error:
        raise HistoricalDataQualityError(
            f"{symbol} {timeframe} bar has non-numeric OHLC values"
        ) from error
    if not all(math.isfinite(value) for value in prices.values()):
        raise HistoricalDataQualityError(f"{symbol} {timeframe} bar has non-finite OHLC values")
    if min(prices.values()) < 0:
        raise HistoricalDataQualityError(f"{symbol} {timeframe} bar has negative prices")
    if prices["high"] < max(prices["open"], prices["close"]) or prices["low"] > min(
        prices["open"], prices["close"]
    ) or prices["low"] > prices["high"]:
        raise HistoricalDataQualityError(f"{symbol} {timeframe} bar violates OHLC bounds")
    if require_volume and not any(field in row for field in ("tick_volume", "volume")):
        raise HistoricalDataQualityError(
            f"{symbol} {timeframe} bar is missing tick_volume/volume"
        )
    for field in ("tick_volume", "volume"):
        if field in row:
            try:
                value = float(row[field])
            except (TypeError, ValueError) as error:
                raise HistoricalDataQualityError(
                    f"{symbol} {timeframe} bar has invalid {field}"
                ) from error
            if not math.isfinite(value) or value < 0:
                raise HistoricalDataQualityError(
                    f"{symbol} {timeframe} bar has invalid {field}"
                )


def _timestamp(row: Mapping[str, Any]) -> datetime:
    value = row.get("timestamp", row.get("time"))
    if value is None:
        raise ValueError("historical row is missing timestamp/time")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        if len(text) >= 10 and text[4] == "." and text[7] == ".":
            text = text[:4] + "-" + text[5:7] + "-" + text[8:]
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            value = datetime.strptime(text, "%Y-%m-%d %H:%M")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _batches(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


__all__ = [
    "HistoricalLoadResult",
    "HistoricalDataLoader",
    "HistoricalLoader",
    "HistoricalDataQualityError",
    "find_gaps",
    "load_historical_data",
    "MT5_OFFICIAL_SOURCE",
    "normalize_mt5_export_row",
    "normalize_mt5_export",
    "validate_overlap",
]

HistoricalDataLoader = HistoricalLoader
