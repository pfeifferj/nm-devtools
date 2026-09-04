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

## What gets measured

Cases that declare a `peer` get a veth into a second namespace and are measured
before and after the action against three different questions:

- `reachable_*` pings the peer. Every ping is a fresh flow, so this answers
  "could I start a new connection right now".
- `established_*` opens a TCP connection to an echo server in the peer namespace
  before the action runs, then sends a byte through it afterwards. This answers
  "did the session I was already holding survive", which is the SSH question.
- `bulk_*` pings with a 1400-byte payload and DF set, so it needs a path MTU of
  at least 1428. This answers "will a full-size transfer still get through",
  which the other two miss entirely: both are small enough to cross any link.

They are not the same question, and seven of the current cases disagree across
them. Dropping ICMP (`nft-drop-echo-request`, `nft-drop-l4proto-icmp`) reads as a
total lockout to ping while TCP is untouched. `nft-ct-established-accept` blocks
new flows and keeps existing ones, which is what that rule is for.
`addr-replace-keeps-subnet` is the reverse: new connections work from the new
address while the established flow dies with the address it was using, the
classic way a readdress drops your shell without making the host unreachable.
And `link-lower-mtu` against `link-mtu-just-fits` is the same action with a
different number, where only the first stops full-size traffic while ping and
the held session notice nothing.

An action is a lockout in the operational sense only when all three go false,
which is 16 of the current cases against 15 that leave everything intact.

## Case design

Cases come in discriminating pairs wherever possible: the same action shape with
opposite outcomes, so nothing downstream can predict the label from the size or
shape of the diff alone. `nft-drop-peer` and `nft-drop-unrelated` differ only in
a destination address; `nft-accept-then-drop` and `nft-drop-then-accept` contain
the identical two rules in opposite order; `route-rule-to-empty-table` and
`route-rule-unreachable` differ only in the rule action; `link-lower-mtu` and
`link-mtu-just-fits` differ only in whether the new MTU clears 1428.

Most cases run `true -> ?`, but not all. `nft-drop-then-accept` starts
unreachable to show that appending an accept below a drop changes nothing, and
`nft-flush-restores` and `link-mtu-restore` start broken and repair. Read the
`*_before` labels; do not assume them.

## Local state is not sufficient

`link-mtu-peer-side` lowers the MTU at the far end. The captured state does not
move at all, diff size zero, and `bulk_after` still goes false.

The consequence is real and invisible to state. It is still predictable, because
the action says what happened: anything reading the action can see the far end
took an MTU of 1280. What no amount of local state will give you is the case
where the far end changes and nobody hands you an action describing it, which is
the shape most real external events take: a peer reboots, a switch reconfigures,
an upstream MTU drops.

Keep the case for the first half of that. A model scored on `state_before` plus
`action` can pass it; a monitor watching only state cannot see it at all.

## What the probes still cannot see

Each probe is one packet or one byte, so they detect severance and MTU but not
statistics. An action that adds latency, drops a fraction of traffic, or
reorders leaves all three labels true; `tc netem` cases would need a probe that
measures rather than one that succeeds or fails.

The peer is a single directly-connected veth, so nothing here exercises a
gateway hop, a second router, asymmetric return paths, or anything a name has to
resolve through. Lockout via DNS or via the default route is out of reach until
the topology grows.

## Reproducibility

Run `selftest` after touching the capture or normalization code. It runs every
case twice and fails if the normalized records differ, or if a run does not
produce the labels a case declares in `expect`. Expect it to take minutes.

`expect` is optional and holds any subset of the record's scalar fields. It is
what catches a probe that is broken rather than merely unstable: two runs of a
`flow_alive` stuck at true agree with each other perfectly. Give a case an
`expect` whenever the label is the reason the case exists.

Two distinct sources of noise are handled, and both were found by `selftest`
rather than by reasoning:

- Volatile fields. The kernel hands out random MACs, EUI-64 link-local addresses
  derived from them, ticking bridge timers, and per-object handles. These are
  stripped or collapsed to constants during normalization.
- Async kernel work. A netlink write returns before DAD, IPv6 link-local
  regeneration, or carrier settling has finished, so an immediate capture races
  it and the case passes selftest most of the time. Captures wait for the state
  to stop moving *and* for no address to be tentative. Waiting for stability
  alone is not enough: two captures taken during DAD agree with each other and
  are both wrong.

Recording the settled state is also the right target. A transition model should
predict where the state lands, not what it looks like mid-flight.

## nmstate inside the namespace

`nmstatectl apply -k` works, but it cannot create `dummy` interfaces there
(typed `Other("dummy")`, netlink returns EOPNOTSUPP), while `linux-bridge`
works. Build topology with iproute2 in `setup` and leave nmstate as the action
under test.

## Case format

One JSON file per case in `cases/`, named for the file stem. Unrelated to the
`nm-vm scenario` reproducers, which run inside a VM. `setup` builds the state
the action runs against, `action` is the single thing being measured:

```json
{
  "name": "nft-drop-peer",
  "description": "output drop rule for the peer address; the lockout case",
  "peer": {"subject_ip": "10.5.5.1/24", "peer_ip": "10.5.5.2/24", "probe": "10.5.5.2"},
  "setup": ["nft add table inet f"],
  "action": {"kind": "shell", "spec": "nft add rule inet f out ip daddr 10.5.5.2 drop"},
  "expect": {"reachable_after": false, "established_after": false}
}
```

`peer` may be `null` for cases that need no connectivity label. `split` marks a
case `holdout` to keep it out of anything a model is fit on; it defaults to
`dev`, so a case has to opt in. A holdout carries no `expect`, since that would
put its answer in the repo. `action.kind` is one of:

| kind | spec | runs |
|------|------|------|
| `shell` | a command | in the subject namespace |
| `nmstate` | a desired state | `nmstatectl apply -k` in the subject namespace |
| `peer_shell` | a command | in the peer namespace, for events the operator did not cause |

Cases are read from `cases/` unless `NM_TRANSITIONS_CASES` names another
directory. A case that nothing has measured yet has no business in the tracked
corpus, so a generator can write somewhere scratch, harvest, and keep only what
earns a place:

```sh
NM_TRANSITIONS_CASES=/tmp/candidates nm-transitions run my-case -o /tmp/out
```

## Record fields

`state_before`, `state_after` and `diff` are normalized; `exit_code` is recorded
but is never a success signal, since an action can exit 0 and still take the
network down. Judge outcomes from `diff` and the three connectivity labels.

| field | what |
|-------|------|
| `case`, `setup`, `action` | what was run |
| `split` | `dev` or `holdout`; an unmarked case records as `dev` |
| `state_before`, `state_after` | normalized nmstate, routes, nftables, qdiscs |
| `diff` | added / removed / changed, keyed by flattened path (lists by element identity where one is unambiguous, else index) |
| `reachable_before`, `reachable_after` | can a new flow be opened (ping), `null` without a peer |
| `established_before`, `established_after` | did a pre-existing TCP flow survive, `null` unless a flow was opened |
| `bulk_before`, `bulk_after` | can a 1428-byte DF packet cross, `null` without a peer |
| `exit_code`, `stderr` | action outcome as reported |
| `versions` | nmstate, kernel, iproute2, nftables |

`versions` exists because model fidelity drifts with the software underneath it;
a corpus without them cannot tell you when behaviour changed.
