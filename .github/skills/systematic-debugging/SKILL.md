---
name: systematic-debugging
description: Investigate root causes before changing code for bugs, test failures, build failures, and unexpected behavior. Use this skill when investigating errors, fixing test failures, resolving build issues, or debugging unexpected application behavior. Keywords: debugging, root cause analysis, bug fix, test failure, build failure, stack trace, hypothesis testing, regression test, error investigation.
---

<!-- Tip: Use /create-skill in chat to generate content with agent assistance -->

# Systematic Debugging

Apply this workflow to every bug, test failure, build failure, integration issue, or unexpected behavior.

## Root-cause investigation
- Read the complete error message, warnings, and stack trace.
- Reproduce the issue consistently and record the exact command and observed output.
- Inspect recent code, dependency, configuration, and environment changes.
- Trace the failing value or control flow backward to its original source.
- For multi-component failures, inspect inputs and outputs at every component boundary.

## Pattern analysis
- Find a working example in the same codebase or the authoritative reference.
- Compare the working and failing paths line by line.
- List all meaningful differences, including configuration and dependency assumptions.

## Hypothesis and testing
- State one specific, testable root-cause hypothesis.
- Test it with the smallest possible diagnostic or code change.
- Change one variable at a time and record whether the evidence confirms the hypothesis.
- If the hypothesis fails, return to investigation and form a new one.

## Implementation and verification
- Create a minimal regression test or reproduction before applying the final fix.
- Implement one root-cause fix without unrelated refactoring.
- Run the relevant existing tests, lint, type checks, and build commands.
- Read the complete output and verify the exit code before claiming success.
- If three fixes fail, stop and question the architecture instead of attempting another symptom-level patch.

## Safety and Quality Rules
- Never guess and patch symptoms.
- Never claim an issue is fixed without fresh verification evidence.
- Report unresolved failures explicitly.