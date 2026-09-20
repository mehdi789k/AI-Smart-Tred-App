"""Add immutable order and position lifecycle audit events."""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create transition tables used by the state-machine audit trail."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "order_transitions" not in inspector.get_table_names():
        op.create_table(
            "order_transitions",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("order_id", sa.String(128), nullable=False),
            sa.Column("from_status", sa.String(32)),
            sa.Column("to_status", sa.String(32), nullable=False),
            sa.Column("actor", sa.String(128), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("correlation_id", sa.String(128), nullable=False),
            sa.Column("payload", sa.JSON()),
            sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_order_transitions_order_timestamp",
            "order_transitions",
            ["order_id", "timestamp"],
        )
    if "position_transitions" not in inspector.get_table_names():
        op.create_table(
            "position_transitions",
            sa.Column("id", sa.String(128), primary_key=True),
            sa.Column("position_id", sa.String(128), nullable=False),
            sa.Column("from_status", sa.String(32)),
            sa.Column("to_status", sa.String(32), nullable=False),
            sa.Column("actor", sa.String(128), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("correlation_id", sa.String(128), nullable=False),
            sa.Column("payload", sa.JSON()),
            sa.ForeignKeyConstraint(["position_id"], ["positions.position_id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_position_transitions_position_timestamp",
            "position_transitions",
            ["position_id", "timestamp"],
        )


def downgrade() -> None:
    """Remove lifecycle audit tables."""

    op.drop_index(
        "ix_position_transitions_position_timestamp", table_name="position_transitions"
    )
    op.drop_table("position_transitions")
    op.drop_index("ix_order_transitions_order_timestamp", table_name="order_transitions")
    op.drop_table("order_transitions")
