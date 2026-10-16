from __future__ import annotations

import sys
from pathlib import Path

# داشبورد به‌صورت ماژول‌های تخت (flat) در src/python/dashboard نوشته شده است؛
# برای اینکه تست‌ها بتوانند engine/strategies را import کنند، این مسیر را
# یک‌بار به sys.path اضافه می‌کنیم.
DASHBOARD_ROOT = Path(__file__).resolve().parents[3] / "src" / "python" / "dashboard"
REPO_ROOT = Path(__file__).resolve().parents[4]

for p in (str(DASHBOARD_ROOT), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
