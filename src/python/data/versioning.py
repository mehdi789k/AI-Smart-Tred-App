"""Schema and model version metadata used by deployment and audit tooling."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "0010"
MODEL_VERSION = "unreleased"


def configured_model_version() -> str:
    """Return the deployed model version without exposing credentials."""

    value = os.getenv("MODEL_VERSION", MODEL_VERSION).strip()
    return value or MODEL_VERSION


async def record_system_versions(repository: Any) -> None:
    """Persist the schema and model versions in one repository transaction."""

    await repository.upsert_system_version("schema", SCHEMA_VERSION)
    await repository.upsert_system_version("model", configured_model_version())


def version_payload() -> dict[str, str]:
    """Return version metadata for health checks and deployment diagnostics."""

    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": configured_model_version(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
