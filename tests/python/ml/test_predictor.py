import pickle

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from src.python.ml.predictor import MLInferenceService, ModelArtifactError


def bars(n=80):
    close = np.linspace(100, 110, n)
    return pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "tick_volume": np.arange(n) + 100,
        }
    )


def artifact(path, names=("close",), data_manifest=None):
    x = np.array([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]])
    y = np.array([0, 1, 2, 0, 1, 2])
    model = LogisticRegression(max_iter=300).fit(x, y)
    with open(path, "wb") as f:
        pickle.dump(
            {
                "model": model,
                "feature_names": list(names),
                "schema_version": "1.0",
                **({"data_manifest": data_manifest} if data_manifest else {}),
            },
            f,
        )


def test_happy_path_and_probabilities(tmp_path):
    p = tmp_path / "model.pkl"
    artifact(p)
    result = MLInferenceService(p).predict(bars())
    assert result.direction in {"BUY", "SELL", "HOLD"}
    assert set(result.probabilities) == {"BUY", "SELL", "HOLD"}
    assert abs(sum(result.probabilities.values()) - 1) < 1e-6


def test_missing_artifact_and_bad_feature_fail_closed(tmp_path):
    with pytest.raises(ModelArtifactError, match="not found"):
        MLInferenceService(tmp_path / "missing.pkl")
    p = tmp_path / "model.pkl"
    artifact(p, ("does_not_exist",))
    with pytest.raises(ValueError, match="cannot reproduce"):
        MLInferenceService(p).predict(bars())


def test_insufficient_data_fails_closed(tmp_path):
    p = tmp_path / "model.pkl"
    artifact(p)
    with pytest.raises(ValueError, match="insufficient"):
        MLInferenceService(p).predict(bars(20))


def test_training_manifest_is_exposed_as_metadata(tmp_path):
    p = tmp_path / "model.pkl"
    manifest = {"manifest_version": "1.0", "required_min_samples": 10000}
    artifact(p, data_manifest=manifest)
    service = MLInferenceService(p)
    assert service.metadata["data_manifest"] == manifest
