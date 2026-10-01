"""Shared helpers for the standalone indicator modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_candles(path: str | Path) -> list[dict[str, Any]]:
    """Load candles from a market-data JSON file."""
    with Path(path).open("r", encoding="utf-8") as file:
        payload = json.load(file)
    candles = payload.get("candles", payload) if isinstance(payload, dict) else payload
    if not isinstance(candles, list):
        raise ValueError("Input JSON must contain a 'candles' list.")
    return candles


def closes(candles: list[dict[str, Any]]) -> list[float]:
    return [float(candle["close"]) for candle in candles]


def highs(candles: list[dict[str, Any]]) -> list[float]:
    return [float(candle["high"]) for candle in candles]


def lows(candles: list[dict[str, Any]]) -> list[float]:
    return [float(candle["low"]) for candle in candles]


def values(candles: list[dict[str, Any]], key: str = "tick_volume") -> list[float]:
    return [float(candle.get(key, 0)) for candle in candles]


def rolling_mean(items: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be at least 1")
    result: list[float | None] = [None] * len(items)
    for index in range(period - 1, len(items)):
        result[index] = sum(items[index - period + 1 : index + 1]) / period
    return result


def true_ranges(candles: list[dict[str, Any]]) -> list[float]:
    result = []
    previous_close: float | None = None
    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        result.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
            if previous_close is not None
            else high - low
        )
        previous_close = close
    return result


def output_with_values(
    candles: list[dict[str, Any]], key: str, indicator_values: list[float | None]
) -> list[dict[str, Any]]:
    return [
        {**candle, key: value}
        for candle, value in zip(candles, indicator_values, strict=True)
    ]
