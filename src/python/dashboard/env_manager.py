"""Safe management of the dashboard environment file."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Mapping

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LINE_PATTERN = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<separator>\s*=\s*)"
    r"(?P<value>.*?)(?P<newline>\r?\n)?$"
)
_MASKED_VALUE = "********"
_ENV_MUTATION_LOCK = threading.RLock()
_SENSITIVE_PARTS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)
_SENSITIVE_DATABASE_KEYS = {
    "DATABASE_URL",
    "DATABASE_URI",
    "DB_URL",
    "DB_URI",
    "POSTGRES_URL",
    "POSTGRES_URI",
    "SQLALCHEMY_DATABASE_URL",
    "SQLALCHEMY_DATABASE_URI",
}
_DEMO_PROFILE = {
    "APP_ENV": "development",
    "MT5_DEMO_ENABLED": "true",
    "MT5_ENABLED": "true",
    "MT5_AUTO_TRADING_ENABLED": "true",
    "MT5_LEGACY_ORDER_PATH_ENABLED": "false",
    "MT5_DEMO_MAX_TRADE_VOLUME": "0.01",
    "MT5_DEMO_MAX_TRADES_PER_SESSION": "3",
    "MT5_DEMO_MAX_DAILY_LOSS": "10",
    "MT5_DEMO_REQUIRE_MANUAL_CONFIRMATION": "true",
    "MT5_DEMO_AUTO_STOP_ON_ERROR": "true",
}
_LIVE_PROFILE = {
    "APP_ENV": "production",
    "MT5_DEMO_ENABLED": "false",
    "MT5_AUTO_TRADING_ENABLED": "false",
}


class EnvironmentManagerError(ValueError):
    """Raised when an environment file operation is unsafe or invalid."""


@dataclass(frozen=True)
class EnvEntry:
    """A parsed environment setting and its safe display representation."""

    key: str
    value: str
    is_sensitive: bool
    display_value: str


def is_sensitive_key(key: str) -> bool:
    """Return whether a setting name should have its value hidden."""

    normalized = key.upper()
    return (
        normalized in _SENSITIVE_DATABASE_KEYS
        or normalized.endswith(("DATABASE_URL", "DATABASE_URI"))
        or any(part in normalized for part in _SENSITIVE_PARTS)
    )


def mask_value(value: str) -> str:
    """Return the fixed marker used for sensitive values."""

    del value
    return _MASKED_VALUE


def build_demo_profile() -> dict[str, str]:
    """Build conservative settings for a non-trading demo environment."""

    return dict(_DEMO_PROFILE)


def build_live_profile() -> dict[str, str]:
    """Build safe baseline settings for a production environment."""

    return dict(_LIVE_PROFILE)


class EnvironmentManager:
    """Read and atomically modify a project-local dotenv file."""

    def __init__(
        self,
        project_root: Path,
        env_path: Path | None = None,
        backup_dir: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.env_path = self._safe_path(env_path or self.project_root / ".env")
        self.backup_dir = self._safe_path(
            backup_dir or self.project_root / "backups"
        )

    def read_entries(self) -> list[EnvEntry]:
        """Return valid dotenv assignments in file order."""

        if not self.env_path.is_file():
            return []
        try:
            lines = self.env_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise EnvironmentManagerError(
                f"Unable to read environment file: {self.env_path}"
            ) from error

        entries: list[EnvEntry] = []
        for line in lines:
            parsed = _LINE_PATTERN.match(f"{line}\n")
            if parsed is None:
                continue
            key = parsed.group("key")
            value = parsed.group("value")
            sensitive = is_sensitive_key(key)
            entries.append(
                EnvEntry(
                    key=key,
                    value=value,
                    is_sensitive=sensitive,
                    display_value=mask_value(value) if sensitive else value,
                )
            )
        return entries

    def update(self, values: Mapping[str, str | None]) -> None:
        """Update settings while preserving omitted or explicitly null values."""

        with _ENV_MUTATION_LOCK:
            self._validate_values(values)
            original = (
                self.env_path.read_text(encoding="utf-8")
                if self.env_path.is_file()
                else ""
            )
            current = {
                entry.key: entry.value for entry in self._parse_text(original)
            }
            approved = self._approved_keys()
            requested: dict[str, str] = {}
            for key, value in values.items():
                if value is None:
                    continue
                if key not in current and key not in approved:
                    raise EnvironmentManagerError(
                        f"Environment key is not approved: {key}"
                    )
                if is_sensitive_key(key) and value == _MASKED_VALUE:
                    raise EnvironmentManagerError(
                        f"Masked value cannot replace sensitive setting {key}"
                    )
                requested[key] = value

            lines = original.splitlines(keepends=True)
            seen: set[str] = set()
            updated_lines: list[str] = []
            for line in lines:
                parsed = _LINE_PATTERN.match(line)
                if parsed is None:
                    updated_lines.append(line)
                    continue
                key = parsed.group("key")
                if key not in requested:
                    updated_lines.append(line)
                    continue
                newline = parsed.group("newline") or ""
                updated_lines.append(
                    f"{parsed.group('prefix')}{key}={requested[key]}{newline}"
                )
                seen.add(key)

            for key, value in requested.items():
                if key not in seen:
                    updated_lines.append(f"{key}={value}\n")
            self._write("".join(updated_lines))

    def delete(self) -> bool:
        """Delete the environment file and report whether it existed."""

        with _ENV_MUTATION_LOCK:
            if not self.env_path.exists():
                return False
            if not self.env_path.is_file():
                raise EnvironmentManagerError(
                    f"Environment path is not a file: {self.env_path}"
                )
            self._backup()
            try:
                self.env_path.unlink()
            except OSError as error:
                raise EnvironmentManagerError(
                    f"Unable to delete environment file: {self.env_path}"
                ) from error
            return True

    def reset(self, profile: Literal["demo", "live"]) -> None:
        """Apply a safe profile while retaining existing secret values."""

        with _ENV_MUTATION_LOCK:
            if profile == "demo":
                values = build_demo_profile()
            elif profile == "live":
                values = build_live_profile()
            else:
                raise EnvironmentManagerError(f"Unknown environment profile: {profile}")
            self.update(values)

    def _safe_path(self, path: Path) -> Path:
        candidate = path.resolve()
        try:
            candidate.relative_to(self.project_root)
        except ValueError as error:
            raise EnvironmentManagerError(
                f"Path must remain inside project root: {candidate}"
            ) from error
        return candidate

    @staticmethod
    def _validate_values(values: Mapping[str, str | None]) -> None:
        for key, value in values.items():
            if not _KEY_PATTERN.fullmatch(key):
                raise EnvironmentManagerError(f"Invalid environment key: {key!r}")
            if value is not None and ("\n" in value or "\r" in value):
                raise EnvironmentManagerError(
                    f"Environment value for {key} contains a newline"
                )

    def _approved_keys(self) -> set[str]:
        """Read the checked-in environment template as the new-key allowlist."""

        template = self.project_root / ".env.example"
        if not template.is_file():
            return set()
        try:
            return {
                entry.key
                for entry in self._parse_text(template.read_text(encoding="utf-8"))
            }
        except OSError as error:
            raise EnvironmentManagerError(
                f"Unable to read environment allowlist: {template}"
            ) from error

    @staticmethod
    def _parse_text(text: str) -> list[EnvEntry]:
        entries: list[EnvEntry] = []
        for line in text.splitlines():
            parsed = _LINE_PATTERN.match(f"{line}\n")
            if parsed is not None:
                key = parsed.group("key")
                value = parsed.group("value")
                entries.append(
                    EnvEntry(key, value, is_sensitive_key(key), value)
                )
        return entries

    def _backup(self) -> None:
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            destination = self.backup_dir / f"{self.env_path.name}.{timestamp}"
            shutil.copy2(self.env_path, destination)
        except OSError as error:
            raise EnvironmentManagerError(
                f"Unable to back up environment file: {self.env_path}"
            ) from error

    def _write(self, text: str) -> None:
        if self.env_path.is_file():
            self._backup()
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.env_path.name}.", suffix=".tmp", dir=self.env_path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(text, encoding="utf-8", newline="")
            os.replace(temporary, self.env_path)
        except OSError as error:
            if temporary.exists():
                temporary.unlink()
            raise EnvironmentManagerError(
                f"Unable to atomically write environment file: {self.env_path}"
            ) from error
