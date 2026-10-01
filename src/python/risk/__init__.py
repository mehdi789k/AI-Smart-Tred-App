"""Risk and capital-management filters for the trading system."""

from .break_even import (
    BreakEvenConfig,
    BreakEvenDecision,
    apply_break_even,
    plan_break_even,
)
from .calculator import (
    DEFAULT_MINIMUM_RR,
    DEFAULT_RISK_PER_TRADE,
    calculate_position_size,
    calculate_risk_reward_ratio,
    filter_minimum_risk_reward,
    meets_minimum_risk_reward,
    normalize_protection_prices,
)
from .circuit_breaker import CircuitBreaker
from .manager import (
    DEFAULT_CONSECUTIVE_LOSS_LIMIT,
    DEFAULT_LOSS_RESET_INTERVAL_HOURS,
    DailyLossFilter,
    MaxDailyLossFilter,
    check_daily_loss,
    check_max_daily_loss,
    evaluate_daily_loss,
)
from .news_proximity import (
    DEFAULT_NEWS_THRESHOLD_HOURS,
    HIGH_IMPACT_NEWS,
    VALID_IMPACTS,
    NewsProximityFilter,
    check_news_proximity,
    evaluate_news_proximity,
)

__all__ = [
    "DEFAULT_MINIMUM_RR",
    "DEFAULT_CONSECUTIVE_LOSS_LIMIT",
    "DEFAULT_LOSS_RESET_INTERVAL_HOURS",
    "calculate_risk_reward_ratio",
    "filter_minimum_risk_reward",
    "meets_minimum_risk_reward",
    "normalize_protection_prices",
    "calculate_position_size",
    "DEFAULT_RISK_PER_TRADE",
    "DailyLossFilter",
    "MaxDailyLossFilter",
    "evaluate_daily_loss",
    "check_daily_loss",
    "check_max_daily_loss",
    "CircuitBreaker",
    "BreakEvenConfig",
    "BreakEvenDecision",
    "plan_break_even",
    "apply_break_even",
    "DEFAULT_NEWS_THRESHOLD_HOURS",
    "HIGH_IMPACT_NEWS",
    "VALID_IMPACTS",
    "NewsProximityFilter",
    "evaluate_news_proximity",
    "check_news_proximity",
]
