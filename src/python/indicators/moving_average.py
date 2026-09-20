"""Simple and exponential moving averages with automatic updates."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import load_candles, rolling_mean

# Customize these values for the desired trading strategy.
MA_PERIOD = 20
MA_METHOD = "both"  # "sma", "ema", or "both"
PRICE_FIELD = "close"
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_sma(prices: list[float], period: int = MA_PERIOD) -> list[float | None]:
    return rolling_mean(prices, period)


def calculate_ema(prices: list[float], period: int = MA_PERIOD) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be at least 1")
    result: list[float | None] = [None] * len(prices)
    if len(prices) < period:
        return result
    ema = sum(prices[:period]) / period
    result[period - 1] = ema
    multiplier = 2 / (period + 1)
    for index in range(period, len(prices)):
        ema = (prices[index] - ema) * multiplier + ema
        result[index] = ema
    return result


def enrich_candles(
    candles: list[dict[str, Any]],
    period: int = MA_PERIOD,
    method: str = MA_METHOD,
    price_field: str = PRICE_FIELD,
) -> list[dict[str, Any]]:
    if method not in {"sma", "ema", "both"}:
        raise ValueError("method must be 'sma', 'ema', or 'both'")
    if price_field not in {"open", "high", "low", "close"}:
        raise ValueError("price_field must be open, high, low, or close")
    prices = [float(candle[price_field]) for candle in candles]
    sma = (
        calculate_sma(prices, period)
        if method in {"sma", "both"}
        else [None] * len(candles)
    )
    ema = (
        calculate_ema(prices, period)
        if method in {"ema", "both"}
        else [None] * len(candles)
    )
    return [
        {
            **candle,
            "sma": sma_value,
            "ema": ema_value,
            "ma_period": period,
            "ma_method": method,
            "ma_price_field": price_field,
        }
        for candle, sma_value, ema_value in zip(candles, sma, ema, strict=True)
    ]


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    return (
        ("_".join(parts[:-3]), parts[-3]) if len(parts) >= 4 else ("market", "unknown")
    )


def save_moving_average(
    input_path: Path,
    period: int = MA_PERIOD,
    method: str = MA_METHOD,
    price_field: str = PRICE_FIELD,
) -> Path:
    output_candles = enrich_candles(
        load_candles(input_path), period, method, price_field
    )
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = (
        input_path.parent / f"{symbol}_moving_average_{timeframe}_{timestamp}.json"
    )
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_moving_average_{timeframe}_*.json")
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(output_candles, ensure_ascii=False, indent=2), encoding="utf-8"
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
    parser.add_argument("--period", type=int, default=MA_PERIOD)
    parser.add_argument("--method", choices=("sma", "ema", "both"), default=MA_METHOD)
    parser.add_argument(
        "--price-field", choices=("open", "high", "low", "close"), default=PRICE_FIELD
    )
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
            f"Output saved: {save_moving_average(input_path, args.period, args.method, args.price_field)}",
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
