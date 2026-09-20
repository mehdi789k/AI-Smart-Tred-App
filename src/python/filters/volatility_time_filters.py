"""Configurable trading-session, news-window, and ATR-volatility filters."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "indicators"))

from indicators.common import load_candles, rolling_mean, true_ranges

ATR_PERIOD = 14
ATR_LOOKBACK = 100
ATR_LOW_PERCENTILE = 20.0
NEWS_WINDOW_MINUTES = 30
SESSION_START = "15:30"
SESSION_END = "19:30"
TIMEZONE_OFFSET_HOURS = 3.5
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except (TypeError, ValueError) as error:
        raise ValueError("session times must use HH:MM format") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("session times must use HH:MM format")
    return hour, minute


def _candle_datetime(candle: dict[str, Any], tz: timezone) -> datetime:
    if candle.get("time_iso"):
        return (
            datetime.fromisoformat(str(candle["time_iso"]).replace("Z", "+00:00"))
            .replace(tzinfo=None)
            .replace(tzinfo=tz)
        )
    if "time" not in candle:
        raise ValueError("candles must contain 'time_iso' or 'time'")
    return datetime.fromtimestamp(float(candle["time"]), tz=timezone.utc).astimezone(tz)


def load_high_impact_news(path: Path | None) -> list[datetime]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("news", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("news JSON must contain a list or a 'news' list")
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("impact", "")).lower() not in {
            "high",
            "red",
        }:
            continue
        value = entry.get("time_iso", entry.get("time"))
        if value is None:
            raise ValueError("each high-impact news item needs time_iso or time")
        result.append(
            datetime.fromtimestamp(float(value), tz=timezone.utc)
            if isinstance(value, (int, float))
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    return result


def calculate_volatility_time_filter(
    candles: list[dict[str, Any]],
    news: list[datetime] | None = None,
    atr_period: int = ATR_PERIOD,
    atr_lookback: int = ATR_LOOKBACK,
    atr_low_percentile: float = ATR_LOW_PERCENTILE,
    news_window_minutes: int = NEWS_WINDOW_MINUTES,
    session_start: str = SESSION_START,
    session_end: str = SESSION_END,
    timezone_offset_hours: float = TIMEZONE_OFFSET_HOURS,
) -> list[dict[str, Any]]:
    """Combine session, high-impact-news, and low-ATR squeeze checks."""
    if atr_period < 1 or atr_lookback < 1:
        raise ValueError("atr_period and atr_lookback must be at least 1")
    if not 0 <= atr_low_percentile <= 100 or news_window_minutes < 0:
        raise ValueError(
            "atr_low_percentile must be 0..100 and news_window_minutes non-negative"
        )
    start_hour, start_minute = _parse_time(session_start)
    end_hour, end_minute = _parse_time(session_end)
    session_start_minutes = start_hour * 60 + start_minute
    session_end_minutes = end_hour * 60 + end_minute
    if session_start_minutes == session_end_minutes:
        raise ValueError("session_start and session_end must differ")

    atr_values = rolling_mean(true_ranges(candles), atr_period)
    tz = timezone(timedelta(hours=timezone_offset_hours))
    high_news = news or []
    result = []
    for index, (candle, atr) in enumerate(zip(candles, atr_values, strict=True)):
        local = _candle_datetime(candle, tz)
        minute = local.hour * 60 + local.minute
        in_session = (
            session_start_minutes <= minute < session_end_minutes
            if session_start_minutes < session_end_minutes
            else minute >= session_start_minutes or minute < session_end_minutes
        )
        news_blocked = any(
            abs(
                _candle_datetime({"time_iso": item.isoformat()}, tz).timestamp()
                - local.timestamp()
            )
            <= news_window_minutes * 60
            for item in high_news
        )
        history = [
            value
            for value in atr_values[max(0, index - atr_lookback + 1) : index + 1]
            if value is not None
        ]
        threshold = None
        squeeze = False
        if atr is not None and len(history) >= min(atr_period, atr_lookback):
            ordered = sorted(history)
            position = (len(ordered) - 1) * atr_low_percentile / 100
            lower = ordered[int(position)]
            upper = ordered[min(int(position) + 1, len(ordered) - 1)]
            threshold = lower + (upper - lower) * (position % 1)
            squeeze = atr <= threshold
        blocked = not in_session or news_blocked or squeeze
        result.append(
            {
                "atr": atr,
                "atr_period": atr_period,
                "atr_low_threshold": threshold,
                "atr_squeeze": squeeze,
                "in_trading_session": in_session,
                "high_impact_news_blocked": news_blocked,
                "volatility_time_filter": "allow" if not blocked else "avoid",
                "news_window_minutes": news_window_minutes,
                "session": f"{session_start}-{session_end}",
            }
        )
    return result


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    return (
        input_path.parent
        / f"{symbol}_volatility_time_filter_{timeframe}_{datetime.now().strftime('%Y%m%d_%H.%M.%S')}.json"
    )


def save_volatility_time_filter(
    input_path: Path, news_path: Path | None = None, **settings: Any
) -> Path:
    candles = load_candles(input_path)
    values = calculate_volatility_time_filter(
        candles, load_high_impact_news(news_path), **settings
    )
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_volatility_time_filter_{timeframe}_*.json")
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            [{**c, **v} for c, v in zip(candles, values, strict=True)],
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
    parser.add_argument("--news-file", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--atr-period", type=int, default=ATR_PERIOD)
    parser.add_argument("--atr-lookback", type=int, default=ATR_LOOKBACK)
    parser.add_argument("--atr-low-percentile", type=float, default=ATR_LOW_PERCENTILE)
    parser.add_argument("--news-window-minutes", type=int, default=NEWS_WINDOW_MINUTES)
    parser.add_argument("--session-start", default=SESSION_START)
    parser.add_argument("--session-end", default=SESSION_END)
    parser.add_argument(
        "--timezone-offset-hours", type=float, default=TIMEZONE_OFFSET_HOURS
    )
    args = parser.parse_args()
    settings = {
        key: getattr(args, key)
        for key in (
            "atr_period",
            "atr_lookback",
            "atr_low_percentile",
            "news_window_minutes",
            "session_start",
            "session_end",
            "timezone_offset_hours",
        )
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
            f"Output saved: {save_volatility_time_filter(input_path, args.news_file, **settings)}",
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
