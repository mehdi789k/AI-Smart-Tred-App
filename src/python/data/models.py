"""SQLAlchemy 2.0 typed persistence models for the trading data contract.

The models intentionally keep provider-specific data in ``payload`` while
exposing the fields used by queries as typed columns.  All timestamps are
normalised to UTC by :class:`UTCDateTime`; ``JSON_PAYLOAD`` is JSONB on
PostgreSQL and JSON on SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from .config import Timeframe

try:
    from sqlalchemy import (
        JSON,
        Boolean,
        CheckConstraint,
        DateTime,
        Float,
        ForeignKey,
        Index,
        Integer,
        Numeric,
        String,
        Text,
        UniqueConstraint,
        func,
    )
    from sqlalchemy import (
        Enum as SAEnum,
    )
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
    from sqlalchemy.types import TypeDecorator

    SQLALCHEMY_AVAILABLE = True
except ImportError:  # pragma: no cover - useful for minimal MT5 installations
    SQLALCHEMY_AVAILABLE = False


class DataStatus(str, Enum):
    """Lifecycle status shared by ingested and derived records."""

    RECEIVED = "received"
    PERSISTED = "persisted"
    ERROR = "error"


class MarketDataKind(str, Enum):
    """Kinds of market observations emitted by the collector."""

    TICK = "tick"
    OHLCV = "ohlcv"


class AccountSnapshotStatus(str, Enum):
    """Account snapshot availability."""

    ACTIVE = "active"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class SignalType(str, Enum):
    """Signal direction produced by indicators and filters."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    NEUTRAL = "neutral"


