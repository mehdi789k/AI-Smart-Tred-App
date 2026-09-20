"""Volume analysis with automatic market-data updates."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import load_candles, rolling_mean

# Customize these values for the desired trading strategy.
VOLUME_PERIOD = 20
VOLUME_SOURCE = "tick_volume"  # "tick_volume" or "real_volume"
VOLUME_SPIKE_RATIO = 1.5
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_volume(
    candles: list[dict[str, Any]],
    period: int = VOLUME_PERIOD,
    source: str = VOLUME_SOURCE,
    spike_ratio: float = VOLUME_SPIKE_RATIO,
) -> list[dict[str, Any]]:
    """Calculate volume average, ratio, spike state, and price direction."""
    if period < 1:
        raise ValueError("period must be at least 1")
    if source not in {"tick_volume", "real_volume"}:
        raise ValueError("source must be 'tick_volume' or 'real_volume'")
    if spike_ratio <= 0:
        raise ValueError("spike_ratio must be greater than zero")

    volumes = [float(candle.get(source, 0)) for candle in candles]
    averages = rolling_mean(volumes, period)
    result = []
    for index, (candle, volume, average) in enumerate(
        zip(candles, volumes, averages, strict=True)
    ):
        ratio = volume / average if average else None
        direction = (
            "up"
            if index and float(candle["close"]) > float(candles[index - 1]["close"])
            else "down"
            if index and float(candle["close"]) < float(candles[index - 1]["close"])
            else "flat"
        )
        result.append(
            {
                **candle,
                "volume": volume,
                "volume_source": source,
                "volume_sma": average,
                "volume_ratio": ratio,
                "volume_spike": ratio is not None and ratio >= spike_ratio,
                "volume_state": (
                    "high"
                    if ratio is not None and ratio >= spike_ratio
                    else "normal"
                    if ratio is not None
                    else "unknown"
                ),
                "volume_direction": direction,
            }
        )
    return result


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    return (
        ("_".join(parts[:-3]), parts[-3]) if len(parts) >= 4 else ("market", "unknown")
    )


def save_volume(
    input_path: Path,
    period: int = VOLUME_PERIOD,
    source: str = VOLUME_SOURCE,
    spike_ratio: float = VOLUME_SPIKE_RATIO,
) -> Path:
    candles = load_candles(input_path)
    result = calculate_volume(candles, period, source, spike_ratio)
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = input_path.parent / f"{symbol}_volume_{timeframe}_{timestamp}.json"
    old_outputs = list(input_path.parent.glob(f"{symbol}_volume_{timeframe}_*.json"))
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
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
    parser.add_argument("--period", type=int, default=VOLUME_PERIOD)
    parser.add_argument(
        "--source", choices=("tick_volume", "real_volume"), default=VOLUME_SOURCE
    )
    parser.add_argument("--spike-ratio", type=float, default=VOLUME_SPIKE_RATIO)
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
            f"Output saved: {save_volume(input_path, args.period, args.source, args.spike_ratio)}",
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
