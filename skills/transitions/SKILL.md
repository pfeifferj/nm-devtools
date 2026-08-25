---
name: transitions
description: "Harvest and extend the (state, action, next-state) corpus produced by bin/nm-transitions. Covers authoring a case, the three connectivity probes, normalization and diff keying, and what selftest can and cannot catch."
when-to-use: "When adding or debugging a transition case, harvesting the corpus, judging whether an action caused a lockout, changing what gets captured or normalized, or any mention of nm-transitions, cases/*.json, selftest, or the transition corpus. Not for VM work; see the testvm skill for that."
allowed-tools: [Bash, Read]
context: inline
---

# transitions

`bin/nm-transitions` runs one action in a throwaway `unshare -rn` namespace and
records the state either side of it. No root, no libvirt, no rollback: namespace
teardown is the rollback. `docs/transitions.md` is the reference for record
fields and the reasoning behind the corpus; this covers the schema, the workflow
and the traps.

```
nm-transitions list                       # 83 cases with descriptions
nm-transitions run -n 1 -o /tmp/x <case>  # harvest, JSONL per case (default -o transitions/)
nm-transitions selftest [CASE...]         # run each twice, assert reproducible
```

`transitions/` is gitignored. Harvest elsewhere when the output is throwaway.

## Case schema

```json
{
  "name": "nft-drop-peer",
  "description": "one line on what it demonstrates",
  "split": "dev",
  "peer": {"subject_ip": "10.5.5.1/24", "peer_ip": "10.5.5.2/24", "probe": "10.5.5.2"},
  "setup": ["nft add table inet f"],
  "action": {"kind": "shell", "spec": "nft add rule inet f out ip daddr 10.5.5.2 drop"},
  "expect": {"reachable_after": false, "established_after": false}
}
```

| field | notes |
|-------|-------|
| `name` | must match the file stem, nothing checks it |
| `split` | `dev` by default; `holdout` opts a case out of anything a model is fit on, and carries no `expect` |
| `peer` | `null` for cases needing no connectivity label, which makes all three labels `null` |
| `setup` | builds the starting state, runs before any probe |
| `action.kind` | `shell` on the host, `peer_shell` on the far end, `nmstate` for a desired state via `nmstatectl apply -k` |
| `expect` | optional, any subset of the record's scalar fields; `selftest` asserts it |

Cases come from `cases/` unless `NM_TRANSITIONS_CASES` points elsewhere, which is
how a generated case gets measured without being written into the tracked corpus.

## Adding a case

1. Write `cases/<name>.json`. `name` must match the file stem; nothing checks it.
2. Harvest once and read the labels back, do not assume them:
   `nm-transitions run -n 1 -o /tmp/x <name>` then inspect `reachable_*`,
   `established_*`, `bulk_*`, `diff` and `exit_code`.
3. Declare what the case demonstrates in `expect`, derived from its design and
   not pasted from step 2, or the harvest becomes its own oracle.
4. `nm-transitions selftest <name>`. Red means the capture is nondeterministic
   or a declared label did not hold.
5. Pair it. The corpus is built on discriminating pairs: the same action shape
   with opposite outcomes, so nothing downstream can predict the label from the
   size of the diff. A case with no counterpart teaches a shortcut.

Build topology with iproute2 in `setup` and leave the thing under test as the
`action`. `nmstatectl apply -k` cannot create dummy interfaces in the namespace
(typed `Other("dummy")`, netlink returns EOPNOTSUPP); `linux-bridge` works.

## The three probes disagree on purpose

| probe | question | blind to |
|-------|----------|----------|
| `reachable_*` | can a new flow start (ping) | MTU, and rules that only spare established flows |
| `established_*` | did a flow opened before the action survive (the SSH question) | anything that only blocks new flows |
| `bulk_*` | does a 1428-byte DF packet cross | everything small enough to fit any link |

An action is an operational lockout only when all three go false. `null` means
not measured: no peer, or, for `established_*`, a flow that never opened.

## What selftest does not catch

It compares run A to run B, so on its own it sees nondeterminism and nothing
else: a wrong capture, an inverted diff, or a probe stuck at True is perfectly
reproducible. What closes that gap is `expect` in a case, holding the run to
the labels the case was written to demonstrate. A case without `expect` has
only its state and diff checked for reproducibility, so give one to any case
whose label is the point.

Changing `capture()` usually means changing `VOLATILE_KEYS`, `VOLATILE_SUFFIXES`
or `IDENTITY` in the same commit, or selftest goes red on the next kernel-random
field. `IDENTITY` is what keys list elements in the diff; `nftables` is left
index-keyed on purpose, because rule order there is semantic.

## Known ceilings, do not file these as bugs

- `unshare -rn` isolates the network namespace, not mounts or UTS. Host DNS is
  dropped from the capture; the host hostname still reaches every record.
- One packet per probe, so latency, loss and reordering read as no change.
- Only cases carrying `expect` constrain the probes. The rest ride on those.
