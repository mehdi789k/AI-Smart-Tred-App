"""Moving Average Convergence Divergence with automatic market-data updates."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from .common import closes, load_candles
from .moving_average import calculate_ema

# Customize these values for the desired trading strategy.
FAST_PERIOD = 12
SLOW_PERIOD = 26
SIGNAL_PERIOD = 9
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_macd(
    prices: list[float],
    fast_period: int = FAST_PERIOD,
    slow_period: int = SLOW_PERIOD,
    signal_period: int = SIGNAL_PERIOD,
) -> list[dict[str, float | None]]:
    """Calculate MACD line, signal line, histogram, and crossover direction."""
    if fast_period < 1 or slow_period < 1 or signal_period < 1:
        raise ValueError("MACD periods must be at least 1")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be smaller than slow_period")

    fast = calculate_ema(prices, fast_period)
    slow = calculate_ema(prices, slow_period)
    macd_line = [
        fast_value - slow_value
        if fast_value is not None and slow_value is not None
        else None
        for fast_value, slow_value in zip(fast, slow, strict=True)
    ]
    valid_line = [value for value in macd_line if value is not None]
    signal_values = calculate_ema(valid_line, signal_period)
    leading_none: list[float | None] = [None] * (len(macd_line) - len(signal_values))
    signal_line = leading_none + signal_values

    result: list[dict[str, float | None]] = []
    previous_histogram: float | None = None
    for macd_value, signal_value in zip(macd_line, signal_line, strict=True):
        histogram = (
            macd_value - signal_value
            if macd_value is not None and signal_value is not None
            else None
        )
        if histogram is None or previous_histogram is None:
            crossover = None
        elif previous_histogram <= 0 < histogram:
            crossover = 1.0
        elif previous_histogram >= 0 > histogram:
            crossover = -1.0
        else:
            crossover = 0.0
        result.append(
            {
                "macd": macd_value,
                "macd_signal": signal_value,
                "macd_histogram": histogram,
                "macd_crossover": crossover,
            }
        )
        previous_histogram = histogram
    return result


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    if len(parts) < 4:
        return "market", "unknown"
    return "_".join(parts[:-3]), parts[-3]


def save_macd(
    input_path: Path,
    fast_period: int = FAST_PERIOD,
    slow_period: int = SLOW_PERIOD,
    signal_period: int = SIGNAL_PERIOD,
) -> Path:
    candles = load_candles(input_path)
    indicators = calculate_macd(
        closes(candles), fast_period, slow_period, signal_period
    )
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = input_path.parent / f"{symbol}_macd_{timeframe}_{timestamp}.json"
    old_outputs = list(input_path.parent.glob(f"{symbol}_macd_{timeframe}_*.json"))
    payload = [
        {
            **candle,
            **indicator,
            "macd_fast_period": fast_period,
            "macd_slow_period": slow_period,
            "macd_signal_period": signal_period,
        }
        for candle, indicator in zip(candles, indicators, strict=True)
    ]
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
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
    parser.add_argument("--fast-period", type=int, default=FAST_PERIOD)
    parser.add_argument("--slow-period", type=int, default=SLOW_PERIOD)
    parser.add_argument("--signal-period", type=int, default=SIGNAL_PERIOD)
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
            f"Output saved: {save_macd(input_path, args.fast_period, args.slow_period, args.signal_period)}",
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
