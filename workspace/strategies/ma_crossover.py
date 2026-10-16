# -*- coding: utf-8 -*-
"""استراتژی ۱: تقاطع میانگین متحرک (SMA / EMA Crossover)."""
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS


class MovingAverageCrossover(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("fast_period", 10),
        ("slow_period", 30),
        ("ma_type", "SMA"),   # SMA | EMA
        ("use_volume_filter", False),
        ("volume_factor", 1.5),
        ("strategy_name", "MA-Crossover"),
    )

    def __init__(self):
        super().__init__()
        ma = bt.indicators.EMA if self.p.ma_type == "EMA" else bt.indicators.SMA
        self.fast_ma = ma(period=self.p.fast_period)
        self.slow_ma = ma(period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        self.vol_avg = bt.indicators.SMA(self.data.volume, period=20)

    def _entry_logic(self):
        vol_ok = True
        if self.p.use_volume_filter:
            vol_ok = self.data.volume[0] > self.vol_avg[0] * self.p.volume_factor
        if self.crossover > 0 and vol_ok:
            self._place_entry(+1)
        elif self.crossover < 0:
            self._place_entry(-1)


SPEC = {
    "key": "MA-Crossover",
    "title": "تقاطع میانگین متحرک",
    "desc": "خرید هنگام قطع MA سریع به بالا؛ فروش/خروج هنگام قطع به پایین.",
    "fields": [
        {"key": "fast_period", "label": "دوره MA سریع", "type": "int", "min": 2, "max": 120, "default": 10},
        {"key": "slow_period", "label": "دوره MA کند", "type": "int", "min": 5, "max": 300, "default": 30},
        {"key": "ma_type", "label": "نوع میانگین", "type": "select", "options": ["SMA", "EMA"], "default": "SMA"},
        {"key": "use_volume_filter", "label": "فیلتر حجم معاملات", "type": "bool", "default": False},
        {"key": "volume_factor", "label": "ضریب حجم (نسبت به میانگین)", "type": "float", "min": 1.0, "max": 5.0, "step": 0.1, "default": 1.5},
    ],
}


def get_strategy_class():
    return MovingAverageCrossover
