"""Average Directional Index with automatic market-data updates."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import highs, load_candles, lows, rolling_mean, true_ranges

# Customize these values for the desired trading strategy.
ADX_PERIOD = 14
ADX_STRONG_THRESHOLD = 25.0
ADX_WEAK_THRESHOLD = 20.0
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_adx(
    candles: list[dict[str, Any]],
    period: int = ADX_PERIOD,
    strong_threshold: float = ADX_STRONG_THRESHOLD,
    weak_threshold: float = ADX_WEAK_THRESHOLD,
) -> list[dict[str, float | str | None]]:
    """Calculate +DI, -DI, ADX, and a configurable trend-strength label."""
    if period < 1:
        raise ValueError("period must be at least 1")
    if weak_threshold > strong_threshold:
        raise ValueError("weak_threshold must not exceed strong_threshold")

    high_values, low_values = highs(candles), lows(candles)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for index in range(1, len(candles)):
        upward = high_values[index] - high_values[index - 1]
        downward = low_values[index - 1] - low_values[index]
        plus_dm.append(upward if upward > downward and upward > 0 else 0.0)
        minus_dm.append(downward if downward > upward and downward > 0 else 0.0)

    atr = rolling_mean(true_ranges(candles), period)
    plus_average = rolling_mean(plus_dm, period)
    minus_average = rolling_mean(minus_dm, period)
    plus_di = [
        100 * plus / current_atr
        if plus is not None and current_atr is not None and current_atr != 0
        else None
        for plus, current_atr in zip(plus_average, atr, strict=True)
    ]
    minus_di = [
        100 * minus / current_atr
        if minus is not None and current_atr is not None and current_atr != 0
        else None
        for minus, current_atr in zip(minus_average, atr, strict=True)
    ]
    dx = [
        100 * abs(plus - minus) / (plus + minus)
        if plus is not None and minus is not None and plus + minus
        else None
        for plus, minus in zip(plus_di, minus_di, strict=True)
    ]
    adx = rolling_mean([value or 0.0 for value in dx], period)

    result = []
    for plus, minus, current_adx in zip(plus_di, minus_di, adx, strict=True):
        if current_adx is None:
            strength = "unknown"
        elif current_adx >= strong_threshold:
            strength = "strong"
        elif current_adx >= weak_threshold:
            strength = "developing"
        else:
            strength = "weak"
        direction = (
            "bullish"
            if plus is not None and minus is not None and plus > minus
            else "bearish"
            if plus is not None and minus is not None and minus > plus
            else "neutral"
        )
        result.append(
            {
                "plus_di": plus,
                "minus_di": minus,
                "adx": current_adx,
                "adx_strength": strength,
                "adx_direction": direction,
            }
        )
    return result


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    return input_path.parent / f"{symbol}_adx_{timeframe}_{timestamp}.json"


def save_adx(input_path: Path) -> Path:
    candles = load_candles(input_path)
    indicators = calculate_adx(candles)
    output_path = _output_path(input_path)
    old_outputs = list(
        input_path.parent.glob(
            f"{input_path.stem.rsplit('_', 3)[0]}_adx_{input_path.stem.rsplit('_', 3)[1]}_*.json"
        )
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            [
                {**candle, **indicator}
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
    args = parser.parse_args()
    input_argument = args.input or DEFAULT_DATA_DIR

    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        print(f"Output saved: {save_adx(input_path)}", flush=True)
        if args.once:
            return
        time.sleep(max(0.0, UPDATE_INTERVAL_SECONDS - (time.monotonic() - started_at)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAutomatic update stopped.", flush=True)
