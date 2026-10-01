from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = PROJECT_ROOT / "src"
WORKFLOW_PATH = SOURCE_ROOT / "python" / "execution" / "live_order_workflow.py"


def _direct_order_send_files() -> set[Path]:
    offenders: set[Path] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "order_send"
            for node in ast.walk(tree)
        ):
            offenders.add(path)
    return offenders


def test_live_order_workflow_is_the_only_direct_mt5_submission_boundary():
    assert _direct_order_send_files() == {WORKFLOW_PATH}


def test_legacy_open_order_path_is_fail_closed():
    legacy = (SOURCE_ROOT / "python" / "mt5_account" / "mt5_trade_orders.py").read_text(
        encoding="utf-8"
    )
    assert "Direct MT5 order submission was removed" in legacy
