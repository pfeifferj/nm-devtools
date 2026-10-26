# Find and use an executable oracle

`nm-tools` describes the available checks without running them. It needs Python
3.9+; it does not need a VM, Cargo, Quint or network access to list an unavailable
oracle. Put this checkout's `bin/` on `PATH`, or symlink `bin/nm-tools` into a PATH
directory. A copied executable needs `NM_DEVTOOLS` pointing at its source checkout.

```sh
nm-tools list
nm-tools --netfyr /path/to/netfyr search "executor cancellation" --json
nm-tools --netfyr /path/to/netfyr describe executor.saved --json
nm-tools --netfyr /path/to/netfyr check executor.saved --json
```

`NM_DEVTOOLS` takes precedence over the executable's real checkout. Netfyr is
always selected explicitly with `--netfyr`; discovery never guesses a sibling
checkout. This works from another directory and with linked Git worktrees.
Search is case-insensitive: every space-separated word must occur in an oracle's
ID, title, description or intent terms. An empty result is explicit.

| ID | Evidence | Execution context |
|----|----------|-------------------|
| `executor.saved` | Nine saved model scenarios replayed against Rust | Host, no Quint |
| `executor.generated` | Newly simulated Quint traces replayed against Rust | Host, Quint 0.33.0 |
| `transitions.measure` | Observed state and connectivity around an action | Rootless network namespaces |
| `transitions.selftest` | Two reproducible measurements plus any declared expectations | Rootless network namespaces |
| `backend.acceptance` | Public backend behavior and independent Linux observations | Root inside a disposable VM |

## Discovery JSON

All four commands support `--json`. The schema-v1 envelope contains `catalog`
and `oracles`; a bad ID, parameter or catalog produces `error` and exit 2.
`list`, `search` and `describe` exit 0 even when a tool is unavailable.
`check` exits 1 if prerequisites are missing or invocation inputs remain unbound;
otherwise it exits 0. That status means inspection completed, not that a test ran.

Each oracle exposes:

- Stable `id`, purpose, intent terms, evidence kind and limitations.
- Canonical `checkout`, execution `context`, source documentation and shared skills.
- `parameters` and a literal `invocation` containing cwd, argv and environment.
- `unresolved_inputs` and `invocation_bound`; placeholders are not runnable arguments.
- Individual prerequisites marked `found`, `missing` or `unknown`, plus effects and evidence contracts.

Checks inspect local paths and executable presence only. Versions, namespace
support, Rust dependency availability and VM readiness remain unknown until the
selected workflow checks them. No discovery command runs another executable,
contacts a VM, installs dependencies, creates namespaces, or generates traffic.
`readiness: unknown` is deliberate even when every executable is present.

## Inspect the selected workspace

Workspace inspection requires Git 2.45 or later.

Copy `tools/workspace.example.json` and edit every checkout, source path and
capability for the task. Invoke the doctor with its absolute config path:

```sh
nm-tools doctor --config /absolute/workspace-tools.json --json
```

There is no default config, sibling checkout search, branch switch or inferred
merged baseline. Named checkouts require explicit absolute paths and a
`required` boolean. Configured capabilities name a checkout, required/optional
status and checkout-relative files and/or executable names. Their `guidance`
explains how to obtain a missing capability separately; it is never executed.
Do not treat the sample's branch-specific files as present on another branch.

The schema-v1 `workspace-doctor` report contains:

- Canonical checkout roots, full HEADs, branch or explicit detached state,
  tracked changes and untracked paths, including linked Git worktrees.
- File and command presence, required versus optional missing prerequisites,
  and `unknown` runtime/version support. Optional missing capabilities do not
  make the doctor fail.
- Configured merged-spec ref, resolved commit, Git blob and content SHA256 per
  source. An optional `expected_commit` must match the full resolved commit.
  This is a caller-selected authority assertion; local Git does not prove
  remote freshness or forge merge status. No fetch occurs.
- When `specdoc` is configured, CLI/MCP entrypoint and shared-engine hashes,
  whether their bytes agree, Node presence, and installed Rust parser package
  versions from JSON metadata. These optional npm packages are prerequisites
  for local Rust context indexing. The doctor never loads JavaScript, native
  modules or package scripts; ABI and actual operation support remain unknown.
- Optional `task_manifest: "/absolute/task.manifest.json"` file SHA256,
  declared identity, content-identity comparison, source selection and gaps.
  SpecDoc must separately verify pinned Git objects and context completeness.

Exit 1 means a required declared prerequisite is missing or the configured
merged commit/manifest identity does not match. Exit 0 means inspection found
no missing required prerequisite; runtime readiness is still unknown. Invalid
config returns exit 2. JSON preserves full detail and human output is compact.

Unlike oracle discovery, the doctor executes fixed local Git reads to inspect
the selected repositories. It disables Git fsmonitor and optional index locks.
It never runs project-declared commands, version probes, installs, network
access, VMs, namespaces or services. Executable names are inspected in absolute
PATH directories; paths and JSON values are never shell commands.

Use `nm-tools` from the explicitly selected checkout containing its catalog and
doctor implementation. The selected SpecDoc checkout's command schema describes
its context interfaces; installed copies can differ from the source checkout.

## Discover declarations with SpecDoc

Use `specdoc context build --repo /path/to/netfyr --format json` or MCP `build`
for a bounded offline view of tracked Cargo packages/test targets, npm scripts,
Make targets, shell-test tags and GitHub workflow declarations. Copy an ID from
the response into `specdoc context build ID --repo /path/to/netfyr --format json`
to inspect its one-hop relationships. This read stays separate from task-focused
requirements retrieval through `context query`.

