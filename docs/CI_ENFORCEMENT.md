# CI and staging enforcement

## GitHub branch protection (manual administrator action)

Workflow files can publish checks but cannot make a check required. A
repository administrator must configure branch protection for `main` in
**Settings → Branches → Branch protection rules** (or the equivalent ruleset):

1. Require a pull request before merging and at least one approving review.
2. Require branches to be up to date before merging.
3. Require the stable `test` job from
   `.github/workflows/python-validation.yml` as a status check.
4. Keep the `runtime-lock-alignment` job required as well, so every change is
   checked in a clean Python 3.12 environment.
5. Prevent bypassing these rules except for the explicitly designated
   repository administrators.

This checkout cannot verify GitHub repository settings. Record the administrator,
date, repository, and a link or screenshot of the applied rule as release
evidence. Do not describe branch protection as enforced solely because the
workflow exists.

## CI validation contract

The `test` job remains the canonical fast repository gate: Python 3.12,
compilation, changed-file Ruff format/lint, changed-file Mypy, the full pytest
suite, Alembic migration validation, and Docker Compose configuration. The
`runtime-lock-alignment` job invokes
`scripts\validate_runtime_lock_alignment.ps1`, which creates a disposable
Python 3.12 environment and validates the full lock installation and runtime
surface. The workflow must not grow conflicting hand-written duplicates of
that validator.

## Staging concurrency evidence

Before Demo activation, run the PostgreSQL concurrency test in a staging
deployment with **two independent workers and two independent database
connections**. Capture:

- deployment identifier and PostgreSQL/TimescaleDB version;
- worker identifiers, transaction/isolation settings, and test timestamp;
- the single winner and the expected conflict/loser result;
- database/audit evidence showing no duplicate claim or order;
- command, test revision, and exit status.

Use a disposable test record or an explicitly scoped staging fixture. Redact
credentials and customer or broker data from all evidence.

## Secret injection and rotation evidence

Staging secrets must be injected by the approved secret manager or deployment
secret mechanism, never committed to the repository, fixtures, workflow output,
or logs. Before Demo activation, retain evidence of:

- successful injection of the required database/API/MT5 settings without
  printing values;
- access scope and owner for each secret;
- a controlled rotation (old value revoked, new value accepted);
- service restart/reload behavior after rotation;
- validation that logs, artifacts, and test output contain no secret values;
- timestamp, operator, deployment revision, and secret-manager audit reference.

Secret values themselves must never be attached to the evidence bundle.
Rotation and injection evidence is an external staging gate; local CI
success does not satisfy it.
