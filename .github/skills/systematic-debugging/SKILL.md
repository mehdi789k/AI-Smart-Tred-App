---
name: systematic-debugging
description: Investigate root causes before changing code for bugs, test failures, build failures, and unexpected behavior.
---

# Systematic Debugging

Apply this workflow to every bug, test failure, build failure, integration issue, or
unexpected behavior.

## Root-cause investigation

1. Read the complete error message, warnings, and stack trace.
2. Reproduce the issue consistently and record the exact command and observed output.
3. Inspect recent code, dependency, configuration, and environment changes.
4. Trace the failing value or control flow backward to its original source.
5. For multi-component failures, inspect inputs and outputs at every component boundary.

## Pattern analysis

1. Find a working example in the same codebase or the authoritative reference.
2. Compare the working and failing paths line by line.
3. List all meaningful differences, including configuration and dependency assumptions.

## Hypothesis and testing

1. State one specific, testable root-cause hypothesis.
2. Test it with the smallest possible diagnostic or code change.
3. Change one variable at a time and record whether the evidence confirms the hypothesis.
4. If the hypothesis fails, return to investigation and form a new one.

## Implementation and verification

1. Create a minimal regression test or reproduction before applying the final fix.
2. Implement one root-cause fix without unrelated refactoring.
3. Run the relevant existing tests, lint, type checks, and build commands.
4. Read the complete output and verify the exit code before claiming success.
5. If three fixes fail, stop and question the architecture instead of attempting another
   symptom-level patch.

Never guess and patch symptoms. Never claim an issue is fixed without fresh verification
evidence, and report unresolved failures explicitly.
