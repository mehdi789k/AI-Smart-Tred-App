"""Fail-closed bridge between the dashboard and the live AutoTrader."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from ..logging_config import log_event, new_correlation_id
from ..risk.break_even import plan_break_even
from .auto_trader import AutoTrader, TradingCycle
from .live_order_workflow import LiveOrderRejected, LiveOrderWorkflow
from .trading import Order

logger = logging.getLogger(__name__)


class LiveTradingLoop:
    """Run one risk-gated live cycle per dashboard rerun.

    The loop is intentionally not a background thread.  Streamlit reruns call
    :meth:`run_once`, which makes disconnects and stale authorizations fail
    closed and keeps all execution auditable.
    """

    def __init__(
        self,
        trader: AutoTrader,
        workflow: LiveOrderWorkflow,
        connector: Any,
        data_provider: Callable[
            [list[str], str], tuple[dict[str, Any], dict[str, float]]
        ],
    ) -> None:
        if trader.config.execution_mode != "live":
            raise ValueError("LiveTradingLoop requires live execution mode")
        self.trader = trader
        self.workflow = workflow
        self.connector = connector
        self.data_provider = data_provider
        self.active = False
        self.started_at: datetime | None = None
        self.last_result: TradingCycle | None = None
        self.last_status = "not_started"
        self.last_break_even_actions: list[dict[str, Any]] = []
        self._partial_close_tickets: set[int] = set()

        def token_provider(order: Order) -> str:
            if not self.workflow.automation_enabled():
                raise LiveOrderRejected(
                    "automation_not_authorized", "automated trading gate is not active"
                )
            if order.order_type.value == "LIMIT":
                action = (
                    f"pending:{order.symbol.upper()}:{order.direction.upper()}_LIMIT:"
                    f"{order.volume:g}:{float(order.price or 0):g}"
                )
            else:
                action = (
                    f"open:{order.symbol.upper()}:{order.direction.upper()}:{order.volume:g}:"
                    f"{float(order.stop_loss or 0):g}:{float(order.take_profit or 0):g}"
                )
            return self.workflow.request_confirmation(action)

        self.trader.configure_live_execution(connector, workflow, token_provider)

    @staticmethod
    def enabled_by_server() -> bool:
        """Require an explicit operator-controlled environment gate."""
        return os.getenv("MT5_AUTO_TRADING_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    def start(self, confirmation_token: str) -> None:
        operation_id = new_correlation_id()
        if not self.enabled_by_server():
            raise LiveOrderRejected(
                "auto_trading_gate_disabled",
                "MT5_AUTO_TRADING_ENABLED is not enabled on the server",
            )
        if not self.connector or not self.connector.is_connected():
            raise LiveOrderRejected("mt5_disconnected", "MT5 is not connected")
        symbols = {symbol.upper() for symbol in self.trader.config.symbols}
        if not symbols or not symbols.issubset(self.workflow.config.allowed_symbols):
            raise LiveOrderRejected(
                "symbol_not_whitelisted", "all automated symbols must be whitelisted"
            )
        self.workflow.confirm_automation(confirmation_token)
        self.trader.start()
        self.active = True
        self.started_at = datetime.now(timezone.utc)
        self.last_status = "started"
        log_event(
            logger,
            logging.WARNING,
            "automation_started",
            operation_id=operation_id,
            symbols=sorted(symbols),
            timeframes=self.trader.config.symbol_timeframes
            or {
                symbol: self.trader.config.timeframes[0]
                for symbol in self.trader.config.symbols
            },
        )

    def restore_active(self) -> None:
        """Resume an active, already-authorized dashboard session after a rerun."""
        if not self.enabled_by_server():
            raise LiveOrderRejected(
                "auto_trading_gate_disabled",
                "MT5_AUTO_TRADING_ENABLED is not enabled on the server",
            )
        if not self.workflow.automation_enabled():
            raise LiveOrderRejected(
                "automation_not_authorized",
                "automated trading authorization is not active",
            )
        if not self.connector or not self.connector.is_connected():
            raise LiveOrderRejected("mt5_disconnected", "MT5 is not connected")
        self.trader.start()
        self.active = True
        self.started_at = datetime.now(timezone.utc)
        self.last_status = "restored"
        log_event(logger, logging.WARNING, "automation_restored")

    def stop(self) -> None:
        self.active = False
        self.trader.stop()
        self.last_status = "stopped"
        log_event(logger, logging.WARNING, "automation_stopped")

    def run_once(self) -> TradingCycle | None:
        cycle_id = new_correlation_id()
        self.last_break_even_actions = []
        if not self.active:
            self.last_status = "inactive"
            return None
        if not self.workflow.automation_enabled():
            self.last_status = "authorization_expired"
            log_event(
                logger,
                logging.WARNING,
                "automation_authorization_expired",
                cycle_id=cycle_id,
            )
            self.stop()
            return None
        if not self.connector or not self.connector.is_connected():
            self.last_status = "mt5_disconnected"
            log_event(logger, logging.ERROR, "mt5_disconnected", cycle_id=cycle_id)
            self.stop()
            return None
        positions = self.connector._mt5.positions_get()
        if positions is None:
            self.last_status = "positions_unavailable"
            log_event(logger, logging.ERROR, "positions_unavailable", cycle_id=cycle_id)
            self.stop()
            return None
        broker_symbols = {
            str(getattr(position, "symbol", "")).upper()
            for position in positions
            if getattr(position, "symbol", None)
        }
        pair_map = self.trader.config.symbol_timeframes or {
            symbol: self.trader.config.timeframes[0]
            for symbol in self.trader.config.symbols
        }
        market_data: dict[str, Any] = {}
        prices: dict[str, float] = {}
        for symbol, timeframe in pair_map.items():
            pair_data, pair_prices = self.data_provider([symbol], timeframe)
            market_data.update(pair_data)
            prices.update(pair_prices)
        if prices:
            self.trader.position_manager.reconcile_external_positions(
                broker_symbols,
                prices,
            )
        managed = [
            position
            for position in positions
            if int(getattr(position, "magic", -1)) == self.workflow.config.magic
        ]
        active_tickets = {
            int(getattr(position, "ticket", 0) or 0) for position in managed
        }
        self._partial_close_tickets.intersection_update(active_tickets)
        if (
            self.trader.config.break_even.enabled
            or self.trader.config.partial_close_enabled
        ):
            for position in managed:
                symbol = str(getattr(position, "symbol", "")).upper()
                position_type = int(getattr(position, "type", -1))
                buy_type = int(getattr(self.connector._mt5, "POSITION_TYPE_BUY", 0))
                sell_type = int(getattr(self.connector._mt5, "POSITION_TYPE_SELL", 1))
                if position_type == buy_type:
                    direction = "BUY"
                elif position_type == sell_type:
                    direction = "SELL"
                else:
                    log_event(
                        logger,
                        logging.ERROR,
                        "break_even_rejected",
                        cycle_id=cycle_id,
                        symbol=symbol,
                        ticket=int(getattr(position, "ticket", 0) or 0),
                        reason="unknown_position_type",
                    )
                    continue
                tick = self.connector._mt5.symbol_info_tick(symbol)
                if tick is None:
                    log_event(
                        logger,
                        logging.ERROR,
                        "break_even_rejected",
                        cycle_id=cycle_id,
                        symbol=symbol,
                        ticket=int(getattr(position, "ticket", 0) or 0),
                        reason="no_quote",
                    )
                    continue
                price = float(tick.bid if direction == "BUY" else tick.ask)
                if self.trader.config.break_even.enabled:
                    decision = plan_break_even(
                        {
                            "direction": direction,
                            "entry_price": getattr(position, "price_open", 0.0),
                            "current_price": price,
                            "stop_loss": getattr(position, "sl", 0.0),
                        },
                        config=self.trader.config.break_even,
                    )
                    if decision.should_move:
                        action = (
                            f"modify_sl:{symbol}:{int(position.ticket)}:"
                            f"{float(decision.new_stop_loss):g}"
                        )
                        try:
                            token = self.workflow.request_confirmation(action)
                            self.workflow.modify_position_stop(
                                position,
                                float(decision.new_stop_loss),
                                token,
                            )
                            self.last_break_even_actions.append(
                                {
                                    "symbol": symbol,
                                    "ticket": int(position.ticket),
                                    "stop_loss": float(decision.new_stop_loss),
                                    "status": "applied",
                                }
                            )
                            log_event(
                                logger,
                                logging.INFO,
                                "break_even_applied",
                                cycle_id=cycle_id,
                                symbol=symbol,
                                ticket=int(position.ticket),
                                stop_loss=float(decision.new_stop_loss),
                            )
                        except LiveOrderRejected as error:
                            self.last_break_even_actions.append(
                                {
                                    "symbol": symbol,
                                    "ticket": int(position.ticket),
                                    "stop_loss": float(decision.new_stop_loss),
                                    "status": "rejected",
                                    "reason": error.code,
                                }
                            )
                if (
                    self.trader.config.partial_close_enabled
                    and int(getattr(position, "ticket", 0) or 0)
                    not in self._partial_close_tickets
                ):
                    entry = float(getattr(position, "price_open", 0.0) or 0.0)
                    stop = float(getattr(position, "sl", 0.0) or 0.0)
                    risk = abs(entry - stop)
                    move = (price - entry) if direction == "BUY" else (entry - price)
                    if (
                        risk > 0
                        and move >= risk * self.trader.config.partial_close_trigger_r
                    ):
                        ticket = int(getattr(position, "ticket", 0) or 0)
                        volume = float(getattr(position, "volume", 0.0) or 0.0)
                        close_volume = volume * (
                            self.trader.config.partial_close_percent / 100.0
                        )
                        action = f"partial_close:{symbol}:{ticket}:{close_volume:g}"
                        try:
                            token = self.workflow.request_confirmation(action)
                            self.workflow.close_position_partial(
                                position,
                                self.trader.config.partial_close_percent,
                                token,
                            )
                            self._partial_close_tickets.add(ticket)
                            log_event(
                                logger,
                                logging.INFO,
                                "partial_close_applied",
                                cycle_id=cycle_id,
                                symbol=symbol,
                                ticket=ticket,
                                percent=self.trader.config.partial_close_percent,
                            )
                        except LiveOrderRejected as error:
                            log_event(
                                logger,
                                logging.ERROR,
                                "partial_close_rejected",
                                cycle_id=cycle_id,
                                symbol=symbol,
                                ticket=ticket,
                                reason=error.code,
                            )
        if len(managed) >= self.trader.config.max_positions:
            self.last_status = "max_positions_reached"
            log_event(
                logger,
                logging.INFO,
                "cycle_skipped",
                cycle_id=cycle_id,
                reason="max_positions_reached",
                managed_positions=len(managed),
            )
            return None
        if not market_data or not prices:
            self.last_status = "market_data_unavailable"
            log_event(
                logger,
                logging.INFO,
                "cycle_skipped",
                cycle_id=cycle_id,
                reason="market_data_unavailable",
                symbols=sorted(self.trader.config.symbols),
                timeframes=pair_map,
            )
            return None
        result = self.trader.run_cycle(market_data, prices)
        self.last_result = result
        self.last_status = (
            "signals_detected_orders_blocked"
            if result.signals_generated and not result.orders_executed
            else "cycle_completed"
        )
        log_event(
            logger,
            logging.INFO,
            "cycle_completed",
            cycle_id=cycle_id,
            signals=result.signals_generated,
            orders=result.orders_executed,
            opened=result.positions_opened,
            closed=result.positions_closed,
            errors=len(result.errors),
            status=self.last_status,
        )
        return result


class ShadowTradingLoop:
    """Run live market-data and signal cycles without any MT5 order path."""

    def __init__(
        self,
        trader: AutoTrader,
        data_provider: Callable[
            [list[str], str], tuple[dict[str, Any], dict[str, float]]
        ],
    ) -> None:
        if trader.config.execution_mode != "shadow":
            raise ValueError("ShadowTradingLoop requires shadow execution mode")
        self.trader = trader
        self.data_provider = data_provider
        self.active = False
        self.last_result: TradingCycle | None = None
        self.last_status = "not_started"

    def start(self) -> None:
        """Enable shadow cycles without a broker confirmation or write."""

        self.trader.start()
        self.active = True
        self.last_status = "started"
        log_event(logger, logging.INFO, "shadow_trading_started")

    def stop(self) -> None:
        """Stop shadow cycles while retaining the append-only ledger."""

        self.active = False
        self.trader.stop()
        self.last_status = "stopped"
        log_event(logger, logging.INFO, "shadow_trading_stopped")

    def run_once(self) -> TradingCycle | None:
        """Fetch current market data and evaluate one shadow trading cycle."""

        if not self.active:
            self.last_status = "inactive"
            return None
        pair_map = self.trader.config.symbol_timeframes or {
            symbol: self.trader.config.timeframes[0]
            for symbol in self.trader.config.symbols
        }
        market_data: dict[str, Any] = {}
        prices: dict[str, float] = {}
        for symbol, timeframe in pair_map.items():
            pair_data, pair_prices = self.data_provider([symbol], timeframe)
            market_data.update(pair_data)
            prices.update(pair_prices)
        self.last_result = self.trader.run_cycle(market_data, prices)
        self.last_status = "running"
        return self.last_result
