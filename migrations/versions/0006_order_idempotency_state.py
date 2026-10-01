"""Link order intents to idempotency and add unknown reconciliation state."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the durable order-state fields required by reconciliation."""

    bind = op.get_bind()
    inspector = inspect(bind)
    if "orders" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "idempotency_key" not in columns:
        op.add_column("orders", sa.Column("idempotency_key", sa.String(128)))
        op.create_index(
            "ix_orders_idempotency_key",
            "orders",
            ["idempotency_key"],
            unique=True,
        )
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'unknown'"
        )


def downgrade() -> None:
    """Remove the order/idempotency link; PostgreSQL enum values remain additive."""

    bind = op.get_bind()
    inspector = inspect(bind)
    if "orders" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("orders")}
    if "ix_orders_idempotency_key" in indexes:
        op.drop_index("ix_orders_idempotency_key", table_name="orders")
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "idempotency_key" in columns:
        op.drop_column("orders", "idempotency_key")
