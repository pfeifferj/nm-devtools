---
name: netfyr-bugs
description: "How a netfyr bug is fixed: reproduce it as a test that fails against current code for the reported reason, find the spec requirement it violates, fix until that test goes green, and keep the test as the regression guard."
when-to-use: "When a netfyr bug is reported, reproduced, triaged, or fixed: crash reports, wrong output, a regression, 'this used to work', a flaky test, or any change to netfyr/netfyr whose purpose is to correct existing behaviour rather than add new behaviour."
allowed-tools: [Bash, Read]
context: inline
---

# netfyr bug wrangling

A bug is a gap between what a spec requires and what the code does. Fixing it is the
same test-first loop as implementing a spec, entered from the other end: the failing
test exists before the diagnosis is trusted, not after the fix looks right.

See the `netfyr` skill for the spec process this sits inside.

## Order of work

1. **Reproduce as a test, before reading the code.** Write the smallest test that
   exercises the reported behaviour and watch it fail. Reading the source first biases
   the reproduction towards the bug you expect rather than the one that was reported.
2. **Check the red is the right red.** The test must fail because the behaviour is wrong,
   with the reported symptom in the failure output. A compile error, a missing fixture,
   or a typo'd path is not a reproduction. If it passes, the report is wrong, the
   reproduction is wrong, or the bug is environmental: say which before going further.
3. **Name the requirement.** Find the `FR-nnn`/`SC-nnn` the failure violates; that spec
   is the `Spec:` trailer on the fix. If no requirement covers the behaviour, the code is
   not buggy but unspecified: the spec goes back to the board first, and the bug becomes
   a spec amendment.
4. **Fix until green.** Nothing else changes in that commit. Do not weaken the test, and
   do not fix the neighbouring thing you noticed on the way.
5. **Run the whole suite.** A fix that reds another test is not done; it is a second bug
   report against your own diff.
6. **Verify the guard.** Revert the fix, confirm the test fails again, restore. A
   regression test that passes against the broken code guards nothing.

## The reproduction test

Same conventions as any netfyr test (`tests/*.sh` with tags and `covers:` in the first
10 lines, `#[cfg(test)]` for pure logic, no skips, `unshare -rn` for network work,
`mktemp -d` with a cleanup trap).

- **Lives where the bug lives.** A parsing or validation bug gets a unit test next to the
  code, not a shell script driving the CLI. Reach for `tests/` only when the bug needs
  the binary, the filesystem, or a namespace to appear.
- **Asserts the symptom, not the cause.** The observable wrong output, exit status, or
  state, so the test survives the fix being rewritten later. Never assert on log wording.
- **Minimal input.** Cut the reporter's case down until removing anything more makes the
  failure disappear. A 3-line reproduction documents the bug; a 200-line one documents
  the reporter's config.
- **Named for the behaviour, not the ticket.** `route-metric-zero-dropped.sh`, not
  `issue-47.sh`. The number is in the commit message; the file has to still make sense
  when the tracker moves.
- **Stays after the fix.** It is a regression test now, and it keeps its `covers:` line.

## Committing a fix

`fix(area): ...`, with the `Spec:` trailer naming the spec whose requirement was
violated, and the tracker reference in the body. Test and fix in one commit: they are one
reviewable increment, and splitting them puts a knowingly-red commit on the branch.

If the bug was a regression, name the commit that introduced it in the body. If the fix
also completes an unimplemented requirement, `implements netfyr/specs#N` still applies.

To find the spec a file answers to, `trace file:<path>` on the specdoc MCP server
(`netfyr` skill) lists the commits that touched it and the specs those commits
implement; `get spec:netfyr/specs#N` then gives the requirements to quote.

## Flaky tests

A test that fails intermittently is a bug in the test until proven otherwise, and it is
urgent: determinism is a spec requirement, and an ignored test is a deleted test.

Reproduce by running it in a loop (`for i in $(seq 50); do ...; done`), then look for the
usual causes in order: shared state between tests, wall-clock or ordering assumptions,
host network reached instead of a namespace, and a temp dir not cleaned by its trap.
Never paper over one with a retry, a sleep, or a skip.
