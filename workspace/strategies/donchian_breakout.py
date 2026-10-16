# -*- coding: utf-8 -*-
"""استراتژی ۳: شکست کانال دونچیان (Breakout)."""
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS


class DonchianBreakout(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("entry_period", 20),
        ("exit_period", 10),
        ("atr_mult", 2.0),      # فاصله حد ضرر بر حسب ATR
        ("strategy_name", "Donchian-Breakout"),
    )

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.entry_period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.entry_period)
        self.exit_low = bt.indicators.Lowest(self.data.low, period=self.p.exit_period)
        self.exit_high = bt.indicators.Highest(self.data.high, period=self.p.exit_period)
        self.atr = bt.indicators.ATR(period=14)

    def _entry_logic(self):
        if self.position:
            # خروج با کانال کوتاه‌مدت
            if self.position.size > 0 and self.data.close[0] < self.exit_low[-1]:
                self.order = self.close()
            elif self.position.size < 0 and self.data.close[0] > self.exit_high[-1]:
                self.order = self.close()
            return
        if self.data.close[0] > self.highest[-1]:
            self._place_entry(+1)
        elif self.p.allow_short and self.data.close[0] < self.lowest[-1]:
            self._place_entry(-1)


SPEC = {
    "key": "Donchian-Breakout",
    "title": "شکست کانال دونچیان",
    "desc": "ورود با شکست سقف N روز و خروج با کانال کوتاه‌تر (روند-دوست).",
    "fields": [
        {"key": "entry_period", "label": "دوره کانال ورود", "type": "int", "min": 5, "max": 120, "default": 20},
        {"key": "exit_period", "label": "دوره کانال خروج", "type": "int", "min": 2, "max": 60, "default": 10},
        {"key": "atr_mult", "label": "ضریب ATR برای حد ضرر", "type": "float", "min": 0.5, "max": 6.0, "step": 0.1, "default": 2.0},
    ],
}


def get_strategy_class():
    return DonchianBreakout
