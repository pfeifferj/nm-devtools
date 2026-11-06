---
name: netfyr-bugs
description: "Diagnose and fix a netfyr regression or incorrect behavior using a reproducing test and the spec requirement it violates."
---

# netfyr bug fixes

Use the [netfyr workflow](../netfyr/SKILL.md) for project conventions and
[SpecDoc access](../netfyr/references/specdoc.md) to find requirements and history.

## Reproduce and locate the requirement

Capture the reported input, observed result, and relevant environment. Build a
focused test that fails on the reported behavior before applying a fix. A build
failure or missing prerequisite does not demonstrate the reported bug.

For Linux network evidence, use the
[network diagnostics workflow](../network-diagnostics/SKILL.md). Analyze an
existing sos report before requesting fresh collection, retain its source and
capture time, and keep missing or unsupported evidence explicit. Diagnostic
findings help target reproduction; they do not establish a violated requirement
or a passing test. Preserve existing guest evidence outside the VM before using
a reproduction workflow that resets it. Collect fresh evidence on the affected
system and namespace; analyst-host state cannot replace a missing snapshot.

Use `trace` on the affected file or symbol, then `get` and `neighbors` on its
spec. Inspect graph freshness and file-level attribution. Compare the merged
requirement with the board's `/changes` history when behavior or approval text
has moved; check for a replacement spec before using a retired requirement.

If no requirement covers the behavior, identify the specification gap and any
existing documented contract. Continue diagnosis without inventing a requirement
or silently changing the intended behavior.

## Fix and verify

Choose the test layer for the failure: Rust unit tests for isolated logic,
shell integration tests for CLI and system interactions. Assert observable
output or state, and follow the repository's tags, binary lookup, namespace,
and missing-prerequisite conventions.

Fix the cause, rerun the regression test, and run the affected suite. Check that
the test fails with the correction removed when this has not already been
established by the reproduction. Keep the test with the fix.

For intermittent failures, preserve the failing evidence and test for shared
state, lifecycle races, and environmental dependence. A retry is not evidence
that the cause was fixed. Use repeated runs when needed to assess stability.

Commit descriptions name the violated requirement and explain the behavior
change. Follow the project's `Spec:` and, when applicable, `Fixes:` trailers.
Use `implements netfyr/specs#N` only when the change completes that spec.
Do not add a user-facing changelog entry for internal test or tooling changes.