Facts retain source path/range, full Git revision and exact source-file SHA256.
Inspect gaps, omissions and extraction limits; untracked files and unsupported
dynamic relationships are not silently treated as resolved facts. `complete`
means the selected declarations fit without recorded gaps. It does not mean a
command is runnable, a test passed or a requirement is covered. The command
executes no project scripts, recipes or checks and contacts no board or runner;
the CLI works without native symbol-index dependencies.

Use this view to find a check's source, then inspect its oracle entry and
execution contract before running it. Pin governing requirements with
`specdoc context pin` and use `context query --manifest FILE --require-complete`
for acceptance retrieval. Trace history separately distinguishes `Spec:`
contributions, resolved by explicit manifest source paths/namespaces, from
`implements` completion. A trace, a build declaration and a pinned context are
different evidence; none is a passing verdict. See
[SpecDoc access](../skills/netfyr/references/specdoc.md) for commands and handoffs.

Use `--param NAME=VALUE` with `describe` or `check` to bind declared parameters:

```sh
nm-tools --netfyr /path/to/netfyr describe executor.generated --json \
  --param seed=42 --param samples=1024 --param traces=1024
nm-tools describe transitions.measure --json \
  --param case=nft-drop-peer --param output=/tmp/transition-evidence
```

Arguments and environment values are data; no shell expansion occurs. A caller
should pass `argv` directly to its process API, with the supplied cwd and merged
environment, after checking the execution context and required inputs. Relative
output parameters are relative to that cwd. `QUINT_BIN`, when selected, is
resolved and included in the invocation environment. Its version is not probed
by discovery. The actual runner enforces the pinned version.

The registry is `tools/oracles.json`. Unknown fields, duplicate IDs, malformed
arguments and unsupported schema versions fail rather than producing a partial
catalog. Case names stay in their existing case files:

```sh
nm-transitions list --json
```

This returns `schema_version`, canonical `case_root`, and live `cases`. Each has
a filename-stem ID, source path, description, slice, origin, split, `ceiling`,
and `has_expectations`. It honors `NM_TRANSITIONS_CASES` and excludes TUI cases.
The discovery entry also supplies this command and the resolved case-root
environment. Listing an empty directory returns an empty list; running or
self-testing an empty selection fails. Holdouts without expectations establish
reproducibility only. A measured action can fail while the measurement succeeds;
inspect the record's action status and labels.

## Executor walkthrough

1. Search for `executor cancellation` and inspect both executor entries in the
   exact netfyr checkout being changed.
2. Start with `executor.saved`. Its invocation is `make test-quint-saved` in that
   checkout. The guarded wrapper requires Make, Bash, Python and Cargo; ordinary
   `cargo test` remains independent of Python and Quint.
3. For fresh traces, inspect `executor.generated`, bind the seed and counts, then
   run its `make test-quint` invocation with the returned environment. Install
   the pinned CLI separately if missing; discovery does not install it.
4. Open `summary.json` in the printed `Quint artifacts` directory. Check
   `outcome`, `exit_code`, `phase`, actual `replayed` counts and provenance.
   Artifact paths are relative to that directory. The summary's `replay` command
   and retained `corpus.json` reproduce a generated failure or success.

A pass requires successful Cargo exit and a fresh Rust completion receipt.
Zero selected tests, a missing/truncated receipt, or a later process failure
cannot produce a pass. Generation retains model traces and per-phase logs;
saved mode clears inherited external-corpus selection. Missing Python or an
unusable output directory fails before a summary can be written. Once started,
an unfinished summary remains `incomplete`; never treat it as passing.

## VM acceptance

`backend.acceptance` exposes a **guest-only** pytest invocation with required
absolute `guest_checkout`, `guest_probe` and `guest_results` parameters. Binding
them does not establish VM readiness or acquire a lease. Follow the
[testvm skill](../skills/testvm/SKILL.md), build and deploy the explicitly selected
probe and complete suite, and read netfyr's `tests/integration/coverage.md`.
Use the existing `vm-test` workflow to retain guest results outside the VM before
rollback. The catalog does not provide automatic suite deployment or a host
shortcut to privileged pytest.

For a task with pinned requirements, the existing runner can retain that
baseline and the selected oracle identity:

```sh
vm-test -m /absolute/task.manifest.json -r backend.acceptance -o /absolute/evidence \
  -s baseline-known-good -- /path/to/the/already-deployed/guest-check
```

The normal VM ownership, prerequisites and evidence workflow still apply.
`VM_TEST_TASK_MANIFEST` and `VM_TEST_ORACLE_ID` provide the same optional defaults;
explicit flags override them. Supply both or neither. Before any VM lookup or
lease claim, the runner reads the manifest once, validates its content identity
and required structural fields, and retains those exact bytes as
`task.manifest.json`. It records `oracle_id`, `task_manifest_id`, exact
`task_manifest_sha256` and required-gap count in the existing `meta` file.
An existing retained manifest is never overwritten. This does not change the
runner's exit status, existing evidence or success definition, and it does not
verify Git source bytes or requirement conformance. Invalid provenance fails
before VM contact; Python is needed only when this optional linkage is used.

The catalog's optional `evidence.task_context` records runner support explicitly.
The executor and transition runners are not integrated with these flags; retain
their existing summary/receipts/measurements and an explicit external provenance
link rather than assuming a manifest environment variable is supported.
