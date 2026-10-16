"""Run safe lifecycle checks for the Smart MT5 Trading System hooks."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FILES = (
    "requirements/lock/full.txt",
    "pytest.ini",
    ".github/workflows/python-validation.yml",
)


def run_session_start() -> int:
    """Report safe local-session checks without reading or printing secrets."""
    missing = [
        relative_path
        for relative_path in REQUIRED_FILES
        if not (PROJECT_ROOT / relative_path).is_file()
    ]
    if missing:
        print(f"[hook] Missing required project files: {', '.join(missing)}")
        return 1

    if (PROJECT_ROOT / ".env").is_file():
        print("[hook] Local .env detected; credentials remain outside hook output.")
    else:
        print("[hook] No local .env detected; use .env.example for configuration.")

    print("[hook] Session checks passed. MT5 live order paths are not invoked.")
    return 0


def run_validation() -> int:
    """Compile source and tests, then run the repository's existing pytest suite."""
    commands = (
        [sys.executable, "-m", "compileall", "-q", "src", "tests"],
        [sys.executable, "-m", "pytest", "-q", "--import-mode=importlib"],
    )
    environment = os.environ.copy()
    environment.setdefault("PYTHONUNBUFFERED", "1")

    for command in commands:
        print(f"[hook] Running: {' '.join(command)}")
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            print(f"[hook] Validation failed with exit code {completed.returncode}.")
            return completed.returncode

    print("[hook] Compile and test validation passed.")
    return 0


def parse_args() -> argparse.Namespace:
    """Parse the lifecycle event supplied by the hook configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--event",
        choices=("session-start", "validation"),
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    """Dispatch the requested hook check and return its process status."""
    arguments = parse_args()
    if arguments.event == "session-start":
        return run_session_start()
    return run_validation()


if __name__ == "__main__":
    raise SystemExit(main())
