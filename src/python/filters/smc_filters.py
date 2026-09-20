"""Configurable Smart Money Concepts liquidity-sweep filter."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "indicators"))

from indicators.common import load_candles

SWEEP_LOOKBACK = 20
MIN_BODY_RATIO = 0.50
RECLAIM_BUFFER = 0.0
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_liquidity_sweeps(
    candles: list[dict[str, Any]],
    lookback: int = SWEEP_LOOKBACK,
    min_body_ratio: float = MIN_BODY_RATIO,
    reclaim_buffer: float = RECLAIM_BUFFER,
) -> list[dict[str, Any]]:
    """Detect bullish support sweeps and bearish resistance sweeps."""
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if not 0 <= min_body_ratio <= 1:
        raise ValueError("min_body_ratio must be between 0 and 1")
    if reclaim_buffer < 0:
        raise ValueError("reclaim_buffer must be non-negative")

    result: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        support = resistance = None
        signal = "none"
        direction = "unknown"
        if index >= lookback:
            previous = candles[index - lookback : index]
            support = min(float(item["low"]) for item in previous)
            resistance = max(float(item["high"]) for item in previous)
            open_price = float(candle["open"])
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
            candle_range = high - low
            body_ratio = abs(close - open_price) / candle_range if candle_range else 0.0
            bullish_sweep = (
                low < support
                and close >= support + reclaim_buffer
                and close > open_price
                and body_ratio >= min_body_ratio
            )
            bearish_sweep = (
                high > resistance
                and close <= resistance - reclaim_buffer
                and close < open_price
                and body_ratio >= min_body_ratio
            )
            if bullish_sweep:
                signal, direction = "buy", "bullish"
            elif bearish_sweep:
                signal, direction = "sell", "bearish"
            else:
                direction = "neutral"
        result.append(
            {
                "liquidity_sweep": signal,
                "sweep_direction": direction,
                "swept_support": support,
                "swept_resistance": resistance,
                "sweep_lookback": lookback,
                "sweep_min_body_ratio": min_body_ratio,
                "sweep_reclaim_buffer": reclaim_buffer,
            }
        )
    return result


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    return input_path.parent / f"{symbol}_smc_filter_{timeframe}_{timestamp}.json"


def save_smc_filter(input_path: Path, **settings: Any) -> Path:
    candles = load_candles(input_path)
    sweeps = calculate_liquidity_sweeps(candles, **settings)
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_smc_filter_{timeframe}_*.json")
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            [
                {**candle, **sweep}
                for candle, sweep in zip(candles, sweeps, strict=True)
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
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--lookback", type=int, default=SWEEP_LOOKBACK)
    parser.add_argument("--min-body-ratio", type=float, default=MIN_BODY_RATIO)
    parser.add_argument("--reclaim-buffer", type=float, default=RECLAIM_BUFFER)
    args = parser.parse_args()
    settings = {
        "lookback": args.lookback,
        "min_body_ratio": args.min_body_ratio,
        "reclaim_buffer": args.reclaim_buffer,
    }
    input_argument = args.input or DEFAULT_DATA_DIR
    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        print(f"Output saved: {save_smc_filter(input_path, **settings)}", flush=True)
        if args.once:
            return
        time.sleep(max(0.0, UPDATE_INTERVAL_SECONDS - (time.monotonic() - started_at)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAutomatic update stopped.", flush=True)
