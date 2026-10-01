#!/usr/bin/env python3
"""Structural validation for MQL5 sources (CI-side, MetaEditor-free).

GitHub-hosted runners do not ship MetaEditor, so the authoritative MQL5
compile stays local. This script performs deterministic structural checks:

- balanced braces/parentheses after stripping comments and string literals;
- every preprocessor conditional block (#if/#ifdef/#ifndef ... #endif) is
  internally balanced in both branches;
- required event handlers exist in the Expert Advisor entry file;
- fail-closed live-trading guard remains referenced by the EA source.

Exit code 0 means the sources pass all structural checks. The script never
connects to MT5, sends orders, or mutates any file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MQL5_DIR = PROJECT_ROOT / "src" / "mql5"
EA_FILE = MQL5_DIR / "SmartTraderEA.mq5"
REQUIRED_EA_HANDLERS = ("OnInit", "OnDeinit", "OnTick")
LIVE_GUARD_PATTERN = re.compile(
    r"ExecutionPolicy|EXECUTION_POLICY_LIVE_ENABLED|live_trading|LIVE_TRADING"
)
CONDITIONAL_OPEN = re.compile(r"^\s*#(if|ifdef|ifndef)\b")
CONDITIONAL_CLOSE = re.compile(r"^\s*#endif\b")


def read_source(path: Path) -> str:
    """Decode an MQL5 source file, tolerating UTF-16 MetaEditor variants."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError(f"cannot decode {path}")


def strip_comments_and_strings(text: str) -> str:
    """Remove comments and string/char literals from a single logical line."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", text)
    return text


def check_balancers(text: str) -> list[str]:
    """Report unbalanced brace/parenthesis groups across the stripped code."""
    code = strip_comments_and_strings(text)
    problems = []
    for opener, closer in (("{", "}"), ("(", ")")):
        opened, closed = code.count(opener), code.count(closer)
        if opened != closed:
            problems.append(
                f"unbalanced '{opener}{closer}': {opened} opens vs {closed} closes"
            )
    return problems


def check_preprocessor_branches(text: str) -> list[str]:
    """Ensure each #if...#endif region is balanced within itself.

    Lines inside conditional regions are evaluated per branch: the region's
    body between #if and #else (and between #else and #endif) must each be
    independently balanced, mirroring how the MQL5 compiler sees them.
    """
    problems = []
    lines = text.splitlines()
    stack: list[int] = []
    else_lines: set[int] = set()
    for index, line in enumerate(lines):
        if CONDITIONAL_OPEN.match(line):
            stack.append(index)
        elif re.match(r"^\s*#else\b", line) and stack:
            else_lines.add(index)
        elif CONDITIONAL_CLOSE.match(line):
            if not stack:
                problems.append(f"line {index + 1}: #endif without matching #if")
                continue
            start = stack.pop()
            if stack:
                continue  # nested region; outermost check covers it later
            segments = _branch_segments(lines, start, index, else_lines)
            for label, segment in segments:
                body = "\n".join(segment)
                body = "\n".join(
                    l
                    for l in body.splitlines()
                    if not re.match(r"\s*#", l)
                )
                for problem in check_balancers(body):
                    problems.append(
                        f"region at line {start + 1} ({label}): {problem}"
                    )
    for start in stack:
        problems.append(f"line {start + 1}: #if without matching #endif")
    return problems


def _branch_segments(
    lines: list[str], start: int, end: int, else_lines: set[int]
) -> list[tuple[str, list[str]]]:
    """Split a preprocessor region into its then/else line segments."""
    body = lines[start + 1 : end]
    split_positions = [
        index
        for index, line in enumerate(body)
        if re.match(r"^\s*#else\b", line)
        and (start + 1 + index) in else_lines
    ]
    if not split_positions:
        return [("then", body)]
    boundary = split_positions[0]
    return [("then", body[:boundary]), ("else", body[boundary + 1 :])]


def validate_file(path: Path) -> list[str]:
    """Run all structural checks for one source file."""
    text = read_source(path)
    problems = check_balancers(text)
    problems.extend(check_preprocessor_branches(text))
    stripped = text.rstrip()
    last_line = stripped.splitlines()[-1] if stripped else ""
    # A trailing preprocessor directive or a macro continuation line is a
    # complete construct in MQL5; only plain-code files must end with } or ;.
    ends_complete = (
        stripped[-1:] in "};#"
        or re.match(r"^\s*#", last_line)
        or last_line.rstrip().endswith("\\")
    )
    if stripped and not ends_complete:
        problems.append(
            f"file does not end with a complete statement (found {stripped[-1:]!r})"
        )
    return problems


def validate_ea_contract() -> list[str]:
    """Check handler presence and the fail-closed live guard in the EA."""
    if not EA_FILE.is_file():
        return [f"missing Expert Advisor entry file: {EA_FILE}"]
    text = read_source(EA_FILE)
    problems = [
        f"missing required handler reference: {handler}"
        for handler in REQUIRED_EA_HANDLERS
        if handler not in text
    ]
    if not LIVE_GUARD_PATTERN.search(text):
        problems.append(
            "no live-trading guard reference found; fail-closed policy may have "
            "been removed from the EA source"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(MQL5_DIR),
        help="directory containing *.mq5 / *.mqh sources",
    )
    arguments = parser.parse_args(argv)
    root = Path(arguments.root)
    sources = sorted(root.glob("*.mq[5h]"))
    if not sources:
        print(f"[mql5-check] no MQL5 sources found under {root}")
        return 1

    failures: list[str] = []
    for path in sources:
        for problem in validate_file(path):
            failures.append(f"{path.relative_to(PROJECT_ROOT)}: {problem}")
    for problem in validate_ea_contract():
        failures.append(f"{EA_FILE.relative_to(PROJECT_ROOT)}: {problem}")

    if failures:
        print("[mql5-check] Structural validation failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"[mql5-check] Passed structural validation for {len(sources)} files. "
        "Local MetaEditor compilation remains mandatory before EA deployment."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
