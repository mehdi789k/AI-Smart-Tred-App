"""Multi-timeframe trend, price-action, and supply/demand filters."""

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

MA_PERIOD = 50
ZONE_TOLERANCE = 0.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"
DEFAULT_HTF_TIMEFRAME = "H4"
UPDATE_INTERVAL_SECONDS = 1.0


def _ema(prices: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("ma_period must be at least 1")
    values: list[float | None] = [None] * len(prices)
    if len(prices) < period:
        return values
    current = sum(prices[:period]) / period
    values[period - 1] = current
    multiplier = 2 / (period + 1)
    for index in range(period, len(prices)):
        current = (prices[index] - current) * multiplier + current
        values[index] = current
    return values


def load_zones(path: Path | None) -> list[dict[str, Any]]:
    """Load supply/demand zones with lower, upper, and type fields."""
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    zones = payload.get("zones", payload) if isinstance(payload, dict) else payload
    if not isinstance(zones, list):
        raise ValueError("zones JSON must contain a list or a 'zones' list")
    result: list[dict[str, Any]] = []
    for zone in zones:
        if not isinstance(zone, dict) or zone.get("type") not in {"supply", "demand"}:
            raise ValueError("each zone must have type 'supply' or 'demand'")
        lower, upper = float(zone["lower"]), float(zone["upper"])
        if lower > upper:
            raise ValueError("zone lower must not exceed upper")
        result.append({**zone, "lower": lower, "upper": upper})
    return result


def _time_value(candle: dict[str, Any]) -> float:
    if "time" in candle:
        return float(candle["time"])
    if "time_iso" in candle:
        return datetime.fromisoformat(
            str(candle["time_iso"]).replace("Z", "+00:00")
        ).timestamp()
    raise ValueError("candles must contain 'time' or 'time_iso'")


def _pattern(previous: dict[str, Any] | None, candle: dict[str, Any]) -> str:
    open_price, close = float(candle["open"]), float(candle["close"])
    high, low = float(candle["high"]), float(candle["low"])
    body = abs(close - open_price)
    upper_wick, lower_wick = high - max(open_price, close), min(open_price, close) - low
    if body and lower_wick >= body * 2 and upper_wick <= body:
        return "bullish_pinbar"
    if body and upper_wick >= body * 2 and lower_wick <= body:
        return "bearish_pinbar"
    if previous is not None:
        prev_open, prev_close = float(previous["open"]), float(previous["close"])
        if (
            close > open_price
            and prev_close < prev_open
            and open_price <= prev_close
            and close >= prev_open
        ):
            return "bullish_engulfing"
        if (
            close < open_price
            and prev_close > prev_open
            and open_price >= prev_close
            and close <= prev_open
        ):
            return "bearish_engulfing"
    return "none"


def calculate_structure_mtf_filter(
    ltf_candles: list[dict[str, Any]],
    htf_candles: list[dict[str, Any]],
    zones: list[dict[str, Any]] | None = None,
    ma_period: int = MA_PERIOD,
    zone_tolerance: float = ZONE_TOLERANCE,
) -> list[dict[str, Any]]:
    """Allow only LTF bullish/bearish patterns aligned with HTF trend and zones."""
    if ma_period < 1 or zone_tolerance < 0:
        raise ValueError("ma_period must be at least 1 and zone_tolerance non-negative")
    if not htf_candles:
        raise ValueError("htf_candles must not be empty")
    htf_times = [_time_value(candle) for candle in htf_candles]
    htf_ma = _ema([float(candle["close"]) for candle in htf_candles], ma_period)
    result: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for candle in ltf_candles:
        timestamp = _time_value(candle)
        htf_index = max(
            (index for index, value in enumerate(htf_times) if value <= timestamp),
            default=None,
        )
        price = float(candle["close"])
        pattern = _pattern(previous, candle)
        previous = candle
        htf_ma_value = htf_ma[htf_index] if htf_index is not None else None
        if htf_index is None or htf_ma_value is None:
            result.append(
                {
                    "mtf_filter": "unknown",
                    "htf_trend": "unknown",
                    "price_action": pattern,
                    "zone_type": "none",
                }
            )
            continue
        htf_close = float(htf_candles[htf_index]["close"])
        htf_trend = (
            "bullish"
            if htf_close > htf_ma_value
            else "bearish"
            if htf_close < htf_ma_value
            else "neutral"
        )
        matching_zone = next(
            (
                zone
                for zone in (zones or [])
                if float(zone["lower"]) - zone_tolerance
                <= price
                <= float(zone["upper"]) + zone_tolerance
                and (
                    (htf_trend == "bullish" and zone["type"] == "demand")
                    or (htf_trend == "bearish" and zone["type"] == "supply")
                )
            ),
            None,
        )
        bullish_pattern = pattern.startswith("bullish")
        bearish_pattern = pattern.startswith("bearish")
        allowed = matching_zone is not None and (
            (htf_trend == "bullish" and bullish_pattern)
            or (htf_trend == "bearish" and bearish_pattern)
        )
        result.append(
            {
                "mtf_filter": "allow" if allowed else "avoid",
                "htf_trend": htf_trend,
                "htf_ma": htf_ma_value,
                "htf_ma_period": ma_period,
                "price_action": pattern,
                "zone_type": matching_zone["type"] if matching_zone else "none",
                "zone_lower": matching_zone["lower"] if matching_zone else None,
                "zone_upper": matching_zone["upper"] if matching_zone else None,
                "no_mans_land": matching_zone is None,
            }
        )
    return result


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    return (
        input_path.parent
        / f"{symbol}_structure_mtf_filter_{timeframe}_{datetime.now().strftime('%Y%m%d_%H.%M.%S')}.json"
    )


def save_structure_mtf_filter(
    input_path: Path, htf_input: Path, zones_path: Path | None = None, **settings: Any
) -> Path:
    candles = load_candles(input_path)
    values = calculate_structure_mtf_filter(
        candles, load_candles(htf_input), load_zones(zones_path), **settings
    )
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_structure_mtf_filter_{timeframe}_*.json")
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
    parser.add_argument("--htf-input", type=Path)
    parser.add_argument("--zones-file", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--htf-timeframe", default=DEFAULT_HTF_TIMEFRAME)
    parser.add_argument("--ma-period", type=int, default=MA_PERIOD)
    parser.add_argument("--zone-tolerance", type=float, default=ZONE_TOLERANCE)
    args = parser.parse_args()
    input_argument = args.input or DEFAULT_DATA_DIR
    htf_input = args.htf_input
    settings = {"ma_period": args.ma_period, "zone_tolerance": args.zone_tolerance}
    while True:
        started_at = time.monotonic()
        input_path: Path
        if input_argument.is_dir():
            input_path = latest_input_file(input_argument, args.symbol, args.timeframe)
            if htf_input is None:
                htf_input = latest_input_file(
                    input_argument, args.symbol, args.htf_timeframe
                )
        else:
            input_path = input_argument
            if htf_input is None:
                raise ValueError("--htf-input is required when input is a file")
        print(
            f"Output saved: {save_structure_mtf_filter(input_path, htf_input, args.zones_file, **settings)}",
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
