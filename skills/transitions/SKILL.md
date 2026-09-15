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

```sh
nm-transitions list                       # cases with descriptions
nm-transitions run -n 1 -o /tmp/x <case>  # harvest, JSONL per case (default -o transitions/)
nm-transitions selftest [CASE...]         # run each twice, assert reproducible
```

`transitions/` is gitignored. Harvest elsewhere when the output is throwaway.
Each case's JSONL is replaced after all requested repetitions succeed; failed
runs preserve the previous file. `-n` must be positive.

## Case schema

```json
{
  "name": "nft-drop-peer",
  "description": "drop outgoing packets addressed to the peer",
  "slice": "nft-drop",
  "origin": "handwritten",
  "split": "dev",
  "peer": {"subject_ip": "10.5.5.1/24", "peer_ip": "10.5.5.2/24", "probe": "10.5.5.2"},
  "setup": [
    "nft add table inet f",
    "nft add chain inet f out \"{ type filter hook output priority 0; policy accept; }\""
  ],
  "action": {"kind": "shell", "spec": "nft add rule inet f out ip daddr 10.5.5.2 drop"},
  "expect": {"reachable_after": false, "established_after": false}
}
```

| field | notes |
|-------|-------|
| `name` | must match the file stem |
| `slice` | mechanism group; defaults to the part of `name` before the first hyphen |
| `origin` | `handwritten` (default) or `generated` |
| `ceiling` | defaults to `false`; `true` marks an outcome that depends on state omitted from local capture |
| `split` | `dev` (default) or `holdout`; holdouts omit `expect` |
| `peer` | `null` for cases needing no connectivity label, which makes all three labels `null` |
| `setup` | subject commands before measurement; defaults to `[]` |
| `peer_setup` | peer commands after subject setup and before measurement; defaults to `[]` and requires a peer when used |
| `action.kind` | `shell` in the subject namespace, `peer_shell` in the peer namespace, `nmstate` for a desired state via `nmstatectl apply -k` |
| `expect` | optional, any subset of the record's scalar fields; `selftest` asserts it |

`shell` and `peer_shell` run one program directly. Command strings use
`shlex.split` for argument quoting; shell expansion, pipes, and redirects are
not supported. Quote nft brace expressions as in the example above.

The harness records metadata without selecting training or evaluation sets.
`peer_setup` is included for replay, but peer state is absent from the capture.

Cases come from `cases/` unless `NM_TRANSITIONS_CASES` points elsewhere, which is
how a generated case gets measured without being written into the tracked corpus.

## Adding a case

1. Write `cases/<name>.json`. `name` must match the file stem.
2. Harvest once and read the labels back, do not assume them:
   `nm-transitions run -n 1 -o /tmp/x <name>` then inspect `reachable_*`,
   `established_*`, `bulk_*`, `diff` and `exit_code`.
3. For a dev case, declare `expect` from its intended behavior. Check the
   measurements against that expectation. Holdouts omit `expect`.
4. Run `nm-transitions selftest <name>` to check reproducibility and expectations.
5. Where useful, repeat an existing action under a different starting state
   that changes the outcome. Verify the labels before assigning paired cases
   to dev and holdout.

Build topology with iproute2 in `setup` and leave the thing under test as the
`action`. `nmstatectl apply -k` cannot create dummy interfaces in the namespace
(typed `Other("dummy")`, netlink returns EOPNOTSUPP); `linux-bridge` works.

In nmstate 2.2.61 kernel mode, `state: down` leaves veth up and exits 0;
changing its MTU leaves it unchanged and fails verification. Adding an explicit
interface type does not fix either behavior. `state: absent` deletes the pair.
Keep the requested action and measured result separate when describing a case.

For conntrack cases, add a `ct state` rule in `setup` before the TCP flow opens.
Adding the first tracking rule in `action` misses the handshake.

## The three probes

| probe | question | blind to |
|-------|----------|----------|
| `reachable_*` | does a fresh ICMP echo succeed | TCP-specific rules and failures limited to larger packets |
| `established_*` | does the TCP flow opened after setup still carry data | rules that only block new flows |
| `bulk_*` | does a 1428-byte DF packet cross | TCP-specific behavior and throughput |

`null` means not measured: no peer, or, for `established_*`, a flow that never
opened. Read each label separately; these probes cover specific traffic types.

## What selftest does not catch

`selftest` compares normalized state, diff, all six connectivity labels, and
action exit status between two runs. It checks `expect` against both runs.
A consistently wrong result can still pass without an expectation: for
example, a probe stuck at true is reproducible. Holdouts receive only the
reproducibility check.

Changing `capture()` usually means changing `VOLATILE_KEYS`, `VOLATILE_SUFFIXES`
or `IDENTITY` in the same commit, or selftest goes red on the next kernel-random
field. `IDENTITY` is what keys list elements in the diff; `nftables` is left
index-keyed on purpose, because rule order there is semantic.

## Capture limits

- `unshare -rn` isolates the network namespace, not mounts or UTS. Host DNS is
  dropped from the capture; the host hostname still reaches every record.
- One packet or byte per probe cannot measure loss rates, latency distributions,
  or reordering. Partial loss can cause unstable labels; delays can time out.
  Random packet corruption is excluded until statistical probes are available.
- Peer state is not captured. Mark a case `ceiling: true` when its outcome
  depends on that omitted state, and record the required commands in `peer_setup`.
