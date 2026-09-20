"""Configurable RSI/MACD divergence and tick-volume confirmation filters."""

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

from indicators.common import closes, load_candles, rolling_mean
from indicators.macd import calculate_macd
from indicators.rsi import calculate_rsi

RSI_PERIOD = 14
MACD_FAST_PERIOD = 12
MACD_SLOW_PERIOD = 26
MACD_SIGNAL_PERIOD = 9
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
VOLUME_PERIOD = 20
VOLUME_DECLINE_RATIO = 0.90
LEVEL_TOLERANCE = 0.001
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def _pivot(values: list[float], index: int, left: int, right: int, low: bool) -> bool:
    if index < left or index + right >= len(values):
        return False
    window = values[index - left : index + right + 1]
    return values[index] == (min(window) if low else max(window))


def calculate_divergence(
    candles: list[dict[str, Any]],
    rsi_period: int = RSI_PERIOD,
    macd_fast_period: int = MACD_FAST_PERIOD,
    macd_slow_period: int = MACD_SLOW_PERIOD,
    macd_signal_period: int = MACD_SIGNAL_PERIOD,
    pivot_left: int = PIVOT_LEFT,
    pivot_right: int = PIVOT_RIGHT,
) -> list[dict[str, Any]]:
    """Detect confirmed bullish/bearish RSI or MACD divergence at pivot points."""
    if pivot_left < 1 or pivot_right < 1:
        raise ValueError("pivot_left and pivot_right must be at least 1")
    prices = closes(candles)
    rsi_values = calculate_rsi(prices, rsi_period)
    macd_values = calculate_macd(
        prices, macd_fast_period, macd_slow_period, macd_signal_period
    )
    result: list[dict[str, Any]] = []
    prior_low: int | None = None
    prior_high: int | None = None
    for index, price in enumerate(prices):
        signal = "none"
        reason = "none"
        if (
            _pivot(prices, index, pivot_left, pivot_right, True)
            and rsi_values[index] is not None
            and macd_values[index]["macd"] is not None
        ):
            if prior_low is not None:
                current_rsi = rsi_values[index]
                prior_rsi = rsi_values[prior_low]
                current_macd = macd_values[index]["macd"]
                prior_macd = macd_values[prior_low]["macd"]
                bullish_rsi = (
                    current_rsi is not None
                    and price < prices[prior_low]
                    and current_rsi > (prior_rsi or 0)
                )
                bullish_macd = (
                    current_macd is not None
                    and price < prices[prior_low]
                    and current_macd > (prior_macd or 0)
                )
                if bullish_rsi or bullish_macd:
                    signal, reason = "buy", "rsi" if bullish_rsi else "macd"
            prior_low = index
        if (
            _pivot(prices, index, pivot_left, pivot_right, False)
            and rsi_values[index] is not None
            and macd_values[index]["macd"] is not None
        ):
            if prior_high is not None:
                current_rsi = rsi_values[index]
                prior_rsi = rsi_values[prior_high]
                current_macd = macd_values[index]["macd"]
                prior_macd = macd_values[prior_high]["macd"]
                bearish_rsi = (
                    current_rsi is not None
                    and price > prices[prior_high]
                    and current_rsi < (prior_rsi or 0)
                )
                bearish_macd = (
                    current_macd is not None
                    and price > prices[prior_high]
                    and current_macd < (prior_macd or 0)
                )
                if bearish_rsi or bearish_macd:
                    signal, reason = "sell", "rsi" if bearish_rsi else "macd"
            prior_high = index
        result.append(
            {
                "divergence_signal": signal,
                "divergence_indicator": reason,
                "rsi": rsi_values[index],
                "macd": macd_values[index]["macd"],
                "rsi_period": rsi_period,
                "macd_fast_period": macd_fast_period,
                "macd_slow_period": macd_slow_period,
                "macd_signal_period": macd_signal_period,
            }
        )
    return result


