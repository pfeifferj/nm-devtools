---
name: netfyr
description: "How netfyr is developed: specs are written and reviewed on the SpecDoc board, merged into netfyr/specs, then implemented test-first in netfyr/netfyr with every commit, test, and PR quoting the spec it came from."
when-to-use: "When writing or reviewing a netfyr spec, starting work on one, deciding what a test should assert, wording a commit or PR in netfyr/netfyr, or any mention of SpecDoc, the spec board, netfyr/specs, the Spec: trailer, FR-/SC- ids, or 'which spec does this belong to'."
allowed-tools: [Bash, Read]
context: inline
---

# netfyr

netfyr is a declarative Linux network configuration tool (netlink, no daemon on the
query and apply paths). Development is spec-driven end to end: a spec is reviewed and
approved before any code exists, and every artefact downstream of it names it.

## The three places

| where | what lives there | who writes it |
|-------|------------------|---------------|
| SpecDoc, a HedgeDoc fork: notes at [md.josie.cloud](https://md.josie.cloud), board at [specs.josie.cloud](https://specs.josie.cloud) | specs while they are being written and reviewed: collaborative notes, CriticMarkup comment threads, kanban board | authors and reviewers |
| [netfyr/specs](https://github.com/netfyr/specs) | approved specs as `[area/]NNN-slug.md` at the repo apex, `roles.yml`, `.github/validate.py` | nobody by hand; the board opens the PR |
| [netfyr/netfyr](https://github.com/netfyr/netfyr) | the Rust workspace, shell integration tests, `scripts/check-spec-ref.sh` | implementers |

The rule that ties them together: **no code without an approved spec.** If a change has
no spec to point at, write the spec first. That includes tooling, CI, and docs changes.

## Spec lifecycle

1. **Draft.** A note on the board with `tags: [spec, draft]`, `owner`, `namespace:
   netfyr/specs`, and `area:` picking the directory. Design argument happens here, in the
   note and its comment threads, not in a PR.
2. **ready-for-review** once the author considers it reviewable.
3. **in-review** automatically, as soon as the first comment thread appears.
4. **approved** when quorum is met (`roles.yml`: approvers `bengal`, `pfeifferj`,
   `approvals-required: 1`) *and* every thread is resolved. An `approved` tag alone does
   nothing on a governed repo.
5. The board then locks the note, opens the spec PR against netfyr/specs with
   CriticMarkup resolved and frontmatter stripped, and records `Spec-Id`, `Reviewed-on`,
   and `Reviewed-by` trailers. **The PR number becomes the spec's reference number.**
6. **implemented** when a commit carrying `implements netfyr/specs#N` merges to the
   default branch of an implementation repo. The card leaves the board.

Reviewing happens in CriticMarkup (`{>>@name: text<<}` comments, insert/delete/replace
suggestions) through the editor toolbar. A thread is resolved by accepting, deleting, or
using the resolve button. Unresolved threads block approval, which is the point: an
unanswered design objection cannot be merged past.

Full reference: `~/src/hedgedoc/docs/spec-lifecycle.md`.

## Writing a spec

Follow the shape of the merged specs (`meta/000-project-setup.md` is the model), because
the structure is what makes a spec implementable and testable:

```
# Feature Specification: <Title>
<abstract paragraph; the board uses it as the PR abstract>

## User Scenarios & Testing
### User Story N - <name> (Priority: P0..P2)
**Why this priority**: / **Independent Test**: / **Acceptance Scenarios**:
1. **Given** ... **When** ... **Then** ...
### Edge Cases

## Requirements
### Functional Requirements
- **FR-001**: The X MUST ...
### Key Entities

## Success Criteria
- **SC-001**: <observable, measurable outcome>

## Assumptions
```

Rules:

- **Every requirement gets a stable id.** `FR-nnn` and `SC-nnn` are the vocabulary
  everything downstream quotes. Never renumber a merged requirement.
- **Requirements are testable and behavioural.** RFC 2119 MUST/SHOULD, stated as an
  observable outcome, not an implementation. "MUST reject a reference that does not
  resolve" is a requirement; "MUST use a regex" is not.
- **Say why inline.** The reason a requirement exists belongs in the requirement, in
  parentheses or a trailing sentence. Reviewers approve reasoning, not assertions.
- **Acceptance scenarios are the test plan.** Each Given/When/Then becomes at least one
  test. If a scenario cannot be turned into a test, it is prose, not a scenario.
- **Numbering and area.** The number is assigned when the note is created and has gaps
  (`meta/000`, `core/002`, `core/003`, then `009`); it is not the file order and not the
  spec's reference number either. Do not write a spec path into commits before the spec
  merges: until then the number and the slug can both still move.
  `roles.yml` `areas` is an allowlist (`meta`, `workspace`, `testing`, `core`, `api`,
  `plugins`, `cli`, `migration`, `observability`). Specs land at the apex
  (`specs-dir: .`).
- **Dependencies point backwards only.** State them in Assumptions. If your spec needs
  something unmerged, say so rather than implementing it yourself: it belongs to whoever
  owns the dependency.

`python3 .github/validate.py` in netfyr/specs is what CI runs. It rejects a path that is
not `[area/]NNN-slug.md`, leftover frontmatter, a file with no `# ` heading, and
unresolved CriticMarkup.

## Implementing a spec

One spec per branch, one spec per PR. Branch name mirrors the spec path
(`core/002-schema-validation`).

**Quote the spec everywhere:**

| artefact | how the spec appears |
|----------|----------------------|
| commit message | `Spec: core/002-schema-validation` trailer in the final paragraph, exactly one, enforced by the `commit-msg` hook and by `scripts/check-spec-ref.sh --range` in CI |
| the commit that completes the spec | additionally `implements netfyr/specs#N`, which is what moves the board card to implemented |
| pull request | description ends with the same `Spec:` trailer; CI fails if it disagrees with the commits |
| test script | header comment naming the requirements it covers: `# covers: FR-009, SC-004` |
| code comment | cite the id (`FR-004 forbids a second dependency here`), never paraphrase the requirement. A paraphrase drifts from the spec silently |
| `CHANGELOG.md` | entry under `## [Unreleased]` naming the spec in parentheses |

`scripts/check-spec-ref.sh` (netfyr/netfyr) takes `--msg FILE` or `--range A..B`, prints
the accepted reference on stdout, and with `VERIFY_SPEC_EXISTS=1` resolves it against
netfyr/specs. Merge, revert, and `fixup!`/`squash!` commits are exempt; nothing else is.

**Deviation is a spec change.** If the code cannot satisfy a requirement, the spec goes
back to the board and gets amended or superseded. Implementing something other than what
FR-nnn says, and explaining it in the PR, is the failure mode this whole process exists
to prevent.

## Test-first, deterministically

Order of work, per spec:

1. Read the spec's Acceptance Scenarios and Success Criteria and write the test list.
   Every scenario and every SC gets at least one test before any implementation exists.
2. Write the tests. They must fail **for the right reason**: because the behaviour is
   wrong or absent, not because the code does not compile. A compilation error is never a
   valid red.
3. Implement until green. Do not edit a test to make it pass; if a test is wrong, say why
   it is wrong against the spec, then fix it deliberately.
4. Re-read the spec and check every FR and SC is covered by something that would fail if
   the behaviour regressed.

**Unit vs integration** (FR-005, FR-006 of `meta/000-project-setup`): unit tests are
`#[cfg(test)]` modules in the source file, covering parsing, validation, invariants, and
pure functions, with no external tools, namespaces, or built binaries. Shell scripts in
`tests/` cover CLI behaviour, system interaction, and end-to-end flows. When in doubt,
unit test.

**Integration test conventions:**

```bash
#!/bin/bash
# tags: ipv4 routing
# covers: FR-003, SC-002
```

- Descriptive name (`set-mtu.sh`), tags in the first 10 lines, which is the window the
  runner scans. An untagged test drops out of every filtered run.
- `make test`, `make test TAGS=ipv4,routing` (OR), `make test TAGS=ipv4+routing` (AND).
- **No skipping.** A missing prerequisite fails: `command -v unshare >/dev/null || {
  echo "FAIL: unshare not available" >&2; exit 1; }`. Never `exit 0`. A skip and a pass
  are indistinguishable in CI, and `tests/no-skip-policy.sh` greps for the pattern.
- Locate binaries through `NETFYR_TARGET_DIR` (exported by the runner) with an in-tree
  fallback. Never hardcode `../target/debug`.
- Helpers go in `tests/lib/` and are sourced; anything matching `tests/*.sh` is run as a
  test.

**Determinism is a requirement, not a nicety.** A test that passes only sometimes gets
ignored, and an ignored test is a deleted test. So: no network, no dependence on wall
clock or ordering, no shared mutable state between tests, temp dirs via `mktemp -d` with
a cleanup `trap`, network work in a `unshare -rn` namespace rather than against the host.
Quote fixtures literally; a test asserting on log wording breaks on a reword, so assert
the observable state change instead. Run a new test twice before pushing it.

## Traps

- **The workspace is empty until the first crate lands.** `cargo build/check/test/clippy`
  and therefore `make fmt`/`make clippy` all exit 101 on a virtual manifest with no
  members. `make test` detects it and skips the build. Both workarounds are marked
  `ponytail:` and get deleted with the first crate.
- **A spec's number is not its reference number.** The filename number comes from the note
  (`meta/000-project-setup`); the reference number that `implements` takes is the number of
  the pull request the board opened for it (10, for that spec).
- **A bare `implements #N` only resolves inside the spec repo.** From netfyr/netfyr write
  `implements netfyr/specs#N`.
- **The board only closes the loop on the default branch.** A merged feature branch that
  never reaches `main` leaves the card open.
- Specs carry no frontmatter once merged: the board strips it. Do not add any by hand.
