from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYTHON_SOURCE = PROJECT_ROOT / "src" / "python"
CANONICAL_WORKFLOW = (
    PROJECT_ROOT / "src" / "python" / "execution" / "live_order_workflow.py"
)


def test_broker_order_submission_has_one_application_entrypoint() -> None:
    """Prevent application code from bypassing the central workflow."""
    callers: list[Path] = []
    for source_file in PYTHON_SOURCE.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "order_send"
            for node in ast.walk(tree)
        ):
            callers.append(source_file)

    assert callers == [CANONICAL_WORKFLOW]
