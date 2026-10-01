# Execution Safety and API Authentication Design

**Date:** 2026-09-16  
**Status:** Approved for implementation planning

## Goal

Reduce the risk of unauthorized reads, duplicate live orders, and loss of
execution safety state after an API restart or dependency failure.

## Scope

This phase covers:

1. Requiring authentication for sensitive `/api/v1/*` endpoints.
2. Separating read, order-review, execution, and emergency roles.
3. Persisting execution safety state in the existing SQLAlchemy database.
4. Preserving fail-closed behavior when authentication or durable state is
   unavailable.
5. Distinguishing ZeroMQ timeout, transport, protocol, and unexpected errors.
6. Adding regression and integration tests for the above behavior.

This phase does not send orders to MT5, change MQL5 files, add new public API
features, or replace the existing broker reconciliation policy.

## Current Risks

- `GET /api/v1/data/ohlc` and `GET /api/v1/orders/unknown` use an
  authentication dependency that permits anonymous access when credentials are
  not configured.
- Demo activation, emergency stop, authorization expiry, and the demo trade
  counter are process-local in `LiveOrderWorkflow`.
- Production startup currently combines migration execution with
  `Base.metadata.create_all`, allowing ORM schema creation to mask migration
  drift.
- ZeroMQ gateway handling maps broad exceptions to one unavailable error,
  reducing diagnostic precision.

## Design

### Authentication and roles

`AuthenticationService` remains the single token-validation boundary. Add an
`order_review` role. The API will require:

| Route | Required role |
|---|---|
| `/api/v1/data/ohlc` | `read_only` |
| `/api/v1/orders/unknown` | `order_review` |
| `/api/v1/signals/{signal_id}/execute` when live | `signal_execution` |
| reconciliation routes | `emergency_stop` |

Health remains public. Readiness remains non-executing and must not grant
trading permission. Production configuration must fail closed when protected
API credentials are not configured; test factories may inject an explicit
anonymous mode for existing isolated tests.

### Durable execution control

Add a database model and repository for a single account/symbol execution
control record. The record stores:

- stable account/symbol scope
- emergency-stop state and reason
- demo activation state
- authorization/session expiry
- demo trade count
- daily loss snapshot
- version and update metadata

State updates are transactional and use a version check to prevent lost
updates. Startup loads this record before exposing live execution. Missing or
unreadable control state blocks live execution. Confirmation tokens remain
short-lived and single-use; only a hash is persisted.

### Execution workflow integration

`LiveOrderWorkflow` receives a durable control store through dependency
injection. Existing in-memory fields remain only as a per-request cache and are
refreshed from the store before every live order. Emergency stop and demo
limits are persisted before and after state transitions. No restart may
implicitly activate demo mode or clear an emergency stop.

### ZeroMQ error taxonomy

`MQL5ExecutionGateway.request` will preserve fail-closed behavior while
classifying:

- timeout (`zmq.error.Again` or `TimeoutError`)
- transport/socket failure (`zmq.error.ZMQError`)
- local message construction/serialization errors
- invalid response schema
- unexpected internal exceptions

Only transport failures reset the socket. Unexpected internal exceptions are
logged and re-raised without being disguised as broker unavailability.
Timeouts never trigger an automatic order retry.

### Schema ownership

Production startup will continue to run Alembic migrations, then verify the
schema. `Base.metadata.create_all` remains available only for explicit
test/local ephemeral setup and must not be used by the production lifespan.

## Failure handling

- Missing API credentials in production: startup/configuration failure.
- Missing durable control record: live order rejected with a stable safety
  error.
- Database unavailable during a live order: order is not sent.
- Confirmation-token replay: order rejected.
- ZeroMQ timeout: order outcome is unknown; no retry is issued.
- Invalid EA response: gateway unavailable/protocol error; no order is
  considered accepted.
- Concurrent control update: retry the state read once, then fail closed if
  the version conflict remains.

## Testing strategy

Tests must cover authentication status codes and role separation, persistence
across workflow recreation, token single-use, emergency-stop persistence,
database-unavailable fail-closed behavior, ZeroMQ error classification, and
the existing idempotency/unknown-order behavior. No real MT5 account or
`order_send` call is used in this phase.

## Acceptance criteria

1. Sensitive API reads return `401` without credentials.
2. `order_review` cannot execute or reset emergency state.
3. A recreated workflow preserves emergency stop and demo limits.
4. Missing durable state prevents live execution.
5. Gateway timeout and protocol errors cannot create an automatic retry.
6. Production startup does not call `create_all`.
7. Existing tests and all new regression tests pass.
