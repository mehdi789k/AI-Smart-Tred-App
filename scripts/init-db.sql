-- اسکریپت راه‌اندازی اولیه دیتابیس TimescaleDB
-- ایجاد جداول hypertable برای داده‌های سری زمانی

-- فعال کردن افزونه TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- جدول داده‌های بازار (OHLCV)
CREATE TABLE IF NOT EXISTS market_data (
    time TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT NOT NULL,
    spread DOUBLE PRECISION DEFAULT 0
);

-- تبدیل به hypertable
SELECT create_hypertable('market_data', 'time', if_not_exists => TRUE);

-- ایندکس‌ها
CREATE INDEX IF NOT EXISTS idx_market_data_symbol_time 
ON market_data (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_symbol_timeframe 
ON market_data (symbol, timeframe);

-- جدول سیگنال‌های تولیدشده
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    signal_type VARCHAR(10) NOT NULL CHECK (signal_type IN ('BUY', 'SELL', 'CLOSE')),
    price DOUBLE PRECISION NOT NULL,
    score DOUBLE PRECISION DEFAULT 0,
    ml_prediction INTEGER,
    confidence DOUBLE PRECISION DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- ایندکس برای سیگنال‌ها
CREATE INDEX IF NOT EXISTS idx_signals_time 
ON signals (time DESC);

CREATE INDEX IF NOT EXISTS idx_signals_symbol 
ON signals (symbol);

-- جدول معاملات اجراشده
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    ticket BIGINT NOT NULL,
    time_open TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    time_close TIMESTAMPTZ,
    symbol VARCHAR(20) NOT NULL,
    type VARCHAR(10) NOT NULL CHECK (type IN ('BUY', 'SELL')),
    volume DOUBLE PRECISION NOT NULL,
    open_price DOUBLE PRECISION NOT NULL,
    close_price DOUBLE PRECISION,
    sl DOUBLE PRECISION,
    tp DOUBLE PRECISION,
    commission DOUBLE PRECISION DEFAULT 0,
    swap DOUBLE PRECISION DEFAULT 0,
    profit DOUBLE PRECISION DEFAULT 0,
    status VARCHAR(20) DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'PENDING', 'CANCELLED')),
    strategy_name VARCHAR(50),
    signal_id INTEGER REFERENCES signals(id),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- ایندکس برای معاملات
CREATE INDEX IF NOT EXISTS idx_trades_ticket 
ON trades (ticket);

CREATE INDEX IF NOT EXISTS idx_trades_time_open 
ON trades (time_open DESC);

CREATE INDEX IF NOT EXISTS idx_trades_status 
ON trades (status);

-- جدول پیش‌بینی‌های هوش مصنوعی
CREATE TABLE IF NOT EXISTS ml_predictions (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    model_name VARCHAR(50) NOT NULL,
    prediction INTEGER NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    features JSONB DEFAULT '{}'::jsonb,
    model_version VARCHAR(20)
);

-- ایندکس برای پیش‌بینی‌ها
CREATE INDEX IF NOT EXISTS idx_ml_predictions_time 
ON ml_predictions (time DESC);

CREATE INDEX IF NOT EXISTS idx_ml_predictions_symbol 
ON ml_predictions (symbol);

