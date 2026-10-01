"""Canonical probability ensemble for artifact-backed inference."""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np


class ProbabilityEnsemble:
    """Average class probabilities from already-fitted estimators."""

    def __init__(self, models: Iterable[Any]) -> None:
        self.models = tuple(models)
        if len(self.models) < 2:
            raise ValueError("at least two models are required")
        self.classes_ = np.asarray([0, 1, 2])

    def predict_proba(self, features: Any) -> np.ndarray:
        """Return normalized probabilities for BUY/HOLD/SELL class indices."""

        probabilities = [
            np.asarray(model.predict_proba(features), dtype=float)
            for model in self.models
        ]
        if any(values.ndim != 2 or values.shape[1] != 3 for values in probabilities):
            raise ValueError(
                "all ensemble models must return three class probabilities"
            )
        result = np.mean(probabilities, axis=0)
        totals = result.sum(axis=1, keepdims=True)
        if np.any(totals <= 0) or not np.isfinite(result).all():
            raise ValueError("ensemble probabilities are invalid")
        return result / totals

    def predict(self, features: Any) -> np.ndarray:
        """Return the most probable class index for each input row."""

        return np.argmax(self.predict_proba(features), axis=1)
