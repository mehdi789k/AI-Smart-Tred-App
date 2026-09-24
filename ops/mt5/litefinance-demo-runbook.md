# LiteFinance Demo operator runbook

This procedure is for a controlled, Demo-only seam check. It does not validate
LiteFinance broker behavior automatically and must not be used with a Live
account or credentials.

## Preconditions

- Obtain project-owner and second-approver confirmation for the Demo window.
- Confirm `MT5_AUTO_TRADING_ENABLED=false` until the final, explicitly approved
  canary step.
- Use the symbols currently visible in the operator's MT5 **Market Watch**.
  Do not assume that `XAUUSD` or a broker suffix exists; record the exact
  symbol name and select only symbols approved for this run.
- Keep credentials in the terminal/account manager or environment injection.
  Never paste them into tickets, logs, fixtures, or screenshots.

## Prepare and compile the EA

1. Open the LiteFinance **Demo** account in MetaTrader 5 and confirm the
   account label is Demo.
2. In Market Watch, show the approved symbol(s), record their exact names, and
   remove unapproved symbols from the test scope.
3. Open the repository EA in MetaEditor, compile with strict mode, and save the
   compile result. A compile warning or error stops the run.
4. Start with `ops\mt5\SmartTraderEA.demo.set`, then replace
   `InpSymbolWhitelist` with the exact Market Watch symbol. The broker-symbol
   example is illustrative only.
5. Required safety flags are:

   ```text
   InpAllowLiveTrading=false
   InpEnableZmq=false
   InpMaxLotSize=0.01
   InpDailyLossLimitAccount=100.0
   InpMagicNumber=26090901
   ```

   `InpEnableZmq=true` is permitted only for the separately approved Demo
   seam check, with the local endpoint, an operator watching the terminal, and
   the Live flag still false. It is never enabled for replay tests.

## Execute and capture evidence

Before any automatic Demo start, run the readiness helper in its explicit
trading mode:

```powershell
py -3 scripts\verify_demo_readiness.py `
  --base-url http://127.0.0.1:8000 `
  --require-demo-trading `
  --demo-symbol <exact-Market-Watch-symbol> `
  --max-daily-loss 10
```

This mode requires `/ready` to report `account.trade_mode=demo`, connected MT5,
`trading.allowed=true`, and an armed circuit breaker. Missing, real, contest,
or unknown identity stops the run. It only reads `/health` and `/ready`; it
never calls an order endpoint. The expected safe states are `Demo trading
ready` when all gates pass, or `blocked: account is not verified Demo` /
`waiting for Demo MT5 readiness` when they do not.

For a dashboard health check when the API cannot access the Windows terminal,
use `--allow-direct-dashboard` instead. This is read-only and must not be
combined with `--require-demo-trading`; `mt5=unavailable` in this mode does
not authorize trading.

1. Run the offline matrix first:

   ```powershell
   py -3 -m pytest tests\e2e\test_litefinance_demo_contract.py -q
   ```

   Save the console output and commit-independent fixture checksums as the
   replay evidence. This proves the local boundary only.
2. For a Demo seam check, capture UTC timestamps, terminal/account Demo label,
   EA compile result, exact Market Watch symbols, EA input screenshot, and
   correlation IDs. Redact account numbers and all credentials.
3. Exercise heartbeat, accepted, rejected, timeout, duplicate, unknown,
   reconciliation, restart, and circuit-breaker scenarios from the approved
   checklist. A timeout or unknown result remains unresolved; do not retry an
   order automatically.
4. For an accepted result, capture broker order/deal identifiers. For an
   unknown result, capture the read-only broker history and operator decision
   as reconciliation evidence. Do not mark replay-only evidence as broker
   validation.
5. Record circuit-breaker activation, reason, daily-loss state, and the manual
   reset confirmation in the append-only audit log.

## Cleanup

- Disable `InpEnableZmq` and keep `InpAllowLiveTrading=false`.
- Stop the EA and API/replay process; close the Demo terminal session if the
  test window is complete.
- Delete only run-generated screenshots and redacted exports from the
  designated evidence location, according to retention policy. Do not delete
  audit or reconciliation records.
- Confirm no credentials, logs, databases, or generated artifacts were added
  to the repository root.

## Rollback and stop conditions

Stop immediately on an unexpected order, symbol mismatch, missing protective
levels, protocol error, duplicate submission, unavailable history, or any
uncertain account state. Activate the circuit breaker, preserve evidence, and
independently compare positions with the Demo broker.

Rollback means restoring the previous EA input preset, disabling ZMQ and
automated trading, and reverting this phase's change set. Never reset a
circuit breaker or resubmit an unknown order without explicit operator
review. Escalate any discrepancy; this runbook contains no Live activation
procedure.

## Windows local read-only auto-start

To make the local infrastructure and dashboard available after the current
Windows user logs on, run this once from the project root:

```powershell
.\scripts\register_local_demo_autostart.ps1
```

The installer first attempts a user-logon Task Scheduler entry. If Windows
denies that operation, it creates an equivalent shortcut in the current
user's Startup folder. The startup path launches Docker services and the host
Streamlit dashboard at `http://127.0.0.1:8501`, and verifies API/dashboard
health before exiting. Streamlit is launched as an independent process so the
startup script ending does not terminate the dashboard or interrupt its
WebSocket connection. Re-running the startup script is idempotent while the
dashboard is healthy.

This path is deliberately read-only: `MT5_AUTO_TRADING_ENABLED` and
`MT5_LEGACY_ORDER_PATH_ENABLED` are forced to `false`. It does not store
credentials and does not start a broker order canary. To stop the local stack
manually, run:

```powershell
.\scripts\stop_local_demo.ps1
```
