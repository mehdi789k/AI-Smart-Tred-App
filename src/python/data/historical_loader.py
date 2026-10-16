from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Sequence

from .config import DEFAULT_TIMEFRAMES, resolve_timeframes
from .models import MarketDataRecord
from .mt5_connector import MT5DataUnavailableError, MetaTraderConnector


class HistoricalDataLoader:
    """Download and normalize OHLCV data for all market-watch symbols and timeframes."""

    def __init__(self, connector: MetaTraderConnector | None = None) -> None:
        self.connector = connector or MetaTraderConnector()
        self.failures: list[dict[str, str]] = []

    def load_market_watch_data(
        self,
        *,
        symbols: Iterable[str] | None = None,
        timeframes: Sequence[str] | None = None,
        count: int = 1000,
    ) -> dict[str, dict[str, list[MarketDataRecord]]]:
        selected_symbols = list(symbols) if symbols is not None else self.connector.list_market_watch_symbols()
        selected_timeframes = resolve_timeframes(timeframes or DEFAULT_TIMEFRAMES)
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("count must be a positive integer.")

        result: dict[str, dict[str, list[MarketDataRecord]]] = defaultdict(dict)
        self.failures = []

        for symbol in selected_symbols:
            clean_symbol = str(symbol).strip()
            if not clean_symbol:
                continue
            for timeframe in selected_timeframes:
                try:
                    rows = self.connector.fetch_ohlcv(
                        clean_symbol, timeframe, start_index=0, count=count
                    )
                except MT5DataUnavailableError as exc:
                    result[clean_symbol][timeframe] = []
                    self.failures.append(
                        {
                            "symbol": clean_symbol,
                            "timeframe": timeframe,
                            "reason": str(exc),
                        }
                    )
                    continue

                if not rows:
                    result[clean_symbol][timeframe] = []
                    self.failures.append(
                        {
                            "symbol": clean_symbol,
                            "timeframe": timeframe,
                            "reason": (
                                "MT5 returned zero historical bars for this symbol/timeframe."
                            ),
                        }
                    )
                    continue

                records = [
                    MarketDataRecord(
                        symbol=clean_symbol,
                        timeframe=timeframe,
                        timestamp=row["time"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row.get("real_volume") or row.get("tick_volume") or 0,
                        metadata={
                            "tick_volume": row.get("tick_volume"),
                            "spread": row.get("spread"),
                        },
                    )
                    for row in rows
                ]
                result[clean_symbol][timeframe] = records
        return dict(result)

    @staticmethod
    def validate_records(records: Sequence[MarketDataRecord]) -> list[MarketDataRecord]:
        clean: list[MarketDataRecord] = []
        for record in records:
            if record.high < record.low:
                continue
            if record.open == 0 and record.close == 0:
                continue
            clean.append(record)
        return clean

    @staticmethod
    def summary(
        payload: dict[str, dict[str, list[MarketDataRecord]]],
        failures: Sequence[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        total_points = 0
        total_symbols = 0
        total_timeframes = 0
        records_by_pair: dict[str, int] = {}
        for symbol in payload:
            total_symbols += 1
            if isinstance(payload[symbol], dict):
                total_timeframes += len(payload[symbol])
                for timeframe in payload[symbol]:
                    record_count = len(payload[symbol][timeframe])
                    total_points += record_count
                    records_by_pair[f"{symbol}_{timeframe}"] = record_count
        failure_count = len(failures or [])
        return {
            "symbol_count": total_symbols,
            "timeframe_count": total_timeframes,
            "record_count": total_points,
            "successful_extractions": total_timeframes - failure_count,
            "failure_count": failure_count,
            "symbols": sorted(payload),
            "records_by_symbol_timeframe": records_by_pair,
            "failed_extractions": list(failures or []),
        }
