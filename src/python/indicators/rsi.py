"""Relative Strength Index with automatic market-data updates."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import load_candles

# Customize these values for the desired trading strategy.
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
PRICE_FIELD = "close"
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_rsi(prices: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be at least 1")
    result: list[float | None] = [None] * len(prices)
    gains = [max(prices[i] - prices[i - 1], 0.0) for i in range(1, len(prices))]
    losses = [max(prices[i - 1] - prices[i], 0.0) for i in range(1, len(prices))]
    if len(prices) <= period:
        return result
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for index in range(period, len(prices)):
        if index > period:
            average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        result[index] = (
            100.0
            if average_loss == 0
            else 100 - 100 / (1 + average_gain / average_loss)
        )
    return result


def enrich_candles(
    candles: list[dict[str, Any]],
    period: int = RSI_PERIOD,
    overbought: float = RSI_OVERBOUGHT,
    oversold: float = RSI_OVERSOLD,
    price_field: str = PRICE_FIELD,
) -> list[dict[str, Any]]:
    if not 0 <= oversold < overbought <= 100:
        raise ValueError("thresholds must satisfy 0 <= oversold < overbought <= 100")
    if price_field not in {"open", "high", "low", "close"}:
        raise ValueError("price_field must be open, high, low, or close")
    rsi_values = calculate_rsi(
        [float(candle[price_field]) for candle in candles], period
    )
    return [
        {
            **candle,
            "rsi": rsi,
            "rsi_signal": (
                "overbought"
                if rsi is not None and rsi >= overbought
                else "oversold"
                if rsi is not None and rsi <= oversold
                else "neutral"
                if rsi is not None
                else "unknown"
            ),
            "rsi_period": period,
            "rsi_overbought": overbought,
            "rsi_oversold": oversold,
            "rsi_price_field": price_field,
        }
        for candle, rsi in zip(candles, rsi_values, strict=True)
    ]


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    return (
        ("_".join(parts[:-3]), parts[-3]) if len(parts) >= 4 else ("market", "unknown")
    )


def save_rsi(
    input_path: Path,
    period: int = RSI_PERIOD,
    overbought: float = RSI_OVERBOUGHT,
    oversold: float = RSI_OVERSOLD,
    price_field: str = PRICE_FIELD,
) -> Path:
    output_candles = enrich_candles(
        load_candles(input_path), period, overbought, oversold, price_field
    )
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = input_path.parent / f"{symbol}_rsi_{timeframe}_{timestamp}.json"
    old_outputs = list(input_path.parent.glob(f"{symbol}_rsi_{timeframe}_*.json"))
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
    parser.add_argument("--period", type=int, default=RSI_PERIOD)
    parser.add_argument("--overbought", type=float, default=RSI_OVERBOUGHT)
    parser.add_argument("--oversold", type=float, default=RSI_OVERSOLD)
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
            f"Output saved: {save_rsi(input_path, args.period, args.overbought, args.oversold, args.price_field)}",
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
