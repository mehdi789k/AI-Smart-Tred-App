# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added
- Hardened execution and safety gates around order workflow, daily-loss enforcement, and Demo configuration limits.
- Durable execution-control state with transactional writes and optimistic version checks.
- Strict ZeroMQ/EA protocol validation and fail-closed handling for timeouts, malformed responses, and unknown outcomes.
- Project-level readiness and Demo-readiness evidence artifacts for operational review.
- Recovery drill documentation for staging backup/restore and RTO/RPO interpretation.

### Improved
- Clarified the boundary between local validation and live execution proof.
- Tightened the role-based auth model for sensitive routes and operational actions.
- Documented the exact conditions under which Demo execution is allowed and when it remains blocked.
- Added structured reporting for the remaining broker/EA E2E gate and final confirmation requirements.

### Fixed
- ZeroMQ response contract mismatch in the MQL5/EA path was corrected to prevent false rejection of valid accepted states.
- Readiness checks were updated to avoid overstating operational readiness without live broker proof.
- Demo-safe runtime configuration was aligned around bounded symbols, size limits, and disabled auto-trading.

### Security and control note
- This project remains in a fail-closed configuration.
- No live order execution is approved by default.
- Final confirmation and live broker evidence are still mandatory before sending any Demo or production order.

## [2026-09-18]

### Documentation and readiness updates
- Added explicit readiness and safety documentation for the remaining P0.1 live-broker gate.
- Recorded the Demo-limited validation path and the operational boundary for read-only checks.
- Captured the current status of backup/restore, staging drill, and final readiness evidence in repository artifacts.

### Status
- Local validation: complete and documented
- Live EA/broker execution: blocked pending fresh evidence
- Final live-trade approval: not granted

### Merge-ready documentation
- Added the explicit final gate checklist for reopening P0.1 only after fresh broker evidence and operator approval.
- Clarified that the repo is ready for safe review and merge documentation, but not for live trading or real Demo execution.
