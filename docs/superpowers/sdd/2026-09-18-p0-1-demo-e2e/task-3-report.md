# Task 3 evidence: durable broker outcome state

## Implemented

- Canonical live execution now serializes broker result evidence before durable
  transition, including retcode, broker order ID, and broker deal ID.
- Explicit broker rejection is terminal (`rejected`) and its idempotency record
  is retained, so replay cannot invoke the workflow or `order_send` again.
- Missing or malformed broker responses remain fail-closed `unknown` outcomes.
- Malformed retcodes are classified as `AmbiguousOrderOutcome`; no resend is
  attempted.
- Accepted MT5 retcodes now require positive broker order and deal identifiers.
  Missing or non-positive identifiers are classified as `AmbiguousOrderOutcome`,
  persisted as `unknown`, and remain idempotently non-retryable.
- Magic-number and confirmation validation now runs before the durable
  idempotency claim, so corrected retries are not permanently wedged.
- Durable repository transitions persist broker order/deal identifiers and
  accept the terminal `rejected` idempotency status.
- Identifier validation is limited to canonical opening market and pending
  orders. Non-opening actions such as `TRADE_ACTION_SLTP` retain accepted
  broker results even when MT5 omits opening-order identifiers, preserving
  break-even stop updates.

## Tests

RED was verified first with regression tests for missing/non-positive accepted
broker identifiers, incomplete durable acceptance, and corrected idempotency
retries after invalid magic or missing confirmation. Focused validation:

```text
py -3.12 -m pytest -q tests/python/execution/test_live_order_workflow.py::test_accepted_broker_result_without_positive_identifiers_is_ambiguous tests/python/api/test_execution_safety.py::test_incomplete_accepted_execution_is_unknown_and_not_retried tests/python/api/test_execution_safety.py::test_invalid_magic_does_not_claim_idempotency
4 passed
```

Final focused validation:

```text
py -3.12 -m pytest -q tests/python/execution/test_live_order_workflow.py tests/python/api/test_execution_safety.py
56 passed
py -3.12 -m ruff check [changed Python files]
All checks passed
```

Mypy was also run against the changed source modules. It reports five existing
SQLAlchemy typing errors in `src/python/data/database.py` (including the
pre-existing enum assignment and optional control-state return diagnostics);
the focused pytest and Ruff gates pass.

The regression was first reproduced with a failing break-even SLTP test using
an accepted result without `order`/`deal` fields. After scoping identifier
validation to opening order calls, the regression and opening-order
fail-closed cases pass:

```text
py -3.12 -m pytest -q tests/python/execution/test_live_order_workflow.py::test_modify_position_stop_accepts_sltp_result_without_opening_identifiers tests/python/execution/test_live_order_workflow.py::test_accepted_broker_result_without_positive_identifiers_is_ambiguous
3 passed
py -3.12 -m ruff check src/python/execution/live_order_workflow.py tests/python/execution/test_live_order_workflow.py
All checks passed
```
