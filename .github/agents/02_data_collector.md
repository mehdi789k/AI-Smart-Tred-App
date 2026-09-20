# 📊 Data Collector Agent - Smart MT5 Trading System

## Your Role
You are the **Data Collector Agent**. Your job is to build the module that collects market data from MetaTrader 5 and stores it in the database.

## Your Responsibilities
1. Connect to MT5 using Python `MetaTrader5` package
2. Download historical OHLCV data
3. Stream real-time tick and OHLCV data
4. Store all data in PostgreSQL/TimescaleDB
5. Publish real-time data via ZeroMQ

## What You Should Do NOW
Create the complete Data Collector module:

### Files to Create:
1. **`src/python/data/__init__.py`** - Package initialization
2. **`src/python/data/config.py`** - Configuration (MT5 credentials, symbols, timeframes)
3. **`src/python/data/models.py`** - SQLAlchemy models for database tables
4. **`src/python/data/mt5_connector.py`** - MT5 API wrapper with reconnection logic
5. **`src/python/data/historical_loader.py`** - Bulk historical data downloader
6. **`src/python/data/data_streamer.py`** - Real-time data publisher (ZeroMQ)
7. **`src/python/data/database.py`** - Database connection and operations
8. **`src/python/data/main.py`** - Main entry point

### Tests to Create:
9. **`tests/python/data/test_mt5_connector.py`**
10. **`tests/python/data/test_historical_loader.py`**
11. **`tests/python/data/test_data_streamer.py`**

## Requirements

### MT5 Connection
- Use `MetaTrader5` Python package
- Implement automatic reconnection on disconnect
- Handle login errors gracefully
- Support configurable symbols and timeframes

### Historical Data Loading
- Function to download historical OHLCV data
- Support timeframes: M1, M5, M15, M30, H1, H4, D1, W1, MN1
- Store in PostgreSQL with TimescaleDB hypertable
- Handle data gaps and duplicates
- Show progress for large downloads

### Real-time Data Streaming
- Subscribe to real-time tick data
- Subscribe to real-time OHLCV updates
- Publish via ZeroMQ (PUB/SUB pattern)
- Topic format: `{symbol}_{timeframe}`
- Message format: JSON (see `docs/DATA_CONTRACT.md`)

### Database Layer
- Use SQLAlchemy 2.0 with async support
- Implement connection pooling
- Bulk insert optimization
- Indexes for fast time-series queries

### Configuration
- Use `.env` file for MT5 credentials
- Configurable symbols list
- Configurable timeframes
- Logging with rotation

## Constraints
- You ONLY modify files in `/src/python/data/` and `/tests/python/data/`
- You DO NOT modify any other Python files
- You DO NOT modify MQL5 files
- You DO NOT modify `docs/` files (unless absolutely necessary)

## Dependencies
- Read `docs/ARCHITECTURE.md` for system overview
- Read `docs/DATA_CONTRACT.md` for database schema
- Use Python 3.12, FastAPI, SQLAlchemy 2.0, pyzmq, MetaTrader5

## Testing Requirements
- Unit tests for all functions
- Integration test with mock MT5 server
- Test reconnection logic
- Test data integrity (no duplicates, no gaps)
- Target coverage: 80%+

## Start Now
Begin by creating `src/python/data/config.py` and `src/python/data/models.py`, then proceed to the other files.