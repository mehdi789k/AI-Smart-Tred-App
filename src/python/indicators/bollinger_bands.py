"""Bollinger Bands with automatic market-data updates."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

from .common import closes, load_candles

# Customize these values for the desired trading strategy.
BAND_PERIOD = 20
STANDARD_DEVIATIONS = 2.0
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_bollinger_bands(
    prices: list[float],
    period: int = BAND_PERIOD,
    deviations: float = STANDARD_DEVIATIONS,
) -> list[dict[str, float | None]]:
    """Calculate middle, upper, lower bands, width, and %B."""
    if period < 1:
        raise ValueError("period must be at least 1")
    if deviations < 0:
        raise ValueError("deviations must be non-negative")

    result: list[dict[str, float | None]] = []
    for index, price in enumerate(prices):
        window = prices[index - period + 1 : index + 1]
        if len(window) < period:
            result.append(
                {
                    "bollinger_middle": None,
                    "bollinger_upper": None,
                    "bollinger_lower": None,
                    "bollinger_width": None,
                    "bollinger_percent_b": None,
                }
            )
            continue
        mean = sum(window) / period
        standard_deviation = math.sqrt(
            sum((item - mean) ** 2 for item in window) / period
        )
        upper = mean + deviations * standard_deviation
        lower = mean - deviations * standard_deviation
        band_width = upper - lower
        result.append(
            {
                "bollinger_middle": mean,
                "bollinger_upper": upper,
                "bollinger_lower": lower,
                "bollinger_width": band_width,
                "bollinger_percent_b": (
                    (price - lower) / band_width if band_width else 0.5
                ),
            }
        )
    return result


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    if len(parts) < 4:
        return "market", "unknown"
    return "_".join(parts[:-3]), parts[-3]


def save_bollinger_bands(
    input_path: Path,
    period: int = BAND_PERIOD,
    deviations: float = STANDARD_DEVIATIONS,
) -> Path:
    candles = load_candles(input_path)
    indicators = calculate_bollinger_bands(closes(candles), period, deviations)
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = input_path.parent / (
        f"{symbol}_bollinger_bands_{timeframe}_{timestamp}.json"
    )
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_bollinger_bands_{timeframe}_*.json")
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            [
                {
                    **candle,
                    **indicator,
                    "bollinger_period": period,
                    "bollinger_deviations": deviations,
                }
                for candle, indicator in zip(candles, indicators, strict=True)
            ],
            ensure_ascii=False,
            indent=2,
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
    parser.add_argument("--period", type=int, default=BAND_PERIOD)
    parser.add_argument("--deviations", type=float, default=STANDARD_DEVIATIONS)
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
            f"Output saved: {save_bollinger_bands(input_path, args.period, args.deviations)}",
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
