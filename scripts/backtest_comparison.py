#!/usr/bin/env python3
"""
Backtest Comparison: With vs Without ML

This script performs A/B testing to compare trading performance:
- Strategy-only (SMC, Trend, Mean Reversion)
- Strategy + ML Ensemble

Metrics compared:
- Profit Factor
- Max Drawdown
- Win Rate
- Sharpe Ratio
- Total Return

Usage:
    python scripts/backtest_comparison.py --symbol XAUUSD_l --timeframe M5
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.python.logging_config import get_logger
from src.python.indicators.common import load_candles
from scripts.train_model import FeatureEngineer, TrainingConfig

logger = get_logger("ml.backtest")


@dataclass
class BacktestConfig:
    """Configuration for backtest comparison."""
    
    symbol: str = "XAUUSD_l"
    timeframe: str = "M5"
    initial_capital: float = 10000.0
    risk_per_trade: float = 0.02  # 2% risk per trade
    
    # Trading parameters
    use_sl_tp: bool = True
    sl_multiplier: float = 1.0
    tp_multiplier: float = 2.0
    
    # ML parameters
    ml_weight: float = 0.25  # Weight of ML in signal scoring
    min_ml_confidence: float = 0.6  # Minimum ML confidence to trade
    model_path: Optional[str] = None
    
    # Output
    output_dir: str = "backtest_results"


@dataclass
class Trade:
    """Represents a single trade."""
    entry_time: datetime
    entry_price: float
    direction: str  # 'BUY' or 'SELL'
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_percent: float = 0.0
    size: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    reason: str = ""  # Why the trade was closed


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    total_return: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    largest_winner: float = 0.0
    largest_loser: float = 0.0
    config: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'total_return': self.total_return,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.win_rate,
            'profit_factor': self.profit_factor,
            'max_drawdown': self.max_drawdown,
            'sharpe_ratio': self.sharpe_ratio,
            'avg_trade_pnl': self.avg_trade_pnl,
            'avg_winner': self.avg_winner,
            'avg_loser': self.avg_loser,
            'largest_winner': self.largest_winner,
            'largest_loser': self.largest_loser,
            'config': self.config
        }


class SimpleTradingStrategy:
    """Simple trading strategy for backtesting."""
    
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.logger = get_logger("ml.strategy")
    
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate trading signals based on technical indicators."""
        df = df.copy()
        
        # Calculate RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-10)
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # Calculate MACD
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        # Calculate Bollinger Bands
        df['bb_middle'] = df['close'].rolling(20).mean()
        df['bb_std'] = df['close'].rolling(20).std()
        df['bb_lower'] = df['bb_middle'] - (df['bb_std'] * 2)
        df['bb_upper'] = df['bb_middle'] + (df['bb_std'] * 2)
        
        # Calculate ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr'] = true_range.rolling(14).mean()
        
        # Generate base signals
        df['signal'] = 0  # 0 = HOLD, 1 = BUY, -1 = SELL
        
        # RSI oversold/overbought
        df.loc[df['rsi'] < 30, 'signal'] = 1
        df.loc[df['rsi'] > 70, 'signal'] = -1
        
        # MACD crossovers
        df.loc[(df['macd'] > df['macd_signal']) & (df['rsi'] < 50), 'signal'] = 1
        df.loc[(df['macd'] < df['macd_signal']) & (df['rsi'] > 50), 'signal'] = -1
        
        # Mean reversion at Bollinger Bands
        df.loc[df['close'] < df['bb_lower'], 'signal'] = 1
        df.loc[df['close'] > df['bb_upper'], 'signal'] = -1
        
        return df
    
    def apply_ml_filter(
        self, 
        df: pd.DataFrame, 
        model_data: Optional[Dict],
        feature_cols: List[str]
    ) -> pd.DataFrame:
        """Apply ML model predictions to filter/enhance signals."""
        if model_data is None:
            self.logger.info("No ML model provided, skipping ML filter")
            return df
        
        df = df.copy()
        model = model_data['model']
        
        # Generate features needed by model
        df_ml = self._prepare_ml_features(df, feature_cols)
        
        # Make predictions
        predictions = []
        confidences = []
        
        for i in range(len(df_ml)):
            if i < len(feature_cols):  # Not enough data for features
                predictions.append('HOLD')
                confidences.append(0.0)
                continue
            
            features = df_ml[feature_cols].iloc[i].values.reshape(1, -1)
            
            try:
                pred = model.predict(features)[0]
                proba = model.predict_proba(features)[0]
                
                direction_map = {0: 'SELL', 1: 'HOLD', 2: 'BUY'}
                predictions.append(direction_map.get(pred, 'HOLD'))
                confidences.append(float(np.max(proba)))
            except Exception as e:
                predictions.append('HOLD')
                confidences.append(0.0)
        
        df['ml_prediction'] = predictions
        df['ml_confidence'] = confidences
        
        # Adjust signals based on ML
        df['signal_ml'] = df['signal'].copy()
        
        for i in range(len(df)):
            ml_pred = df.iloc[i]['ml_prediction']
            ml_conf = df.iloc[i]['ml_confidence']
            base_signal = df.iloc[i]['signal']
            
            if ml_conf < self.config.min_ml_confidence:
                df.iloc[i, df.columns.get_loc('signal_ml')] = 0
                continue
            
            # If ML disagrees with strong signal, reduce or reverse
            if ml_pred == 'BUY' and base_signal == -1:
                df.iloc[i, df.columns.get_loc('signal_ml')] = 0  # Cancel sell
            elif ml_pred == 'SELL' and base_signal == 1:
                df.iloc[i, df.columns.get_loc('signal_ml')] = 0  # Cancel buy
            elif ml_pred == 'BUY' and base_signal == 0:
                df.iloc[i, df.columns.get_loc('signal_ml')] = 1  # Add buy signal
            elif ml_pred == 'SELL' and base_signal == 0:
                df.iloc[i, df.columns.get_loc('signal_ml')] = -1  # Add sell signal
        
        return df
    
    def _prepare_ml_features(self, df: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
        """Prepare exactly the feature schema used by the training pipeline."""
        model_config = TrainingConfig.from_dict(
            {
                "symbol": self.config.symbol,
                "timeframe": self.config.timeframe,
            }
        )
        engineered = FeatureEngineer(model_config).calculate_all_features(df.copy())
        # FeatureEngineer drops warm-up rows; reindexing keeps predictions aligned
        # with candle timestamps while leaving unavailable rows as NaN.
        return engineered.reindex(df.index)[feature_cols]


class Backtester:
    """Run backtests on trading strategies."""
    
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.logger = get_logger("ml.backtester")
    
    def run(
        self, 
        df: pd.DataFrame, 
        use_ml: bool = False,
        model_data: Optional[Dict] = None,
        feature_cols: Optional[List[str]] = None
    ) -> BacktestResult:
        """Run backtest with optional ML enhancement."""
        
        self.logger.info(f"Running backtest {'with ML' if use_ml else 'without ML'}...")
        
        # Generate signals
        strategy = SimpleTradingStrategy(self.config)
        df_signals = strategy.generate_signals(df)
        
        if use_ml and model_data and feature_cols:
            df_signals = strategy.apply_ml_filter(df_signals, model_data, feature_cols)
            signal_col = 'signal_ml'
        else:
            signal_col = 'signal'
        
        # Execute trades
        trades = []
        equity = self.config.initial_capital
        equity_curve = [equity]
        position = None
        
        for i in range(len(df_signals)):
            row = df_signals.iloc[i]
            signal = row[signal_col]
            
            # Close position if opposite signal
            if position:
                should_close = False
                exit_price = row['close']
                
                if position.direction == 'BUY' and signal == -1:
                    should_close = True
                elif position.direction == 'SELL' and signal == 1:
                    should_close = True
                
                # Check SL/TP
                if self.config.use_sl_tp and position.sl and position.tp:
                    if row['low'] <= position.sl or row['high'] >= position.tp:
                        should_close = True
                        if row['low'] <= position.sl:
                            exit_price = position.sl
                        elif row['high'] >= position.tp:
                            exit_price = position.tp
                
                if should_close:
                    pnl = self._calculate_pnl(position, exit_price)
                    position.exit_time = row.name
                    position.exit_price = exit_price
                    position.pnl = pnl
                    position.pnl_percent = pnl / equity * 100
                    trades.append(position)
                    equity += pnl
                    position = None
            
            # Open new position
            if not position and signal != 0:
                direction = 'BUY' if signal > 0 else 'SELL'
                entry_price = row['close']
                
                # Calculate position size based on risk
                atr = row.get('atr', row['close'] * 0.01)
                sl_distance = atr * self.config.sl_multiplier
                sl = entry_price - sl_distance if direction == 'BUY' else entry_price + sl_distance
                tp = entry_price + atr * self.config.tp_multiplier if direction == 'BUY' else entry_price - atr * self.config.tp_multiplier
                
                risk_amount = equity * self.config.risk_per_trade
                size = risk_amount / abs(entry_price - sl) if sl != entry_price else 1.0
                
                position = Trade(
                    entry_time=row.name,
                    entry_price=entry_price,
                    direction=direction,
                    size=size,
                    sl=sl,
                    tp=tp,
                    reason='signal'
                )
            
            equity_curve.append(equity)
        
        # Close any open position at the end
        if position:
            exit_price = df_signals.iloc[-1]['close']
            pnl = self._calculate_pnl(position, exit_price)
            position.exit_time = df_signals.iloc[-1].name
            position.exit_price = exit_price
            position.pnl = pnl
            trades.append(position)
        
        # Calculate metrics
        result = self._calculate_metrics(trades, equity_curve)
        result.config = {
            'use_ml': use_ml,
            'symbol': self.config.symbol,
            'timeframe': self.config.timeframe,
            'initial_capital': self.config.initial_capital
        }
        
        return result
    
    def _calculate_pnl(self, trade: Trade, exit_price: float) -> float:
        """Calculate PnL for a trade."""
        if trade.direction == 'BUY':
            return (exit_price - trade.entry_price) * trade.size
        else:
            return (trade.entry_price - exit_price) * trade.size
    
    def _calculate_metrics(self, trades: List[Trade], equity_curve: List[float]) -> BacktestResult:
        """Calculate comprehensive backtest metrics."""
        
        result = BacktestResult(
            trades=trades,
            equity_curve=equity_curve
        )
        
        if not trades:
            return result
        
        # Basic stats
        result.total_trades = len(trades)
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        
        result.winning_trades = len(winners)
        result.losing_trades = len(losers)
        result.win_rate = len(winners) / len(trades) if trades else 0
        
        # PnL stats
        all_pnls = [t.pnl for t in trades]
        result.total_return = sum(all_pnls)
        result.avg_trade_pnl = np.mean(all_pnls) if all_pnls else 0
        
        winner_pnls = [t.pnl for t in winners]
        loser_pnls = [t.pnl for t in losers]
        
        result.avg_winner = np.mean(winner_pnls) if winner_pnls else 0
        result.avg_loser = np.mean(loser_pnls) if loser_pnls else 0
        result.largest_winner = max(winner_pnls) if winner_pnls else 0
        result.largest_loser = min(loser_pnls) if loser_pnls else 0
        
        # Profit Factor
        gross_profit = sum(winner_pnls) if winner_pnls else 0
        gross_loss = abs(sum(loser_pnls)) if loser_pnls else 0
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Drawdown
        equity_array = np.array(equity_curve)
        running_max = np.maximum.accumulate(equity_array)
        drawdowns = (equity_array - running_max) / running_max
        result.max_drawdown = abs(np.min(drawdowns))
        
        # Sharpe Ratio (assuming daily returns, 252 trading days)
        returns = np.diff(equity_array) / equity_array[:-1]
        if len(returns) > 1 and np.std(returns) > 0:
            result.sharpe_ratio = np.sqrt(252) * np.mean(returns) / np.std(returns)
        
        return result


def load_model(model_path: str) -> Tuple[Optional[Dict], Optional[List[str]]]:
    """Load trained ML model."""
    try:
        with open(model_path, 'rb') as f:
            model_data = pickle.load(f)
        
        feature_names = model_data.get('feature_names', [])
        logger.info(f"Loaded model from {model_path}")
        logger.info(f"Model features: {len(feature_names)}")
        
        return model_data, feature_names
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        return None, None


def main():
    """Run backtest comparison."""
    parser = argparse.ArgumentParser(description="Compare backtest with/without ML")
    parser.add_argument("--symbol", type=str, default="XAUUSD_l", help="Symbol")
    parser.add_argument("--timeframe", type=str, default="M5", help="Timeframe")
    parser.add_argument("--model-path", type=str, help="Path to trained ML model")
    parser.add_argument("--data-dir", type=str, default="market_data", help="Market data directory")
    parser.add_argument("--output-dir", type=str, default="backtest_results", help="Output directory")
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="Initial capital")
    
    args = parser.parse_args()
    
    # Load market data
    data_dir = Path(args.data_dir)
    pattern = f"{args.symbol}_{args.timeframe}_*.json"
    files = list(data_dir.glob(pattern))
    
    if not files:
        logger.error(f"No data found for {args.symbol} {args.timeframe}")
        return
    
    latest_file = max(files, key=lambda f: f.stat().st_mtime)
    logger.info(f"Loading data from {latest_file}")
    
    candles = load_candles(latest_file)
    df = pd.DataFrame(candles)
    df['datetime'] = pd.to_datetime(df['time_iso'])
    df = df.set_index('datetime')
    
    logger.info(f"Loaded {len(df)} candles")
    
    # Load ML model if provided
    model_data = None
    feature_cols = None
    if args.model_path:
        model_data, feature_cols = load_model(args.model_path)
    
    # Create config
    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        initial_capital=args.initial_capital,
        model_path=args.model_path
    )
    
    # Run backtests
    backtester = Backtester(config)
    
    logger.info("\n" + "="*60)
    logger.info("Backtest WITHOUT ML")
    logger.info("="*60)
    result_no_ml = backtester.run(df, use_ml=False)
    logger.info(f"Total Return: ${result_no_ml.total_return:.2f} ({result_no_ml.total_return/config.initial_capital*100:.2f}%)")
    logger.info(f"Win Rate: {result_no_ml.win_rate*100:.1f}%")
    logger.info(f"Profit Factor: {result_no_ml.profit_factor:.2f}")
    logger.info(f"Max Drawdown: {result_no_ml.max_drawdown*100:.2f}%")
    logger.info(f"Sharpe Ratio: {result_no_ml.sharpe_ratio:.2f}")
    
    if model_data and feature_cols:
        logger.info("\n" + "="*60)
        logger.info("Backtest WITH ML")
        logger.info("="*60)
        result_with_ml = backtester.run(df, use_ml=True, model_data=model_data, feature_cols=feature_cols)
        logger.info(f"Total Return: ${result_with_ml.total_return:.2f} ({result_with_ml.total_return/config.initial_capital*100:.2f}%)")
        logger.info(f"Win Rate: {result_with_ml.win_rate*100:.1f}%")
        logger.info(f"Profit Factor: {result_with_ml.profit_factor:.2f}")
        logger.info(f"Max Drawdown: {result_with_ml.max_drawdown*100:.2f}%")
        logger.info(f"Sharpe Ratio: {result_with_ml.sharpe_ratio:.2f}")
        
        # Comparison
        logger.info("\n" + "="*60)
        logger.info("COMPARISON (ML vs No-ML)")
        logger.info("="*60)
        logger.info(f"Return Improvement: ${result_with_ml.total_return - result_no_ml.total_return:.2f}")
        logger.info(f"Win Rate Change: {(result_with_ml.win_rate - result_no_ml.win_rate)*100:.1f}%")
        logger.info(f"Profit Factor Change: {result_with_ml.profit_factor - result_no_ml.profit_factor:.2f}")
        logger.info(f"Drawdown Change: {(result_with_ml.max_drawdown - result_no_ml.max_drawdown)*100:.2f}%")
    
    # Save results
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = {
        'timestamp': timestamp,
        'symbol': args.symbol,
        'timeframe': args.timeframe,
        'without_ml': result_no_ml.to_dict(),
        'with_ml': result_with_ml.to_dict() if model_data else None,
        'comparison': {
            'return_improvement': result_with_ml.total_return - result_no_ml.total_return if model_data else None,
            'win_rate_change': (result_with_ml.win_rate - result_no_ml.win_rate) * 100 if model_data else None,
            'pf_change': result_with_ml.profit_factor - result_no_ml.profit_factor if model_data else None
        } if model_data else None
    }
    
    results_path = output_dir / f"backtest_comparison_{args.symbol}_{args.timeframe}_{timestamp}.json"
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
