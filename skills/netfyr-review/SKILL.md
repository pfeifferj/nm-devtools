---
name: netfyr-review
description: "Adversarial review of a netfyr PR against the spec it names: resolve the diff and its spec, tier it by what it touches, check requirement-to-test traceability, hand the generic code dimensions to /deep-review, verify apply-path behaviour in a netns or a test VM, then emit a verdict."
when-to-use: "When reviewing, approving, or requesting changes on a PR in netfyr/netfyr or netfyr/specs, when asked whether a netfyr branch is ready to open as a PR, or when triaging why a netfyr PR's CI is red."
allowed-tools: [Agent, Read, Bash, Glob, Grep]
context: inline
argument-hint: "[<pr-url> | 'branch' | <commit-sha>] [--no-vm]"
---

# Reviewing a netfyr PR

The spec is the baseline, not the diff. A PR is correct when it does what its spec says,
no less and nothing else. Good code that is a bad match for its spec is a block, and code
that improves on the spec is still a block until the spec catches up.

See the `netfyr` skill for the process, `netfyr-bugs` for what a fix PR must carry, and
`testvm` for the VMs used in Phase 5.

## Phase 1: Resolve the target and its spec

| Argument | Diff |
|----------|------|
| `<pr-url>` | `gh pr diff <n>` and `gh pr view <n>` |
| `branch` / empty | `git diff $(git merge-base HEAD origin/main)...HEAD` |
| `<commit-sha>` | `git show <sha>` |

Then: `git log <base>..HEAD` for the commit messages, and
`scripts/check-spec-ref.sh --range <base>..HEAD` to extract the accepted `Spec:`
reference. Fetch that spec from netfyr/specs and **read its Acceptance Scenarios, FRs,
and SCs before the diff**. Reviewing the diff first means reviewing whether the code does
what it does.

IMPORTANT: no resolvable spec means stop here and report it. Reviewing an unspecified
diff is how deviation gets merged.

## Phase 2: Tier the diff

Score each changed file. This drives Phase 5, not whether to review.

- **APPLY**: anything that writes system state (netlink send paths, interface/route/addr
  mutation, rollback and checkpointing, privilege handling). Requires Phase 5.
- **QUERY**: netlink read paths, state normalization, serialization. Requires Phase 5
  only where the diff changes what is observed.
- **PURE**: parsing, validation, schema, diffing, CLI argument handling, pure functions.
  Unit-testable; no VM.
- **META**: docs, CI, `Makefile`, test harness. Check the harness cannot now skip.

Output `path | tier | reason` and keep it.

## Phase 3: Spec conformance

Netfyr-specific and blocking. Run it inline, before any generic review.

Build the traceability table: every FR and SC in the spec against the test that covers
it, from the `covers:` headers and `#[cfg(test)]` module names.

| requirement | covering test | would it fail without the diff? |
|-------------|---------------|---------------------------------|

Answer the third column by checking out the base and running that test, not by reading
it. A test that passes against `main` is not evidence of anything.

Blocks:

- **Undeclared deviation.** The code does something other than what FR-nnn says, and the
  PR explains why. The explanation is the problem: a requirement that turned out wrong
  goes back to the board as an amendment, and the PR waits. This is the failure mode the
  whole process exists to prevent.
- **A requirement with no test**, or one covered by a test that is green on the base.
- **Scope creep.** One spec per PR; a hunk the spec does not mention is a second PR,
  however small.
- **Tests edited to go green.** Legitimate expectation changes say which requirement made
  the old expectation wrong.
- **A skip**: `exit 0` on a missing prerequisite, a silently-passing conditional, a retry
  loop hiding a flake. `tests/no-skip-policy.sh` greps for the pattern; read the diff for
  what it cannot.
- **A paraphrased requirement in a code comment.** Comments cite the id; a paraphrase
  drifts from the spec and nothing detects it.
- **Missing or duplicate `Spec:` trailer**, or trailers disagreeing between the commits
  and the PR body. CI catches this; do not approve around it.
