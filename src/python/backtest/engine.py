"""Event-driven, cost-aware backtesting and walk-forward evaluation.

The backtest package is intentionally independent from the persistence layer.
It consumes the OHLCV contract (``symbol/timeframe/timestamp/open/high/low/
close/volume``) and returns immutable execution records that can be serialised
by :mod:`backtest.report`.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .metrics import PerformanceMetrics, calculate_metrics
from .strategy import OrderIntent, Strategy, normalize_intents

logger = logging.getLogger(__name__)

BarKey = tuple[str, str]
Window = int | timedelta


class BacktestError(RuntimeError):
    """Raised when a simulation cannot safely produce a trustworthy result."""


def _timestamp(value: Any) -> datetime:
    """Normalise supported timestamp values to timezone-aware UTC."""

    if isinstance(value, datetime):
        result = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    if hasattr(value, "to_pydatetime"):
        return _timestamp(value.to_pydatetime())
    if isinstance(value, (int, float, np.integer, np.floating)):
        if not np.isfinite(float(value)):
            raise ValueError("timestamp must be finite")
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid timestamp: {value!r}") from exc
        return _timestamp(parsed)
    raise ValueError(f"unsupported timestamp: {value!r}")


def _finite_non_negative(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not np.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


@dataclass(frozen=True)
class Bar:
    """Validated OHLCV observation used by the simulator."""

    timestamp: datetime
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread: float | None = None
    indicators: Mapping[str, Any] = field(default_factory=dict)
    ml_prediction: Any = None

    def __post_init__(self) -> None:
        timestamp = _timestamp(self.timestamp)
        symbol = str(self.symbol).strip()
        timeframe = str(self.timeframe).strip()
        if not symbol or not timeframe:
            raise ValueError("bar requires non-empty symbol and timeframe")
        prices = {}
        for name in ("open", "high", "low", "close"):
            try:
                price = float(getattr(self, name))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"bar {name} must be numeric") from exc
            if not np.isfinite(price) or price <= 0:
                raise ValueError(f"bar {name} must be finite and positive")
            prices[name] = price
        if prices["high"] < max(prices["open"], prices["close"]):
            raise ValueError("bar high must be >= open and close")
        if (
            prices["low"] > min(prices["open"], prices["close"])
            or prices["low"] > prices["high"]
        ):
            raise ValueError("bar low/high values are inconsistent")
        volume = _finite_non_negative(self.volume, "bar volume")
        spread = (
            None
            if self.spread is None
            else _finite_non_negative(self.spread, "bar spread")
        )
        if not isinstance(self.indicators, Mapping):
            raise ValueError("bar indicators must be a mapping")
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timeframe", timeframe)
        for name, value in prices.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "volume", volume)
        object.__setattr__(self, "spread", spread)

    @classmethod
    def from_value(
        cls,
        value: Any,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> "Bar":
        if isinstance(value, cls):
            return value
        if hasattr(value, "to_dict") and not isinstance(value, Mapping):
            value = value.to_dict()
        if not isinstance(value, Mapping):
            raise TypeError("bar must be a Bar, mapping, or dataframe row")
        row = dict(value)
        ts = row.get("timestamp", row.get("time", row.get("datetime", row.get("date"))))
        if ts is None:
            raise ValueError("bar requires timestamp/time")
        resolved_symbol = str(row.get("symbol", symbol or "UNKNOWN"))
        resolved_tf = str(row.get("timeframe", timeframe or ""))
        prices = {}
        try:
            prices = {key: float(row[key]) for key in ("open", "high", "low", "close")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("bar requires numeric open/high/low/close") from exc
        volume = row.get("volume", row.get("tick_volume", 0.0))
        return cls(
            _timestamp(ts),
            resolved_symbol,
            resolved_tf,
            **prices,
            volume=volume or 0.0,
            spread=row.get("spread"),
            indicators=row.get("indicators", {}) or {},
            ml_prediction=row.get("ml_prediction"),
        )


@dataclass(frozen=True)
class Fill:
    timestamp: datetime
    symbol: str
    timeframe: str
    side: str
    quantity: float
    price: float
    commission: float
    order_id: str
    partial: bool = False


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    stop_loss: float | None = None
    take_profit: float | None = None
    pnl: float = 0.0
    pnl_percent: float = 0.0
    exit_reason: str = "SIGNAL"
    commission: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    timeframe: str
    direction: str
    quantity: float
    entry_price: float
    entry_time: datetime
    stop_loss: float | None = None
    take_profit: float | None = None
    entry_commission: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestConfig:
    """Execution and reporting assumptions.

    ``commission`` is a fraction of notional (10 bps is ``0.001``).
    ``spread`` and non-rate ``slippage`` are absolute price units; rate values
    are enabled by their corresponding ``*_is_rate`` flag.
    """

    initial_balance: float = 10_000.0
    commission: float = 0.0001
    spread: float = 0.0
    slippage: float = 0.0
    spread_is_rate: bool = False
    slippage_is_rate: bool = True
    risk_per_trade: float = 0.01
    max_positions: int = 1
    max_fill_ratio: float = 1.0
    warmup_bars: int = 0
    periods_per_year: float = 252.0

    def __post_init__(self) -> None:
        for name in (
            "initial_balance",
            "commission",
            "spread",
            "slippage",
            "risk_per_trade",
        ):
            value = _finite_non_negative(getattr(self, name), name)
            if name == "initial_balance" and value <= 0:
                raise ValueError("initial_balance must be positive")
        if not isinstance(self.max_positions, int) or self.max_positions < 1:
            raise ValueError("max_positions must be a positive integer")
        if not isinstance(self.warmup_bars, int) or self.warmup_bars < 0:
            raise ValueError("warmup_bars must be a non-negative integer")
        if (
            not np.isfinite(float(self.max_fill_ratio))
            or not 0 < self.max_fill_ratio <= 1
        ):
            raise ValueError("max_fill_ratio must be between 0 and 1")
        if not np.isfinite(float(self.periods_per_year)) or self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    start_balance: float = 0.0
    end_balance: float = 0.0
    metrics: PerformanceMetrics | None = None
    positions: list[Position] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def total_pnl(self) -> float:
        return self.end_balance - self.start_balance

    @property
    def total_pnl_percent(self) -> float:
        return self.total_pnl / self.start_balance * 100 if self.start_balance else 0.0

    @property
    def win_rate(self) -> float:
        return self.metrics.win_rate * 100 if self.metrics else 0.0

    @property
    def max_drawdown(self) -> float:
        return self.metrics.max_drawdown if self.metrics else 0.0

    @property
    def max_drawdown_percent(self) -> float:
        return self.metrics.max_drawdown_percent * 100 if self.metrics else 0.0

    def to_dict(self) -> dict[str, Any]:
        metrics = self.metrics.to_dict() if self.metrics else {}
        data = {
            "total_trades": self.total_trades,
            "winning_trades": sum(trade.pnl > 0 for trade in self.trades),
            "losing_trades": sum(trade.pnl <= 0 for trade in self.trades),
            "total_pnl": self.total_pnl,
            "total_pnl_percent": self.total_pnl_percent,
            "start_balance": self.start_balance,
            "end_balance": self.end_balance,
            # Legacy dashboard aliases.
            "final_balance": self.end_balance,
            "total_return": metrics.get(
                "total_return",
                self.total_pnl / self.start_balance if self.start_balance else 0.0,
            ),
            "win_rate": self.win_rate,
            "max_drawdown": self.max_drawdown,
        }
        data.update(metrics)
        return data


@dataclass
class WalkForwardResult:
    folds: list[BacktestResult] = field(default_factory=list)
    train_windows: list[tuple[datetime, datetime]] = field(default_factory=list)
    test_windows: list[tuple[datetime, datetime]] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    periods_per_year: float = 252.0

    @property
    def out_of_sample(self) -> BacktestResult:
        """Compound independent OOS folds without resetting aggregate equity."""

        if not self.folds:
            return BacktestResult()
        trades = [trade for fold in self.folds for trade in fold.trades]
        fills = [fill for fold in self.folds for fill in fold.fills]
        start = self.folds[0].start_balance
        balance = start
        equity = [start]
        for fold in self.folds:
            if fold.start_balance <= 0:
                continue
            for point in fold.equity_curve[1:]:
                equity.append(balance * float(point) / fold.start_balance)
            balance *= fold.end_balance / fold.start_balance
        result = BacktestResult(
            trades=trades,
            fills=fills,
            equity_curve=equity,
            start_balance=start,
            end_balance=balance,
        )
        result.metrics = calculate_metrics(
            equity, trades, start, periods_per_year=self.periods_per_year
        )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "folds": [fold.to_dict() for fold in self.folds],
            "out_of_sample": self.out_of_sample.to_dict(),
            "train_windows": self.train_windows,
            "test_windows": self.test_windows,
            "parameters": self.parameters,
        }


class BacktestEngine:
    """Deterministic bar event loop with realistic execution costs."""

    def __init__(self, config: BacktestConfig | None = None, **kwargs: Any) -> None:
        self.config = config or BacktestConfig(**kwargs)

    @property
    def initial_balance(self) -> float:
        """Compatibility property used by the legacy dashboard."""

        return self.config.initial_balance

    def _bars(self, data: Any) -> list[Bar]:
        if data is None:
            return []
        if hasattr(data, "to_dict") and not isinstance(data, Mapping):
            rows = data.to_dict("records")
            bars = [
                Bar.from_value(
                    row,
                    str(row.get("symbol", "UNKNOWN")),
                    str(row.get("timeframe", "H1")),
                )
                for row in rows
            ]
        elif isinstance(data, Mapping):
            bars = []
            for key, values in data.items():
                symbol, timeframe = key if isinstance(key, tuple) else (key, "H1")
                if isinstance(values, Mapping) and "candles" in values:
                    timeframe = values.get("timeframe", timeframe)
                    symbol = values.get("symbol", symbol)
                    values = values["candles"]
                if isinstance(values, Mapping):
                    values = [values]
                try:
                    bars.extend(
                        Bar.from_value(row, str(symbol), str(timeframe))
                        for row in values
                    )
                except TypeError as exc:
                    raise ValueError(
                        "mapping values must be bar mappings or sequences"
                    ) from exc
        else:
            try:
                bars = [Bar.from_value(row) for row in data]
            except TypeError as exc:
                raise ValueError(
                    "data must be a dataframe, mapping, or iterable of bars"
                ) from exc
        bars.sort(key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
        previous: dict[BarKey, datetime] = {}
        for bar in bars:
            key = (bar.symbol, bar.timeframe)
            if key in previous and bar.timestamp <= previous[key]:
                raise ValueError(f"duplicate or non-chronological bar for {key}")
            previous[key] = bar.timestamp
        return bars

    def _cost(self, price: float, bar: Bar, side: str) -> float:
        if not np.isfinite(price) or price <= 0:
            raise ValueError("execution price must be finite and positive")
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("execution side must be BUY or SELL")
        spread = bar.spread if bar.spread is not None else self.config.spread
        spread_value = price * spread if self.config.spread_is_rate else spread
        slippage = (
            price * self.config.slippage
            if self.config.slippage_is_rate
            else self.config.slippage
        )
        return price + (spread_value / 2 + slippage) * (1 if side == "BUY" else -1)

    def _commission(self, price: float, quantity: float) -> float:
        return abs(price * quantity) * self.config.commission

    def _strategy_intents(
        self,
        strategy: Any,
        bar: Bar,
        history: list[Bar],
        position: Position | None,
    ) -> list[OrderIntent]:
        callback = getattr(strategy, "on_bar", None)
        if callable(callback):
            try:
                intents = normalize_intents(
                    callback(bar, bar.indicators, bar.ml_prediction)
                )
            except Exception as exc:
                logger.exception("Strategy on_bar failed at %s", bar.timestamp)
                raise BacktestError("strategy on_bar failed") from exc
            if intents:
                return intents
        legacy = getattr(strategy, "generate_signal", None)
        if not callable(legacy):
            return []
        try:
            signal = legacy(
                {
                    "symbol": bar.symbol,
                    "timeframe": bar.timeframe,
                    "candles": [asdict(item) for item in history],
                },
                asdict(position) if position else None,
            )
            if isinstance(signal, Mapping):
                direction = str(signal.get("direction", "HOLD")).upper()
                strength = float(signal.get("strength", 1.0))
                entry_price = signal.get("entry_price")
                stop_loss = signal.get("stop_loss")
                take_profit = signal.get("take_profit")
            else:
                direction = str(getattr(signal, "direction", "HOLD")).upper()
                strength = float(getattr(signal, "strength", 1.0))
                entry_price = getattr(signal, "entry_price", None)
                stop_loss = getattr(signal, "stop_loss", None)
                take_profit = getattr(signal, "take_profit", None)
        except Exception as exc:
            logger.exception("Legacy strategy failed at %s", bar.timestamp)
            raise BacktestError("legacy strategy failed") from exc
        if (
            direction not in {"BUY", "SELL"}
            or not np.isfinite(strength)
            or strength <= 0
        ):
            return []
        return [
            OrderIntent(
                side=direction,
                quantity=1.0,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={"strength": strength},
            )
        ]

    def run(
        self, strategy: Any, data: Any = None, **kwargs: Any
    ) -> BacktestResult | dict[str, Any]:
        """Run one chronological simulation.

        ``run(dataframe, strategy)`` remains supported for the dashboard and
        returns its historical summary mapping.  New callers should use
        ``run(strategy, data)`` to receive :class:`BacktestResult`.
        """

        reset_strategy = bool(kwargs.pop("reset_strategy", True))
        if kwargs:
            raise TypeError(f"unsupported run options: {', '.join(sorted(kwargs))}")
        dataframe_api = (
            data is not None
            and hasattr(strategy, "to_dict")
            and not hasattr(strategy, "on_bar")
            and not hasattr(strategy, "generate_signal")
        )
        if dataframe_api:
            strategy, data = data, strategy
        bars = self._bars(data)
        if not bars:
            result = BacktestResult(
                start_balance=self.config.initial_balance,
                end_balance=self.config.initial_balance,
                equity_curve=[self.config.initial_balance],
            )
            result.metrics = calculate_metrics(
                result.equity_curve,
                [],
                result.start_balance,
                self.config.periods_per_year,
            )
            return result.to_dict() if dataframe_api else result

        reset = getattr(strategy, "reset", None)
        if reset_strategy and callable(reset):
            reset()
        balance = self.config.initial_balance
        positions: dict[BarKey, Position] = {}
        trades: list[Trade] = []
        fills: list[Fill] = []
        history: defaultdict[BarKey, list[Bar]] = defaultdict(list)
        equity = [balance]
        order_ids = count()

        for bar in bars:
            key = (bar.symbol, bar.timeframe)
            history[key].append(bar)
            position = positions.get(key)
            if position:
                exit_price, reason = self._trigger_exit(position, bar)
                if exit_price is not None:
                    trade, balance = self._close(
                        position,
                        bar,
                        exit_price,
                        reason,
                        balance,
                        fills,
                        str(next(order_ids)),
                    )
                    trades.append(trade)
                    positions.pop(key, None)
                    position = None

            # Warmup is per symbol/timeframe stream, not the global merged
            # event index, so a second symbol is not accidentally skipped.
            if len(history[key]) - 1 >= self.config.warmup_bars:
                intents = self._strategy_intents(strategy, bar, history[key], position)
                available_volume = bar.volume
                for intent in intents:
                    if intent.action == "HOLD" or (
                        intent.action != "CLOSE" and intent.quantity <= 0
                    ):
                        continue
                    if (intent.symbol and intent.symbol != bar.symbol) or (
                        intent.timeframe and intent.timeframe != bar.timeframe
                    ):
                        continue
                    position = positions.get(key)
                    raw_price = intent.price if intent.price is not None else bar.close
                    if intent.order_type == "LIMIT" and not self._limit_touched(
                        intent, bar
                    ):
                        continue
                    if intent.action == "CLOSE":
                        if position is not None:
                            trade, balance = self._close(
                                position,
                                bar,
                                raw_price,
                                "SIGNAL",
                                balance,
                                fills,
                                str(next(order_ids)),
                            )
                            trades.append(trade)
                            positions.pop(key, None)
                            position = None
                        continue
                    if position is not None:
                        if position.direction != intent.side:
                            trade, balance = self._close(
                                position,
                                bar,
                                raw_price,
                                "REVERSAL",
                                balance,
                                fills,
                                str(next(order_ids)),
                            )
                            trades.append(trade)
                            positions.pop(key, None)
                            position = None
                        else:
                            logger.debug(
                                "Ignoring duplicate same-direction order for %s", key
                            )
                            continue
                    if (
                        intent.action != "OPEN"
                        or len(positions) >= self.config.max_positions
                    ):
                        continue
                    self._validate_protection_levels(intent, raw_price)
                    requested = intent.quantity * (
                        intent.fill_ratio
                        if intent.fill_ratio is not None
                        else self.config.max_fill_ratio
                    )
                    quantity = requested
                    if available_volume > 0:
                        quantity = min(quantity, available_volume)
                        available_volume -= quantity
                    if quantity <= 0:
                        continue
                    price = self._cost(raw_price, bar, intent.side)
                    commission = self._commission(price, quantity)
                    balance -= commission
                    partial = quantity + 1e-12 < intent.quantity
                    fills.append(
                        Fill(
                            bar.timestamp,
                            bar.symbol,
                            bar.timeframe,
                            intent.side,
                            quantity,
                            price,
                            commission,
                            str(next(order_ids)),
                            partial=partial,
                        )
                    )
                    positions[key] = Position(
                        bar.symbol,
                        bar.timeframe,
                        intent.side,
                        quantity,
                        price,
                        bar.timestamp,
                        intent.stop_loss,
                        intent.take_profit,
                        commission,
                        dict(intent.metadata),
                    )

            mark = balance
            for open_position in positions.values():
                mark_price = self._cost(
                    bar.close,
                    bar,
                    "SELL" if open_position.direction == "BUY" else "BUY",
                )
                mark += (
                    (mark_price - open_position.entry_price) * open_position.quantity
                    if open_position.direction == "BUY"
                    else (open_position.entry_price - mark_price)
                    * open_position.quantity
                )
            equity.append(float(mark))

        # Liquidate all positions at the last bar of their own series.
        for key, position in list(positions.items()):
            bar = next(
                item for item in reversed(bars) if (item.symbol, item.timeframe) == key
            )
            trade, balance = self._close(
                position, bar, bar.close, "END", balance, fills, str(next(order_ids))
            )
            trades.append(trade)
            positions.pop(key, None)
        if not equity or abs(equity[-1] - balance) > 1e-12:
            equity.append(float(balance))
        result = BacktestResult(
            trades=trades,
            fills=fills,
            equity_curve=equity,
            start_balance=self.config.initial_balance,
            end_balance=float(balance),
            positions=[],
        )
        result.metrics = calculate_metrics(
            result.equity_curve,
            trades,
            result.start_balance,
            self.config.periods_per_year,
        )
        logger.info(
            "Backtest complete: bars=%d trades=%d pnl=%.6f",
            len(bars),
            len(trades),
            result.total_pnl,
        )
        return result.to_dict() if dataframe_api else result

    @staticmethod
    def _limit_touched(intent: OrderIntent, bar: Bar) -> bool:
        if intent.price is None:
            raise ValueError("LIMIT order requires price")
        return (
            bar.low <= intent.price
            if intent.side == "BUY"
            else bar.high >= intent.price
        )

    @staticmethod
    def _validate_protection_levels(intent: OrderIntent, entry_price: float) -> None:
        if intent.stop_loss is not None:
            valid_stop = (
                intent.stop_loss < entry_price
                if intent.side == "BUY"
                else intent.stop_loss > entry_price
            )
            if not valid_stop:
                raise ValueError(
                    "stop_loss must be below a long entry or above a short entry"
                )
        if intent.take_profit is not None:
            valid_target = (
                intent.take_profit > entry_price
                if intent.side == "BUY"
                else intent.take_profit < entry_price
            )
            if not valid_target:
                raise ValueError(
                    "take_profit must be above a long entry or below a short entry"
                )

    @staticmethod
    def _trigger_exit(position: Position, bar: Bar) -> tuple[float | None, str]:
        # If both levels are touched in one OHLC bar, stop wins conservatively.
        if position.direction == "BUY":
            if position.stop_loss is not None and bar.low <= position.stop_loss:
                return position.stop_loss, "SL"
            if position.take_profit is not None and bar.high >= position.take_profit:
                return position.take_profit, "TP"
        else:
            if position.stop_loss is not None and bar.high >= position.stop_loss:
                return position.stop_loss, "SL"
            if position.take_profit is not None and bar.low <= position.take_profit:
                return position.take_profit, "TP"
        return None, ""

    def _close(
        self,
        position: Position,
        bar: Bar,
        raw_price: float,
        reason: str,
        balance: float,
        fills: list[Fill],
        order_id: str,
    ) -> tuple[Trade, float]:
        price = self._cost(
            raw_price,
            bar,
            "SELL" if position.direction == "BUY" else "BUY",
        )
        commission = self._commission(price, position.quantity)
        gross = (
            (price - position.entry_price) * position.quantity
            if position.direction == "BUY"
            else (position.entry_price - price) * position.quantity
        )
        pnl = gross - position.entry_commission - commission
        balance += gross - commission
        fills.append(
            Fill(
                bar.timestamp,
                bar.symbol,
                bar.timeframe,
                "SELL" if position.direction == "BUY" else "BUY",
                position.quantity,
                price,
                commission,
                order_id,
            )
        )
        pct = (
            pnl / abs(position.entry_price * position.quantity)
            if position.entry_price
            else 0.0
        )
        return (
            Trade(
                position.entry_time,
                bar.timestamp,
                position.symbol,
                position.direction,
                position.entry_price,
                price,
                position.quantity,
                position.stop_loss,
                position.take_profit,
                pnl,
                pct,
                reason,
                position.entry_commission + commission,
                position.metadata,
            ),
            balance,
        )

    def walk_forward(
        self,
        strategy_factory: Callable[[dict[str, Any]], Any] | Callable[[], Any] | Any,
        data: Any,
        train_window: Window,
        test_window: Window,
        step: Window | None = None,
        optimizer: Any = None,
    ) -> WalkForwardResult:
        """Fit on each in-sample window and evaluate only its following OOS window.

        Windows are bar-count based by default.  ``timedelta`` windows are also
        accepted and are evaluated using UTC timestamps.  Training data is
        never passed to the OOS run, preventing look-ahead leakage.
        """

        bars = self._bars(data)
        if not bars:
            raise ValueError("walk-forward data must not be empty")
        train_size = self._window_size(train_window)
        test_size = self._window_size(test_window)
        step_size = test_size if step is None else self._window_size(step)
        if train_size < 1 or test_size < 1 or step_size < 1:
            raise ValueError("walk-forward windows and step must be positive")
        if step_size < test_size:
            logger.warning(
                "walk-forward OOS windows overlap; aggregate metrics double-count bars"
            )

        folds: list[BacktestResult] = []
        train_windows: list[tuple[datetime, datetime]] = []
        test_windows: list[tuple[datetime, datetime]] = []
        parameters: list[dict[str, Any]] = []
        start = 0
        while start < len(bars):
            train_end = self._advance_index(bars, start, train_window)
            test_end = self._advance_index(bars, train_end, test_window)
            if train_end <= start or test_end <= train_end or test_end > len(bars):
                break
            train = bars[start:train_end]
            test = bars[train_end:test_end]
            selected: dict[str, Any] = {}
            factory = strategy_factory
            if optimizer is not None:
                selected = dict(optimizer.optimize(factory, train, self))
                factory = optimizer.build_strategy(factory, selected)
            elif callable(factory) and not isinstance(factory, Strategy):
                factory = self._build_strategy(factory, selected)
            else:
                reset = getattr(factory, "reset", None)
                if callable(reset):
                    reset()
            trainer = getattr(factory, "train", None)
            if callable(trainer):
                try:
                    trained = trainer(train)
                except Exception as exc:
                    logger.exception(
                        "walk-forward training failed for %s", train_windows
                    )
                    raise BacktestError("walk-forward training failed") from exc
                if trained is not None:
                    factory = trained
            # Training has just happened; resetting here could discard model
            # state learned from the in-sample window.
            fold = self.run(factory, test, reset_strategy=False)
            if not isinstance(fold, BacktestResult):
                raise BacktestError("walk-forward run must return a BacktestResult")
            folds.append(fold)
            parameters.append(selected)
            train_windows.append((train[0].timestamp, train[-1].timestamp))
            test_windows.append((test[0].timestamp, test[-1].timestamp))
            next_start = self._advance_index(
                bars, start, step if step is not None else test_window
            )
            if next_start <= start:
                break
            start = next_start
        if not folds:
            raise ValueError("data is shorter than train_window + test_window")
        return WalkForwardResult(
            folds,
            train_windows,
            test_windows,
            parameters,
            self.config.periods_per_year,
        )

    @staticmethod
    def _build_strategy(
        factory: Callable[..., Any], parameters: Mapping[str, Any]
    ) -> Any:
        parameters = dict(parameters)
        try:
            signature = inspect.signature(factory)
            positional = [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if positional and positional[0].name.lower() in {
                "params",
                "parameters",
                "config",
            }:
                return factory(parameters)
            if (
                not parameters
                and positional
                and positional[0].default is not inspect.Parameter.empty
            ):
                return factory()
            if not positional and not parameters:
                return factory()
            if positional and parameters:
                return factory(**parameters)
        except (TypeError, ValueError):
            pass
        try:
            return factory(parameters)
        except TypeError:
            return factory(**dict(parameters))

    @staticmethod
    def _window_size(window: Window) -> int:
        if isinstance(window, bool):
            raise TypeError("window must be an integer or timedelta")
        if isinstance(window, timedelta):
            return 1 if window > timedelta(0) else 0
        if isinstance(window, int):
            return window
        raise TypeError("window must be an integer or timedelta")

    @staticmethod
    def _advance_index(bars: Sequence[Bar], start: int, window: Window) -> int:
        """Return the index after a count- or time-based window."""

        if isinstance(window, int) and not isinstance(window, bool):
            return start + window
        if isinstance(window, timedelta):
            if window <= timedelta(0) or start >= len(bars):
                return start
            cutoff = bars[start].timestamp + window
            index = start
            while index < len(bars) and bars[index].timestamp < cutoff:
                index += 1
            return max(index, start + 1)
        raise TypeError("window must be an integer or timedelta")