def calculate_volume_confirmation(
    candles: list[dict[str, Any]],
    resistance_levels: list[float] | None = None,
    volume_period: int = VOLUME_PERIOD,
    volume_decline_ratio: float = VOLUME_DECLINE_RATIO,
    level_tolerance: float = LEVEL_TOLERANCE,
) -> list[dict[str, Any]]:
    """Detect weakening bullish tick volume near a configured resistance level."""
    if volume_period < 1 or not 0 < volume_decline_ratio <= 1 or level_tolerance < 0:
        raise ValueError("invalid volume or level settings")
    levels = [float(level) for level in (resistance_levels or [])]
    volumes = [float(candle.get("tick_volume", 0)) for candle in candles]
    averages = rolling_mean(volumes, volume_period)
    result = []
    for index, candle in enumerate(candles):
        close = float(candle["close"])
        near_resistance = any(
            abs(close - level) <= max(abs(level) * level_tolerance, 1e-12)
            for level in levels
        )
        bullish = float(candle["close"]) > float(candle["open"])
        previous_bullish_volume = None
        for previous_index in range(index - 1, -1, -1):
            if float(candles[previous_index]["close"]) > float(
                candles[previous_index]["open"]
            ):
                previous_bullish_volume = volumes[previous_index]
                break
        declining = (
            previous_bullish_volume is not None
            and volumes[index] <= previous_bullish_volume * volume_decline_ratio
        )
        sell_confirmation = near_resistance and bullish and declining
        volume_average = averages[index]
        result.append(
            {
                "volume": volumes[index],
                "volume_sma": volume_average,
                "volume_ratio": volumes[index] / volume_average
                if volume_average
                else None,
                "near_resistance": near_resistance,
                "volume_exhaustion": sell_confirmation,
                "volume_confirmation": "sell"
                if sell_confirmation
                else "none"
                if levels
                else "unknown",
                "volume_period": volume_period,
                "volume_decline_ratio": volume_decline_ratio,
            }
        )
    return result


def calculate_confirmation_filter(
    candles: list[dict[str, Any]],
    resistance_levels: list[float] | None = None,
    **settings: Any,
) -> list[dict[str, Any]]:
    """Combine divergence and volume confirmation into a conservative decision."""
    divergence = calculate_divergence(
        candles,
        **{
            key: value
            for key, value in settings.items()
            if key
            in {
                "rsi_period",
                "macd_fast_period",
                "macd_slow_period",
                "macd_signal_period",
                "pivot_left",
                "pivot_right",
            }
        },
    )
    volume = calculate_volume_confirmation(
        candles,
        resistance_levels,
        **{
            key: value
            for key, value in settings.items()
            if key in {"volume_period", "volume_decline_ratio", "level_tolerance"}
        },
    )
    return [
        {
            **divergence_values,
            **volume_values,
            "confirmation_filter": (
                divergence_values["divergence_signal"]
                if divergence_values["divergence_signal"] != "none"
                else volume_values["volume_confirmation"]
            ),
        }
        for divergence_values, volume_values in zip(divergence, volume, strict=True)
    ]


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    return (
        input_path.parent
        / f"{symbol}_confirmation_filter_{timeframe}_{datetime.now().strftime('%Y%m%d_%H.%M.%S')}.json"
    )


def save_confirmation_filter(
    input_path: Path, levels_path: Path | None = None, **settings: Any
) -> Path:
    candles = load_candles(input_path)
    levels = []
    if levels_path is not None:
        payload = json.loads(levels_path.read_text(encoding="utf-8"))
        levels = (
            payload.get("resistance", payload) if isinstance(payload, dict) else payload
        )
        if not isinstance(levels, list):
            raise ValueError("levels JSON must contain a list or 'resistance' list")
    values = calculate_confirmation_filter(candles, levels, **settings)
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_confirmation_filter_{timeframe}_*.json")
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
    parser.add_argument("--levels-file", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--rsi-period", type=int, default=RSI_PERIOD)
    parser.add_argument("--volume-period", type=int, default=VOLUME_PERIOD)
    parser.add_argument(
        "--volume-decline-ratio", type=float, default=VOLUME_DECLINE_RATIO
    )
    parser.add_argument("--level-tolerance", type=float, default=LEVEL_TOLERANCE)
    args = parser.parse_args()
    settings = {
        "rsi_period": args.rsi_period,
        "volume_period": args.volume_period,
        "volume_decline_ratio": args.volume_decline_ratio,
        "level_tolerance": args.level_tolerance,
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
            f"Output saved: {save_confirmation_filter(input_path, args.levels_file, **settings)}",
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
