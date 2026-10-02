"""Small, read-only checks for the OHLCV schema and natural key.

The verifier is intentionally independent from Alembic's command runner.  It
can therefore be used against a live database (including a read-only
connection) during deployment checks without changing schema or data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import inspect, text

from .versioning import SCHEMA_VERSION

REQUIRED_OHLCV_COLUMNS = frozenset(
    {
        "symbol",
        "timeframe",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "volume",
        "source",
        "ingestion_metadata",
    }
)
NATURAL_KEY = ("symbol", "timeframe", "timestamp")
REQUIRED_EXECUTION_CONTROL_COLUMNS = frozenset(
    {
        "scope",
        "emergency_stop",
        "emergency_stop_reason",
        "demo_active",
        "session_expires_at",
        "demo_trade_count",
        "daily_loss",
        "version",
        "actor",
        "updated_at",
    }
)
EXECUTION_CONTROL_TABLE = "execution_control_state"
MIGRATION_HEAD = SCHEMA_VERSION


@dataclass(frozen=True)
class SchemaAuditReport:
    """Results of a non-destructive OHLCV schema audit."""

    table_exists: bool
    missing_columns: tuple[str, ...]
    natural_key_enforced: bool
    duplicate_rows: int
    alembic_revision: str | None
    expected_revision: str | None

    @property
    def ok(self) -> bool:
        """Whether the schema is safe for OHLCV ingestion."""

        revision_ok = (
            self.expected_revision is None
            or self.alembic_revision == self.expected_revision
        )
        return (
            self.table_exists
            and not self.missing_columns
            and self.natural_key_enforced
            and self.duplicate_rows == 0
            and revision_ok
        )

    @property
    def issues(self) -> tuple[str, ...]:
        """Human-readable problems suitable for deployment logs."""

        issues: list[str] = []
        if not self.table_exists:
            issues.append("missing table: ohlcv_data")
        if self.missing_columns:
            issues.append(f"missing OHLCV columns: {', '.join(self.missing_columns)}")
        if not self.natural_key_enforced:
            issues.append("natural key is not enforced: symbol/timeframe/timestamp")
        if self.duplicate_rows:
            issues.append(f"{self.duplicate_rows} duplicate OHLCV key groups")
        if (
            self.expected_revision is not None
            and self.alembic_revision != self.expected_revision
        ):
            issues.append(
                f"Alembic revision is {self.alembic_revision!r}; "
                f"expected {self.expected_revision!r}"
            )
        return tuple(issues)


@dataclass(frozen=True)
class RuntimeSchemaAuditReport:
    """Results of the non-destructive production schema audit."""

    migration_revision: str | None
    expected_revision: str
    execution_control_missing_columns: tuple[str, ...]
    execution_control_primary_key: tuple[str, ...]
    execution_control_schema_issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the migration head and execution-control schema are ready."""

        return (
            self.migration_revision == self.expected_revision
            and not self.execution_control_missing_columns
            and self.execution_control_primary_key == ("scope",)
            and not self.execution_control_schema_issues
        )

    @property
    def issues(self) -> tuple[str, ...]:
        """Return actionable production startup failures."""

        issues: list[str] = []
        if self.migration_revision != self.expected_revision:
            issues.append(
                f"Alembic migration head is {self.migration_revision!r}; "
                f"expected {self.expected_revision!r}"
            )
        if self.execution_control_missing_columns:
            issues.append(
                "execution-control schema is missing columns: "
                + ", ".join(self.execution_control_missing_columns)
            )
        if self.execution_control_primary_key != ("scope",):
            issues.append(
                "execution-control schema has primary key "
                f"{self.execution_control_primary_key!r}; expected ('scope',)"
            )
        issues.extend(self.execution_control_schema_issues)
        return tuple(issues)


def verify_ohlcv_schema(
    connection: Any, *, expected_revision: str | None = "0004"
) -> SchemaAuditReport:
    """Audit OHLCV columns, uniqueness, duplicates, and Alembic revision.

    ``connection`` is a synchronous SQLAlchemy connection.  No DDL or writes
    are performed.  Composite primary keys count as natural-key enforcement,
    which is how SQLite represents migration 0004.
    """

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "ohlcv_data" not in tables:
        return SchemaAuditReport(
            False,
            tuple(sorted(REQUIRED_OHLCV_COLUMNS)),
            False,
            0,
            None,
            expected_revision,
        )

    columns = {column["name"] for column in inspector.get_columns("ohlcv_data")}
    missing = tuple(sorted(REQUIRED_OHLCV_COLUMNS - columns))
    primary_key = tuple(
        inspector.get_pk_constraint("ohlcv_data").get("constrained_columns") or ()
    )
    unique_sets = [
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("ohlcv_data")
    ]
    unique_sets.extend(
        tuple(index.get("column_names") or ())
        for index in inspector.get_indexes("ohlcv_data")
        if index.get("unique")
    )
    natural_key_enforced = NATURAL_KEY == primary_key or NATURAL_KEY in unique_sets

    duplicate_rows = 0
    if not missing:
        result = connection.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "SELECT symbol, timeframe, timestamp FROM ohlcv_data "
                "GROUP BY symbol, timeframe, timestamp HAVING COUNT(*) > 1"
                ") AS duplicate_keys"
            )
        )
        duplicate_rows = int(result.scalar_one())

    revisions: list[str] = []
    if "alembic_version" in tables:
        revisions = list(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
    revision = revisions[0] if len(revisions) == 1 else None
    return SchemaAuditReport(
        True, missing, natural_key_enforced, duplicate_rows, revision, expected_revision
    )


async def audit_async_database(
    database: Any, *, expected_revision: str | None = "0004"
) -> SchemaAuditReport:
    """Run :func:`verify_ohlcv_schema` through an async SQLAlchemy engine."""

    async with database.engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: verify_ohlcv_schema(
                sync_connection, expected_revision=expected_revision
            )
        )


