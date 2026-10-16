# -*- coding: utf-8 -*-
"""موتور بک‌تست و اجرای استراتژی‌ها (بدون وابستگی به Streamlit — قابل تست)."""
import datetime as dt

import backtrader as bt
import pandas as pd

# منبع حقیقت استراتژی‌ها: پوشه strategies/ — هر فایل .py یک استراتژی.
# در هر فراخوانی دوباره اسکن می‌شود تا فایل‌های تازه‌آپلود/ویرایش‌شده
# بدون ری‌استارت سرور بلافاصله قابل اجرا باشند.
from strategies.base import discover_strategies, build_params


def run_backtest(df, spec_key, strategy_params, initial_cash=100_000,
                 commission=0.001, slippage_perc=0.0005, quiet=True):
    """
    اجرای یک بک‌تست کامل.

    پارامترها:
      df               : DataFrame با ستون‌های Open/High/Low/Close/Volume و ایندکس تاریخ
      spec_key         : کلید استراتژی از STRATEGY_SPECS
      strategy_params  : dict مقادیر کاربر (پارامترهای استراتژی + ریسک)
      initial_cash     : سرمایه اولیه
      commission       : کارمزد (نسبت)
      slippage_perc    : لغزش قیمت (نسبت)

    برمی‌گرداند dict شامل:
      equity (Series), returns (Series), trades (DataFrame),
      metrics (dict), final_value, df (داده ورودی)
    """
    STRATEGY_SPECS = discover_strategies()
    if spec_key not in STRATEGY_SPECS:
        raise ValueError(f"استراتژی ناشناخته: {spec_key}")

    spec = STRATEGY_SPECS[spec_key]
    strat_cls = spec["class"]

    cerebro = bt.Cerebro(stdstats=False)
    feed = bt.feeds.PandasData(dataname=df)
    cerebro.adddata(feed, name=spec_key)
    cerebro.addstrategy(strat_cls, **build_params(spec_key, strategy_params))

    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)
    if slippage_perc > 0:
        cerebro.broker.set_slippage_perc(perc=slippage_perc)

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                        riskfreerate=0.0, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.VWR, _name="vwr")

    results = cerebro.run(plot=False, quiet=quiet)
    strat = results[0]

    # ---- منحنی ارزش پرتفو ----
    if strat.bar_times and strat.equity_curve:
        equity = pd.Series(strat.equity_curve,
                           index=pd.DatetimeIndex(strat.bar_times))
    else:
        equity = pd.Series([initial_cash], index=[df.index[0]])
    returns = equity.pct_change().fillna(0.0)

    # ---- متریک‌ها ----
    ta = strat.analyzers.trades.get_analysis()
    total_closed = ta.get("total", {}).get("closed", 0)
    won = ta.get("won", {}).get("total", 0)
    lost = ta.get("lost", {}).get("total", 0)
    avg_win = ta.get("won", {}).get("pnl", {}).get("average", 0.0) or 0.0
    avg_loss = abs(ta.get("lost", {}).get("pnl", {}).get("average", 0.0) or 0.0)
    dd = strat.analyzers.dd.get_analysis()
    sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio")
    vwr = strat.analyzers.vwr.get_analysis().get("vwr", None)

    final_value = cerebro.broker.getvalue()
    metrics = {
        "final_value": final_value,
        "net_pnl": final_value - initial_cash,
        "total_return_pct": (final_value / initial_cash - 1) * 100,
        "cagr_pct": strat.analyzers.returns.get_analysis().get("rnorm100", 0.0),
        "sharpe": sharpe if sharpe is not None else float("nan"),
        "max_drawdown_pct": dd.get("max", {}).get("drawdown", 0.0),
        "total_trades": total_closed,
        "won": won,
        "lost": lost,
        "win_rate_pct": (won / total_closed * 100) if total_closed else 0.0,
        "profit_factor": (avg_win * won / (avg_loss * lost))
                          if (lost and avg_loss) else float("inf") if won else 0.0,
        "vwr": vwr if vwr is not None else float("nan"),
    }

    trades = pd.DataFrame(strat.trade_log)
    return dict(equity=equity, returns=returns, trades=trades,
                metrics=metrics, df=df, strategy=strat)


def optimize_strategy(df, spec_key, base_params, param_grid,
                      initial_cash=100_000, commission=0.001,
                      slippage_perc=0.0005, max_combos=200):
    """
    جستجوی شبکه‌ای روی پارامترها (Grid Search) و برگرداندن DataFrame نتایج.
    param_grid مثال: {"fast_period": [5,10,20], "slow_period": [30,50,80]}
    """
    import itertools
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))
    if len(combos) > max_combos:
        combos = combos[:max_combos]

    rows = []
    for combo in combos:
        params = dict(base_params)
        params.update(dict(zip(keys, combo)))
        try:
            res = run_backtest(df, spec_key, params, initial_cash,
                               commission, slippage_perc)
            row = dict(zip(keys, combo))
            m = res["metrics"]
            row.update({
                "net_pnl": m["net_pnl"],
                "total_return_pct": m["total_return_pct"],
                "sharpe": m["sharpe"],
                "max_dd_pct": m["max_drawdown_pct"],
                "trades": m["total_trades"],
                "win_rate_pct": m["win_rate_pct"],
            })
            rows.append(row)
        except Exception as e:
            row = dict(zip(keys, combo))
            row["error"] = str(e)
            rows.append(row)
    out = pd.DataFrame(rows)
    if "sharpe" in out.columns:
        out = out.sort_values("sharpe", ascending=False)
    return out
