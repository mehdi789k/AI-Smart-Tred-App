"""Ichimoku Cloud indicator with a one-second market-data updater.

The cloud values are aligned to the candle on which they are visible.  In
other words, a value at candle ``i`` is calculated from the Tenkan/Kijun
values at ``i - displacement``; this avoids using future candles when a
signal is generated.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # Works both as ``python indicators/ichimoku.py`` and as a package.
    from common import highs, load_candles, lows
except ImportError:  # pragma: no cover - exercised when imported as a package.
    from .common import highs, load_candles, lows


TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_PERIOD = 52
DISPLACEMENT = 26
MIN_CLOUD_DISTANCE = 0.0
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def _validate_parameters(
    tenkan: int, kijun: int, senkou: int, displacement: int, min_cloud_distance: float
) -> None:
    if min(tenkan, kijun, senkou) < 1:
        raise ValueError("tenkan, kijun, and senkou periods must be at least 1")
    if displacement < 0:
        raise ValueError("displacement must be non-negative")
    if min_cloud_distance < 0:
        raise ValueError("min_cloud_distance must be non-negative")


def _rolling_midpoint(
    high_values: list[float], low_values: list[float], period: int
) -> list[float | None]:
    """Return the midpoint of the rolling high/low range.

    Monotonic queues keep this O(n), which is useful when the updater is fed
    with a large history on every tick.
    """
    result: list[float | None] = [None] * len(high_values)
    max_queue: deque[int] = deque()
    min_queue: deque[int] = deque()

    for index, (high, low) in enumerate(zip(high_values, low_values, strict=True)):
        while max_queue and high_values[max_queue[-1]] <= high:
            max_queue.pop()
        max_queue.append(index)
        while min_queue and low_values[min_queue[-1]] >= low:
            min_queue.pop()
        min_queue.append(index)

        first_valid = index - period + 1
        while max_queue and max_queue[0] < first_valid:
            max_queue.popleft()
        while min_queue and min_queue[0] < first_valid:
            min_queue.popleft()
        if index >= period - 1:
            result[index] = (high_values[max_queue[0]] + low_values[min_queue[0]]) / 2.0
    return result


def calculate_ichimoku(
    candles: list[dict[str, Any]],
    tenkan: int = TENKAN_PERIOD,
    kijun: int = KIJUN_PERIOD,
    senkou: int = SENKOU_PERIOD,
    displacement: int = DISPLACEMENT,
    min_cloud_distance: float = MIN_CLOUD_DISTANCE,
) -> list[dict[str, float | bool | str | None]]:
    """Calculate Ichimoku values and conservative buy/sell signals.

    A ``buy`` is emitted only when the close is strictly above the visible
    Kumo (by at least ``min_cloud_distance``) and Tenkan is above Kijun.
    Candles inside the Kumo are explicitly labelled ``range`` and can never
    produce a trade signal.
    """
    _validate_parameters(tenkan, kijun, senkou, displacement, min_cloud_distance)
    if not candles:
        return []

    high_values = highs(candles)
    low_values = lows(candles)
    close_values = [float(candle["close"]) for candle in candles]
    tenkan_values = _rolling_midpoint(high_values, low_values, tenkan)
    kijun_values = _rolling_midpoint(high_values, low_values, kijun)
    senkou_b_source = _rolling_midpoint(high_values, low_values, senkou)

    # Span A is calculated from Tenkan/Kijun and both spans are projected
    # forward.  Aligning by displacement here makes the current signal
    # equivalent to comparing price with the cloud currently on the chart.
    senkou_a_source: list[float | None] = [
        (tenkan_value + kijun_value) / 2.0
        if tenkan_value is not None and kijun_value is not None
        else None
        for tenkan_value, kijun_value in zip(tenkan_values, kijun_values, strict=True)
    ]

    def shifted(values: list[float | None]) -> list[float | None]:
        if displacement == 0:
            return values[:]
        if displacement >= len(values):
            return [None] * len(values)
        return [None] * displacement + values[:-displacement]

    span_a = shifted(senkou_a_source)
    span_b = shifted(senkou_b_source)
    result: list[dict[str, float | bool | str | None]] = []

    for close, tenkan_value, kijun_value, current_a, current_b in zip(
        close_values, tenkan_values, kijun_values, span_a, span_b, strict=True
    ):
        if current_a is None or current_b is None:
            cloud_top = cloud_bottom = cloud_distance = None
            signal = "unknown"
            trade_allowed = False
            position = "unknown"
        else:
            cloud_top = max(current_a, current_b)
            cloud_bottom = min(current_a, current_b)
            if close > cloud_top:
                cloud_distance = close - cloud_top
                position = "above_cloud"
            elif close < cloud_bottom:
                cloud_distance = cloud_bottom - close
                position = "below_cloud"
            else:
                cloud_distance = 0.0
                position = "inside_cloud"

            bullish = (
                position == "above_cloud"
                and tenkan_value is not None
                and kijun_value is not None
                and tenkan_value > kijun_value
                and cloud_distance >= min_cloud_distance
            )
            bearish = (
                position == "below_cloud"
                and tenkan_value is not None
                and kijun_value is not None
                and tenkan_value < kijun_value
                and cloud_distance >= min_cloud_distance
            )
            if bullish:
                signal = "buy"
            elif bearish:
                signal = "sell"
            elif position == "inside_cloud":
                signal = "range"
            else:
                signal = "neutral"
            trade_allowed = signal in {"buy", "sell"}

        result.append(
            {
                "tenkan": tenkan_value,
                "kijun": kijun_value,
                "senkou_span_a": current_a,
                "senkou_span_b": current_b,
                "kumo_top": cloud_top,
                "kumo_bottom": cloud_bottom,
                "cloud_distance": cloud_distance,
                "ichimoku_position": position,
                "ichimoku_signal": signal,
                "trade_allowed": trade_allowed,
            }
        )
    return result


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    if len(parts) < 4:
        return "market", "unknown"
    return "_".join(parts[:-3]), parts[-3]


def save_ichimoku(
    input_path: Path,
    tenkan: int = TENKAN_PERIOD,
    kijun: int = KIJUN_PERIOD,
    senkou: int = SENKOU_PERIOD,
    displacement: int = DISPLACEMENT,
    min_cloud_distance: float = MIN_CLOUD_DISTANCE,
) -> Path:
    """Write a timestamped result and remove all previous Ichimoku outputs."""
    candles = load_candles(input_path)
    indicators = calculate_ichimoku(
        candles, tenkan, kijun, senkou, displacement, min_cloud_distance
    )
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = input_path.parent / f"{symbol}_ichimoku_{timeframe}_{timestamp}.json"
    old_outputs = list(input_path.parent.glob(f"{symbol}_ichimoku_{timeframe}_*.json"))
    payload = [
        {
            **candle,
            **indicator,
            "ichimoku_tenkan_period": tenkan,
            "ichimoku_kijun_period": kijun,
            "ichimoku_senkou_period": senkou,
            "ichimoku_displacement": displacement,
            "ichimoku_min_cloud_distance": min_cloud_distance,
        }
        for candle, indicator in zip(candles, indicators, strict=True)
    ]
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_path.replace(output_path)
    for old_output in old_outputs:
        if old_output != output_path and old_output.exists():
            old_output.unlink()
    return output_path


def latest_input_file(
    data_dir: Path, symbol: str = DEFAULT_SYMBOL, timeframe: str = DEFAULT_TIMEFRAME
) -> Path:
    """Return the newest source market-data JSON for a symbol/timeframe."""
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
    parser.add_argument("--tenkan", type=int, default=TENKAN_PERIOD)
    parser.add_argument("--kijun", type=int, default=KIJUN_PERIOD)
    parser.add_argument("--senkou", type=int, default=SENKOU_PERIOD)
    parser.add_argument("--displacement", type=int, default=DISPLACEMENT)
    parser.add_argument("--min-cloud-distance", type=float, default=MIN_CLOUD_DISTANCE)
    args = parser.parse_args()
    input_argument = args.input or DEFAULT_DATA_DIR

    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        output_path = save_ichimoku(
            input_path,
            args.tenkan,
            args.kijun,
            args.senkou,
            args.displacement,
            args.min_cloud_distance,
        )
        print(f"Output saved: {output_path}", flush=True)
        if args.once:
            return
        time.sleep(max(0.0, UPDATE_INTERVAL_SECONDS - (time.monotonic() - started_at)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAutomatic update stopped.", flush=True)
