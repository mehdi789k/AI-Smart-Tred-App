"""Create and verify durable execution safety control state."""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_TABLE_NAME = "execution_control_state"
_MARKER_TABLE = "_migration_0009_execution_control_state"


def _normalize_sql(value: object) -> str | None:
    """Normalize reflected SQL expressions for backend-neutral comparison."""

    if value is None:
        return None
    normalized = " ".join(str(value).strip().lower().split())
    normalized = re.sub(r"::[a-z ]+", "", normalized)
    normalized = normalized.replace("trim(both from ", "trim(")
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    return normalized.strip("'").strip('"')


def _column_type_matches(
    column_type: sa.types.TypeEngine,
    kind: str,
    *,
    length: int | None = None,
    precision: int | None = None,
    scale: int | None = None,
    dialect_name: str | None = None,
) -> bool:
    """Compare reflected types without relying on dialect-specific reprs."""

    if kind == "string":
        return (
            isinstance(column_type, sa.String)
            and not isinstance(column_type, sa.Text)
            and column_type.length == length
        )
    if kind == "text":
        return isinstance(column_type, sa.Text)
    if kind == "boolean":
        return isinstance(column_type, sa.Boolean)
    if kind == "datetime":
        if not isinstance(column_type, sa.DateTime):
            return False
        return dialect_name != "postgresql" or column_type.timezone is True
    if kind == "integer":
        return isinstance(column_type, sa.Integer)
    if kind == "numeric":
        return (
            isinstance(column_type, sa.Numeric)
            and column_type.precision == precision
            and column_type.scale == scale
        )
    return False


def _schema_mismatches(bind: sa.engine.Connection) -> list[str]:
    """Return all execution-control schema differences found by reflection."""

    inspector = inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns(_TABLE_NAME)}
    expected = {
        "scope": ("string", False, None, 255, None, None),
        "emergency_stop": ("boolean", False, "false", None, None, None),
        "emergency_stop_reason": ("text", True, None, None, None, None),
        "demo_active": ("boolean", False, "false", None, None, None),
        "session_expires_at": ("datetime", True, None, None, None, None),
        "demo_trade_count": ("integer", False, "0", None, None, None),
        "daily_loss": ("numeric", False, "0", None, 20, 8),
        "version": ("integer", False, "1", None, None, None),
        "actor": ("string", False, "system", 128, None, None),
        "updated_at": ("datetime", False, "current_timestamp", None, None, None),
    }
    mismatches: list[str] = []
    if not set(expected).issubset(columns):
        mismatches.append(
            f"columns expected at least {sorted(expected)}, found {sorted(columns)}"
        )
    for name, (kind, nullable, default, length, precision, scale) in expected.items():
        column = columns.get(name)
        if column is None:
            continue
        if not _column_type_matches(
            column["type"],
            kind,
            length=length,
            precision=precision,
            scale=scale,
            dialect_name=bind.dialect.name,
        ):
            mismatches.append(f"{name} has unexpected type {column['type']!r}")
        if column["nullable"] is not nullable:
            mismatches.append(f"{name} nullable={column['nullable']!r}")
        if _normalize_sql(column.get("default")) != default:
            mismatches.append(
                f"{name} default={column.get('default')!r}, expected {default!r}"
            )

    primary_key = inspector.get_pk_constraint(_TABLE_NAME).get(
        "constrained_columns", []
    )
    if primary_key != ["scope"]:
        mismatches.append(f"primary key={primary_key!r}, expected ['scope']")

    expected_checks = {
        "ck_execution_control_scope_nonempty": "length(trim(scope)) > 0",
        "ck_execution_control_scope_length": "length(scope) <= 255",
        "ck_execution_control_actor_nonempty": "length(trim(actor)) > 0",
        "ck_execution_control_actor_length": "length(actor) <= 128",
        "ck_execution_control_demo_trade_count_nonnegative": "demo_trade_count >= 0",
        "ck_execution_control_daily_loss_nonnegative": "daily_loss >= 0",
        "ck_execution_control_version_positive": "version >= 1",
    }
    actual_checks = {
        check.get("name"): _normalize_sql(check.get("sqltext"))
        for check in inspector.get_check_constraints(_TABLE_NAME)
    }
    if set(actual_checks) != set(expected_checks):
        mismatches.append(
            f"checks expected {sorted(expected_checks)}, found {sorted(actual_checks)}"
        )
    for name, expression in expected_checks.items():
        if actual_checks.get(name) != expression:
            mismatches.append(
                f"check {name}={actual_checks.get(name)!r}, expected {expression!r}"
            )
    return mismatches