-- جدول لاگ عملکرد مدل‌های ML
CREATE TABLE IF NOT EXISTS ml_performance_log (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_name VARCHAR(50) NOT NULL,
    model_version VARCHAR(20),
    accuracy DOUBLE PRECISION,
    precision DOUBLE PRECISION,
    recall DOUBLE PRECISION,
    f1_score DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    total_trades INTEGER,
    training_samples INTEGER,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- ایندکس برای لاگ عملکرد
CREATE INDEX IF NOT EXISTS idx_ml_performance_time 
ON ml_performance_log (time DESC);

CREATE INDEX IF NOT EXISTS idx_ml_performance_model 
ON ml_performance_log (model_name);

-- جدول تنظیمات استراتژی
CREATE TABLE IF NOT EXISTS strategy_configs (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(50) UNIQUE NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- جدول ریسک و محدودیت‌ها
CREATE TABLE IF NOT EXISTS risk_settings (
    id SERIAL PRIMARY KEY,
    setting_name VARCHAR(50) UNIQUE NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- درج تنظیمات پیش‌فرض ریسک
INSERT INTO risk_settings (setting_name, value, description) VALUES
    ('risk_per_trade', 0.01, 'ریسک هر معامله به درصد سرمایه'),
    ('max_positions', 3, 'حداکثر پوزیشن همزمان'),
    ('daily_loss_limit', 0.03, 'حد ضرر روزانه به درصد'),
    ('max_drawdown', 0.10, 'حداکثر دراودان مجاز'),
    ('min_score_threshold', 0.6, 'حداقل امتیاز سیگنال برای اجرا')
ON CONFLICT (setting_name) DO NOTHING;

-- ویو برای خلاصه عملکرد روزانه
CREATE OR REPLACE VIEW daily_performance AS
SELECT 
    DATE(time_open) as trade_date,
    COUNT(*) as total_trades,
    SUM(profit) as total_profit,
    AVG(profit) as avg_profit,
    SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as winning_trades,
    SUM(CASE WHEN profit <= 0 THEN 1 ELSE 0 END) as losing_trades,
    CASE 
        WHEN SUM(CASE WHEN profit <= 0 THEN 1 ELSE 0 END) = 0 THEN SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)::DOUBLE PRECISION
        ELSE SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)::DOUBLE PRECISION / SUM(CASE WHEN profit <= 0 THEN 1 ELSE 0 END)::DOUBLE PRECISION
    END as profit_ratio
FROM trades
WHERE status = 'CLOSED' AND time_close IS NOT NULL
GROUP BY DATE(time_open)
ORDER BY trade_date DESC;

-- ویو برای آخرین قیمت‌های هر نماد
CREATE OR REPLACE VIEW latest_prices AS
SELECT DISTINCT ON (symbol, timeframe)
    symbol,
    timeframe,
    time,
    open,
    high,
    low,
    close,
    volume,
    spread
FROM market_data
ORDER BY symbol, timeframe, time DESC;

-- تابع برای محاسبه آمار معاملات
CREATE OR REPLACE FUNCTION get_trade_statistics()
RETURNS TABLE (
    total_trades BIGINT,
    winning_trades BIGINT,
    losing_trades BIGINT,
    win_rate DOUBLE PRECISION,
    total_profit DOUBLE PRECISION,
    avg_profit DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        COUNT(*) as total_trades,
        SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as winning_trades,
        SUM(CASE WHEN profit <= 0 THEN 1 ELSE 0 END) as losing_trades,
        CASE 
            WHEN COUNT(*) = 0 THEN 0
            ELSE SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)::DOUBLE PRECISION / COUNT(*)::DOUBLE PRECISION
        END as win_rate,
        COALESCE(SUM(profit), 0) as total_profit,
        COALESCE(AVG(profit), 0) as avg_profit,
        CASE 
            WHEN SUM(CASE WHEN profit < 0 THEN ABS(profit) ELSE 0 END) = 0 THEN 999
            ELSE SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END) / SUM(CASE WHEN profit < 0 THEN ABS(profit) ELSE 0 END)
        END as profit_factor,
        0 as max_drawdown -- نیاز به محاسبه پیچیده‌تر دارد
    FROM trades
    WHERE status = 'CLOSED';
END;
$$ LANGUAGE plpgsql;

-- کامنت‌ها
COMMENT ON TABLE market_data IS 'داده‌های تاریخی بازار (OHLCV)';
COMMENT ON TABLE signals IS 'سیگنال‌های تولیدشده توسط استراتژی‌ها';
COMMENT ON TABLE trades IS 'معاملات اجراشده در MT5';
COMMENT ON TABLE ml_predictions IS 'پیش‌بینی‌های مدل‌های هوش مصنوعی';
COMMENT ON TABLE ml_performance_log IS 'لاگ عملکرد و دقت مدل‌های ML';
COMMENT ON VIEW daily_performance IS 'عملکرد روزانه معاملات';
COMMENT ON VIEW latest_prices IS 'آخرین قیمت‌های هر نماد';
