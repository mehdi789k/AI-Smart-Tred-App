"""Composite ML/indicator signal generation.

This module is deliberately pure: it creates signal payloads and never calls
MT5 or an order executor.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from risk.calculator import normalize_protection_prices

from .scorer import score_signal


class SignalGenerator:
    def __init__(
        self, min_score: float = 70.0, *, auto_correct_protection: bool = False
    ):
        if not math.isfinite(float(min_score)) or not 0 <= float(min_score) <= 100:
            raise ValueError("min_score must be between 0 and 100")
        self.min_score = float(min_score)
        self.auto_correct_protection = auto_correct_protection

    def generate(
        self,
        *,
        symbol: str,
        ml_prediction: Mapping[str, Any],
        indicators: Mapping[str, Any] | None = None,
        quote: Mapping[str, Any],
        stop_loss: Any,
        take_profit: Any,
        timeframe_confirmed: bool = True,
        reason: str = "",
        digits: int | None = None,
        minimum_distance: float = 0.0,
    ) -> dict[str, Any] | None:
        action = str(
            ml_prediction.get("direction", ml_prediction.get("action", ""))
        ).upper()
        if action not in {"BUY", "SELL"}:
            return None
        score = score_signal(
            ml_prediction, indicators, timeframe_confirmed=timeframe_confirmed
        )
        if score < self.min_score or not timeframe_confirmed:
            return None
        bid, ask = quote.get("bid"), quote.get("ask")
        sl, tp = normalize_protection_prices(
            action,
            bid=bid,
            ask=ask,
            stop_loss=stop_loss,
            take_profit=take_profit,
            minimum_distance=minimum_distance,
            digits=digits,
            auto_correct=self.auto_correct_protection,
        )
        entry = ask if action == "BUY" else bid
        return {
            "symbol": symbol,
            "action": action.lower(),
            "direction": action,
            "entry": entry,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "risk_score": score,
            "reason": reason,
            "timeframe_confirmed": True,
        }


def generate_signal(**kwargs: Any) -> dict[str, Any] | None:
    """Convenience wrapper for the default 70-point generator."""
    return SignalGenerator().generate(**kwargs)
