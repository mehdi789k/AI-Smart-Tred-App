"""Fail-closed real-time inference service."""

from __future__ import annotations

import hashlib
import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Sequence

import numpy as np

from .config import MLConfig
from .utils import build_training_features, feature_schema_hash


class ModelArtifactError(ValueError):
    """Raised when a serialized model is unsafe or incompatible to use."""


@dataclass(frozen=True)
class Prediction:
    direction: str
    confidence: float
    probabilities: Dict[str, float]
    feature_names: Sequence[str]
    model_name: str = ""
    model_version: str = ""
    latency_ms: float = 0.0

    @property
    def predicted_return(self) -> float:
        """Expose the bounded directional proxy used by execution scoring."""

        return (self.probabilities["BUY"] - self.probabilities["SELL"]) * 0.01

    @property
    def symbol(self) -> str:
        """Return the symbol associated with this prediction when available."""

        return ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize prediction data for order and audit payloads."""

        return {
            "direction": self.direction,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
            "model_name": self.model_name,
            "model_version": self.model_version,
            "predicted_return": self.predicted_return,
            "latency_ms": self.latency_ms,
        }


class MLInferenceService:
    """Load a training artifact once and expose deterministic dashboard-friendly APIs."""

    def __init__(
        self, artifact_path: str | Path, config: MLConfig | None = None
    ) -> None:
        self.config = config or MLConfig()
        self.artifact_path = Path(artifact_path)
        self.model: Any = None
        self.feature_names: tuple[str, ...] = ()
        self.classes: tuple[Any, ...] = ()
        self.metadata: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.artifact_path.is_file():
            raise ModelArtifactError(f"model artifact not found: {self.artifact_path}")
        try:
            with self.artifact_path.open("rb") as fh:
                artifact = pickle.load(fh)
        except Exception as exc:
            raise ModelArtifactError(f"cannot load model artifact: {exc}") from exc
        if (
            not isinstance(artifact, dict)
            or "model" not in artifact
            or "feature_names" not in artifact
        ):
            raise ModelArtifactError(
                "artifact must be a mapping containing model and feature_names"
            )
        names = artifact["feature_names"]
        if (
            not isinstance(names, (list, tuple))
            or not names
            or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names)
        ):
            raise ModelArtifactError(
                "artifact feature_names must be a non-empty unique string list"
            )
        model = artifact["model"]
        if not callable(getattr(model, "predict_proba", None)):
            raise ModelArtifactError("artifact model must implement predict_proba")
        raw_classes = getattr(model, "classes_", artifact.get("classes_", ()))
        classes = tuple(raw_classes) if raw_classes is not None else ()
        if not classes:
            raise ModelArtifactError("artifact model has no classes_")
        if len(classes) != 3:
            raise ModelArtifactError(
                f"expected exactly 3 classes (BUY/SELL/HOLD), got {len(classes)}"
            )
        self.model, self.feature_names, self.classes = model, tuple(names), classes
        self.metadata = {
            k: artifact[k]
            for k in (
                "model_name",
                "model_version",
                "schema_version",
                "symbol",
                "timeframe",
                "dataset_version",
                "feature_schema_hash",
                "model_checksum",
                "metadata_version",
                "data_manifest",
            )
            if k in artifact
        }
        declared_schema = self.metadata.get("feature_schema_hash")
        if declared_schema and declared_schema != feature_schema_hash(
            self.feature_names
        ):
            raise ModelArtifactError("artifact feature schema hash mismatch")
        declared_checksum = self.metadata.get("model_checksum")
        if declared_checksum:
            actual_checksum = hashlib.sha256(
                pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
            ).hexdigest()
            if declared_checksum != actual_checksum:
                raise ModelArtifactError("artifact model checksum mismatch")
        version = self.metadata.get("schema_version")
        if version is not None and version != self.config.expected_schema_version:
            raise ModelArtifactError(f"unsupported schema_version: {version}")
        for key, expected in (
            ("symbol", self.config.expected_symbol),
            ("timeframe", self.config.expected_timeframe),
        ):
            if expected and self.metadata.get(key) != expected:
                raise ModelArtifactError(
                    f"artifact {key} mismatch: expected {expected}, "
                    f"got {self.metadata.get(key)!r}"
                )

    @staticmethod
    def _direction(value: Any) -> str:
        if isinstance(value, str):
            normalized = value.upper()
            if normalized in {"BUY", "SELL", "HOLD"}:
                return normalized
        if isinstance(value, (int, np.integer)) and int(value) in (0, 1, 2):
            return ("SELL", "HOLD", "BUY")[int(value)]
        raise ModelArtifactError(f"invalid model class: {value!r}")

    def predict(self, ohlcv: object, symbol: str = "") -> Prediction:
        """Generate one latest-bar prediction from real OHLCV rows."""
        started = time.perf_counter()
        features = build_training_features(
            ohlcv, self.feature_names, self.config.min_bars
        )
        vector = features.iloc[[-1]].to_numpy(dtype=float)
        probabilities = np.asarray(self.model.predict_proba(vector), dtype=float)
        if (
            probabilities.shape != (1, len(self.classes))
            or not np.isfinite(probabilities).all()
            or (probabilities < 0).any()
        ):
            raise ModelArtifactError("model returned invalid probabilities")
        row = probabilities[0]
        total = float(row.sum())
        if total <= 0 or abs(total - 1.0) > 1e-3:
            raise ModelArtifactError("model probabilities are not normalized")
        labels = [self._direction(c) for c in self.classes]
        if set(labels) != {"BUY", "SELL", "HOLD"}:
            raise ModelArtifactError("classes must map exactly to BUY, SELL and HOLD")
        probs = {label: float(row[i]) for i, label in enumerate(labels)}
        direction = max(probs, key=lambda label: probs[label])
        return Prediction(
            direction,
            probs[direction],
            probs,
            self.feature_names,
            str(self.metadata.get("model_name", "")),
            str(self.metadata.get("model_version", "")),
            (time.perf_counter() - started) * 1000,
        )

    def predict_batch(self, ohlcv: object) -> list[Prediction]:
        """Predict each complete row after feature warm-up (useful for backtests)."""
        features = build_training_features(
            ohlcv, self.feature_names, self.config.min_bars
        )
        out: list[Prediction] = []
        for i in range(self.config.min_bars - 1, len(features)):
            row = features.iloc[[i]].to_numpy(dtype=float)
            raw = np.asarray(self.model.predict_proba(row), dtype=float)[0]
            if (
                raw.shape != (len(self.classes),)
                or not np.isfinite(raw).all()
                or raw.sum() <= 0
            ):
                raise ModelArtifactError("model returned invalid batch probabilities")
            labels = [self._direction(c) for c in self.classes]
            probs = {label: float(raw[j] / raw.sum()) for j, label in enumerate(labels)}
            direction = max(probs, key=lambda label: probs[label])
            out.append(
                Prediction(
                    direction,
                    probs[direction],
                    probs,
                    self.feature_names,
                    str(self.metadata.get("model_name", "")),
                    str(self.metadata.get("model_version", "")),
                )
            )
        return out

    def feature_importance(self) -> Dict[str, float]:
        """Return model-provided feature importance, or fail rather than inventing it."""
        importance = getattr(self.model, "feature_importances_", None)
        if importance is None or len(importance) != len(self.feature_names):
            raise ModelArtifactError(
                "model does not expose compatible feature_importances_"
            )
        return dict(zip(self.feature_names, map(float, importance)))
