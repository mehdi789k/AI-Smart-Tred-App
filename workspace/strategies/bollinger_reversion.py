# -*- coding: utf-8 -*-
"""استراتژی ۵: بازگشت به میانگین با نوارهای بولینگر."""
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS


class BollingerReversion(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("bb_period", 20),
        ("bb_dev", 2.0),
        ("exit_at_middle", True),
        ("strategy_name", "Bollinger-Reversion"),
    )

    def __init__(self):
        super().__init__()
        self.bb = bt.indicators.BollingerBands(
            period=self.p.bb_period, devfactor=self.p.bb_dev)

    def _entry_logic(self):
        if not self.position:
            if self.data.close[0] < self.bb.lines.bot[0]:
                self._place_entry(+1)
            elif self.p.allow_short and self.data.close[0] > self.bb.lines.top[0]:
                self._place_entry(-1)
        elif self.p.exit_at_middle:
            mid = self.bb.lines.mid[0]
            if self.position.size > 0 and self.data.close[0] >= mid:
                self.order = self.close()
            elif self.position.size < 0 and self.data.close[0] <= mid:
                self.order = self.close()


SPEC = {
    "key": "Bollinger-Reversion",
    "title": "نوارهای بولینگر",
    "desc": "خرید با برخورد به بان پایین و خروج در خط وسط.",
    "fields": [
        {"key": "bb_period", "label": "دوره بولینگر", "type": "int", "min": 5, "max": 100, "default": 20},
        {"key": "bb_dev", "label": "انحراف معیار", "type": "float", "min": 0.5, "max": 4.0, "step": 0.1, "default": 2.0},
        {"key": "exit_at_middle", "label": "خروج در خط وسط", "type": "bool", "default": True},
    ],
}


def get_strategy_class():
    return BollingerReversion