def _marker_schema_mismatches(bind: sa.engine.Connection) -> list[str]:
    """Return provenance-marker schema differences found by reflection."""

    inspector = inspect(bind)
    columns = {
        column["name"]: column for column in inspector.get_columns(_MARKER_TABLE)
    }
    expected = {
        "revision": ("string", False, 32),
        "owns_table": ("boolean", False, None),
        "owns_marker": ("boolean", False, None),
    }
    mismatches: list[str] = []
    if set(columns) != set(expected):
        mismatches.append(
            f"columns expected {sorted(expected)}, found {sorted(columns)}"
        )
    for name, (kind, nullable, length) in expected.items():
        column = columns.get(name)
        if column is None:
            continue
        if not _column_type_matches(
            column["type"], kind, length=length, dialect_name=bind.dialect.name
        ):
            mismatches.append(f"{name} has unexpected type {column['type']!r}")
        if name != "revision" and column["nullable"] is not nullable:
            mismatches.append(f"{name} nullable={column['nullable']!r}")

    primary_key = inspector.get_pk_constraint(_MARKER_TABLE).get(
        "constrained_columns", []
    )
    if primary_key != ["revision"]:
        mismatches.append(f"primary key={primary_key!r}, expected ['revision']")
    return mismatches


def _ensure_marker_table(bind: sa.engine.Connection) -> bool:
    """Create or validate the private provenance table and report ownership."""

    if _MARKER_TABLE in inspect(bind).get_table_names():
        mismatches = _marker_schema_mismatches(bind)
        if mismatches:
            raise RuntimeError("marker schema mismatch: " + "; ".join(mismatches))
        return False

    op.create_table(
        _MARKER_TABLE,
        sa.Column("revision", sa.String(32), primary_key=True),
        sa.Column("owns_table", sa.Boolean(), nullable=False),
        sa.Column("owns_marker", sa.Boolean(), nullable=False),
    )
    return True


def _record_provenance(
    bind: sa.engine.Connection, owns_table: bool, owns_marker: bool
) -> None:
    """Persist ownership without trusting pre-existing provenance rows."""

    marker = sa.table(
        _MARKER_TABLE,
        sa.column("revision", sa.String(32)),
        sa.column("owns_table", sa.Boolean()),
        sa.column("owns_marker", sa.Boolean()),
    )
    existing = bind.execute(
        sa.select(marker.c.owns_table, marker.c.owns_marker).where(
            marker.c.revision == revision
        )
    ).one_or_none()
    if existing is not None:
        raise RuntimeError("marker ownership cannot be proven for revision 0009")
    bind.execute(
        sa.insert(marker).values(
            revision=revision, owns_table=owns_table, owns_marker=owns_marker
        )
    )


def upgrade() -> None:
    """Create or verify the versioned execution-control schema."""

    bind = op.get_bind()
    table_exists = _TABLE_NAME in inspect(bind).get_table_names()
    created = False
    if not table_exists:
        op.create_table(
            _TABLE_NAME,
            sa.Column("scope", sa.String(255), primary_key=True),
            sa.Column(
                "emergency_stop",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
            sa.Column("emergency_stop_reason", sa.Text()),
            sa.Column(
                "demo_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
            sa.Column("session_expires_at", sa.DateTime(timezone=True)),
            sa.Column(
                "demo_trade_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "daily_loss",
                sa.Numeric(20, 8),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.Column(
                "actor",
                sa.String(128),
                nullable=False,
                server_default=sa.text("'system'"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "length(trim(scope)) > 0",
                name="ck_execution_control_scope_nonempty",
            ),
            sa.CheckConstraint(
                "length(scope) <= 255",
                name="ck_execution_control_scope_length",
            ),
            sa.CheckConstraint(
                "length(trim(actor)) > 0",
                name="ck_execution_control_actor_nonempty",
            ),
            sa.CheckConstraint(
                "length(actor) <= 128",
                name="ck_execution_control_actor_length",
            ),
            sa.CheckConstraint(
                "demo_trade_count >= 0",
                name="ck_execution_control_demo_trade_count_nonnegative",
            ),
            sa.CheckConstraint(
                "daily_loss >= 0",
                name="ck_execution_control_daily_loss_nonnegative",
            ),
            sa.CheckConstraint(
                "version >= 1",
                name="ck_execution_control_version_positive",
            ),
        )
        created = True

    mismatches = _schema_mismatches(bind)
    if mismatches:
        raise RuntimeError(
            "execution_control_state schema mismatch: " + "; ".join(mismatches)
        )
    marker_created = _ensure_marker_table(bind)
    _record_provenance(bind, created, marker_created)


def downgrade() -> None:
    """Remove only execution-control state and marker tables owned by 0009."""

    bind = op.get_bind()
    if _MARKER_TABLE not in inspect(bind).get_table_names():
        return
    mismatches = _marker_schema_mismatches(bind)
    if mismatches:
        raise RuntimeError("marker schema mismatch: " + "; ".join(mismatches))

    marker = sa.table(
        _MARKER_TABLE,
        sa.column("revision", sa.String(32)),
        sa.column("owns_table", sa.Boolean()),
        sa.column("owns_marker", sa.Boolean()),
    )
    ownership = bind.execute(
        sa.select(marker.c.owns_table, marker.c.owns_marker).where(
            marker.c.revision == revision
        )
    ).one_or_none()
    if ownership is None:
        raise RuntimeError("marker ownership cannot be proven for revision 0009")

    owns_table, owns_marker = ownership
    if owns_table and _TABLE_NAME in inspect(bind).get_table_names():
        op.drop_table(_TABLE_NAME)
    if owns_marker:
        op.drop_table(_MARKER_TABLE)
