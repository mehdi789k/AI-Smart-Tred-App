"""Pure performance calculations for backtest results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class PerformanceMetrics:
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_percent: float = 0.0
    volatility: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    average_trade: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(values: Iterable[float]) -> np.ndarray:
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(number):
            result.append(number)
    return np.asarray(result, dtype=float)


def calculate_metrics(
    equity_curve: Sequence[float] | None = None,
    trades: Sequence[Any] | None = None,
    initial_balance: float | None = None,
    periods_per_year: float = 252.0,
) -> PerformanceMetrics:
    """Calculate return, risk and trade statistics without mutating inputs."""

    if equity_curve is None:
        equity_curve = ()
    equity = _finite(equity_curve)
    if initial_balance is None:
        initial_balance = float(equity[0]) if equity.size else 0.0
    if not np.isfinite(float(initial_balance)) or initial_balance <= 0:
        raise ValueError("initial_balance must be positive")
    if not np.isfinite(float(periods_per_year)) or periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if equity.size and np.any(equity <= 0):
        raise ValueError("equity curve must contain positive finite values")
    if equity.size:
        returns = np.diff(equity) / equity[:-1]
        returns = returns[np.isfinite(returns)]
        ending = float(equity[-1])
        running = np.maximum.accumulate(equity)
        drawdowns = running - equity
        max_dd = float(np.max(drawdowns, initial=0.0))
        max_dd_pct = float(
            np.max(
                np.divide(
                    drawdowns, running, out=np.zeros_like(drawdowns), where=running != 0
                )
            )
        )
    else:
        returns, ending, max_dd, max_dd_pct = (
            np.array([], dtype=float),
            initial_balance,
            0.0,
            0.0,
        )
    total_return = (ending - initial_balance) / initial_balance
    annualized = (
        float(
            (ending / initial_balance) ** (periods_per_year / max(len(equity) - 1, 1))
            - 1
        )
        if ending >= 0
        else -1.0
    )
    volatility = (
        float(np.std(returns, ddof=1) * sqrt(periods_per_year))
        if len(returns) > 1
        else 0.0
    )
    mean = float(np.mean(returns)) if len(returns) else 0.0
    std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = mean / std * sqrt(periods_per_year) if std > 0 else 0.0
    downside = returns[returns < 0]
    # Downside deviation is the RMS of negative returns, not sample standard
    # deviation of the negative subset.  This remains defined for one loss.
    downside_std = (
        float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    )
    sortino = mean / downside_std * sqrt(periods_per_year) if downside_std > 0 else 0.0

    pnl = _finite(
        (
            getattr(t, "pnl", t.get("pnl", 0.0) if isinstance(t, Mapping) else 0.0)
            for t in (trades or ())
        )
    )
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    profit_factor = (
        float(wins.sum() / abs(losses.sum()))
        if losses.size
        else (float("inf") if wins.size else 0.0)
    )
    return PerformanceMetrics(
        total_return=float(total_return),
        annualized_return=annualized,
        sharpe_ratio=float(sharpe),
        sortino_ratio=float(sortino),
        max_drawdown=max_dd,
        max_drawdown_percent=max_dd_pct,
        volatility=volatility,
        win_rate=float(len(wins) / len(pnl)) if len(pnl) else 0.0,
        profit_factor=profit_factor,
        total_trades=int(len(pnl)),
        average_trade=float(np.mean(pnl)) if len(pnl) else 0.0,
    )


# Friendly alias used by callers that prefer a verb-based name.
performance_metrics = calculate_metrics
