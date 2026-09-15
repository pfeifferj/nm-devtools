---
name: netfyr-review
description: "Review a netfyr implementation or spec PR against its requirements, revision history, tests, and measured behavior. Use for a netfyr PR, branch, or commit review."
---

# netfyr review

Read the [netfyr workflow](../netfyr/SKILL.md) for contribution rules and
[SpecDoc access](../netfyr/references/specdoc.md) for graph and API queries.

## Establish the baseline

Resolve the requested PR, branch, or commit and its base. Read the commit
messages and run `scripts/check-spec-ref.sh --range BASE..HEAD` in an
implementation checkout. Read the named merged spec, applicable top-level
specs, and dependencies.

Use `get` for the spec and `trace` for changed files. Check index freshness,
resolve ambiguous symbol IDs, and follow supersession links. Traces connect
whole files to spec-linked commits; inspect the diff to determine which
requirements the change actually affects.

If the board text differs from the merged file, inspect `/changes` or the
spec repo's history. A pending revision is not an already-merged requirement.
Missing provenance limits the conformance verdict; continue useful code and
test review while identifying the missing reference.

## Review the change

Check each affected acceptance scenario, FR, and SC against implementation and
test evidence. Identify unmet requirements, unrelated behavior changes, and
tests that would pass with the bug or missing feature present. Run a focused
regression test against the base when that establishes whether it catches the
change; broad tests can legitimately pass before the change.

Check the implementation's own contribution rules, including the shared
`Spec:` trailer, completing-commit rules, test tags, binary lookup, and failure
on missing prerequisites. Requirement IDs in tests or comments help traceability;
do not invent a mandatory comment format absent from the project.

Size additional code review to the change. Use an available `deep-review` skill
when a broader audit is useful; it is not a prerequisite for reading a small PR.
Style preferences alone do not block a correct change.

For a spec PR, compare the generated file with the reviewed publication:
requirements, accepted suggestions, path, and references must survive conversion.
Use the revision comparison when reviewing an amendment. Raise design changes
on the spec itself; a pending amendment does not establish conformance.

## Verify and report

Run checks relevant to the changed code. Apply paths and changed kernel
observations need an isolated namespace or VM test. Use the cheapest environment
that exercises the behavior. For VM work, follow the `testvm` lease and lifecycle
instructions; account for NetworkManager or other services owning the same links.

Report the spec and revision reviewed, findings with file/line and requirement
references, tests actually run, and unresolved coverage or environment limits.
Separate unmet requirements and failing checks from optional improvements.
If runtime testing was excluded by the user, state that limitation in the verdict.
