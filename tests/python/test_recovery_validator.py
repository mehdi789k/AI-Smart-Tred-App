from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_recovery.ps1"


def test_recovery_validator_rejects_missing_backup_before_restore() -> None:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(VALIDATOR),
            "-BackupFile",
            str(ROOT / "backups" / "missing-staging.dump"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Backup file not found" in result.stdout + result.stderr
