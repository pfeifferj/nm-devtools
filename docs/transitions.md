# Transition harvesting

`bin/nm-transitions` records what an action does to network state: it builds a
topology in a throwaway namespace, captures state, applies one action, captures
again, and emits the pair plus a structured diff as JSONL. It touches neither
libvirt nor NetworkManager: each run is `unshare -rn`, so it needs no root and
no rollback, because namespace teardown is the rollback.

```sh
nm-transitions list                          # known cases
nm-transitions run -n 20 -o transitions/     # 20 reps of each, JSONL per case
nm-transitions run nft-drop-peer             # just one
nm-transitions selftest                      # run each twice, assert reproducible
```

`run` replaces each case's JSONL after all requested repetitions succeed. A
failed run preserves the previous file. `-n` must be positive.

## What gets measured

Cases that declare a `peer` get a veth into a second namespace and are measured
before and after the action:

- `reachable_*`: a fresh ICMP echo request succeeds.
- `established_*`: a TCP connection opened after setup still carries a byte.
  Both measurements use the same connection.
- `bulk_*`: an ICMP echo request with a 1400-byte payload and DF set succeeds.
  It needs a path MTU of at least 1428 bytes.

Read the labels separately. `nft-drop-echo-request` blocks ping while leaving
TCP intact. `nft-ct-established-accept` permits established traffic while
blocking new flows. `link-lower-mtu` prevents the larger packet from crossing
while small packets still pass. These probes cover specific traffic patterns;
they do not prove that every application can connect.

## Case design

Many cases repeat an identical action under different starting conditions.
For example, `nft-drop-peer` appends a drop rule to an empty chain, while
`nft-drop-drop-peer-accept-above-drop` appends it below an existing accept.
Other cases cover distinct commands or boundary values.

Read the `*_before` labels. `nft-drop-then-accept` starts unreachable;
`nft-flush-restores` and `link-mtu-restore` start with faults the action repairs.

For conntrack cases, install a `ct state` rule in `setup` so tracking starts
before the TCP handshake. Adding the first tracking rule in `action` misses
connection establishment.

## Local state is not sufficient

`link-mtu-peer-side` lowers the peer's MTU. Its action describes the change,
but the local capture has no peer MTU field.

`peer_setup` configures the far end before measurement. For example,
`link-mtu-restore-peer-low` restores the local MTU while the peer remains at
1280. `ceiling: true` marks cases whose outcome depends on state omitted from
the local capture. The record includes `peer_setup` for replay; consumers
evaluating predictions from local state and action alone must exclude it.

## What the probes still cannot see

Each probe samples one packet or byte. It cannot measure a loss rate, latency
distribution, or reordering. Partial loss can produce inconsistent labels;
enough delay can exceed a probe's timeout.

Random packet corruption is excluded from the tracked corpus: it produced
different connectivity labels with identical state. It needs statistical
probes before it can provide a reproducible case.

The peer is a single directly-connected veth, so nothing here exercises a
gateway hop, a second router, asymmetric return paths, or anything a name has to
resolve through. Lockout via DNS or via the default route is out of reach until
the topology grows.

## Reproducibility

Run `selftest` after changing a case, capture, or normalization. It runs each
case twice and compares normalized state, diff, all six connectivity labels,
and the action's exit status. Both runs must also satisfy `expect`. The full
corpus takes minutes to run.

`expect` is optional and holds any subset of the record's scalar fields. It is
what catches consistently wrong results: a probe stuck at true can pass the
reproducibility check. Derive expectations from the case's intended behavior.
Holdout cases omit `expect` and receive reproducibility checks only.

Normalization and capture handle two sources of noise:

- Volatile fields. The kernel hands out random MACs, EUI-64 link-local addresses
  derived from them, ticking bridge timers, and per-object handles. These are
  stripped or collapsed to constants during normalization.
- Async kernel work. A netlink write returns before DAD, IPv6 link-local
  regeneration, or carrier settling has finished. Captures wait for the state
  to stop moving *and* for no address to be tentative. Waiting for stability
  alone is not enough: two captures taken during DAD agree with each other and
  are both wrong.

## nmstate inside the namespace

`nmstatectl apply -k` works, but it cannot create `dummy` interfaces there
(typed `Other("dummy")`, netlink returns EOPNOTSUPP), while `linux-bridge`
works. Build topology with iproute2 in `setup` and leave nmstate as the action
under test.

With nmstate 2.2.61, kernel mode leaves a veth up after `state: down` and
returns success. An MTU change leaves the MTU unchanged and fails verification.
The kernel backend's `nispor/apply.rs` forces non-absent interfaces up and
does not pass the requested MTU to nispor. Explicit `type: veth` or
`type: ethernet` produces the same results. These cases record the requested
action and observed outcome; `state: absent` does delete the veth pair.

## Case format

One JSON file per case in `cases/`, named for the file stem. Unrelated to the
`nm-vm scenario` reproducers, which run inside a VM. `setup` builds the state
the action runs against, `action` is the single thing being measured:

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

`peer` may be `null` for cases needing no connectivity measurements. `setup`
and `peer_setup` default to empty lists. Subject setup runs first, then peer
setup, then the measurements and action. Peer setup requires a peer.

The following metadata is copied into each record:

| field | meaning | default |
|-------|---------|---------|
| `slice` | mechanism group, such as `nft-drop` | part of `name` before the first hyphen |
| `origin` | `handwritten` or `generated` | `handwritten` |
| `split` | `dev` or `holdout`; holdouts omit `expect` | `dev` |
| `ceiling` | outcome depends on state omitted from local capture | `false` |

The harness records these fields; it does not select training or evaluation
sets. `action.kind` is one of:

| kind | spec | runs |
|------|------|------|
| `shell` | a command | in the subject namespace |
| `nmstate` | a desired state | `nmstatectl apply -k` in the subject namespace |
| `peer_shell` | a command | in the peer namespace; requires a peer |

Despite the kind names, commands run directly without a shell. Command strings
are split with `shlex.split`: quotes group arguments, while pipes, redirects,
variable expansion, and command substitution are not interpreted. The nft
chain declaration above passes the quoted brace expression as one argument.
Each setup entry and each action runs one program.

Cases are read from `cases/` unless `NM_TRANSITIONS_CASES` names another
directory. Use a scratch directory to measure candidates before adding them:

```sh
NM_TRANSITIONS_CASES=/tmp/candidates nm-transitions run my-case -o /tmp/out
```

## Record fields

`state_before`, `state_after` and `diff` are normalized; `exit_code` is recorded
but is never a success signal, since an action can exit 0 and still take the
network down. Judge outcomes from `diff` and the three connectivity labels.

| field | what |
|-------|------|
| `case`, `setup`, `peer_setup`, `action` | what was run |
| `slice`, `origin`, `ceiling` | case metadata, with the defaults above |
| `split` | `dev` or `holdout`; an unmarked case records as `dev` |
| `state_before`, `state_after` | normalized nmstate, routes, nftables, qdiscs |
| `diff` | added / removed / changed, keyed by flattened path (lists by element identity where one is unambiguous, else index) |
| `reachable_before`, `reachable_after` | does a fresh ICMP echo succeed, `null` without a peer |
| `established_before`, `established_after` | did a pre-existing TCP flow survive, `null` unless a flow was opened |
| `bulk_before`, `bulk_after` | can a 1428-byte DF packet cross, `null` without a peer |
| `exit_code`, `stderr` | action outcome as reported |
| `versions` | nmstate, kernel, iproute2, nftables |

`versions` identifies the software used to produce the measurements.
