---
name: netfyr
description: "Work on netfyr specs and implementations using SpecDoc's context graph and read API, project spec references, and test conventions. Use when tracing code to requirements, reading or revising a spec, or preparing a netfyr change."
---

# netfyr development

These conventions apply to netfyr implementation and spec repositories. Read
the target checkout's `CONTRIBUTING.md`, relevant specs, and current checks;
other repositories have their own contribution rules.

## Find the requirements

- [netfyr/specs](https://github.com/netfyr/specs) holds merged requirements.
  Read applicable top-level specs, the feature spec, and its dependencies.
- [The SpecDoc board](https://specs.josie.cloud) holds notes under review and
  later revisions. Notes are edited at [md.josie.cloud](https://md.josie.cloud).
- [netfyr/netfyr](https://github.com/netfyr/netfyr) holds the implementation.
  `Spec:` trailers name spec paths; `implements netfyr/specs#N` links completing
  commits to the spec's original PR number.

For graph setup, queries, revision comparisons, or HTTP access, read
[SpecDoc access](references/specdoc.md). Authoritative references:
[context graph](https://specdoc.josie.cloud/context-graph/),
[read API](https://specdoc.josie.cloud/api/), and
[spec lifecycle](https://specdoc.josie.cloud/spec-lifecycle/).

When MCP is available, `brief` gives the current index, `get` reads a spec,
and `trace` connects files to the specs named in their history. Inspect the
freshness header and truncation notice. Graph traces identify files touched by
spec-linked commits; they do not prove which requirement a symbol satisfies.

Use the merged spec for implementation acceptance. A board body is its current
publishable text and may contain an unmerged revision. Compare revisions when
the requirement changed, and follow `supersedes` links before treating an old
spec as current.

## Write or revise a spec

Use the board's feature or top-level template and the namespace's `roles.yml`
for layout, areas, and approval rules. Feature specs use acceptance scenarios,
stable `FR-nnn` requirements, and measurable `SC-nnn` success criteria.
Preserve existing IDs when revising requirements.

The board records approvals made through its editor integration and publishes
when its approval and thread-resolution conditions are met. Typing a name into
`approved-by` does not approve a spec. Read the lifecycle documentation when
changing review state; the graph and public read API do not submit approvals.

The board creates spec PRs with frontmatter removed and CriticMarkup resolved.
An in-place revision keeps the original spec reference and path; a replacement
gets its own reference and `supersedes` relation. For merged history, use
`namespace` and `specPath` from the API to find the file in the specs repo.
Run that repo's `python3 .github/validate.py` for spec-file changes.

## Implement and reference a spec

netfyr's contribution rules allow local implementation while a spec is under
review. Its pre-push check requires referenced specs to exist in the merged
specs repository. Check the actual reference before publishing implementation
work; this requirement does not prevent local investigation, tests, or review.

Keep one spec per implementation PR. Read dependencies and identify missing
ones instead of silently taking over their implementation. Connect the changed
behavior and tests to the relevant acceptance scenarios, FRs, and SCs.

| artifact | reference |
|----------|-----------|
| non-exempt commit | exactly one `Spec: area/NNN-slug` in the final paragraph, without `.md` |
| PR description | the same `Spec:` trailer as its commits |
| completing commit only | additionally `implements netfyr/specs#N`, using the original spec PR number |
| regression fix | `Fixes: HASH12 ("COMMIT SUBJECT")` when an introducing commit is known |
| user-visible changelog entry | spec path in parentheses under `[Unreleased]` |

The completing commit is the last non-exempt commit in its PR; partial work
has no `implements` trailer. The area prefix in a `Spec:` path is optional.
A bare `implements #N` resolves in the wrong repo.
Check current exemptions and reference rules with
`scripts/check-spec-ref.sh --msg FILE` or `--range BASE..HEAD`;
`VERIFY_SPEC_EXISTS=1` enables remote resolution. These commands validate
references; they do not publish changes.

A requirement mismatch needs a spec correction or an implementation correction.
Explain which is needed and continue independent work while it is resolved.
Changelog entries describe changes users or packagers notice; internal tooling
and test scaffolding do not require one.

## Tests and runtime checks

- Unit tests are `#[cfg(test)]` modules beside Rust code. Shell integration tests
  in `tests/*.sh` exercise binaries and system behavior.
- Shell test tags belong in the first ten lines. Put helpers in `tests/lib/`,
  and locate binaries through `NETFYR_TARGET_DIR` with the repository fallback.
- A missing prerequisite fails the test; it must not exit successfully as a skip.
  Use isolated namespaces and private temporary files for network tests.
- Reproduce a bug with a test that fails on the reported behavior, then verify
  the correction. Map coverage to requirement IDs where that helps review.

Run `make test`, `cargo test`, `cargo fmt --check`, and `make clippy` as
appropriate for the changed code and current workspace. `make test TAGS=ipv4,routing`
selects either tag; `TAGS=ipv4+routing` requires both. If the workspace has no
crates, report that limitation rather than claiming its Rust code was tested.

For apply paths and changed observations, verify behavior against a real kernel.
Use `unshare -rn` when sufficient; use a test VM for system services, reboot, or
device-specific behavior. Consult the available `testvm` skill before operating
shared test VMs.

Related workflows: [PR review](../netfyr-review/SKILL.md) and
[bug fixes](../netfyr-bugs/SKILL.md).
