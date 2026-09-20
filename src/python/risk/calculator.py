"""Pure risk/reward calculations and minimum-RR filtering."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_MINIMUM_RR = 2.0
DEFAULT_RISK_PER_TRADE = 0.01


def _price(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _minimum_rr(value: Any) -> float:
    result = _price(value, "minimum_rr")
    if result <= 0:
        raise ValueError("minimum_rr must be greater than zero")
    return result


def calculate_risk_reward_ratio(
    entry_price: Any,
    stop_loss: Any,
    take_profit: Any,
    direction: str = "buy",
) -> float:
    """Return reward/risk for a valid buy or sell setup."""
    entry = _price(entry_price, "entry_price")
    stop = _price(stop_loss, "stop_loss")
    target = _price(take_profit, "take_profit")
    if not isinstance(direction, str) or direction.lower() not in {"buy", "sell"}:
        raise ValueError("direction must be either 'buy' or 'sell'")
    side = direction.lower()
    risk = entry - stop if side == "buy" else stop - entry
    reward = target - entry if side == "buy" else entry - target
    if risk <= 0:
        raise ValueError("stop_loss must define a positive risk distance for direction")
    if reward <= 0:
        raise ValueError(
            "take_profit must define a positive reward distance for direction"
        )
    return reward / risk


def meets_minimum_risk_reward(
    entry_price: Any,
    stop_loss: Any,
    take_profit: Any,
    direction: str = "buy",
    minimum_rr: Any = DEFAULT_MINIMUM_RR,
) -> bool:
    """Whether a setup meets the configurable minimum risk/reward ratio."""
    return calculate_risk_reward_ratio(
        entry_price, stop_loss, take_profit, direction
    ) >= _minimum_rr(minimum_rr)


def filter_minimum_risk_reward(
    setups: Sequence[Mapping[str, Any]],
    minimum_rr: Any = DEFAULT_MINIMUM_RR,
) -> list[Mapping[str, Any]]:
    """Keep setups with RR >= minimum_rr without mutating the input."""
    threshold = _minimum_rr(minimum_rr)
    accepted: list[Mapping[str, Any]] = []
    for index, setup in enumerate(setups):
        if not isinstance(setup, Mapping):
            raise ValueError(f"setup at index {index} must be a mapping")
        try:
            allowed = meets_minimum_risk_reward(
                setup["entry_price"],
                setup["stop_loss"],
                setup["take_profit"],
                setup.get("direction", "buy"),
                threshold,
            )
        except KeyError as error:
            raise ValueError(
                f"setup at index {index} is missing {error.args[0]!r}"
            ) from error
        if allowed:
            accepted.append(setup)
    return accepted


def normalize_protection_prices(
    direction: str,
    *,
    bid: Any,
    ask: Any,
    stop_loss: Any = 0.0,
    take_profit: Any = 0.0,
    minimum_distance: Any = 0.0,
    digits: int | None = None,
    auto_correct: bool = False,
) -> tuple[float, float]:
    """Validate (and optionally safely move) SL/TP against the executable quote.

    A market BUY is filled at ask and protected by a SL below bid/TP above ask.
    A market SELL is filled at bid and protected by a SL above ask/TP below bid.
    Validating against ``entry`` alone is unsafe when the spread is non-zero and
    was the source of the ``SELL stop loss must be above ask price`` failures.
    """
    if not isinstance(direction, str) or direction.upper() not in {"BUY", "SELL"}:
        raise ValueError("direction must be either BUY or SELL")
    side = direction.upper()
    bid_value, ask_value = _price(bid, "bid"), _price(ask, "ask")
    if bid_value <= 0 or ask_value <= 0 or ask_value < bid_value:
        raise ValueError("bid and ask must be positive and ask must be >= bid")
    sl = _price(stop_loss, "stop_loss") if stop_loss not in (None, "") else 0.0
    tp = _price(take_profit, "take_profit") if take_profit not in (None, "") else 0.0
    if sl < 0 or tp < 0:
        raise ValueError("stop_loss and take_profit must be zero or positive")
    distance = _price(minimum_distance, "minimum_distance")
    if distance < 0:
        raise ValueError("minimum_distance must be zero or positive")
    if side == "BUY":
        sl_limit, tp_limit = bid_value - distance, ask_value + distance
        valid = (not sl or sl < sl_limit) and (not tp or tp > tp_limit)
        if auto_correct:
            if sl:
                sl = min(sl, sl_limit)
                if sl >= bid_value:
                    sl = math.nextafter(bid_value, -math.inf)
            if tp:
                tp = max(tp, tp_limit)
                if tp <= ask_value:
                    tp = math.nextafter(ask_value, math.inf)
    else:
        sl_limit, tp_limit = ask_value + distance, bid_value - distance
        valid = (not sl or sl > sl_limit) and (not tp or tp < tp_limit)
        if auto_correct:
            if sl:
                sl = max(sl, sl_limit)
                if sl <= ask_value:
                    sl = math.nextafter(ask_value, math.inf)
            if tp:
                tp = min(tp, tp_limit)
                if tp >= bid_value:
                    tp = math.nextafter(bid_value, -math.inf)
    if not valid and not auto_correct:
        raise ValueError(f"{side} stop loss/take profit is invalid for bid/ask quote")
    if digits is not None:
        if not isinstance(digits, int) or digits < 0:
            raise ValueError("digits must be a non-negative integer")
        # Direction-aware rounding must not move a level back through its limit.
        sl = round(sl, digits) if sl else 0.0
        tp = round(tp, digits) if tp else 0.0
        if side == "SELL" and sl and sl <= ask_value:
            sl = round(ask_value + max(distance, 10**-digits), digits)
        if side == "BUY" and sl and sl >= bid_value:
            sl = round(bid_value - max(distance, 10**-digits), digits)
    return sl, tp


def calculate_position_size(
    account_balance: Any,
    entry_price: Any,
    stop_loss: Any,
    *,
    risk_percent: Any = DEFAULT_RISK_PER_TRADE * 100,
    value_per_unit: Any = 1.0,
    max_position_size: Any | None = None,
    direction: str = "BUY",
) -> float:
    """Fixed-fractional position size, capped by ``max_position_size``."""
    balance = _price(account_balance, "account_balance")
    risk_pct = _price(risk_percent, "risk_percent")
    unit_value = _price(value_per_unit, "value_per_unit")
    entry, stop = _price(entry_price, "entry_price"), _price(stop_loss, "stop_loss")
    side = direction.upper() if isinstance(direction, str) else ""
    distance = entry - stop if side == "BUY" else stop - entry if side == "SELL" else -1
    if distance <= 0:
        raise ValueError("stop_loss must define a positive risk distance for direction")
    size = balance * risk_pct / 100.0 / (distance * unit_value)
    if max_position_size is not None:
        cap = _price(max_position_size, "max_position_size")
        if cap <= 0:
            raise ValueError("max_position_size must be greater than zero")
        size = min(size, cap)
    return size


calculate_rr = calculate_risk_reward_ratio
filter_by_minimum_rr = filter_minimum_risk_reward
