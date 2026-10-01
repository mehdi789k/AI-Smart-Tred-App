"""Stabilize the OHLCV natural key and ingestion provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "ohlcv_data" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("ohlcv_data")}
    if "source" not in columns:
        op.add_column(
            "ohlcv_data",
            sa.Column("source", sa.String(length=64), nullable=False, server_default="mt5"),
        )
    if "ingestion_metadata" not in columns:
        op.add_column(
            "ohlcv_data",
            sa.Column(
                "ingestion_metadata", sa.JSON(), nullable=True, server_default="{}"
            ),
        )
    constraints = {
        item["name"] for item in inspector.get_unique_constraints("ohlcv_data")
    }
    primary_key = inspector.get_pk_constraint("ohlcv_data")
    primary_key_columns = tuple(primary_key.get("constrained_columns") or ())
    natural_key = ("symbol", "timeframe", "timestamp")
    # SQLite represents the same natural key as the composite primary key and
    # cannot add named constraints without rebuilding the table.  The model
    # still declares the named constraint for PostgreSQL/TimescaleDB.
    if (
        "uq_ohlcv_symbol_timeframe_timestamp" not in constraints
        and set(primary_key_columns) != set(natural_key)
        and bind.dialect.name != "sqlite"
    ):
        op.create_unique_constraint(
            "uq_ohlcv_symbol_timeframe_timestamp",
            "ohlcv_data",
            ["symbol", "timeframe", "timestamp"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "ohlcv_data" not in inspector.get_table_names():
        return
    constraints = {item["name"] for item in inspector.get_unique_constraints("ohlcv_data")}
    if (
        "uq_ohlcv_symbol_timeframe_timestamp" in constraints
        and bind.dialect.name != "sqlite"
    ):
        op.drop_constraint(
            "uq_ohlcv_symbol_timeframe_timestamp", "ohlcv_data", type_="unique"
        )
    columns = {column["name"] for column in inspector.get_columns("ohlcv_data")}
    if "ingestion_metadata" in columns:
        op.drop_column("ohlcv_data", "ingestion_metadata")
    if "source" in columns:
        op.drop_column("ohlcv_data", "source")
