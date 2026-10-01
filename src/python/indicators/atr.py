"""Average True Range (ATR) with automatic market-data updates."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import closes, load_candles, true_ranges

# Customize these values for the desired trading strategy.
ATR_PERIOD = 14
ATR_METHOD = "wilder"  # "wilder" or "sma"
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_atr(
    candles: list[dict[str, Any]],
    period: int = ATR_PERIOD,
    method: str = ATR_METHOD,
) -> list[float | None]:
    """Calculate ATR using Wilder smoothing or a simple moving average."""
    if period < 1:
        raise ValueError("period must be at least 1")
    if method not in {"wilder", "sma"}:
        raise ValueError("method must be 'wilder' or 'sma'")

    ranges = true_ranges(candles)
    result: list[float | None] = [None] * len(ranges)
    if len(ranges) < period:
        return result
    if method == "sma":
        for index in range(period - 1, len(ranges)):
            result[index] = sum(ranges[index - period + 1 : index + 1]) / period
        return result

    average = sum(ranges[:period]) / period
    result[period - 1] = average
    for index in range(period, len(ranges)):
        average = ((average * (period - 1)) + ranges[index]) / period
        result[index] = average
    return result


def enrich_candles(
    candles: list[dict[str, Any]],
    period: int = ATR_PERIOD,
    method: str = ATR_METHOD,
) -> list[dict[str, Any]]:
    """Add ATR, ATR percentage, and a configurable smoothing method to candles."""
    atr_values = calculate_atr(candles, period, method)
    prices = closes(candles)
    return [
        {
            **candle,
            "atr": atr,
            "atr_percent": 100 * atr / price if atr is not None and price else None,
            "atr_period": period,
            "atr_method": method,
        }
        for candle, price, atr in zip(candles, prices, atr_values, strict=True)
    ]


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    if len(parts) < 4:
        return "market", "unknown"
    return "_".join(parts[:-3]), parts[-3]


def save_atr(
    input_path: Path,
    period: int = ATR_PERIOD,
    method: str = ATR_METHOD,
) -> Path:
    candles = load_candles(input_path)
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = input_path.parent / f"{symbol}_atr_{timeframe}_{timestamp}.json"
    old_outputs = list(input_path.parent.glob(f"{symbol}_atr_{timeframe}_*.json"))
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            enrich_candles(candles, period, method), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    for old_output in old_outputs:
        if old_output != output_path:
            old_output.unlink()
    return output_path


def latest_input_file(data_dir: Path, symbol: str, timeframe: str) -> Path:
    candidates = list(data_dir.glob(f"{symbol}_{timeframe}_*.json"))
    if not candidates:
        raise FileNotFoundError(f"No market-data file found for {symbol} {timeframe}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", nargs="?", type=Path, help="JSON file or market_data directory"
    )
    parser.add_argument("--once", action="store_true", help="run only one update")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--period", type=int, default=ATR_PERIOD)
    parser.add_argument("--method", choices=("wilder", "sma"), default=ATR_METHOD)
    args = parser.parse_args()
    input_argument = args.input or DEFAULT_DATA_DIR

    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        print(
            f"Output saved: {save_atr(input_path, args.period, args.method)}",
            flush=True,
        )
        if args.once:
            return
        time.sleep(max(0.0, UPDATE_INTERVAL_SECONDS - (time.monotonic() - started_at)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAutomatic update stopped.", flush=True)
