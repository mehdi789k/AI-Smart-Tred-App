"""Small filesystem model registry with atomic metadata updates."""

from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .governance import (
    ModelPromotionError,
    PromotionCriteria,
    evaluate_promotion,
)
from .predictor import MLInferenceService


class ModelRegistry:
    """Resolve model artifacts while preserving governance metadata."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def register(self, name: str, artifact: str | Path, version: str) -> Path:
        path = Path(artifact)
        if not path.is_file():
            raise FileNotFoundError(path)
        if not name.strip() or not version.strip():
            raise ValueError("model name and version are required")
        metadata = self._artifact_metadata(path)
        data = self._read()
        data.setdefault("models", {}).setdefault(name, {})[version] = {
            "path": str(path.resolve()),
            **metadata,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write(data)
        return path

    def resolve(self, name: str, version: str | None = None) -> Path:
        data = self._read()
        entry = data.get("models", {}).get(name, {})
        if not entry:
            raise FileNotFoundError(f"model not registered: {name}")
        version = version or sorted(entry)[-1]
        if version not in entry:
            raise FileNotFoundError(f"model version not registered: {name}/{version}")
        record = entry[version]
        return Path(record["path"] if isinstance(record, dict) else record)

    def metadata(self, name: str, version: str | None = None) -> dict[str, Any]:
        """Return immutable registry metadata for a registered artifact."""

        data = self._read()
        entry = data.get("models", {}).get(name, {})
        selected = version or (sorted(entry)[-1] if entry else None)
        if selected is None or selected not in entry:
            raise FileNotFoundError(f"model version not registered: {name}/{selected}")
        record = entry[selected]
        return dict(record) if isinstance(record, dict) else {"path": record}

    def promote(
        self,
        name: str,
        version: str,
        evaluation: dict[str, Any],
        *,
        criteria: PromotionCriteria | None = None,
    ) -> dict[str, Any]:
        """Promote only artifacts with complete reproducibility and risk evidence."""

        data = self._read()
        entry = data.get("models", {}).get(name, {})
        if version not in entry or not isinstance(entry[version], dict):
            raise FileNotFoundError(f"model version not registered: {name}/{version}")
        record = entry[version]
        decision = evaluate_promotion(
            record,
            evaluation,
            criteria=criteria or PromotionCriteria(),
        )
        if decision["status"] != "approved":
            raise ModelPromotionError(
                f"model promotion rejected: {', '.join(decision['reasons'])}"
            )
        record["promotion"] = decision
        self._write(data)
        return decision

    def load(self, name: str, version: str | None = None) -> MLInferenceService:
        return MLInferenceService(self.resolve(name, version))

    @staticmethod
    def _artifact_metadata(path: Path) -> dict[str, Any]:
        """Extract non-secret governance fields without changing the artifact."""

        artifact_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            with path.open("rb") as handle:
                artifact = pickle.load(handle)
        except (OSError, pickle.PickleError, EOFError, ValueError) as exc:
            raise ValueError(f"cannot inspect model artifact: {path}") from exc
        if not isinstance(artifact, dict):
            raise ValueError("model artifact must be a mapping")
        fields = (
            "model_version",
            "dataset_version",
            "feature_schema_hash",
            "model_checksum",
            "walk_forward_validation",
        )
        return {
            "artifact_checksum": artifact_checksum,
            **{field: artifact[field] for field in fields if field in artifact},
        }

    def _read(self) -> dict[str, Any]:
        file = self.root / "registry.json"
        return json.loads(file.read_text()) if file.exists() else {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.root / "registry.json.tmp"
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.root / "registry.json")
