from __future__ import annotations

from typing import Any, Iterable

try:
    from sqlalchemy import Integer, String, DateTime, Float, create_engine
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
except Exception:  # pragma: no cover - SQLAlchemy is optional for lightweight environments.
    create_engine = None
    Session = None
    Integer = String = DateTime = Float = None

    class DeclarativeBase:  # type: ignore[misc]
        pass

    def mapped_column(*args, **kwargs):
        return None


class Base(DeclarativeBase):
    pass


class MarketOHLCV(Base):
    __tablename__ = "market_ohlcv"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    timestamp: Mapped[Any] = mapped_column(DateTime, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)


class DatabaseManager:
    """Persist candles in a SQLAlchemy database when available."""

    def __init__(self, database_url: str = "sqlite:///market_data.db") -> None:
        if create_engine is None:
            raise RuntimeError("SQLAlchemy is required to use the database manager.")
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)

    def insert_many(self, rows: Iterable[dict[str, Any]]) -> None:
        if Session is None:
            return
        with Session(self.engine) as session:
            for row in rows:
                session.add(
                    MarketOHLCV(
                        symbol=row["symbol"],
                        timeframe=row["timeframe"],
                        timestamp=row["timestamp"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row.get("volume", 0),
                    )
                )
            session.commit()
