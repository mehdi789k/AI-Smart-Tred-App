"""Command-line entry point for the market-data collector."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from threading import Event, Lock
from pathlib import Path

from .config import DataConfig, get_settings
from .database import AsyncDatabase
from .data_streamer import MarketDataStreamer
from .historical_loader import HistoricalLoader
from .mt5_connector import MT5Connector
from .runtime import CollectorHealth, CollectorLock, health_is_stale

logger = logging.getLogger(__name__)


async def run_service(
    config: DataConfig | None = None,
    *,
    load_history: bool = True,
    stop_event: Event | None = None,
) -> None:
    """Initialize storage, optionally backfill, then run the live publisher."""

    config = config or get_settings()
    config.configure_logging()
    database = AsyncDatabase(config.database_url)
    connector = MT5Connector(config)
    streamer: MarketDataStreamer | None = None
    completed = False
    health = CollectorHealth(
        Path(getattr(config, "health_file", "data/collector_health.json")), Lock()
    )
    collector_lock = CollectorLock(
        getattr(config, "lock_file", "data/collector.lock")
    )
    try:
        collector_lock.acquire()
        health.update("starting")
        await database.initialize(hypertables=config.database_url.startswith("postgresql"))
        connector.connect()
        symbols = config.symbols or connector.visible_symbols()
        if not symbols:
            raise RuntimeError("No symbols are visible in the MT5 Market Watch")
        logger.info("Collecting %d Market Watch symbols across %d timeframes", len(symbols), len(config.timeframes))
        if load_history:
            loader = HistoricalLoader(connector, database.repository)
            await loader.load(symbols, config.timeframes, count=config.history_bars)
        streamer = MarketDataStreamer(
            connector, config, repository=database.repository
        )
        # Keep the resolved Market Watch list for the live polling phase.
        config.symbols = tuple(symbols)
        # The polling API is intentionally synchronous because MT5 and pyzmq
        # are synchronous APIs; keep it off the asyncio event loop.
        health.update("running", mt5="connected", symbols=list(symbols))
        await asyncio.to_thread(
            streamer.run,
            stop_event,
            lambda published: health.update("running", published=published),
        )
        completed = True
    except Exception as error:
        health.update("failed", error_type=type(error).__name__)
        logger.exception("Market-data collector failed")
        raise
    finally:
        if health.path.exists() and completed:
            health.update("stopped")
        if streamer is not None:
            streamer.close()
        connector.disconnect()
        await database.dispose()
        collector_lock.release()


async def run_stage_a(
    config: DataConfig | None = None,
    *,
    count: int = 30_000,
) -> None:
    """Run the production Stage A XAUUSD/M5 backfill and quality gate."""

    config = config or get_settings()
    config.configure_logging()
    database = AsyncDatabase(config.database_url)
    connector = MT5Connector(config)
    try:
        await database.initialize(hypertables=config.database_url.startswith("postgresql"))
        connector.connect()
        loader = HistoricalLoader(connector, database.repository)
        result = await loader.load_stage_a(
            count=count,
            source_symbol=config.mt5_xauusd_symbol,
        )
        logger.info(
            "Stage A complete: %s %s fetched=%d stored=%d duplicates=%d gaps=%d",
            result.symbol, result.timeframe, result.fetched, result.stored,
            result.duplicates_removed, len(result.gaps),
        )
    except Exception:
        logger.exception("Stage A XAUUSD M5 collection failed")
        raise
    finally:
        connector.disconnect()
        await database.dispose()


async def run_backfill(
    config: DataConfig | None = None,
    *,
    target_bars: int,
    chunk_size: int = 1_000,
    stop_event: Event | None = None,
) -> None:
    """Run resumable backwards pagination for configured subscriptions."""

    config = config or get_settings()
    config.configure_logging()
    database = AsyncDatabase(config.database_url)
    connector = MT5Connector(config)
    try:
        await database.initialize(hypertables=config.database_url.startswith("postgresql"))
        connector.connect()
        symbols = config.symbols or connector.visible_symbols()
        loader = HistoricalLoader(connector, database.repository, batch_size=chunk_size)
        results = await loader.backfill(
            symbols, config.timeframes, target_bars=target_bars,
            chunk_size=chunk_size, stop_event=stop_event,
        )
        for result in results:
            logger.info(
                "Backfill %s %s: pages=%d requested=%d available=%d fetched=%d "
                "stored=%d coverage=%s..%s gaps=%d stopped=%s exhausted=%s",
                result.symbol, result.timeframe, result.pages,
                result.requested_bars, result.available_bars, result.fetched,
                result.stored, result.coverage_start, result.coverage_end,
                len(result.gaps), result.stopped, result.exhausted,
            )
    finally:
        connector.disconnect()
        await database.dispose()


def build_parser() -> argparse.ArgumentParser:
    """Create the collector CLI parser."""

    parser = argparse.ArgumentParser(description="Smart MT5 market-data collector")
    parser.add_argument(
        "--no-history", action="store_true", help="skip the initial historical backfill"
    )
    parser.add_argument(
        "--stage-a", action="store_true",
        help="load and quality-gate at least 30000 XAUUSD M5 bars, then exit",
    )
    parser.add_argument(
        "--bars", type=int, default=30_000,
        help="Stage A bar count (minimum 30000)",
    )
    parser.add_argument(
        "--backfill", type=int, metavar="BARS",
        help="run resumable backwards paginated backfill and exit",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=1_000,
        help="page size for --backfill",
    )
    parser.add_argument(
        "--health", action="store_true",
        help="print collector health JSON and exit",
    )
    parser.add_argument(
        "--watchdog", action="store_true",
        help="check heartbeat freshness and exit non-zero when stale",
    )
    parser.add_argument(
        "--watchdog-timeout", type=float, default=15.0,
        help="maximum heartbeat age in seconds for --watchdog",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the collector until interrupted."""

    args = build_parser().parse_args(argv)
    try:
        if args.health:
            config = get_settings()
            health = CollectorHealth(Path(getattr(config, "health_file", "data/collector_health.json")), Lock())
            payload = health.read()
            print(json.dumps(payload, ensure_ascii=False))
            if args.watchdog and (
                payload.get("status") != "running"
                or health_is_stale(payload, args.watchdog_timeout)
            ):
                raise SystemExit(1)
        elif args.stage_a:
            asyncio.run(run_stage_a(count=args.bars))
        elif args.backfill is not None:
            asyncio.run(run_backfill(target_bars=args.backfill, chunk_size=args.chunk_size))
        else:
            asyncio.run(run_service(load_history=not args.no_history))
    except KeyboardInterrupt:
        logger.info("data collector stopped")


if __name__ == "__main__":
    main()
