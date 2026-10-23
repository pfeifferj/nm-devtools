# SpecDoc access

Read the maintained [context graph documentation](https://specdoc.josie.cloud/context-graph/)
and [read API documentation](https://specdoc.josie.cloud/api/) for interface details.
The documentation host is `specdoc.josie.cloud`; the board API is served by
`https://specs.josie.cloud`.

## Context graph

Use an existing SpecDoc MCP connection when available. The server runs in the
implementation checkout and reads its working tree, git history, and the selected
requirements sources. Manifest-backed retrieval and build discovery work offline;
unpinned requirements exploration reads the board.
Read tools need no credential. A locally configured bot token adds separate
write tools; the context workflow uses the read tools.

For a client using `.mcp.json`, replace the server path with the actual SpecDoc
checkout and run it with the implementation repository as its working directory:

```json
{
  "mcpServers": {
    "specdoc": {
      "command": "node",
      "args": ["/path/to/specdoc/mcp/server.js"],
      "env": {
        "SPECDOC_URL": "https://specs.josie.cloud",
        "SPECDOC_NAMESPACE": "netfyr/specs"
      }
    }
  }
}
```

Without an explicit namespace, the server infers spec repositories from
`implements owner/repo#N` commits; with neither, it reads all board namespaces.

| tool | use |
|------|-----|
| `context(task, anchors?, seen?, manifest?, require_complete?, max_docs?, max_tokens?)` | complete task-relevant spec sections, source authority, freshness, receipts, omissions and required gaps |
| `build(id?, repo?, max_tokens?)` | bounded local build, test and CI declarations with source evidence; optional id selects a node and its one-hop relationships |
| `preview(config, require_valid?, max_tokens?)` | offline change-bundle diffs, obligation dispositions, provider/relationship findings and declared rule checks; no approval or semantic-preservation verdict |
| `brief(max_tokens?)` | index overview; approved and implemented specs are listed, other statuses counted |
| `search(query, kind?, level?, limit?, max_tokens?)` | names and paths, or words in spec titles and abstracts; `kind` is `symbol` or `spec`, `level` is `fold`, `preview`, or `full` |
| `get(id, max_tokens?)` | spec's current published-form body, versioned section from context, symbol source, or file outline |
| `neighbors(id, direction?, max_tokens?)` | one hop through dependencies, supersession, callers, references, or implementing commits; `direction` is `in`, `out`, or `both` |
| `trace(id, max_tokens?)` | spec → contribution/completion commits → files → symbols, or files/symbols back to specs; use the same manifest for explicit source mappings |

Use IDs returned by the tools: `spec:netfyr/specs#N`, `spec:<shortid>` for
unnumbered notes, `file:src/lib.rs`, `sym:src/lib.rs#Name`, and `commit:<sha>`.
A symbol ID can include `@<line>` to distinguish definitions with the same name.
Context also returns `section:<encoded-note-id>:<body-hash-prefix>:<number>`
IDs for `get`; the hash prefix is 16 hexadecimal characters and section numbers
start at one. Copy the ID from the result. If the source body changes, rerun
`context` to obtain current IDs.

Start with packaged CLI `context query` or MCP `context` and known spec anchors,
then search or trace the relevant files. File, symbol and commit anchors discover specs through file-level
implementation links; context returns spec excerpts. Use `brief` when you need
a code overview. Without a manifest, context automatically selects live approved or implemented
specs in the configured namespace; explicit spec or section anchors can reach
draft, retired or out-of-scope specs with warnings. Automatic top-level specs
and dependencies use the same lifecycle filter. Unresolved, draft or retired
dependencies are reported for direct inspection. These required sources are
selected independently of optional model scores. If scope is every project
because no namespace was configured or inferred, automatic discovery is
disabled; set `SPECDOC_NAMESPACE=netfyr/specs`.

Context keeps sections and ancestor introductions whole. Lists of labelled
`FR-` and `SC-` items become individual complete requirements, retaining nested
examples and surrounding conditions. Shared ancestor text and document metadata appear once per
response; each section lists its source ranges. Exact section anchors come
first, then explicit spec anchors; within those groups, matching requirements
precede other text. Unrelated dependency text remains a required omission when
it does not fit. Word matching can miss implicit constraints or synonyms, so
use known spec anchors and check the governing sources.

Its default 1500-token
budget (500–20000) includes headers, provenance and omission notices, estimated
at four characters per token. A section that does not fit is omitted; use its
returned ID with `get` and a larger budget. Section retrieval returns the
complete unit or reports the budget required. Other read tools default to 1500 tokens and
`brief` to 1000. Retrieve omitted requirements before claiming a complete
conformance review. Board text is the current publishable form and can be ahead
of merged requirements; use merged spec files for implementation acceptance.
Document responses including JSON metadata are capped at 256 KiB each, with a
2 MiB total read allowance. `max_docs` counts attempts, including failures.
Body selection has a ten-second deadline after metadata refresh. Optional ranking
then has its own configured deadline, two seconds by default.
Required anchors, dependencies and top-level specs can still be omitted by these limits; inspect
the notices and retrieve unread sources directly.

Context and section `get` receipts identify only fully returned sections. Pass them in `seen`
only while you still have the corresponding text in your context. The server
has no session-wide read history; omitting `seen` fetches content again. Matching
receipts also suppress explicitly anchored sections. Omit receipts after
compaction, in a new subagent, or when earlier text is unavailable.
Changed source content is returned again. A receipt does not prove conformance.

Optional Laya ranking runs behind the same MCP tool when `SPECDOC_LAYA_URL`
names a loopback HTTP(S) service. It sends the task and candidate spec excerpts
to that service, whose operator controls retention. SpecDoc does not persist
tasks or scores. `SPECDOC_LAYA_MODEL` defaults to `english` and
`SPECDOC_LAYA_TIMEOUT_MS` to `2000` (100–60000). A longer timeout is available for
explicit CPU timing tests. `SPECDOC_LAYA_MAX_CANDIDATES` defaults to `16` (1–16)
and `SPECDOC_LAYA_BATCH_SIZE` to `8` (1–8). Scoring reports elapsed time; a timeout
reports its budget and workload. Explicit spec and section anchors,
top-level specs and dependencies bypass model ranking. Laya reranks optional
sections that already match task words within the same priority group, up to 16 complete sections of at most
6000 characters each. Longer sections retain lexical order, and tasks over
2000 characters bypass Laya. The model input limit is 512 tokens; an unavailable,
invalid or truncated response falls back to word matching. No extra tool
connection is required.

Each response identifies the indexed commit and dirty working tree. Tracked-file
edits are reflected on subsequent calls; untracked files are outside the graph.
Spec refresh follows the board's cache lifetime. A `stale:` response may contain
cached data from before an outage: report that limit and verify current
requirements in the spec repository before relying on it for acceptance.

Rust symbols are matched by name, so references can have multiple candidates.
Traces are file-level associations, not proof that a commit changed a particular
symbol. The graph excludes review threads, approvals, revision history, and notes
hidden from guests. Read top-level specs separately when the brief does not
include them; follow supersession links before using a retired spec.

Without MCP, use the packaged CLI from the selected SpecDoc checkout. Install
its dependencies separately using that checkout's instructions, or use its
installed `specdoc` executable. The source entrypoint is
`node /path/to/specdoc/cli/bin/specdoc.js`. Repeat `--anchors` or `--seen` for
each value. Select the implementation worktree and namespace explicitly:

```sh
specdoc context query 'renew a lease before expiry' --repo /path/to/netfyr \
  --namespace netfyr/specs --board-url https://specs.josie.cloud \
  --anchors 'spec:netfyr/specs#7' --format json
specdoc context get 'spec:netfyr/specs#7' --repo /path/to/netfyr \
  --namespace netfyr/specs --board-url https://specs.josie.cloud --max-tokens 4000
specdoc context trace 'file:crates/netfyr-backend/src/lib.rs' --repo /path/to/netfyr \
  --namespace netfyr/specs --board-url https://specs.josie.cloud
```

Pin an implementation task's selected requirements before acceptance retrieval:

```sh
specdoc schema context.pin --format json
specdoc context pin --config /absolute/task-context.json --output /absolute/task.manifest.json
specdoc context query 'renew a lease before expiry' --repo /path/to/netfyr \
  --manifest /absolute/task.manifest.json --require-complete --format json
```

Use the selected checkout's command schema and task-config example. The pin
config names an explicit implementation base/scope and each governing Git
repository/ref/path with selected requirement IDs and dependencies. Pinning
resolves immutable Git objects and hashes; selecting a ref as merged remains
an explicit caller assertion. It does not fetch or prove forge merge status.
A dependency map from the planning backlog remains a proposal until reviewed.

Structured retrieval exposes source authority/freshness, required gaps and
omissions. In strict mode, unresolved required context fails rather than becoming
an implicit acceptance baseline. Exploratory partial reads remain useful; report
their limits. Board text is a separately identified mutable proposal source.
Inspect the pinned manifest and fetch missing requirements before acceptance.

MCP provides readable text plus a `structuredContent` packet, excluding the
redundant packet `text` field. CLI `--format json` returns the packet in `data`.
A strict MCP failure
preserves the packet with `isError: true`; CLI strict errors retain it in
`error.details.packet`. Inspect `requiredGaps` instead of treating a readable
partial response as complete.

Retain the same manifest file and identity through a task and its handoffs.
Record the implementation base, selected requirements/dependencies, owned paths,
remaining gaps, current work and evidence artifact paths. A new agent or a
compacted context must retrieve text again; copied receipts are not proof that
it has read or retained that text. Regenerate a manifest deliberately when the
baseline changes and explain the changed sources. Saved text has no automatic
freshness guarantee.

## Discover build and test declarations

Build discovery is a separate offline read, available through packaged CLI
`context build` and MCP `build`. It does not add an unrestricted build graph to
requirements queries:

```sh
specdoc context build --repo /path/to/netfyr --format json
specdoc context build 'build:cargo-workspace:Cargo.toml' \
  --repo /path/to/netfyr --max-tokens 4000 --format json
```

Copy node IDs from the overview for the selected checkout; the second example
assumes its root manifest declares a Cargo workspace. A selected ID returns
that node and one-hop relationships. An unknown ID fails. Increase the budget or
narrow to a returned ID when whole nodes, edges or gaps are omitted.

The packet reports tracked working-tree declarations from Cargo manifests, npm
scripts, Make targets, shell-test tags and GitHub workflows. Each fact carries
source path/range, source-file SHA256 and observed Git revision. Inspect `gaps`,
`limits`, `omissions` and `counts`; `complete` refers only to the selected
declarations. Untracked files are outside this view, and unsupported or
unreadable declarations remain explicit gaps.

These are declarations, not executed checks, resolved feature/`cfg` behavior,
effective workflow environments, requirement coverage or passing results. Build
discovery does not contact the board, fetch dependencies, evaluate Make recipes,
run npm scripts or start runners. The CLI path works without optional native
symbol-index dependencies. Before executing a declared command, use the
project's prerequisites, execution environment and evidence contract; for
netfyr's available oracles, use `nm-tools` and the [discovery guide](../../../docs/tools.md).

## Read contribution and completion history

`Spec: core/NNN-feature` in a commit's final trailer block records a
`contributes_to` relationship. `implements netfyr/specs#N` records completion.
Partial work therefore stays discoverable without being presented as a
completed feature. Neither relationship proves requirement-level coverage or
that a particular symbol changed.

Resolve a `Spec:` path through the task manifest's explicit source `namespace`,
`path` and optional published `reference`. The path matches the source path with
an optional `.md` suffix; supply the area path when the source has one. An
unmapped or ambiguous path stays unresolved with its reason and candidates.
The parser never obtains a PR number from filename digits, and an `implements`
reference does not establish the authority of a current board revision. Use
the same manifest with `context trace`/MCP `trace` and `get` when following these
links.

## Public read API

| request | purpose |
|---------|---------|
| `GET /api/specs?ns=netfyr/specs` | metadata, including status, kind, namespace, specPath, dependencies, and supersession |
| `GET /api/specs/<id>` | metadata plus published-form body; `Accept: text/markdown` selects the body |
| `GET /api/specs/<id>/revisions` | raw note revision series, newest first |
| `GET /api/specs/<id>/revisions/<time>` | raw markdown at a revision time or `current` |
| `GET /api/specs/<id>/changes?from=...&to=...` | published-form snapshot comparison, changed requirement IDs, and word-level diff |

For HTTP paths, use the note ID, alias, or `urlId` from API metadata. A graph ID
such as `spec:netfyr/specs#7` is not an HTTP note ID. URL-encode path segments and
query values.

An `implements owner/repo#N` reference maps to metadata with that `namespace`
and `pr` number. A `Spec:` trailer already supplies the merged repo's file path
(append `.md`). `specPath` can be null even for merged specs. When only a path
is known and metadata has no matching `specPath`, inspect the spec file's git
history and associated publication PR: `Spec-Id` and `Reviewed-on` trailers
identify the note. Never infer a PR number or note ID from the filename number.

Follow `next` until it is null when collecting the corpus; pass each cursor
back unchanged as `cursor`. Read `at` and `stale` on list responses.
The API includes retired specs. A `superseded` entry can have a replacement PR
that is still open: follow the replacement reference, but check its merge status
and the specs repository before changing the implementation acceptance baseline.
Keep the existing merged requirements until the replacement lands. Read
applicable `kind: top-level` specs before feature requirements.
`dependsOn` and `supersedes` contain declared references, not resolved URLs.

Check HTTP status and respect rate-limit responses. Fetch metadata first and
only the bodies needed for the task. An error response or incomplete page
sequence must not be treated as a successfully fetched corpus.

## Revisions and approval text

The body endpoint strips frontmatter and resolves CriticMarkup. It supplies
publishable text, which may be ahead of the version merged into the spec repo.
For landed requirements and history, use the resolved spec path and git.

Request `/api/specs/<id>/changes` without anchors to discover the available
snapshots before choosing a comparison. The `from` and `to` anchors accept
snapshot IDs, `approval:<login>`, `status:<phase>`, `published:rN`, or
`current`. Inspect the returned anchors and added, removed, and changed
requirement IDs before describing what moved since approval or publication.

Revision endpoints return raw notes with frontmatter and review markup.
Saved revisions can lag edits; `current` comes from the board's copy.
If the editor is unavailable, the series can contain only `current`, while
a single-revision request returns an error. Report missing history rather than
inferring that the note had no earlier revisions.

An existing approval may predate the current text; it is not automatically
revoked by edits. Use snapshot comparisons to identify the difference and the
[spec lifecycle](https://specdoc.josie.cloud/spec-lifecycle/) for review actions.
The editor's `/api/note/<id>` and approval routes are internal integration
interfaces, not a supported external API. Use the public read routes above.
