# SpecDoc access

Read the maintained [context graph documentation](https://specdoc.josie.cloud/context-graph/)
and [read API documentation](https://specdoc.josie.cloud/api/) for interface details.
The documentation host is `specdoc.josie.cloud`; the board API is served by
`https://specs.josie.cloud`.

## Context graph

Use an existing SpecDoc MCP connection when available. The server runs in the
implementation checkout and reads its working tree, git history, and the board.
It needs no credential and does not write to the board.

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
| `brief(max_tokens?)` | index overview; approved and implemented specs are listed, other statuses counted |
| `search(query, kind?, level?, limit?, max_tokens?)` | names and paths, or words in spec titles and abstracts; `kind` is `symbol` or `spec`, `level` is `fold`, `preview`, or `full` |
| `get(id, max_tokens?)` | spec's current published-form body, symbol source, or file outline |
| `neighbors(id, direction?, max_tokens?)` | one hop through dependencies, supersession, callers, references, or implementing commits; `direction` is `in`, `out`, or `both` |
| `trace(id, max_tokens?)` | spec → commits → files → symbols, or files/symbols back to specs |

Use IDs returned by the tools: `spec:netfyr/specs#N`, `spec:<shortid>` for
unnumbered notes, `file:src/lib.rs`, `sym:src/lib.rs#Name`, and `commit:<sha>`.
A symbol ID can include `@<line>` to distinguish definitions with the same name.

Start with `brief` and the task's spec, then search or trace the relevant files.
Walk one hop at a time. Defaults are 1000 tokens for `brief` and 1500 for other
tools; the truncation footer says how to narrow the query or increase the budget.
Retrieve omitted requirements before claiming a complete conformance review.

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

Without MCP, the CLI can generate a brief from the implementation checkout:

```sh
node /path/to/specdoc/mcp/server.js brief --out .specdoc/brief.md
```

Regenerate that file when its source changes; a saved brief has no live freshness
check. Replace the example server path before running it.

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
