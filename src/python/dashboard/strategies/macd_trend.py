# -*- coding: utf-8 -*-
"""استراتژی ۴: MACD هم‌جه با روند."""
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS


class MacdTrend(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("macd_fast", 12),
        ("macd_slow", 26),
        ("macd_signal", 9),
        ("trend_period", 100),
        ("strategy_name", "MACD-Trend"),
    )

    def __init__(self):
        super().__init__()
        self.macd = bt.indicators.MACD(
            self.data.close,
            period_me1=self.p.macd_fast,
            period_me2=self.p.macd_slow,
            period_signal=self.p.macd_signal)
        self.cross = bt.indicators.CrossOver(self.macd.macd, self.macd.signal)
        self.trend = bt.indicators.EMA(self.data.close, period=self.p.trend_period)

    def _entry_logic(self):
        uptrend = self.data.close[0] > self.trend[0]
        if self.cross > 0 and uptrend:
            self._place_entry(+1)
        elif self.cross < 0:
            self._place_entry(-1)


SPEC = {
    "key": "MACD-Trend",
    "title": "MACD هم‌جه با روند",
    "desc": "تنها وقتی وارد شو که MACD سیگنال داد و قیمت بالای EMA روند باشد.",
    "fields": [
        {"key": "macd_fast", "label": "MACD سریع", "type": "int", "min": 2, "max": 50, "default": 12},
        {"key": "macd_slow", "label": "MACD کند", "type": "int", "min": 5, "max": 100, "default": 26},
        {"key": "macd_signal", "label": "دوره سیگنال", "type": "int", "min": 2, "max": 30, "default": 9},
        {"key": "trend_period", "label": "دوره EMA روند", "type": "int", "min": 20, "max": 300, "default": 100},
    ],
}


def get_strategy_class():
    return MacdTrend