def verify_runtime_schema(
    connection: Any, *, expected_revision: str = MIGRATION_HEAD
) -> RuntimeSchemaAuditReport:
    """Audit the migration head and durable execution-control schema.

    This function only reflects schema and reads the current Alembic revision;
    it never creates or mutates database objects.
    """

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    revisions: list[str] = []
    if "alembic_version" in tables:
        revisions = list(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
    revision = revisions[0] if len(revisions) == 1 else None

    if EXECUTION_CONTROL_TABLE not in tables:
        missing_columns = tuple(sorted(REQUIRED_EXECUTION_CONTROL_COLUMNS))
        primary_key: tuple[str, ...] = ()
        schema_issues: tuple[str, ...] = ()
    else:
        reflected_columns = inspector.get_columns(EXECUTION_CONTROL_TABLE)
        columns = {column["name"] for column in reflected_columns}
        missing_columns = tuple(sorted(REQUIRED_EXECUTION_CONTROL_COLUMNS - columns))
        primary_key = tuple(
            inspector.get_pk_constraint(EXECUTION_CONTROL_TABLE).get(
                "constrained_columns"
            )
            or ()
        )
        expected_types: dict[
            str, tuple[type[sa.types.TypeEngine], bool, int | None]
        ] = {
            "scope": (sa.String, False, 255),
            "emergency_stop": (sa.Boolean, False, None),
            "emergency_stop_reason": (sa.Text, True, None),
            "demo_active": (sa.Boolean, False, None),
            "session_expires_at": (sa.DateTime, True, None),
            "demo_trade_count": (sa.Integer, False, None),
            "daily_loss": (sa.Numeric, False, None),
            "version": (sa.Integer, False, None),
            "actor": (sa.String, False, 128),
            "updated_at": (sa.DateTime, False, None),
        }
        schema_issue_list: list[str] = []
        for name, (expected_type, nullable, length) in expected_types.items():
            column = next(
                (item for item in reflected_columns if item["name"] == name), None
            )
            if column is None:
                continue
            if not isinstance(column["type"], expected_type):
                schema_issue_list.append(
                    f"{name} has unexpected type {column['type']!r}"
                )
            if (
                name in {"scope", "actor"}
                and getattr(column["type"], "length", None) != length
            ):
                schema_issue_list.append(f"{name} has unexpected length")
            if column["nullable"] is not nullable:
                schema_issue_list.append(f"{name} nullable={column['nullable']!r}")
        expected_checks = {
            "ck_execution_control_scope_nonempty",
            "ck_execution_control_scope_length",
            "ck_execution_control_actor_nonempty",
            "ck_execution_control_actor_length",
            "ck_execution_control_demo_trade_count_nonnegative",
            "ck_execution_control_daily_loss_nonnegative",
            "ck_execution_control_version_positive",
        }
        actual_checks = {
            check.get("name")
            for check in inspector.get_check_constraints(EXECUTION_CONTROL_TABLE)
        }
        if actual_checks != expected_checks:
            schema_issue_list.append("execution-control check constraints do not match")
        schema_issues = tuple(schema_issue_list)
    return RuntimeSchemaAuditReport(
        migration_revision=revision,
        expected_revision=expected_revision,
        execution_control_missing_columns=missing_columns,
        execution_control_primary_key=primary_key,
        execution_control_schema_issues=schema_issues,
    )


async def audit_runtime_database(
    database: Any, *, expected_revision: str = MIGRATION_HEAD
) -> RuntimeSchemaAuditReport:
    """Run :func:`verify_runtime_schema` through an async SQLAlchemy engine."""

    async with database.engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: verify_runtime_schema(
                sync_connection, expected_revision=expected_revision
            )
        )


__all__ = [
    "NATURAL_KEY",
    "REQUIRED_OHLCV_COLUMNS",
    "REQUIRED_EXECUTION_CONTROL_COLUMNS",
    "EXECUTION_CONTROL_TABLE",
    "MIGRATION_HEAD",
    "SchemaAuditReport",
    "RuntimeSchemaAuditReport",
    "audit_async_database",
    "audit_runtime_database",
    "verify_runtime_schema",
    "verify_ohlcv_schema",
]
