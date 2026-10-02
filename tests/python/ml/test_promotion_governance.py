import json
import math
import multiprocessing
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
from src.python.ml.predictor import MLInferenceService
from src.python.ml.registry import ModelRegistry
from src.python.ml.utils import feature_schema_hash


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


def _register_after_barrier(root, artifact, version, barrier):
    """Worker used to exercise registry updates from independent processes."""
    registry = ModelRegistry(root)
    barrier.wait()
    registry.register("concurrent", artifact, version)


def _write_artifact(path):
    model = LogisticRegression(max_iter=100).fit(
        np.array([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]]),
        np.array([0, 1, 2, 0, 1, 2]),
    )
    with open(path, "wb") as handle:
        pickle.dump(
            {
                "model": model,
                "feature_names": ["close"],
                **{
                    **complete_metadata(),
                    "feature_schema_hash": feature_schema_hash(["close"]),
                },
            },
            handle,
        )


def _approve(registry, version):
    return registry.promote(
        "test",
        version,
        build_evaluation_record(
            passing_evaluation(),
            policy_version="ml-promotion-v1",
            rollback_version="2026.09.18",
        ),
    )


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


def test_registry_default_load_fails_closed_before_promotion(tmp_path):
    artifact = tmp_path / "model.pkl"
    _write_artifact(artifact)
    registry = ModelRegistry(tmp_path / "registry")
    registry.register("test", artifact, "2026.09.19")

    with pytest.raises(ModelPromotionError, match="approved active"):
        registry.load("test")


def test_registry_resolve_rejects_unapproved_explicit_version(tmp_path):
    artifact = tmp_path / "model.pkl"
    _write_artifact(artifact)
    registry = ModelRegistry(tmp_path / "registry")
    registry.register("test", artifact, "2026.09.19")

    # Metadata remains inspectable before approval, but its path must not bypass
    # governance when constructing an inference service directly.
    assert registry.metadata("test", "2026.09.19")["path"] == str(artifact.resolve())
    with pytest.raises(ModelPromotionError, match="not approved"):
        MLInferenceService(registry.resolve("test", "2026.09.19"))


def test_registry_load_selects_approved_active_version_and_rejects_unapproved(
    tmp_path,
):
    artifact = tmp_path / "model.pkl"
    _write_artifact(artifact)
    registry = ModelRegistry(tmp_path / "registry")
    registry.register("test", artifact, "2026.09.19")

    with pytest.raises(ModelPromotionError, match="not approved"):
        registry.load("test", "2026.09.19")

    _approve(registry, "2026.09.19")
    later_artifact = tmp_path / "later.pkl"
    _write_artifact(later_artifact)
    registry.register("test", later_artifact, "z-unapproved")
    assert registry.load("test").artifact_path == artifact


def test_registry_load_rejects_artifact_checksum_mismatch(tmp_path):
    artifact = tmp_path / "model.pkl"
    _write_artifact(artifact)
    registry = ModelRegistry(tmp_path / "registry")
    registry.register("test", artifact, "2026.09.19")
    _approve(registry, "2026.09.19")
    artifact.write_bytes(artifact.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        registry.load("test")


def test_registry_rollback_to_previously_approved_version_is_audited(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    artifacts = {}
    for version in ("2026.09.19", "2026.09.20"):
        artifact = tmp_path / f"{version}.pkl"
        _write_artifact(artifact)
        artifacts[version] = artifact
        registry.register("test", artifact, version)
        _approve(registry, version)

    registry.rollback("test", "2026.09.19")

    assert registry.load("test").artifact_path == artifacts["2026.09.19"]
    data = registry._read()
    assert data["models"]["test"]["active_version"] == "2026.09.19"
    event = data["models"]["test"]["version_history"][-1]
    assert event["action"] == "rollback"
    assert event["from_version"] == "2026.09.20"
    assert event["to_version"] == "2026.09.19"


@pytest.mark.parametrize("target_kind", ["missing", "unapproved"])
def test_registry_rejects_unsafe_rollback_without_changing_active(
    tmp_path, target_kind
):
    registry = ModelRegistry(tmp_path / "registry")
    active_artifact = tmp_path / "active.pkl"
    _write_artifact(active_artifact)
    registry.register("test", active_artifact, "active")
    _approve(registry, "active")
    target = "missing"
    if target_kind == "unapproved":
        unapproved_artifact = tmp_path / "unapproved.pkl"
        _write_artifact(unapproved_artifact)
        registry.register("test", unapproved_artifact, "unapproved")
        target = "unapproved"

    before = registry._read()
    with pytest.raises((FileNotFoundError, ModelPromotionError)):
        registry.rollback("test", target)
    after = registry._read()

    assert after["models"]["test"]["active_version"] == "active"
    assert after["models"]["test"].get("version_history", []) == before["models"][
        "test"
    ].get("version_history", [])


def test_concurrent_process_registration_preserves_all_versions(tmp_path):
    context = multiprocessing.get_context("spawn")
    root = tmp_path / "registry"
    artifacts = []
    versions = [f"v{i}" for i in range(4)]
    for version in versions:
        artifact = tmp_path / f"{version}.pkl"
        _write_artifact(artifact)
        artifacts.append(artifact)
    barrier = context.Barrier(len(versions))
    processes = [
        context.Process(
            target=_register_after_barrier,
            args=(root, artifact, version, barrier),
        )
        for artifact, version in zip(artifacts, versions)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join()
        assert process.exitcode == 0

    registered = json.loads((root / "registry.json").read_text())
    assert set(registered["models"]["concurrent"]) == set(versions)
