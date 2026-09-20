"""Create the historical trading data schema for revision 0001.

Revision ID: 0001
Revises:
"""

from alembic import op

from src.python.data.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Keep revision 0001 independent from tables introduced by later revisions.
_INITIAL_TABLES = (
    "symbols",
    "ohlcv_data",
    "market_ticks",
    "account_snapshots",
    "indicator_calculations",
    "filter_evaluations",
    "orders",
    "idempotency_records",
    "positions",
    "trade_executions",
    "project_logs",
    "ai_predictions",
    "trading_decisions",
)


def upgrade() -> None:
    """Create only tables that existed before the versioned follow-up revisions."""

    bind = op.get_bind()
    for table_name in _INITIAL_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    """Remove only the tables owned by the initial revision."""

    bind = op.get_bind()
    for table_name in reversed(_INITIAL_TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
