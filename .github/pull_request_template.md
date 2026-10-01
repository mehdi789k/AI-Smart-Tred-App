# Pull request checklist

## Safety and contracts

- [ ] I reviewed the risk gates affected by this change; order paths remain fail-closed.
- [ ] I updated `docs/API_CONTRACT.md` and/or `docs/DATA_CONTRACT.md` when endpoints or data structures changed, or confirmed neither applies.
- [ ] I updated `docs/ARCHITECTURE.md` when system design changed, or confirmed it does not apply.
- [ ] MT5 live execution remains disabled by default; fail-closed behavior and replay/Strategy Tester guardrails are preserved.

## Validation

- [ ] I added or updated tests for the behavior changed.
- [ ] I ran the relevant tests, lint, formatting, and type checks.
- [ ] I reviewed the CI coverage report; any missing coverage is explained below.
- [ ] I checked that no credentials, generated artifacts, or unrelated runtime data are included.

## Summary

<!-- Describe the change and its operational impact. -->

## Risk / rollback notes

<!-- Describe risk exposure, monitoring, and rollback steps. -->