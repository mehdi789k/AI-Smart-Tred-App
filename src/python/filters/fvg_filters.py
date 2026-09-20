"""Configurable Fair Value Gap detection and entry-delay filter."""

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

MIN_GAP_SIZE = 0.0
MAX_PATH_DISTANCE = 0.0  # 0 means unlimited
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def _validate_settings(min_gap_size: float, max_path_distance: float) -> None:
    if min_gap_size < 0 or max_path_distance < 0:
        raise ValueError("min_gap_size and max_path_distance must be non-negative")


def calculate_fvg_filter(
    candles: list[dict[str, Any]],
    entry_signals: list[str] | None = None,
    min_gap_size: float = MIN_GAP_SIZE,
    max_path_distance: float = MAX_PATH_DISTANCE,
) -> list[dict[str, Any]]:
    """Detect FVGs and delay entries while a relevant gap remains unfilled."""
    _validate_settings(min_gap_size, max_path_distance)
    if entry_signals is not None and len(entry_signals) != len(candles):
        raise ValueError("entry_signals must have the same length as candles")
    signals = entry_signals or ["none"] * len(candles)
    active: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []

    for index, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        filled_ids: set[int] = set()
        for gap in active:
            if low <= gap["upper"] and high >= gap["lower"]:
                gap["filled_at"] = index
                filled_ids.add(gap["id"])
        active = [gap for gap in active if gap["id"] not in filled_ids]

        new_gap: dict[str, Any] | None = None
        if index >= 2:
            two_back = candles[index - 2]
            older_high = float(two_back["high"])
            older_low = float(two_back["low"])
            bullish_size = low - older_high
            bearish_size = older_low - high
            if bullish_size >= min_gap_size and bullish_size > 0:
                new_gap = {
                    "id": index,
                    "type": "bullish",
                    "lower": older_high,
                    "upper": low,
                    "size": bullish_size,
                    "created_at": index,
                }
            elif bearish_size >= min_gap_size and bearish_size > 0:
                new_gap = {
                    "id": index,
                    "type": "bearish",
                    "lower": high,
                    "upper": older_low,
                    "size": bearish_size,
                    "created_at": index,
                }
            if new_gap is not None:
                active.append(new_gap)

        direction = signals[index].lower()
        if direction not in {"buy", "sell", "none"}:
            raise ValueError("entry signals must be 'buy', 'sell', or 'none'")
        relevant = [
            gap
            for gap in active
            if (
                direction == "none"
                or (
                    direction == "buy"
                    and gap["type"] == "bullish"
                    and gap["upper"] < close
                )
                or (
                    direction == "sell"
                    and gap["type"] == "bearish"
                    and gap["lower"] > close
                )
            )
            and (
                max_path_distance == 0
                or abs(close - (gap["upper"] if direction == "buy" else gap["lower"]))
                <= max_path_distance
            )
        ]
        nearest = min(
            relevant,
            key=lambda gap: abs(
                close - (gap["upper"] if direction == "buy" else gap["lower"])
            ),
            default=None,
        )
        result.append(
            {
                "fvg_filter": "wait"
                if nearest is not None and direction != "none"
                else "detect"
                if nearest is not None
                else "allow"
                if direction != "none"
                else "none",
                "fvg_signal": direction,
                "fvg_type": nearest["type"] if nearest else "none",
                "fvg_lower": nearest["lower"] if nearest else None,
                "fvg_upper": nearest["upper"] if nearest else None,
                "fvg_size": nearest["size"] if nearest else None,
                "fvg_unfilled": nearest is not None,
                "active_fvg_count": len(active),
                "fvg_min_gap_size": min_gap_size,
                "fvg_max_path_distance": max_path_distance,
            }
        )
    return result


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    return (
        input_path.parent
        / f"{symbol}_fvg_filter_{timeframe}_{datetime.now().strftime('%Y%m%d_%H.%M.%S')}.json"
    )


def save_fvg_filter(
    input_path: Path,
    signals_path: Path | None = None,
    **settings: Any,
) -> Path:
    candles = load_candles(input_path)
    signals: list[str] | None = None
    if signals_path is not None:
        payload = json.loads(signals_path.read_text(encoding="utf-8"))
        signals = (
            payload.get("signals", payload) if isinstance(payload, dict) else payload
        )
        if not isinstance(signals, list):
            raise ValueError("signals JSON must contain a list or a 'signals' list")
    values = calculate_fvg_filter(candles, signals, **settings)
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_fvg_filter_{timeframe}_*.json")
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            [
                {**candle, **value}
                for candle, value in zip(candles, values, strict=True)
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
    parser.add_argument("--signals-file", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--min-gap-size", type=float, default=MIN_GAP_SIZE)
    parser.add_argument("--max-path-distance", type=float, default=MAX_PATH_DISTANCE)
    args = parser.parse_args()
    settings = {
        "min_gap_size": args.min_gap_size,
        "max_path_distance": args.max_path_distance,
    }
    input_argument = args.input or DEFAULT_DATA_DIR
    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        print(
            f"Output saved: {save_fvg_filter(input_path, args.signals_file, **settings)}",
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
