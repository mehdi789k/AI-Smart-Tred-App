from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

CANDIDATE_EXECUTABLES = (
    "terminal64.exe",
    "terminal.exe",
    "mt5.exe",
    "metatrader5.exe",
)


def _windows_install_roots() -> list[Path]:
    """Return the most common Windows installation roots for MetaTrader 5."""
    home = Path.home()
    program_files = [
        Path(r"C:\Program Files\MetaTrader 5"),
        Path(r"C:\Program Files (x86)\MetaTrader 5"),
        Path(r"C:\Program Files\MetaQuotes\MetaTrader 5"),
        Path(r"C:\Program Files (x86)\MetaQuotes\MetaTrader 5"),
        home / "AppData" / "Local" / "Programs" / "MetaTrader 5",
        home / "AppData" / "Local" / "MetaTrader 5",
        home / "AppData" / "Roaming" / "MetaTrader 5",
    ]

    for env_var in ("APPDATA", "LOCALAPPDATA", "ProgramData"):
        value = os.environ.get(env_var)
        if value:
            program_files.append(Path(value) / "MetaTrader 5")
            program_files.append(Path(value) / "MetaQuotes" / "MetaTrader 5")

    return program_files


def find_mt5_executable(custom_path: str | None = None) -> str:
    """Locate the MetaTrader 5 terminal executable on Windows.

    The function first checks an explicit user-provided path. Otherwise, it searches the
    standard installation folders used by MetaTrader 5 and returns the first valid EXE.
    """
    if custom_path:
        candidate = Path(custom_path).expanduser()
        if candidate.is_file() and candidate.suffix.lower() == ".exe":
            return str(candidate)
        raise FileNotFoundError(f"MetaTrader 5 executable not found at: {candidate}")

    for root in _windows_install_roots():
        if not root.exists():
            continue

        for candidate in root.rglob("*"):
            if candidate.is_file() and candidate.name.lower() in {item.lower() for item in CANDIDATE_EXECUTABLES}:
                return str(candidate)

        for file_name in CANDIDATE_EXECUTABLES:
            direct_path = root / file_name
            if direct_path.is_file():
                return str(direct_path)

    raise FileNotFoundError(
        "MetaTrader 5 was not found in the standard Windows installation locations. "
        "Pass --path to specify a custom installation path."
    )


def launch_mt5(executable_path: str, wait: bool = False) -> subprocess.Popen | None:
    """Launch MetaTrader 5 in a normal desktop window.

    When running on Windows, prefer `subprocess.Popen` so the process starts in a fresh,
    visible console. If that fails, fall back to `os.startfile` for a shell-initiated launch.
    """
    if os.name != "nt":
        raise OSError("This launcher is intended for Windows only.")

    executable = Path(executable_path).expanduser()
    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise FileNotFoundError(f"Not a valid executable: {executable_path}")

    try:
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        process = subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            creationflags=creationflags,
            close_fds=True,
            shell=False,
        )
        if wait:
            process.wait()
        return process
    except (OSError, ValueError):
        os.startfile(str(executable))  # type: ignore[attr-defined]
        return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the installed MetaTrader 5 terminal on Windows.")
    parser.add_argument(
        "--path",
        help="Explicit path to the MetaTrader 5 terminal executable (for example: C:\\Program Files\\MetaTrader 5\\terminal64.exe)",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Keep the Python process alive until MetaTrader 5 exits.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the executable path without launching the application.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        executable_path = find_mt5_executable(args.path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(executable_path)
        return 0

    print(f"Starting MetaTrader 5: {executable_path}")
    try:
        launch_mt5(executable_path, wait=args.wait)
    except (FileNotFoundError, OSError) as exc:
        print(f"Error: Unable to launch MetaTrader 5: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
