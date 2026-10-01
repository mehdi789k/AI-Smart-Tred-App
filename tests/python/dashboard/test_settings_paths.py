"""Tests for resolving the dashboard's persisted settings file."""

from pathlib import Path

from src.python.dashboard.settings_paths import resolve_dashboard_settings_path


def test_dashboard_settings_path_uses_environment_override(tmp_path: Path) -> None:
    configured_path = tmp_path / "isolated-settings.json"

    result = resolve_dashboard_settings_path(
        tmp_path,
        environ={"DASHBOARD_SETTINGS_FILE": str(configured_path)},
    )

    assert result == configured_path


def test_dashboard_settings_path_defaults_to_project_data_directory(
    tmp_path: Path,
) -> None:
    result = resolve_dashboard_settings_path(tmp_path, environ={})

    assert result == tmp_path / "data" / "dashboard_settings.json"


def test_blank_dashboard_settings_override_uses_default_path(
    tmp_path: Path,
) -> None:
    result = resolve_dashboard_settings_path(
        tmp_path,
        environ={"DASHBOARD_SETTINGS_FILE": "  "},
    )

    assert result == tmp_path / "data" / "dashboard_settings.json"
