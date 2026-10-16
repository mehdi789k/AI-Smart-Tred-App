---
name: Bug report
about: Report a reproducible defect or unsafe behavior.
title: "[Bug]: "
---

## Summary

Describe the observed problem.

## Reproduction

List the steps and commands needed to reproduce it. Remove credentials and
account-specific or broker-sensitive information.

## Expected behavior

Describe what should happen.

## Actual behavior

Describe what happened, including sanitized logs or error output.

## Environment

- Python / MT5 / Docker versions:
- Operating system:
- Commit or release:

## Safety impact

- Does this affect live/demo execution, order state, risk limits, or reconciliation?
- Did any order or external side effect occur? Do not include account identifiers.

## Validation

- [ ] I reproduced this with live trading disabled.
- [ ] I removed secrets and private trading data from this report.
