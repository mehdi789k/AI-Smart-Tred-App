"""Add deployment version registry.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the table used to audit schema and model versions."""

    bind = op.get_bind()
    if "system_versions" not in inspect(bind).get_table_names():
        op.create_table(
            "system_versions",
            sa.Column("component", sa.String(length=64), nullable=False),
            sa.Column("version", sa.String(length=128), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("component"),
        )


def downgrade() -> None:
    """Remove deployment version metadata."""

    if "system_versions" in inspect(op.get_bind()).get_table_names():
        op.drop_table("system_versions")
