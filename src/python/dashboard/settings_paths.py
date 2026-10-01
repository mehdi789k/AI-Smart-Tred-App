"""Resolve filesystem paths used by the dashboard."""

import os
from collections.abc import Mapping
from pathlib import Path


def resolve_dashboard_settings_path(
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the configured dashboard settings path or its project default."""
    environment = os.environ if environ is None else environ
    configured_path = environment.get("DASHBOARD_SETTINGS_FILE", "")
    if configured_path.strip():
        return Path(configured_path).expanduser()
    return project_root / "data" / "dashboard_settings.json"
