"""Add request fingerprints and terminal states for durable idempotency."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make idempotency claims durable and distinguish pending from terminal."""

    bind = op.get_bind()
    inspector = inspect(bind)
    if "idempotency_records" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("idempotency_records")}
    if "request_hash" not in columns:
        op.add_column("idempotency_records", sa.Column("request_hash", sa.String(64)))
    if "status" not in columns:
        op.add_column(
            "idempotency_records",
            sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        )


def downgrade() -> None:
    """Remove only the columns introduced by this revision."""

    bind = op.get_bind()
    inspector = inspect(bind)
    if "idempotency_records" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("idempotency_records")}
    if "status" in columns:
        op.drop_column("idempotency_records", "status")
    if "request_hash" in columns:
        op.drop_column("idempotency_records", "request_hash")
