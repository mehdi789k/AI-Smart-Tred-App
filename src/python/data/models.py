from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class MarketDataRecord:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class MarketWatchSymbol:
    symbol: str
    visible: bool = True
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    updated_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "visible": self.visible,
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