class OrderStatus(str, Enum):
    """Order lifecycle status."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class PositionStatus(str, Enum):
    """Position lifecycle status."""

    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"


class TradeSide(str, Enum):
    """Trade direction."""

    BUY = "buy"
    SELL = "sell"


class LogLevel(str, Enum):
    """Persisted application log severity."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class DecisionAction(str, Enum):
    """Action selected by the strategy/AI decision layer."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"
    NO_ACTION = "no_action"


def utc_now() -> datetime:
    """Return an aware UTC timestamp suitable for application defaults."""

    return datetime.now(timezone.utc)


if SQLALCHEMY_AVAILABLE:
    JSON_PAYLOAD = JSON().with_variant(JSONB(), "postgresql")

    class UTCDateTime(TypeDecorator[datetime]):
        """Store and return timezone-aware UTC datetimes on every backend."""

        impl = DateTime
        cache_ok = True

        def load_dialect_impl(self, dialect: Any) -> Any:
            return dialect.type_descriptor(DateTime(timezone=True))

        def process_bind_param(
            self, value: datetime | None, dialect: Any
        ) -> datetime | None:
            if value is None:
                return None
            return (
                value.astimezone(timezone.utc)
                if value.tzinfo
                else value.replace(tzinfo=timezone.utc)
            )

        def process_result_value(
            self, value: datetime | None, dialect: Any
        ) -> datetime | None:
            if value is None:
                return None
            return (
                value.replace(tzinfo=timezone.utc)
                if value.tzinfo is None
                else value.astimezone(timezone.utc)
            )

    def _enum(enum_type: type[Enum], name: str) -> Any:
        return SAEnum(
            enum_type,
            name=name,
            values_callable=lambda members: [member.value for member in members],
        )

    DATA_STATUS_ENUM = _enum(DataStatus, "data_status")

    class Base(DeclarativeBase):
        """Declarative base for all collector tables."""

    class Symbol(Base):
        """Tradable instrument known to the collector."""

        __tablename__ = "symbols"

        name: Mapped[str] = mapped_column(String(64), primary_key=True)
        description: Mapped[str | None] = mapped_column(String(255))
        is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
        created_at: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, server_default=func.now(), nullable=False
        )
        updated_at: Mapped[datetime] = mapped_column(
            UTCDateTime(),
            default=utc_now,
            onupdate=utc_now,
            server_default=func.now(),
            nullable=False,
        )
        candles: Mapped[list["OHLCVBar"]] = relationship(
            back_populates="instrument", cascade="all, delete-orphan"
        )
        ticks: Mapped[list["MarketTick"]] = relationship(
            back_populates="instrument", cascade="all, delete-orphan"
        )

    class OHLCVBar(Base):
        """A time-bucketed OHLCV observation."""

        __tablename__ = "ohlcv_data"
        __table_args__ = (
            UniqueConstraint(
                "symbol",
                "timeframe",
                "timestamp",
                name="uq_ohlcv_symbol_timeframe_timestamp",
            ),
            Index("ix_ohlcv_symbol_tf_timestamp", "symbol", "timeframe", "timestamp"),
            Index("ix_ohlcv_timestamp", "timestamp"),
        )

        symbol: Mapped[str] = mapped_column(
            String(64), ForeignKey("symbols.name", ondelete="CASCADE"), primary_key=True
        )
        timeframe: Mapped[Timeframe] = mapped_column(
            _enum(Timeframe, "timeframe"), primary_key=True
        )
        timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
        open: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        high: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        low: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        close: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        tick_volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
        volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
        spread: Mapped[int | None] = mapped_column(Integer)
        status: Mapped[DataStatus] = mapped_column(
            DATA_STATUS_ENUM, default=DataStatus.PERSISTED, nullable=False
        )
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        source: Mapped[str] = mapped_column(
            String(64), default="mt5", server_default="mt5", nullable=False
        )
        ingestion_metadata: Mapped[dict[str, Any] | None] = mapped_column(
            JSON_PAYLOAD, default=dict, server_default="{}"
        )
        created_at: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, server_default=func.now(), nullable=False
        )
        instrument: Mapped[Symbol] = relationship(back_populates="candles")

        def to_payload(self) -> dict[str, Any]:
            """Return a JSON-compatible candle message body."""

            return {
                "symbol": self.symbol,
                "timeframe": self.timeframe.value
                if isinstance(self.timeframe, Timeframe)
                else str(self.timeframe),
                "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
                "open": float(self.open),
                "high": float(self.high),
                "low": float(self.low),
                "close": float(self.close),
                "tick_volume": self.tick_volume,
                "volume": self.volume,
                "spread": self.spread,
                "source": self.source,
                "ingestion_metadata": self.ingestion_metadata or {},
            }

    class MarketTick(Base):
        """A raw MT5 tick."""

        __tablename__ = "market_ticks"
        __table_args__ = (
            Index("ix_ticks_symbol_timestamp", "symbol", "timestamp"),
            Index("ix_ticks_timestamp", "timestamp"),
        )

        symbol: Mapped[str] = mapped_column(
            String(64), ForeignKey("symbols.name", ondelete="CASCADE"), primary_key=True
        )
        timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
        bid: Mapped[float] = mapped_column(
            Numeric(20, 10), primary_key=True, default=0.0
        )
        ask: Mapped[float] = mapped_column(
            Numeric(20, 10), primary_key=True, default=0.0
        )
        last: Mapped[float | None] = mapped_column(Numeric(20, 10))
        volume: Mapped[float | None] = mapped_column(Float)
        status: Mapped[DataStatus] = mapped_column(
            DATA_STATUS_ENUM, default=DataStatus.PERSISTED, nullable=False
        )
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        instrument: Mapped[Symbol] = relationship(back_populates="ticks")

    class AccountSnapshot(Base):
        """Point-in-time account equity/margin snapshot."""

        __tablename__ = "account_snapshots"
        __table_args__ = (
            Index("ix_account_snapshot_account_timestamp", "account_id", "timestamp"),
            Index("ix_account_snapshot_timestamp", "timestamp"),
        )

        account_id: Mapped[str] = mapped_column(String(128), primary_key=True)
        timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
        balance: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
        equity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
        margin: Mapped[float] = mapped_column(Numeric(20, 8), default=0, nullable=False)
        free_margin: Mapped[float] = mapped_column(
            Numeric(20, 8), default=0, nullable=False
        )
        currency: Mapped[str | None] = mapped_column(String(16))
        status: Mapped[AccountSnapshotStatus] = mapped_column(
            _enum(AccountSnapshotStatus, "account_snapshot_status"),
            default=AccountSnapshotStatus.ACTIVE,
            nullable=False,
        )
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)

    class IndicatorCalculation(Base):
        """Indicator value calculated for one candle."""

        __tablename__ = "indicator_calculations"
        __table_args__ = (
            UniqueConstraint(
                "symbol",
                "timeframe",
                "timestamp",
                "indicator_name",
                name="uq_indicator_observation",
            ),
            Index(
                "ix_indicator_symbol_tf_timestamp", "symbol", "timeframe", "timestamp"
            ),
            Index(
                "ix_indicator_category_time",
                "symbol",
                "timeframe",
                "category",
                "timestamp",
            ),
        )

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        symbol: Mapped[str] = mapped_column(String(64), nullable=False)
        timeframe: Mapped[Timeframe] = mapped_column(
            _enum(Timeframe, "indicator_timeframe")
        )
        timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
        indicator_name: Mapped[str] = mapped_column(String(128), nullable=False)
        category: Mapped[str] = mapped_column(
            String(64), default="general", server_default="general", nullable=False
        )
        value: Mapped[float | None] = mapped_column(Float)
        values: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        status: Mapped[DataStatus] = mapped_column(
            DATA_STATUS_ENUM, default=DataStatus.PERSISTED, nullable=False
        )

    class FilterEvaluation(Base):
        """Evaluation of a trading filter and its resulting signal."""

        __tablename__ = "filter_evaluations"
        __table_args__ = (
            UniqueConstraint(
                "symbol",
                "timeframe",
                "timestamp",
                "filter_name",
                name="uq_filter_observation",
            ),
            Index("ix_filter_symbol_tf_timestamp", "symbol", "timeframe", "timestamp"),
            Index(
                "ix_filter_category_time",
                "symbol",
                "timeframe",
                "category",
                "timestamp",
            ),
        )

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        symbol: Mapped[str] = mapped_column(String(64), nullable=False)
        timeframe: Mapped[Timeframe] = mapped_column(
            _enum(Timeframe, "filter_timeframe")
        )
        timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
        filter_name: Mapped[str] = mapped_column(String(128), nullable=False)
        category: Mapped[str] = mapped_column(
            String(64), default="general", server_default="general", nullable=False
        )
        passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
        signal: Mapped[SignalType] = mapped_column(
            _enum(SignalType, "signal_type"), default=SignalType.NEUTRAL, nullable=False
        )
        score: Mapped[float | None] = mapped_column(Float)
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)

    class TradingOrder(Base):
        """Order submitted to MT5 or another execution venue."""

        __tablename__ = "orders"
        __table_args__ = (
            Index("ix_orders_account_created", "account_id", "created_at"),
            Index("ix_orders_symbol_status", "symbol", "status"),
        )

        order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
        idempotency_key: Mapped[str | None] = mapped_column(
            String(128), unique=True, index=True
        )
        broker_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
        broker_deal_id: Mapped[str | None] = mapped_column(String(128), index=True)
        account_id: Mapped[str | None] = mapped_column(String(128), index=True)
        symbol: Mapped[str] = mapped_column(String(64), nullable=False)
        side: Mapped[TradeSide] = mapped_column(_enum(TradeSide, "order_side"))
        quantity: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        price: Mapped[float | None] = mapped_column(Numeric(20, 10))
        stop_loss: Mapped[float | None] = mapped_column(Numeric(20, 10))
        take_profit: Mapped[float | None] = mapped_column(Numeric(20, 10))
        status: Mapped[OrderStatus] = mapped_column(
            _enum(OrderStatus, "order_status"),
            default=OrderStatus.PENDING,
            nullable=False,
        )
        created_at: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, server_default=func.now(), nullable=False
        )
        updated_at: Mapped[datetime] = mapped_column(
            UTCDateTime(),
            default=utc_now,
            onupdate=utc_now,
            server_default=func.now(),
            nullable=False,
        )
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        executions: Mapped[list["TradeExecution"]] = relationship(
            back_populates="order", cascade="all, delete-orphan"
        )

    class OrderTransition(Base):
        """Immutable audit event for one order lifecycle transition."""

        __tablename__ = "order_transitions"
        __table_args__ = (
            Index("ix_order_transitions_order_timestamp", "order_id", "timestamp"),
        )

        id: Mapped[str] = mapped_column(
            String(128), primary_key=True, default=lambda: str(uuid4())
        )
        order_id: Mapped[str] = mapped_column(
            String(128),
            ForeignKey("orders.order_id", ondelete="CASCADE"),
            nullable=False,
        )
        from_status: Mapped[str | None] = mapped_column(String(32))
        to_status: Mapped[str] = mapped_column(String(32), nullable=False)
        actor: Mapped[str] = mapped_column(String(128), nullable=False)
        timestamp: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, nullable=False
        )
        correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)

    class PositionTransition(Base):
        """Immutable audit event for one position lifecycle transition."""

        __tablename__ = "position_transitions"
        __table_args__ = (
            Index(
                "ix_position_transitions_position_timestamp", "position_id", "timestamp"
            ),
        )

        id: Mapped[str] = mapped_column(
            String(128), primary_key=True, default=lambda: str(uuid4())
        )
        position_id: Mapped[str] = mapped_column(
            String(128),
            ForeignKey("positions.position_id", ondelete="CASCADE"),
            nullable=False,
        )
        from_status: Mapped[str | None] = mapped_column(String(32))
        to_status: Mapped[str] = mapped_column(String(32), nullable=False)
        actor: Mapped[str] = mapped_column(String(128), nullable=False)
        timestamp: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, nullable=False
        )
        correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)

    class IdempotencyRecord(Base):
        """Durable state for one write request across API restarts."""

        __tablename__ = "idempotency_records"

        key: Mapped[str] = mapped_column(String(128), primary_key=True)
        request_hash: Mapped[str | None] = mapped_column(String(64))
        status: Mapped[str] = mapped_column(
            String(32),
            default="in_progress",
            server_default="in_progress",
            nullable=False,
        )
        response: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        created_at: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, server_default=func.now(), nullable=False
        )
        completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    class ExecutionControlState(Base):
        """Durable fail-closed execution controls for one stable scope."""

        __tablename__ = "execution_control_state"
        __table_args__ = (
            CheckConstraint(
                "length(trim(scope)) > 0",
                name="ck_execution_control_scope_nonempty",
            ),
            CheckConstraint(
                "length(scope) <= 255",
                name="ck_execution_control_scope_length",
            ),
            CheckConstraint(
                "length(trim(actor)) > 0",
                name="ck_execution_control_actor_nonempty",
            ),
            CheckConstraint(
                "length(actor) <= 128",
                name="ck_execution_control_actor_length",
            ),
            CheckConstraint(
                "demo_trade_count >= 0",
                name="ck_execution_control_demo_trade_count_nonnegative",
            ),
            CheckConstraint(
                "daily_loss >= 0",
                name="ck_execution_control_daily_loss_nonnegative",
            ),
            CheckConstraint(
                "version >= 1",
                name="ck_execution_control_version_positive",
            ),
        )

        scope: Mapped[str] = mapped_column(String(255), primary_key=True)
        emergency_stop: Mapped[bool] = mapped_column(
            Boolean, default=False, server_default="false", nullable=False
        )
        emergency_stop_reason: Mapped[str | None] = mapped_column(Text)
        demo_active: Mapped[bool] = mapped_column(
            Boolean, default=False, server_default="false", nullable=False
        )
        session_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
        demo_owner_approval: Mapped[str | None] = mapped_column(String(128))
        demo_second_approval: Mapped[str | None] = mapped_column(String(128))
        demo_selected_symbols: Mapped[list[str] | None] = mapped_column(JSON_PAYLOAD)
        demo_limits: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        demo_configuration_hash: Mapped[str | None] = mapped_column(String(64))
        demo_trade_count: Mapped[int] = mapped_column(
            Integer, default=0, server_default="0", nullable=False
        )
        daily_loss: Mapped[float] = mapped_column(
            Numeric(20, 8), default=0, server_default="0", nullable=False
        )
        version: Mapped[int] = mapped_column(
            Integer, default=1, server_default="1", nullable=False
        )
        actor: Mapped[str] = mapped_column(
            String(128), default="system", server_default="system", nullable=False
        )
        updated_at: Mapped[datetime] = mapped_column(
            UTCDateTime(),
            default=utc_now,
            onupdate=utc_now,
            server_default=func.now(),
            nullable=False,
        )

    class Position(Base):
        """Current or historical account position."""

        __tablename__ = "positions"
        __table_args__ = (
            Index(
                "ix_positions_account_symbol_status", "account_id", "symbol", "status"
            ),
            Index("ix_positions_updated_at", "updated_at"),
        )

        position_id: Mapped[str] = mapped_column(String(128), primary_key=True)
        account_id: Mapped[str | None] = mapped_column(String(128), index=True)
        symbol: Mapped[str] = mapped_column(String(64), nullable=False)
        side: Mapped[TradeSide] = mapped_column(_enum(TradeSide, "position_side"))
        quantity: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        average_price: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        unrealized_pnl: Mapped[float] = mapped_column(
            Numeric(20, 8), default=0, nullable=False
        )
        status: Mapped[PositionStatus] = mapped_column(
            _enum(PositionStatus, "position_status"),
            default=PositionStatus.OPEN,
            nullable=False,
        )
        opened_at: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, nullable=False
        )
        updated_at: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False
        )
        closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        executions: Mapped[list["TradeExecution"]] = relationship(
            back_populates="position"
        )

    class TradeExecution(Base):
        """Fill/execution event linked to an order and optionally a position."""

        __tablename__ = "trade_executions"
        __table_args__ = (
            Index("ix_execution_symbol_timestamp", "symbol", "timestamp"),
            Index("ix_execution_order_timestamp", "order_id", "timestamp"),
        )

        execution_id: Mapped[str] = mapped_column(
            String(128), primary_key=True, default=lambda: str(uuid4())
        )
        order_id: Mapped[str | None] = mapped_column(
            String(128), ForeignKey("orders.order_id", ondelete="SET NULL")
        )
        position_id: Mapped[str | None] = mapped_column(
            String(128), ForeignKey("positions.position_id", ondelete="SET NULL")
        )
        symbol: Mapped[str] = mapped_column(String(64), nullable=False)
        timestamp: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, nullable=False
        )
        side: Mapped[TradeSide] = mapped_column(_enum(TradeSide, "execution_side"))
        quantity: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        price: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
        fee: Mapped[float] = mapped_column(Numeric(20, 10), default=0, nullable=False)
        realized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 10))
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        order: Mapped[TradingOrder | None] = relationship(back_populates="executions")
        position: Mapped[Position | None] = relationship(back_populates="executions")

    class ProjectLog(Base):
        """Structured project/service log retained for audit and diagnostics."""

        __tablename__ = "project_logs"
        __table_args__ = (
            Index("ix_project_logs_timestamp", "timestamp"),
            Index("ix_project_logs_level_timestamp", "level", "timestamp"),
        )

        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        timestamp: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, server_default=func.now(), nullable=False
        )
        level: Mapped[LogLevel] = mapped_column(
            _enum(LogLevel, "log_level"), default=LogLevel.INFO, nullable=False
        )
        logger_name: Mapped[str | None] = mapped_column(String(255))
        message: Mapped[str] = mapped_column(Text, nullable=False)
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)

    class AIPrediction(Base):
        """Model prediction associated with a symbol/timeframe observation."""

        __tablename__ = "ai_predictions"
        __table_args__ = (
            Index(
                "ix_prediction_symbol_tf_timestamp", "symbol", "timeframe", "timestamp"
            ),
            Index("ix_prediction_model_timestamp", "model_name", "timestamp"),
        )

        id: Mapped[str] = mapped_column(
            String(128), primary_key=True, default=lambda: str(uuid4())
        )
        model_name: Mapped[str] = mapped_column(String(128), nullable=False)
        model_version: Mapped[str | None] = mapped_column(String(64))
        symbol: Mapped[str] = mapped_column(String(64), nullable=False)
        timeframe: Mapped[Timeframe | None] = mapped_column(
            _enum(Timeframe, "prediction_timeframe")
        )
        timestamp: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, nullable=False
        )
        prediction: Mapped[float | None] = mapped_column(Float)
        label: Mapped[str | None] = mapped_column(String(128))
        confidence: Mapped[float | None] = mapped_column(Float)
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        decisions: Mapped[list["TradingDecision"]] = relationship(
            back_populates="prediction"
        )

    class TradingDecision(Base):
        """Auditable decision produced from indicators, filters, and predictions."""

        __tablename__ = "trading_decisions"
        __table_args__ = (
            Index("ix_decision_symbol_timestamp", "symbol", "timestamp"),
            Index("ix_decision_action_timestamp", "action", "timestamp"),
        )

        id: Mapped[str] = mapped_column(
            String(128), primary_key=True, default=lambda: str(uuid4())
        )
        symbol: Mapped[str] = mapped_column(String(64), nullable=False)
        timeframe: Mapped[Timeframe | None] = mapped_column(
            _enum(Timeframe, "decision_timeframe")
        )
        timestamp: Mapped[datetime] = mapped_column(
            UTCDateTime(), default=utc_now, nullable=False
        )
        action: Mapped[DecisionAction] = mapped_column(
            _enum(DecisionAction, "decision_action"), nullable=False
        )
        confidence: Mapped[float | None] = mapped_column(Float)
        reason: Mapped[str | None] = mapped_column(Text)
        prediction_id: Mapped[str | None] = mapped_column(
            String(128), ForeignKey("ai_predictions.id", ondelete="SET NULL")
        )
        payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_PAYLOAD)
        prediction: Mapped[AIPrediction | None] = relationship(
            back_populates="decisions"
        )

    class SystemVersion(Base):
        """Auditable schema and model version recorded during deployment."""

        __tablename__ = "system_versions"

        component: Mapped[str] = mapped_column(String(64), primary_key=True)
        version: Mapped[str] = mapped_column(String(128), nullable=False)
        updated_at: Mapped[datetime] = mapped_column(
            UTCDateTime(),
            default=utc_now,
            onupdate=utc_now,
            server_default=func.now(),
            nullable=False,
        )

    # Domain-friendly names used by consumers.
    Candle = OHLCVBar
    Tick = MarketTick
    OHLCV = OHLCVBar
    Order = TradingOrder
    AIPredictionRecord = AIPrediction
    Decision = TradingDecision

else:
    JSON_PAYLOAD = None
    DATA_STATUS_ENUM = None

    class Base:  # type: ignore[no-redef]
        """Placeholder that fails clearly when SQLAlchemy is unavailable."""

        metadata = None

    class _MissingModel:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("SQLAlchemy is required to instantiate data models")

    Symbol: Any = _MissingModel  # type: ignore[no-redef]
    OHLCVBar: Any = _MissingModel  # type: ignore[no-redef]
    MarketTick: Any = _MissingModel  # type: ignore[no-redef]
    AccountSnapshot: Any = _MissingModel  # type: ignore[no-redef]
    IndicatorCalculation: Any = _MissingModel  # type: ignore[no-redef]
    FilterEvaluation: Any = _MissingModel  # type: ignore[no-redef]
    TradingOrder: Any = _MissingModel  # type: ignore[no-redef]
    Position: Any = _MissingModel  # type: ignore[no-redef]
    TradeExecution: Any = _MissingModel  # type: ignore[no-redef]
    OrderTransition: Any = _MissingModel  # type: ignore[no-redef]
    PositionTransition: Any = _MissingModel  # type: ignore[no-redef]
    ProjectLog: Any = _MissingModel  # type: ignore[no-redef]
    AIPrediction: Any = _MissingModel  # type: ignore[no-redef]
    TradingDecision: Any = _MissingModel  # type: ignore[no-redef]
    ExecutionControlState: Any = _MissingModel  # type: ignore[no-redef]
    UTCDateTime: Any = object  # type: ignore[no-redef]
    Candle: Any = OHLCVBar  # type: ignore[no-redef]
    OHLCV: Any = OHLCVBar  # type: ignore[no-redef]
    Tick: Any = MarketTick  # type: ignore[no-redef]
    Order: Any = TradingOrder  # type: ignore[no-redef]
    AIPredictionRecord: Any = AIPrediction  # type: ignore[no-redef]
    Decision: Any = TradingDecision  # type: ignore[no-redef]


__all__ = [
    "AIPrediction",
    "AIPredictionRecord",
    "AccountSnapshot",
    "Base",
    "Candle",
    "DataStatus",
    "DATA_STATUS_ENUM",
    "Decision",
    "DecisionAction",
    "ExecutionControlState",
    "FilterEvaluation",
    "IndicatorCalculation",
    "LogLevel",
    "MarketDataKind",
    "MarketTick",
    "OHLCV",
    "OHLCVBar",
    "Order",
    "OrderStatus",
    "OrderTransition",
    "Position",
    "PositionStatus",
    "PositionTransition",
    "ProjectLog",
    "SQLALCHEMY_AVAILABLE",
    "SignalType",
    "Symbol",
    "Tick",
    "Timeframe",
    "TradeExecution",
    "TradeSide",
    "TradingDecision",
    "TradingOrder",
    "UTCDateTime",
    "utc_now",
]
