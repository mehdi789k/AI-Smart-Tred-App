#!/usr/bin/env python3
"""
Retraining Scheduler for ML Models

This script implements a walk-forward retraining strategy:
1. Load existing model and performance metrics
2. Gather new market data since last training
3. Retrain model with expanded dataset
4. Compare new model with old model
5. Deploy if new model shows improvement

Usage:
    python scripts/retrain_model.py --symbol XAUUSD_l --timeframe M5
    python scripts/retrain_model.py --schedule weekly
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.python.logging_config import get_logger
from src.python.indicators.common import load_candles

logger = get_logger("ml.retrain")


class RetrainingScheduler:
    """Manage periodic model retraining."""
    
    def __init__(
        self, 
        symbol: str, 
        timeframe: str,
        models_dir: str = "models",
        data_dir: str = "market_data"
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.models_dir = Path(models_dir)
        self.data_dir = Path(data_dir)
        self.logger = get_logger("ml.retrain")
        
        # Performance thresholds for model replacement
        self.min_accuracy_improvement = 0.02  # 2% improvement required
        self.min_pf_improvement = 0.1  # 0.1 profit factor improvement
    
    def find_latest_model(self) -> Optional[Path]:
        """Find the most recently trained model."""
        pattern = f"random_forest_latest.pkl"
        model_files = list(self.models_dir.glob(pattern))
        
        if not model_files:
            return None
        
        return max(model_files, key=lambda f: f.stat().st_mtime)
    
    def load_model_performance(self, model_path: Path) -> Dict[str, Any]:
        """Load model and its historical performance."""
        try:
            with open(model_path, 'rb') as f:
                model_data = pickle.load(f)
            
            metrics = model_data.get('metrics', {})
            config = model_data.get('config', {})
            timestamp = model_data.get('timestamp', 'unknown')
            
            self.logger.info(f"Loaded model from {timestamp}")
            self.logger.info(f"Current metrics: {metrics}")
            
            return {
                'model': model_data['model'],
                'feature_names': model_data.get('feature_names', []),
                'metrics': metrics,
                'config': config,
                'timestamp': timestamp
            }
        except Exception as e:
            self.logger.error(f"Failed to load model: {e}")
            return {}
    
    def check_retrain_needed(self, last_training: datetime, schedule: str) -> bool:
        """Check if retraining is needed based on schedule."""
        now = datetime.now()
        
        if schedule == 'daily':
            return (now - last_training).days >= 1
        elif schedule == 'weekly':
            return (now - last_training).days >= 7
        elif schedule == 'monthly':
            return (now - last_training).days >= 30
        else:
            # Manual trigger
            return True
    
    def gather_new_data(self, from_date: datetime) -> Optional[pd.DataFrame]:
        """Gather new market data since last training."""
        pattern = f"{self.symbol}_{self.timeframe}_*.json"
        files = list(self.data_dir.glob(pattern))
        
        if not files:
            self.logger.warning(f"No data found for {self.symbol} {self.timeframe}")
            return None
        
        latest_file = max(files, key=lambda f: f.stat().st_mtime)
        candles = load_candles(latest_file)
        
        if not candles:
            return None
        
        df = pd.DataFrame(candles)
        df['datetime'] = pd.to_datetime(df['time_iso'])
        df = df.set_index('datetime')
        
        # Filter for new data
        new_data = df[df.index > from_date]
        
        self.logger.info(f"Found {len(new_data)} new candles since {from_date}")
        
        return new_data if len(new_data) > 0 else None
    
    def should_replace_model(
        self, 
        old_metrics: Dict[str, float], 
        new_metrics: Dict[str, float]
    ) -> bool:
        """Determine if new model should replace old model."""
        
        old_acc = old_metrics.get('accuracy', 0)
        new_acc = new_metrics.get('accuracy', 0)
        
        old_pf = old_metrics.get('profit_factor', 0)
        new_pf = new_metrics.get('profit_factor', 0)
        
        accuracy_improved = (new_acc - old_acc) >= self.min_accuracy_improvement
        pf_improved = (new_pf - old_pf) >= self.min_pf_improvement
        
        self.logger.info(f"Accuracy change: {old_acc:.4f} -> {new_acc:.4f} ({'+' if new_acc > old_acc else ''}{new_acc - old_acc:.4f})")
        self.logger.info(f"Profit Factor change: {old_pf:.4f} -> {new_pf:.4f} ({'+' if new_pf > old_pf else ''}{new_pf - old_pf:.4f})")
        
        # Replace if either metric shows significant improvement
        return accuracy_improved or pf_improved


def main():
    """Main retraining logic."""
    parser = argparse.ArgumentParser(description="Retrain ML models periodically")
    parser.add_argument("--symbol", type=str, default="XAUUSD_l", help="Symbol")
    parser.add_argument("--timeframe", type=str, default="M5", help="Timeframe")
    parser.add_argument("--schedule", type=str, default="manual", 
                       choices=["daily", "weekly", "monthly", "manual"],
                       help="Retraining schedule")
    parser.add_argument("--models-dir", type=str, default="models", help="Models directory")
    parser.add_argument("--data-dir", type=str, default="market_data", help="Data directory")
    parser.add_argument("--force", action="store_true", help="Force retraining")
    
    args = parser.parse_args()
    
    scheduler = RetrainingScheduler(
        symbol=args.symbol,
        timeframe=args.timeframe,
        models_dir=args.models_dir,
        data_dir=args.data_dir
    )
    
    logger.info("="*60)
    logger.info(f"ML Model Retraining Check - {args.symbol} {args.timeframe}")
    logger.info("="*60)
    
    # Find latest model
    latest_model = scheduler.find_latest_model()
    
    if not latest_model:
        logger.info("No existing model found. Running initial training...")
        logger.info("Please run: python scripts/train_model.py")
        return
    
    # Load current model
    current_model_data = scheduler.load_model_performance(latest_model)
    
    if not current_model_data:
        logger.error("Failed to load current model")
        return
    
    # Check if retraining is needed
    last_training_str = current_model_data.get('timestamp', '')
    try:
        last_training = datetime.fromisoformat(last_training_str)
    except:
        last_training = datetime.fromtimestamp(latest_model.stat().st_mtime)
    
    needs_retrain = args.force or scheduler.check_retrain_needed(last_training, args.schedule)
    
    if not needs_retrain:
        next_train = last_training + timedelta(days=(
            1 if args.schedule == 'daily' else
            7 if args.schedule == 'weekly' else
            30 if args.schedule == 'monthly' else 0
        ))
        logger.info(f"Retraining not needed. Next scheduled: {next_train}")
        return
    
    logger.info("Retraining triggered...")
    
    # Gather new data
    new_data = scheduler.gather_new_data(last_training)
    
    if new_data is None:
        logger.info("No new data available. Skipping retraining.")
        return
    
    # Run training with expanded dataset
    logger.info("Starting retraining process...")
    
    # Import training module
    from scripts.train_model import (
        TrainingConfig, FeatureEngineer, TripleBarrierLabeler, 
        ModelTrainer, load_market_data
    )
    
    config = TrainingConfig(
        symbols=[args.symbol],
        timeframes=[args.timeframe],
        years_of_data=2,
        output_dir=args.models_dir
    )
    
    try:
        # Load all available data
        df = load_market_data(args.symbol, args.timeframe, 2, scheduler.data_dir)
        
        # Feature engineering
        feature_engineer = FeatureEngineer(config)
        df_features = feature_engineer.calculate_all_features(df)
        feature_names = feature_engineer.feature_names
        
        # Labeling
        labeler = TripleBarrierLabeler(config)
        df_labeled = labeler.label(df_features)
        
        # Train new model
        trainer = ModelTrainer(config)
        X_train, y_train, X_val, y_val, X_test, y_test = trainer.prepare_data(
            df_labeled, feature_names
        )
        
        # Train Random Forest
        new_model = trainer.train_random_forest(X_train, y_train, feature_names)
        new_metrics = trainer.evaluate_model(new_model, X_test, y_test, 'random_forest')
        trainer.save_model(new_model, 'random_forest', config.output_dir, feature_names)
        
        # Compare with old model
        old_metrics = current_model_data.get('metrics', {})
        
        logger.info("\n" + "="*60)
        logger.info("MODEL COMPARISON")
        logger.info("="*60)
        
        should_replace = scheduler.should_replace_model(old_metrics, new_metrics)
        
        if should_replace:
            logger.info("✓ New model shows improvement - DEPLOYING")
            
            # Save as production model
            prod_path = scheduler.models_dir / f"{args.symbol}_{args.timeframe}_production.pkl"
            with open(prod_path, 'wb') as f:
                pickle.dump({
                    'model': new_model,
                    'feature_names': feature_names,
                    'metrics': new_metrics,
                    'config': config.__dict__,
                    'timestamp': datetime.now().isoformat(),
                    'replaced': True
                }, f)
            
            logger.info(f"Production model saved to {prod_path}")
        else:
            logger.info("✗ New model does not show sufficient improvement - KEEPING OLD MODEL")
            
            # Archive the new model for reference
            archive_path = scheduler.models_dir / f"random_forest_archived_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl"
            with open(archive_path, 'wb') as f:
                pickle.dump({
                    'model': new_model,
                    'feature_names': feature_names,
                    'metrics': new_metrics,
                    'reason': 'not_better_than_current',
                    'timestamp': datetime.now().isoformat()
                }, f)
            
            logger.info(f"Archived model saved to {archive_path}")
        
        # Save retraining log
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'symbol': args.symbol,
            'timeframe': args.timeframe,
            'old_metrics': old_metrics,
            'new_metrics': new_metrics,
            'replaced': should_replace,
            'new_data_points': len(new_data)
        }
        
        log_file = scheduler.models_dir / "retraining_log.json"
        if log_file.exists():
            with open(log_file, 'r') as f:
                logs = json.load(f)
        else:
            logs = []
        
        logs.append(log_entry)
        with open(log_file, 'w') as f:
            json.dump(logs, f, indent=2)
        
        logger.info(f"Retraining log saved to {log_file}")
        
    except Exception as e:
        logger.error(f"Retraining failed: {e}", exc_info=True)
        return
    
    logger.info("\n" + "="*60)
    logger.info("Retraining process completed!")
    logger.info("="*60)


if __name__ == "__main__":
    main()