- **Nondeterminism**: network outside a namespace, wall-clock or ordering dependence,
  shared state between tests, no cleanup trap, an untagged test (it drops out of every
  filtered run), a hardcoded `../target/debug` instead of `NETFYR_TARGET_DIR`.

## Phase 4: Generic code review

Invoke `/deep-review` on the same diff and fold its findings in. It covers scope, reuse,
quality, perf, security, tests, and voice; do not re-derive those here.

Two demotions when merging its output: a `[SCOPE]` finding that the spec explicitly
requires is not a finding, and a `[QUALITY]` suggestion that would deviate from an FR
loses to the FR. Everything else keeps its severity.

## Phase 5: Run it

`make test`, `make fmt`, `make clippy` locally. Green CI proves the suite passed, not
that the suite tests the spec.

Then, for APPLY-tier and observation-changing QUERY-tier diffs, exercise the built binary
against a real kernel. Cheapest sufficient environment wins:

1. **Rootless netns** (`unshare -rn`, or a `nm-transitions` case in this repo) for
   anything that only needs interfaces, addresses, and routes. No VM, no root, seconds.
   A transitions case also gives before/after normalized state, which is the right shape
   for checking an apply-path claim.
2. **A test VM** (`testvm up`, then ssh) when the change needs real hardware paths,
   systemd, a reboot, or a distro's kernel: `testvm snapshot before-review`, scp the
   binary in, run it, `testvm rollback before-review`. `TESTVM_DOMAIN=nm-c10s` for the
   RHEL target.

VM traps that produce fake review results:

- **Stop NetworkManager first.** The guests run NM (a patched dev build on `nm-rawhide`),
  and two daemons managing the same links produce state changes the diff did not cause.
- **Fix the clock after a rollback** before installing anything: snapshots capture RAM,
  so the guest resumes frozen and `dnf` will remove packages to satisfy dependencies
  while exiting 0. See the `testvm` skill.
- Roll back afterwards. A guest carrying your test state silently poisons the next review.

`--no-vm` skips this phase; say so in the report rather than implying it ran.

## Phase 6: Report

```
SPEC: <spec path>, <one line on what it requires>
SCOPE: <one line>
TIERS: <n> apply, <n> query, <n> pure
VERIFIED: <netns | vm:<domain> | none (--no-vm)>

BLOCKING
  - <file:line | FR-nnn>: <issue>

IMPORTANT
  - <file:line>: <issue>

MINOR
  - <file:line>: <issue>

UNCOVERED REQUIREMENTS
  - <FR-nnn>: <no test | test green on base>

CHECKED CLEAN
  - <area>: <what you actually ran>

VERDICT: APPROVE | FIX FIRST | BLOCK
```

`BLOCK` for any Phase 3 block or failing test. `FIX FIRST` for an IMPORTANT-tier finding
that survives. Drop empty sections; never emit `APPROVE` without naming what you ran.

## What is a comment, not a block

Naming, structure, and factoring inside a correct implementation. Say it once, mark it
non-blocking, approve. Missing `CHANGELOG.md` entry, a clearer commit subject, a test
that could be smaller: ask, do not hold the branch.

Design objections belong on the SpecDoc note as CriticMarkup threads, before the spec
merges. Raising one for the first time on an implementation PR is late and makes the
approval meaningless. Move it back to the board, block on the amendment, and say that is
what is happening.

Reviewing a netfyr/specs PR is a different job: the board already carried the review and
the PR is generated, so check it for a bad conversion (dropped section, leftover
CriticMarkup, wrong path or number) rather than re-arguing it.

## Traps

- **`implements netfyr/specs#N` only moves the board card from the default branch.** A
  feature branch merged into another feature branch leaves the spec open; check the base.
- **A bare `implements #N` in netfyr/netfyr resolves nothing.** It needs the repo prefix.
- Merge, revert, and `fixup!`/`squash!` commits are exempt from the trailer check, so a
  branch of fixups can pass CI with the real commit unreferenced.
- **The workspace is empty until the first crate lands**, so `cargo`-backed targets exit
  101 and `make test` skips the build. On such a PR, a green `make test` means nothing
  was built.
