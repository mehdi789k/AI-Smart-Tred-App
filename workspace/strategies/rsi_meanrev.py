# -*- coding: utf-8 -*-
"""استراتژی ۲: RSI بازگشت به میانگین (Mean Reversion)."""
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS


class RSIMeanReversion(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("rsi_period", 14),
        ("oversold", 30),
        ("overbought", 70),
        ("exit_level", 50),
        ("strategy_name", "RSI-MeanRev"),
    )

    def __init__(self):
        super().__init__()
        self.rsi = bt.indicators.RSI_Safe(self.data.close, period=self.p.rsi_period)

    def _entry_logic(self):
        if not self.position:
            if self.rsi[0] < self.p.oversold:
                self._place_entry(+1)
            elif self.p.allow_short and self.rsi[0] > self.p.overbought:
                self._place_entry(-1)
        else:
            # خروج وقتی RSI به سطح میانی رسید
            pos_size = self.position.size
            if pos_size > 0 and self.rsi[0] > self.p.exit_level:
                self.order = self.close()
            elif pos_size < 0 and self.rsi[0] < self.p.exit_level:
                self.order = self.close()


SPEC = {
    "key": "RSI-MeanRev",
    "title": "بازگشت به میانگین با RSI",
    "desc": "خرید در اشباع فروش و خروج نزدیک خط وسط؛ مناسب بازار رِنج.",
    "fields": [
        {"key": "rsi_period", "label": "دوره RSI", "type": "int", "min": 2, "max": 60, "default": 14},
        {"key": "oversold", "label": "آستانه اشباع فروش", "type": "int", "min": 5, "max": 45, "default": 30},
        {"key": "overbought", "label": "آستانه اشباع خرید", "type": "int", "min": 55, "max": 95, "default": 70},
        {"key": "exit_level", "label": "سطح خروج", "type": "int", "min": 40, "max": 60, "default": 50},
    ],
}


def get_strategy_class():
    return RSIMeanReversion
