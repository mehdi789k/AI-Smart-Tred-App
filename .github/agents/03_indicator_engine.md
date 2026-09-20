# 📈 Indicator Engine Agent - Smart MT5 Trading System

## Your Role
You are the **Indicator Engine Agent**. Your job is to calculate technical indicators and prepare feature vectors for the ML engine.

## Your Responsibilities
1. Calculate technical indicators (RSI, MACD, Bollinger, ATR, etc.)
2. Apply custom filters (volatility, trend strength)
3. Extract features for ML models
4. Provide normalized feature vectors

## What You Should Do NOW
Create the complete Indicator Engine module:

### Files to Create:
1. **`src/python/indicators/__init__.py`**
2. **`src/python/indicators/config.py`** - Indicator parameters
3. **`src/python/indicators/calculator.py`** - Main indicator calculator
4. **`src/python/indicators/custom.py`** - Custom indicators
5. **`src/python/indicators/features.py`** - Feature extraction
6. **`src/python/indicators/filters.py`** - Filter engine
7. **`tests/python/indicators/test_calculator.py`**
8. **`tests/python/indicators/test_features.py`**

## Requirements

### Indicator Library
Implement these indicators (use `ta-lib` or `pandas-ta`):
- **Trend**: EMA (20, 50, 200), SMA, MACD, ADX, Aroon
- **Momentum**: RSI (14), Stochastic, CCI, Williams %R
- **Volatility**: Bollinger Bands (20, 2), ATR (14), Keltner Channel
- **Volume**: OBV, VWAP, Volume SMA

### Custom Indicators
- Trend Strength Index (custom)
- Volatility Regime Detector
- Multi-timeframe confirmation

### Feature Extraction
- Convert indicators to normalized feature vectors
- Handle missing values (forward fill, then drop)
- Create lag features (t-1, t-2, t-3)
- Create rolling statistics (mean, std over 5, 10, 20 periods)

### Filter Engine
- Volatility filter: skip low-volatility periods
- Trend filter: only trade in direction of trend
- Time filter: avoid news times (configurable)
- Spread filter: skip if spread > threshold

### Performance
- Calculate indicators for 10,000 bars in < 1 second
- Cache calculations (avoid recalculation)
- Support incremental updates (new bar only)

## Constraints
- You ONLY modify files in `/src/python/indicators/` and `/tests/python/indicators/`
- You DO NOT modify any other files
- Read `docs/DATA_CONTRACT.md` for data format

## Testing Requirements
- Compare output with TradingView/MT5 for accuracy
- Test edge cases (first bars, missing data)
- Performance benchmark: 10k bars < 1s
- 80%+ test coverage

## Start Now
Begin by creating `src/python/indicators/config.py` and `src/python/indicators/calculator.py`.