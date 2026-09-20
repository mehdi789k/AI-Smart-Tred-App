"""Deterministic confidence scoring for composite trading signals."""

from __future__ import annotations

from typing import Any, Mapping


def score_signal(
    ml_prediction: Any,
    indicators: Mapping[str, Any] | None = None,
    *,
    timeframe_confirmed: bool = True,
) -> float:
    """Return a 0-100 score from ML confidence and indicator agreement."""
    if isinstance(ml_prediction, Mapping):
        confidence = ml_prediction.get(
            "confidence", ml_prediction.get("probability", 0)
        )
        direction = str(
            ml_prediction.get("direction", ml_prediction.get("action", ""))
        ).lower()
    else:
        confidence, direction = ml_prediction, ""
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence if confidence <= 1 else confidence / 100))
    values = list((indicators or {}).values())
    agreement = sum(
        1
        for value in values
        if value is True
        or (
            isinstance(value, str)
            and value.lower()
            in {
                direction,
                "pass",
                "passed",
                "bullish" if direction == "buy" else "bearish",
            }
        )
    )
    indicator_bonus = (agreement / len(values) * 20) if values else 0
    return round(
        max(
            0.0,
            min(
                100.0,
                confidence * 80 + indicator_bonus + (10 if timeframe_confirmed else 0),
            ),
        ),
        2,
    )
