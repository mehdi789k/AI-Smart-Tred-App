"""Add category columns and indexes required by derived-data queries.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Bring legacy derived-data tables to the current query contract."""

    bind = op.get_bind()
    inspector = inspect(bind)
    table_columns = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in ("indicator_calculations", "filter_evaluations")
        if table in inspector.get_table_names()
    }
    for table in ("indicator_calculations", "filter_evaluations"):
        if "category" not in table_columns.get(table, set()):
            op.add_column(
                table,
                sa.Column(
                    "category",
                    sa.String(length=64),
                    nullable=False,
                    server_default="general",
                ),
            )
    existing_indexes = {
        index["name"]
        for table in ("indicator_calculations", "filter_evaluations")
        if table in inspector.get_table_names()
        for index in inspector.get_indexes(table)
    }
    if "ix_indicator_category_time" not in existing_indexes:
        op.create_index(
            "ix_indicator_category_time",
            "indicator_calculations",
            ["symbol", "timeframe", "category", "timestamp"],
        )
    if "ix_filter_category_time" not in existing_indexes:
        op.create_index(
            "ix_filter_category_time",
            "filter_evaluations",
            ["symbol", "timeframe", "category", "timestamp"],
        )


def downgrade() -> None:
    """Remove only indexes and columns introduced by this revision."""

    bind = op.get_bind()
    inspector = inspect(bind)
    for index_name, table in (
        ("ix_filter_category_time", "filter_evaluations"),
        ("ix_indicator_category_time", "indicator_calculations"),
    ):
        if table in inspector.get_table_names() and index_name in {
            index["name"] for index in inspector.get_indexes(table)
        }:
            op.drop_index(index_name, table_name=table)
    for table in ("filter_evaluations", "indicator_calculations"):
        if table in inspector.get_table_names() and "category" in {
            column["name"] for column in inspector.get_columns(table)
        }:
            op.drop_column(table, "category")
