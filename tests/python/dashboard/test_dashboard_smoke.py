"""Read-only Streamlit smoke tests for every dashboard navigation page."""

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_PATH = PROJECT_ROOT / "src" / "python" / "dashboard" / "app.py"


def test_all_dashboard_pages_render_without_unhandled_exceptions():
    """Every navigation page must render safely with MT5 unavailable."""
    os.environ["MT5_ENABLED"] = "false"
    os.environ["MT5_DASHBOARD_DIRECT"] = "false"

    initial = AppTest.from_file(str(DASHBOARD_PATH)).run(timeout=60)
    pages = initial.sidebar.radio[0].options

    assert pages
    for page in pages:
        app = AppTest.from_file(str(DASHBOARD_PATH)).run(timeout=60)
        app.sidebar.radio[0].set_value(page).run(timeout=60)
        assert not app.exception, f"Unhandled exception on dashboard page {page!r}"
