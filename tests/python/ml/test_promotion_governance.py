import math
import pickle

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from src.python.ml.governance import (
    ModelPromotionError,
    PromotionCriteria,
    build_evaluation_record,
    evaluate_promotion,
)
from src.python.ml.registry import ModelRegistry


def complete_metadata() -> dict[str, object]:
    return {
        "dataset_version": "dataset-v1",
        "feature_schema_hash": "feature-schema-v1",
        "artifact_checksum": "artifact-v1",
        "walk_forward_validation": {
            "folds": 5,
            "out_of_sample": True,
            "completed": True,
        },
    }


def passing_evaluation() -> dict[str, float]:
    return {
        "risk_adjusted_return": 0.18,
        "max_drawdown": 0.12,
        "sharpe_ratio": 1.25,
        "stability": 0.86,
    }


def test_build_evaluation_record_requires_all_combined_metrics():
    record = build_evaluation_record(
        passing_evaluation(),
        policy_version="ml-promotion-v1",
        rollback_version="2026.09.18",
    )

    assert record["policy_version"] == "ml-promotion-v1"
    assert record["rollback_version"] == "2026.09.18"
    assert record["metrics"] == passing_evaluation()


def test_build_evaluation_record_rejects_non_finite_metric():
    metrics = passing_evaluation()
    metrics["sharpe_ratio"] = math.nan

    with pytest.raises(ModelPromotionError, match="sharpe_ratio"):
        build_evaluation_record(
            metrics,
            policy_version="ml-promotion-v1",
            rollback_version="2026.09.18",
        )


def test_evaluate_promotion_approves_complete_artifact_and_metrics():
    decision = evaluate_promotion(
        complete_metadata(),
        build_evaluation_record(
            passing_evaluation(),
            policy_version="ml-promotion-v1",
            rollback_version="2026.09.18",
        ),
        criteria=PromotionCriteria(),
    )

    assert decision["status"] == "approved"
    assert decision["policy_version"] == "ml-promotion-v1"
    assert decision["rollback_version"] == "2026.09.18"
    assert decision["reasons"] == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("dataset_version", "", "dataset_version"),
        ("feature_schema_hash", "", "feature_schema_hash"),
        ("artifact_checksum", "", "artifact_checksum"),
    ],
)
def test_evaluate_promotion_rejects_missing_reproducibility_metadata(
    field: str, value: str, message: str
):
    metadata = complete_metadata()
    metadata[field] = value
    evaluation = build_evaluation_record(
        passing_evaluation(),
        policy_version="ml-promotion-v1",
        rollback_version="2026.09.18",
    )

    decision = evaluate_promotion(metadata, evaluation, criteria=PromotionCriteria())

    assert decision["status"] == "rejected"
    assert any(message in reason for reason in decision["reasons"])


def test_evaluate_promotion_rejects_incomplete_walk_forward_evidence():
    metadata = complete_metadata()
    metadata["walk_forward_validation"] = {"folds": 5, "completed": False}
    evaluation = build_evaluation_record(
        passing_evaluation(),
        policy_version="ml-promotion-v1",
        rollback_version="2026.09.18",
    )

    decision = evaluate_promotion(metadata, evaluation, criteria=PromotionCriteria())

    assert decision["status"] == "rejected"
    assert any("walk-forward" in reason for reason in decision["reasons"])


def test_evaluate_promotion_rejects_metrics_below_policy_thresholds():
    metrics = passing_evaluation()
    metrics.update(
        {
            "risk_adjusted_return": -0.01,
            "max_drawdown": 0.31,
            "sharpe_ratio": 0.1,
            "stability": 0.4,
        }
    )
    evaluation = build_evaluation_record(
        metrics,
        policy_version="ml-promotion-v1",
        rollback_version="2026.09.18",
    )

    decision = evaluate_promotion(
        complete_metadata(), evaluation, criteria=PromotionCriteria()
    )

    assert decision["status"] == "rejected"
    assert len(decision["reasons"]) == 4


def test_registry_promote_persists_approved_evaluation_and_rollback_metadata(
    tmp_path,
):
    model = LogisticRegression(max_iter=100).fit(
        np.array([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]]),
        np.array([0, 1, 2, 0, 1, 2]),
    )
    artifact = tmp_path / "model.pkl"
    with artifact.open("wb") as handle:
        pickle.dump(
            {
                "model": model,
                "feature_names": ["close"],
                **complete_metadata(),
            },
            handle,
        )
    registry = ModelRegistry(tmp_path / "registry")
    registry.register("test", artifact, "2026.09.19")
    evaluation = build_evaluation_record(
        passing_evaluation(),
        policy_version="ml-promotion-v1",
        rollback_version="2026.09.18",
    )

    registry.promote("test", "2026.09.19", evaluation)

    metadata = registry.metadata("test", "2026.09.19")
    assert metadata["promotion"]["status"] == "approved"
    assert metadata["promotion"]["rollback_version"] == "2026.09.18"
