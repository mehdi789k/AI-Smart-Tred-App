"""Strategy contracts used by the event-driven backtest engine.

The backtester deliberately keeps this interface small.  A strategy may return
an :class:`OrderIntent` from ``on_bar`` (or a collection of intents), while
legacy strategies that expose ``generate_signal`` are adapted by the engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class OrderIntent:
    """An order request emitted by a strategy.

    ``side`` is BUY/SELL and ``action`` is OPEN, CLOSE, or HOLD.  Quantity is
    expressed in instrument units.  A zero quantity is a valid HOLD intent but
    an executable order must have a positive quantity.
    """

    side: str = "BUY"
    quantity: float = 0.0
    symbol: str | None = None
    timeframe: str | None = None
    action: str = "OPEN"
    order_type: str = "MARKET"
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reduce_only: bool = False
    fill_ratio: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        side = str(self.side).upper()
        action = str(self.action).upper()
        order_type = str(self.order_type).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("order side must be BUY or SELL")
        if action not in {"OPEN", "CLOSE", "HOLD"}:
            raise ValueError("order action must be OPEN, CLOSE, or HOLD")
        if order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("order_type must be MARKET or LIMIT")
        try:
            quantity = float(self.quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError("order quantity must be numeric") from exc
        if not math.isfinite(quantity) or quantity < 0:
            raise ValueError("order quantity must be finite and non-negative")
        if self.fill_ratio is not None:
            try:
                fill_ratio = float(self.fill_ratio)
            except (TypeError, ValueError) as exc:
                raise ValueError("fill_ratio must be numeric") from exc
            if not math.isfinite(fill_ratio) or not 0 < fill_ratio <= 1:
                raise ValueError("fill_ratio must be between 0 and 1")
            object.__setattr__(self, "fill_ratio", fill_ratio)
        for name in ("price", "stop_loss", "take_profit"):
            value = getattr(self, name)
            if value is not None:
                try:
                    number = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{name} must be numeric") from exc
                if not math.isfinite(number) or number <= 0:
                    raise ValueError(f"{name} must be finite and positive")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "quantity", quantity)


class Strategy:
    """Base strategy with event-driven and optional training hooks."""

    def on_bar(
        self,
        bar: Any,
        indicators: Mapping[str, Any] | None = None,
        ml_prediction: Any = None,
    ) -> OrderIntent | Sequence[OrderIntent] | None:
        """Return order intent(s) for a completed bar."""

        return None

    def on_tick(self, tick: Any) -> OrderIntent | Sequence[OrderIntent] | None:
        """Optional tick callback.  The default implementation is a no-op."""

        return None

    def train(self, data: Any, **kwargs: Any) -> Any:
        """Optional walk-forward training hook.

        Returning ``self`` is convenient for in-place strategies; returning a
        new strategy is also supported by :meth:`BacktestEngine.walk_forward`.
        """

        return self

    def reset(self) -> None:
        """Reset state between independent backtests."""

    # Compatibility hook for the repository's existing strategy classes.
    def generate_signal(
        self, data: Mapping[str, Any], current_position: Any = None
    ) -> Any:
        return None


def normalize_intents(value: Any) -> list[OrderIntent]:
    """Convert common strategy return values into validated order intents."""

    if value is None:
        return []
    if isinstance(value, OrderIntent):
        return [value]
    if isinstance(value, (str, bytes)):
        text = value.decode() if isinstance(value, bytes) else value
        if text.upper() in {"BUY", "SELL"}:
            return [OrderIntent(side=text, quantity=1)]
        return []
    if isinstance(value, Mapping):
        return [OrderIntent(**dict(value))]
    if isinstance(value, Sequence):
        result: list[OrderIntent] = []
        for item in value:
            result.extend(normalize_intents(item))
        return result
    return []
