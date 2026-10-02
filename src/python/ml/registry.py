"""Small filesystem model registry with process-safe metadata updates."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .governance import (
    ModelPromotionError,
    PromotionCriteria,
    evaluate_promotion,
)
from .predictor import MLInferenceService

_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class ModelRegistry:
    """Resolve model artifacts while preserving governance metadata."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        lock_key = str(self.root.resolve())
        with _THREAD_LOCKS_GUARD:
            self._thread_lock = _THREAD_LOCKS.setdefault(lock_key, threading.RLock())
        self._initialize_lock_file()

    def register(self, name: str, artifact: str | Path, version: str) -> Path:
        path = Path(artifact)
        if not path.is_file():
            raise FileNotFoundError(path)
        if not name.strip() or not version.strip():
            raise ValueError("model name and version are required")
        metadata = self._artifact_metadata(path)
        with self._locked():
            data = self._read()
            entry = data.setdefault("models", {}).setdefault(name, {})
            previous = entry.get(version)
            record = {
                "path": str(path.resolve()),
                **metadata,
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }
            if (
                isinstance(previous, dict)
                and previous.get("artifact_checksum") == metadata["artifact_checksum"]
            ):
                if previous.get("promotion", {}).get("status") == "approved":
                    record["promotion"] = previous["promotion"]
            elif entry.get("active_version") == version:
                entry.pop("active_version", None)
                entry.setdefault("version_history", []).append(
                    self._history_event(
                        "registration_replaced",
                        version,
                        None,
                    )
                )
            entry[version] = record
            self._write(data)
        return path

    def resolve(self, name: str, version: str | None = None) -> Path:
        """Resolve an explicit version or the approved active version."""

        with self._locked():
            data = self._read()
            entry = data.get("models", {}).get(name, {})
            if not entry:
                raise FileNotFoundError(f"model not registered: {name}")
            selected = version if version is not None else entry.get("active_version")
            if selected is None:
                raise ModelPromotionError(
                    f"model has no approved active version: {name}"
                )
            if selected not in entry:
                raise FileNotFoundError(
                    f"model version not registered: {name}/{selected}"
                )
            record = entry[selected]
            if version is not None and (
                not isinstance(record, dict)
                or not isinstance(record.get("promotion"), dict)
                or record["promotion"].get("status") != "approved"
            ):
                raise ModelPromotionError(
                    f"model version is not approved: {name}/{selected}"
                )
            return Path(record["path"] if isinstance(record, dict) else record)

    def metadata(self, name: str, version: str | None = None) -> dict[str, Any]:
        """Return registry metadata for a version or approved active version."""

        with self._locked():
            data = self._read()
            entry = data.get("models", {}).get(name, {})
            selected = version if version is not None else entry.get("active_version")
            if selected is None:
                raise ModelPromotionError(
                    f"model has no approved active version: {name}"
                )
            if selected not in entry:
                raise FileNotFoundError(
                    f"model version not registered: {name}/{selected}"
                )
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

        with self._locked():
            data = self._read()
            entry = data.get("models", {}).get(name, {})
            if version not in entry or not isinstance(entry[version], dict):
                raise FileNotFoundError(
                    f"model version not registered: {name}/{version}"
                )
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
            previous = entry.get("active_version")
            entry["active_version"] = version
            entry.setdefault("version_history", []).append(
                self._history_event("promotion", previous, version)
            )
            self._write(data)
            return decision

    def rollback(self, name: str, version: str) -> dict[str, Any]:
        """Explicitly restore a registered version with prior approval."""

        with self._locked():
            data = self._read()
            entry = data.get("models", {}).get(name, {})
            record = entry.get(version)
            if not isinstance(record, dict):
                raise FileNotFoundError(
                    f"model version not registered: {name}/{version}"
                )
            promotion = record.get("promotion")
            if not isinstance(promotion, dict) or promotion.get("status") != "approved":
                raise ModelPromotionError(
                    f"rollback target is not approved: {name}/{version}"
                )
            previous = entry.get("active_version")
            entry["active_version"] = version
            entry.setdefault("version_history", []).append(
                self._history_event("rollback", previous, version)
            )
            self._write(data)
            return dict(record)

    def load(self, name: str, version: str | None = None) -> MLInferenceService:
        """Load an approved model and verify its exact registered artifact bytes."""

        with self._locked():
            data = self._read()
            entry = data.get("models", {}).get(name, {})
            if not entry:
                raise FileNotFoundError(f"model not registered: {name}")
            selected = version if version is not None else entry.get("active_version")
            if selected is None:
                raise ModelPromotionError(
                    f"model has no approved active version: {name}"
                )
            if selected not in entry:
                raise FileNotFoundError(
                    f"model version not registered: {name}/{selected}"
                )
            record = entry[selected]
            if (
                not isinstance(record, dict)
                or record.get("promotion", {}).get("status") != "approved"
            ):
                raise ModelPromotionError(
                    f"model version is not approved: {name}/{selected}"
                )
            path = Path(record["path"])
            checksum = record.get("artifact_checksum")
            if not isinstance(checksum, str) or not checksum:
                raise ModelPromotionError(
                    f"registered artifact checksum is missing: {name}/{selected}"
                )
        return MLInferenceService(path, expected_artifact_checksum=checksum)

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

    @staticmethod
    def _history_event(
        action: str, from_version: str | None, to_version: str | None
    ) -> dict[str, Any]:
        return {
            "action": action,
            "from_version": from_version,
            "to_version": to_version,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize registry access across threads and OS processes."""

        with self._thread_lock:
            lock_path = self.root / ".registry.lock"
            with lock_path.open("r+b") as lock_file:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    while True:
                        try:
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(0.01)
                    try:
                        yield
                    finally:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _initialize_lock_file(self) -> None:
        """Create a stable lock byte before processes start acquiring it."""

        path = self.root / ".registry.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if path.stat().st_size:
                return
            # Recover an empty lock file left by an interrupted first-time setup.
            with path.open("r+b") as lock_file:
                lock_file.write(b"\0")
                lock_file.flush()
            return
        try:
            os.write(descriptor, b"\0")
        finally:
            os.close(descriptor)

    def _read(self) -> dict[str, Any]:
        file = self.root / "registry.json"
        return json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}

    def _write(self, data: dict[str, Any]) -> None:
        """Atomically replace the registry using a unique same-directory file."""

        destination = self.root / "registry.json"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=".registry.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temp_path = Path(temporary.name)
                json.dump(data, temporary, indent=2)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temp_path, destination)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
