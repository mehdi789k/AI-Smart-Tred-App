from pathlib import Path

import pytest

from src.python.dashboard.env_manager import (
    EnvironmentManager,
    EnvironmentManagerError,
    build_demo_profile,
    build_live_profile,
    is_sensitive_key,
    mask_value,
)


def test_read_entries_masks_sensitive_values(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("MT5_LOGIN=\nAPI_AUTH_TOKEN=\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("MT5_LOGIN=123\nAPI_AUTH_TOKEN=secret\n# keep\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    entries = manager.read_entries()

    assert [(entry.key, entry.display_value) for entry in entries] == [
        ("MT5_LOGIN", "123"),
        ("API_AUTH_TOKEN", "********"),
    ]


def test_sensitive_update_requires_explicit_replacement(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "API_AUTH_TOKEN=\nMT5_LOGIN=\n", encoding="utf-8"
    )
    env = tmp_path / ".env"
    env.write_text("API_AUTH_TOKEN=old\nMT5_LOGIN=123\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    manager.update({"MT5_LOGIN": "456"})

    assert env.read_text(encoding="utf-8") == "API_AUTH_TOKEN=old\nMT5_LOGIN=456\n"


def test_masking_helpers_identify_and_hide_credentials() -> None:
    assert is_sensitive_key("API_AUTH_TOKEN")
    assert is_sensitive_key("MT5_PASSWORD")
    assert is_sensitive_key("DATABASE_URL")
    assert is_sensitive_key("POSTGRES_DATABASE_URL")
    assert is_sensitive_key("SQLALCHEMY_DATABASE_URI")
    assert mask_value("secret") == "********"


def test_invalid_keys_and_newlines_are_rejected(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("VALID_KEY=\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path)

    with pytest.raises(EnvironmentManagerError):
        manager.update({"not-valid": "value"})
    with pytest.raises(EnvironmentManagerError):
        manager.update({"NEW_KEY": "value"})
    with pytest.raises(EnvironmentManagerError):
        manager.update({"VALID_KEY": "line\nbreak"})


def test_mask_marker_cannot_replace_sensitive_value(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("API_AUTH_TOKEN=\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("API_AUTH_TOKEN=old\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    with pytest.raises(EnvironmentManagerError):
        manager.update({"API_AUTH_TOKEN": "********"})


def test_update_writes_backup_and_preserves_unmanaged_lines(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("A=\nB=\nC=\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("# header\nA=one\n\nB=two\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    manager.update({"A": "updated", "C": "three"})

    assert env.read_text(encoding="utf-8") == "# header\nA=updated\n\nB=two\nC=three\n"
    backups = list((tmp_path / "backups").glob(".env.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "# header\nA=one\n\nB=two\n"


def test_atomic_write_failure_keeps_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env.example").write_text("A=\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("A=one\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    def fail_replace(source: str, destination: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("src.python.dashboard.env_manager.os.replace", fail_replace)

    with pytest.raises(EnvironmentManagerError):
        manager.update({"A": "two"})

    assert env.read_text(encoding="utf-8") == "A=one\n"
    assert not list(tmp_path.glob(".env.*.tmp"))


def test_existing_key_can_be_updated_even_when_not_in_example(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("APP_ENV=\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("LEGACY_KEY=old\n", encoding="utf-8")

    EnvironmentManager(tmp_path, env_path=env).update({"LEGACY_KEY": "new"})

    assert "LEGACY_KEY=new" in env.read_text(encoding="utf-8")


def test_mutations_use_distinct_temporary_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env.example").write_text("A=\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("A=one\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)
    seen: list[str] = []
    original_replace = __import__("os").replace

    def record_replace(source, destination):
        seen.append(str(source))
        return original_replace(source, destination)

    monkeypatch.setattr("src.python.dashboard.env_manager.os.replace", record_replace)
    manager.update({"A": "two"})
    manager.update({"A": "three"})

    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_delete_is_idempotent_and_reports_removal(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=one\n", encoding="utf-8")
    manager = EnvironmentManager(tmp_path, env_path=env)

    assert manager.delete() is True
    assert manager.delete() is False


def test_reset_preserves_credentials_and_disables_auto_trading(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "MT5_PASSWORD=\nAPI_AUTH_TOKEN=\nMT5_AUTO_TRADING_ENABLED=\n"
        "APP_ENV=\nMT5_DEMO_ENABLED=\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text(
        "MT5_PASSWORD=keep-me\nAPI_AUTH_TOKEN=keep-token\n"
        "MT5_AUTO_TRADING_ENABLED=true\n",
        encoding="utf-8",
    )

    EnvironmentManager(tmp_path, env_path=env).reset("live")

    text = env.read_text(encoding="utf-8")
    assert "MT5_PASSWORD=keep-me" in text
    assert "API_AUTH_TOKEN=keep-token" in text
    assert "APP_ENV=production" in text
    assert "MT5_DEMO_ENABLED=false" in text
    assert "MT5_AUTO_TRADING_ENABLED=false" in text


def test_demo_reset_applies_conservative_profile(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "\n".join(
            [
                "MT5_PASSWORD=",
                "APP_ENV=",
                "MT5_DEMO_ENABLED=",
                "MT5_ENABLED=",
                "MT5_AUTO_TRADING_ENABLED=",
                "MT5_DEMO_MAX_TRADE_VOLUME=",
                "MT5_DEMO_MAX_TRADES_PER_SESSION=",
                "MT5_DEMO_MAX_DAILY_LOSS=",
                "MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION=",
                "MT5_DEMO_AUTO_STOP_ON_ERROR=",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("MT5_PASSWORD=keep-me\n", encoding="utf-8")

    EnvironmentManager(tmp_path, env_path=env).reset("demo")

    text = env.read_text(encoding="utf-8")
    assert "APP_ENV=development" in text
    assert "MT5_DEMO_ENABLED=true" in text
    assert "MT5_ENABLED=false" in text
    assert "MT5_AUTO_TRADING_ENABLED=false" in text
    assert "MT5_DEMO_MAX_TRADE_VOLUME=0.01" in text
    assert "MT5_DEMO_MAX_TRADES_PER_SESSION=3" in text
    assert "MT5_DEMO_MAX_DAILY_LOSS=10" in text


def test_profile_builders_are_safe_defaults() -> None:
    assert build_demo_profile()["MT5_AUTO_TRADING_ENABLED"] == "false"
    assert build_live_profile()["MT5_AUTO_TRADING_ENABLED"] == "false"


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentManagerError):
        EnvironmentManager(tmp_path, env_path=tmp_path / "nested" / ".." / ".." / ".env")
