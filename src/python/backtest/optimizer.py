"""Small, deterministic parameter and walk-forward optimization utilities."""

from __future__ import annotations

import inspect
import logging
import math
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Callable, Mapping, Sequence

from .engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizationResult:
    parameters: dict[str, Any]
    score: float
    backtest: BacktestResult
    trials: list[dict[str, Any]] = field(default_factory=list)


def parameter_combinations(
    parameter_grid: Mapping[str, Sequence[Any]],
) -> list[dict[str, Any]]:
    if not isinstance(parameter_grid, Mapping) or not parameter_grid:
        raise ValueError("parameter_grid must be a non-empty mapping")
    keys = list(parameter_grid)
    values = []
    for key in keys:
        choices = list(parameter_grid[key])
        if not choices:
            raise ValueError(f"parameter grid {key!r} must not be empty")
        values.append(choices)
    return [dict(zip(keys, combination)) for combination in product(*values)]


class GridOptimizer:
    """Evaluate every parameter combination against an in-sample window."""

    def __init__(
        self,
        parameter_grid: Mapping[str, Sequence[Any]],
        objective: str = "sharpe_ratio",
        maximize: bool = True,
    ):
        self.parameter_grid = parameter_grid
        self.objective = objective
        self.maximize = maximize
        parameter_combinations(parameter_grid)  # validate eagerly
        self.last_trials: list[dict[str, Any]] = []

    def build_strategy(
        self, factory: Callable[..., Any], parameters: Mapping[str, Any]
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
            return factory(**parameters)

    def optimize(
        self, factory: Callable[..., Any], data: Any, engine: BacktestEngine
    ) -> dict[str, Any]:
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a non-empty metric name")
        best: OptimizationResult | None = None
        self.last_trials = []
        for parameters in parameter_combinations(self.parameter_grid):
            strategy = self.build_strategy(factory, parameters)
            result = engine.run(strategy, data)
            if not isinstance(result, BacktestResult):
                raise TypeError(
                    "optimizer requires BacktestEngine.run to return BacktestResult"
                )
            if not result.metrics or not hasattr(result.metrics, self.objective):
                raise ValueError(f"unknown optimization objective: {self.objective}")
            score = float(getattr(result.metrics, self.objective))
            if not math.isfinite(score):
                score = -math.inf if self.maximize else math.inf
            candidate = OptimizationResult(parameters, score, result)
            self.last_trials.append({"parameters": dict(parameters), "score": score})
            if best is None or (
                score > best.score if self.maximize else score < best.score
            ):
                best = candidate
        logger.info(
            "Grid optimization complete: trials=%d objective=%s",
            len(self.last_trials),
            self.objective,
        )
        return best.parameters if best else {}


def optimize(
    strategy_factory: Callable[..., Any],
    data: Any,
    parameter_grid: Mapping[str, Sequence[Any]],
    engine: BacktestEngine | None = None,
    objective: str = "sharpe_ratio",
    maximize: bool = True,
) -> OptimizationResult:
    """One-shot grid optimization returning the winning backtest as well."""

    engine = engine or BacktestEngine()
    optimizer = GridOptimizer(parameter_grid, objective, maximize)
    best_params = optimizer.optimize(strategy_factory, data, engine)
    result = engine.run(optimizer.build_strategy(strategy_factory, best_params), data)
    if not isinstance(result, BacktestResult):
        raise TypeError(
            "optimizer requires BacktestEngine.run to return BacktestResult"
        )
    score = (
        float(getattr(result.metrics, objective))
        if result.metrics and hasattr(result.metrics, objective)
        else 0.0
    )
    return OptimizationResult(best_params, score, result, list(optimizer.last_trials))
