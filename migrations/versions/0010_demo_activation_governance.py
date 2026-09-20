"""Add durable two-person Demo activation governance fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist the approvals and immutable activation scope."""
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("execution_control_state")
    }
    columns = (
        sa.Column("demo_owner_approval", sa.String(length=128), nullable=True),
        sa.Column("demo_second_approval", sa.String(length=128), nullable=True),
        sa.Column("demo_selected_symbols", sa.JSON(), nullable=True),
        sa.Column("demo_limits", sa.JSON(), nullable=True),
        sa.Column("demo_configuration_hash", sa.String(length=64), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("execution_control_state", column)


def downgrade() -> None:
    """Remove Demo activation governance fields."""
    op.drop_column("execution_control_state", "demo_configuration_hash")
    op.drop_column("execution_control_state", "demo_limits")
    op.drop_column("execution_control_state", "demo_selected_symbols")
    op.drop_column("execution_control_state", "demo_second_approval")
    op.drop_column("execution_control_state", "demo_owner_approval")
