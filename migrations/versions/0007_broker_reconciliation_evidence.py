"""Persist broker identifiers discovered during unknown-order reconciliation."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add indexed broker ticket fields without changing existing order states."""

    bind = op.get_bind()
    inspector = inspect(bind)
    if "orders" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "broker_order_id" not in columns:
        op.add_column("orders", sa.Column("broker_order_id", sa.String(128)))
        op.create_index("ix_orders_broker_order_id", "orders", ["broker_order_id"])
    if "broker_deal_id" not in columns:
        op.add_column("orders", sa.Column("broker_deal_id", sa.String(128)))
        op.create_index("ix_orders_broker_deal_id", "orders", ["broker_deal_id"])


def downgrade() -> None:
    """Remove broker evidence columns and their indexes."""

    bind = op.get_bind()
    inspector = inspect(bind)
    if "orders" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("orders")}
    if "ix_orders_broker_deal_id" in indexes:
        op.drop_index("ix_orders_broker_deal_id", table_name="orders")
    if "ix_orders_broker_order_id" in indexes:
        op.drop_index("ix_orders_broker_order_id", table_name="orders")
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "broker_deal_id" in columns:
        op.drop_column("orders", "broker_deal_id")
    if "broker_order_id" in columns:
        op.drop_column("orders", "broker_order_id")
