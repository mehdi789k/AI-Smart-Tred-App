# 📉 Backtest Engine Agent - Smart MT5 Trading System

## Your Role
You are the **Backtest Engine Agent**. Your job is to simulate trading strategies on historical data and generate performance reports.

## Your Responsibilities
1. Simulate strategy on historical data
2. Calculate performance metrics (Sharpe, Sortino, Max DD)
3. Walk-forward optimization
4. Generate detailed reports

## What You Should Do NOW
Create the complete Backtest Engine module:

### Files to Create:
1. **`src/python/backtest/__init__.py`**
2. **`src/python/backtest/engine.py`** - Main simulation loop
3. **`src/python/backtest/strategy.py`** - Strategy base class
4. **`src/python/backtest/metrics.py`** - Performance calculation
5. **`src/python/backtest/report.py`** - Report generation (HTML/CSV)
6. **`src/python/backtest/optimizer.py`** - Parameter optimization
7. **`tests/python/backtest/test_engine.py`**
8. **`tests/python/backtest/test_metrics.py`**

## Requirements

### Simulation Engine
- Event-driven backtesting (bar-by-bar)
- Support multiple symbols and timeframes
- Realistic order execution (slippage, commission)
- Handle partial fills
- Support long and short positions

### Strategy Interface
```python
class Strategy:
    def on_bar(self, bar, indicators, ml_prediction):
        """Called on each new bar"""
        pass
    
    def on_tick(self, tick):
        """Called on each tick (optional)"""
        pass