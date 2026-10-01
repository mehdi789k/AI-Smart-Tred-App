"""Dataset and model governance helpers for reproducible ML releases."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .utils import dataset_version, feature_schema_hash


class ModelPromotionError(ValueError):
    """Raised when a model cannot satisfy the promotion governance contract."""


@dataclass(frozen=True)
class PromotionCriteria:
    """Minimum evidence required before a model can be promoted."""

    min_risk_adjusted_return: float = 0.0
    max_drawdown: float = 0.20
    min_sharpe_ratio: float = 0.5
    min_stability: float = 0.70

    def __post_init__(self) -> None:
        values = (
            self.min_risk_adjusted_return,
            self.max_drawdown,
            self.min_sharpe_ratio,
            self.min_stability,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("promotion criteria must be finite")
        if self.max_drawdown < 0 or not 0 <= self.min_stability <= 1:
            raise ValueError("promotion criteria bounds are invalid")


def build_evaluation_record(
    metrics: Mapping[str, Any],
    *,
    policy_version: str,
    rollback_version: str,
) -> dict[str, Any]:
    """Build the auditable combined evaluation record used by promotion."""

    required = (
        "risk_adjusted_return",
        "max_drawdown",
        "sharpe_ratio",
        "stability",
    )
    missing = [name for name in required if name not in metrics]
    if missing:
        raise ModelPromotionError(
            f"evaluation missing required metrics: {', '.join(missing)}"
        )
    if not policy_version.strip() or not rollback_version.strip():
        raise ModelPromotionError(
            "policy_version and rollback_version are required for promotion"
        )
    normalized: dict[str, float] = {}
    for name in required:
        try:
            value = float(metrics[name])
        except (TypeError, ValueError) as exc:
            raise ModelPromotionError(f"metric {name} must be numeric") from exc
        if not math.isfinite(value):
            raise ModelPromotionError(f"metric {name} must be finite")
        normalized[name] = value
    return {
        "metrics": normalized,
        "policy_version": policy_version,
        "rollback_version": rollback_version,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_promotion(
    metadata: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    *,
    criteria: PromotionCriteria,
) -> dict[str, Any]:
    """Return an explicit approval or rejection decision without fail-open defaults."""

    reasons: list[str] = []
    for field in ("dataset_version", "feature_schema_hash", "artifact_checksum"):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            reasons.append(f"missing {field}")
    walk_forward = metadata.get("walk_forward_validation")
    if not isinstance(walk_forward, Mapping):
        reasons.append("missing walk-forward validation evidence")
    else:
        if walk_forward.get("completed") is not True:
            reasons.append("walk-forward validation is incomplete")
        if walk_forward.get("out_of_sample") is not True:
            reasons.append("walk-forward validation is not out-of-sample")
        try:
            if int(walk_forward.get("folds", 0)) < 2:
                reasons.append("walk-forward validation requires at least two folds")
        except (TypeError, ValueError):
            reasons.append("walk-forward validation fold count is invalid")

    metrics = evaluation.get("metrics")
    if not isinstance(metrics, Mapping):
        reasons.append("missing combined evaluation metrics")
        metrics = {}
    thresholds = (
        (
            "risk_adjusted_return",
            criteria.min_risk_adjusted_return,
            lambda actual, minimum: actual >= minimum,
            "below minimum",
        ),
        (
            "max_drawdown",
            criteria.max_drawdown,
            lambda actual, maximum: actual <= maximum,
            "above maximum",
        ),
        (
            "sharpe_ratio",
            criteria.min_sharpe_ratio,
            lambda actual, minimum: actual >= minimum,
            "below minimum",
        ),
        (
            "stability",
            criteria.min_stability,
            lambda actual, minimum: actual >= minimum,
            "below minimum",
        ),
    )
    for name, threshold, predicate, comparison in thresholds:
        try:
            actual = float(metrics[name])
        except (KeyError, TypeError, ValueError):
            reasons.append(f"missing metric {name}")
            continue
        if not math.isfinite(actual):
            reasons.append(f"metric {name} is not finite")
        elif not predicate(actual, threshold):
            reasons.append(f"metric {name} {comparison} policy threshold")

    policy_version = evaluation.get("policy_version")
    rollback_version = evaluation.get("rollback_version")
    if not isinstance(policy_version, str) or not policy_version.strip():
        reasons.append("missing policy_version")
    if not isinstance(rollback_version, str) or not rollback_version.strip():
        reasons.append("missing rollback_version")
    return {
        "status": "approved" if not reasons else "rejected",
        "policy_version": policy_version,
        "rollback_version": rollback_version,
        "reasons": reasons,
        "metrics": dict(metrics),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "criteria": asdict(criteria),
    }


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 checksum of a file without loading it fully in memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dataset_manifest(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
    source: str,
    labels: list[Any] | None = None,
) -> dict[str, Any]:
    """Build deterministic metadata used to reproduce a training dataset."""

    if not dataset_name.strip() or not source.strip():
        raise ValueError("dataset_name and source are required")
    return {
        "dataset_name": dataset_name,
        "source": source,
        "row_count": len(frame),
        "columns": [str(column) for column in frame.columns],
        "dataset_version": dataset_version(frame, labels),
        "feature_schema_hash": feature_schema_hash(frame),
    }


def write_manifest(manifest: dict[str, Any], path: str | Path) -> Path:
    """Write a validated JSON manifest atomically."""

    required = {"dataset_name", "source", "dataset_version", "feature_schema_hash"}
    if not required.issubset(manifest):
        raise ValueError(
            f"manifest missing required fields: {sorted(required - set(manifest))}"
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target
