"""Generate a verifiable report of recent closed trades from the live MT5 terminal."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5


def build_report(days: int) -> str:
    """Read closed deals from MT5 and return a human-readable report."""
    requested_to = datetime.now(timezone.utc)
    date_from = requested_to - timedelta(days=days, hours=12)
    date_to = requested_to + timedelta(hours=12)

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        deals = []
        chunk_start = date_from
        while chunk_start < date_to:
            chunk_end = min(chunk_start + timedelta(days=7), date_to)
            chunk = mt5.history_deals_get(chunk_start, chunk_end)
            if chunk is None:
                raise RuntimeError(f"MT5 history query failed: {mt5.last_error()}")
            deals.extend(chunk)
            chunk_start = chunk_end
        trade_deals = [
            deal
            for deal in deals
            if deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
        ]
        closed_deals = [
            deal
            for deal in trade_deals
            if deal.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)
        ]

        total_profit = sum(float(deal.profit) for deal in closed_deals)
        total_commission = sum(float(deal.commission) for deal in closed_deals)
        total_swap = sum(float(deal.swap) for deal in closed_deals)
        net_result = total_profit + total_commission + total_swap

        lines = [
            "MT5 RECENT CLOSED TRADES REPORT",
            "=" * 34,
            f"Generated: {date_to.isoformat(timespec='seconds')}",
            f"Requested period: {(requested_to - timedelta(days=days)).isoformat(timespec='seconds')} to "
            f"{requested_to.isoformat(timespec='seconds')} UTC",
            f"Query window (12h safety margin): {date_from.isoformat(timespec='seconds')} to "
            f"{date_to.isoformat(timespec='seconds')} UTC",
            f"Terminal: {getattr(terminal, 'name', 'Unknown')}",
            f"Connected: {getattr(terminal, 'connected', False)}",
            f"Login: {getattr(account, 'login', 'Unknown')}",
            f"Server: {getattr(account, 'server', 'Unknown')}",
            f"Balance: {getattr(account, 'balance', 0.0):.2f} "
            f"{getattr(account, 'currency', '')}",
            f"Equity: {getattr(account, 'equity', 0.0):.2f} "
            f"{getattr(account, 'currency', '')}",
            f"Free margin: {getattr(account, 'margin_free', 0.0):.2f} "
            f"{getattr(account, 'currency', '')}",
            "",
            f"All account deal records: {len(deals)}",
            f"Recent BUY/SELL deal events: {len(trade_deals)}",
            f"  Entry events: {sum(deal.entry == mt5.DEAL_ENTRY_IN for deal in trade_deals)}",
            f"  Exit events: {len(closed_deals)}",
            f"Closed trades (exit deals): {len(closed_deals)}",
            f"Total gross profit: {total_profit:.2f}",
            f"Total commission: {total_commission:.2f}",
            f"Total swap: {total_swap:.2f}",
            f"Net result: {net_result:.2f}",
            "",
            "CLOSED TRADE DETAILS (EXIT EVENTS)",
            "-" * 20,
        ]

        if not closed_deals:
            lines.append("No closed trades found in this period.")
        else:
            for index, deal in enumerate(
                sorted(closed_deals, key=lambda item: item.time), start=1
            ):
                direction = "BUY" if deal.type == mt5.DEAL_TYPE_BUY else "SELL"
                deal_time = datetime.fromtimestamp(deal.time, timezone.utc).isoformat(
                    timespec="seconds"
                )
                lines.extend(
                    [
                        f"{index}. Ticket: {deal.ticket}",
                        f"   Order: {deal.order}",
                        f"   Position: {deal.position_id}",
                        f"   Symbol: {deal.symbol}",
                        f"   Direction: {direction}",
                        f"   Volume: {deal.volume:.2f}",
                        f"   Price: {deal.price:.5f}",
                        f"   Time: {deal_time}",
                        f"   Profit: {deal.profit:.2f}",
                        f"   Commission: {deal.commission:.2f}",
                        f"   Swap: {deal.swap:.2f}",
                        "",
                    ]
                )

        lines.extend(
            [
                "",
                "ALL BUY/SELL DEAL EVENTS",
                "-" * 25,
            ]
        )
        for index, deal in enumerate(
            sorted(trade_deals, key=lambda item: item.time), start=1
        ):
            direction = "BUY" if deal.type == mt5.DEAL_TYPE_BUY else "SELL"
            entry_name = {
                mt5.DEAL_ENTRY_IN: "ENTRY",
                mt5.DEAL_ENTRY_OUT: "EXIT",
                mt5.DEAL_ENTRY_OUT_BY: "EXIT_BY",
            }.get(deal.entry, str(deal.entry))
            lines.extend(
                [
                    f"{index}. {entry_name} {direction} "
                    f"{deal.symbol} {deal.volume:.2f} @ {deal.price:.5f} "
                    f"(ticket {deal.ticket}, profit {deal.profit:.2f})",
                ]
            )

        return "\n".join(lines).rstrip() + "\n"
    finally:
        mt5.shutdown()


def main() -> None:
    """Generate and save the requested MT5 report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.days < 1:
        raise SystemExit("--days must be at least 1")

    report = build_report(args.days)
    output = args.output or Path(__file__).with_name("recent_mt5_trades_report.txt")
    output.write_text(report, encoding="utf-8")
    print(report)
    print(f"Report saved to: {output}")


if __name__ == "__main__":
    main()
