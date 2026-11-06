# nm-devtools

NetworkManager and nmstate development tooling: test VM wrappers, the testvm
agent skill, libvirt definitions, and the containerized NM release process.
Assumes a Linux host with libvirt/qemu; the guests are Fedora and CentOS Stream.

Use `nm-tools list` or `nm-tools search "executor cancellation"` to find a check.
`nm-tools doctor --config FILE` inspects selected checkouts and prerequisites.
See [tool discovery](docs/tools.md) for commands and configuration.

## Layout

| path | what |
|------|------|
| `bin/nm-tools` | discover executable oracles and inspect local prerequisites without running them |
| `tools/oracles.json` | versioned oracle descriptions, invocation parameters and evidence contracts |
| `tools/workspace_doctor.py`, `tools/workspace.example.json` | read-only explicit workspace inspection and editable configuration example |
| `bin/testvm` | lifecycle for the nmtest VMs: up/down/status/rollback/snapshot, `-d` or `$TESTVM_DOMAIN` picks the domain |
| `bin/nm-vm` | install build dependencies and build/deploy/run NetworkManager inside the VM |
| `bin/vm-test` | run one command in a VM from a snapshot: prerequisites, hash-checked deploy, evidence collection, rollback |
| `bin/nmstate-vm` | build nmstatectl on the host (Rawhide) or inside the guest (Stream 9/10), deploy and apply YAML states |
| `bin/nm-transitions` | harvest (state, action, next-state) records in a rootless netns; no VM involved |
| `bin/nm-tui-drive` | drive nmtui against the NM mock service over a pty; asserts on activations, never on the screen |
| `bin/net-diagnose` | analyze Linux network evidence from native checks or sos reports; optional active probes |
| `tools/net_diagnose_sos.py` | optional sos report importer for `net-diagnose` |
| `cases/*.json` | transition case definitions consumed by `nm-transitions` |
| `cases/tui-*.json` | nmtui case definitions consumed by `nm-tui-drive` |
| `release/release-container.sh` | run NM's release.sh inside a Fedora container so releases work from any host distro; host gpg-agent socket for tag signing |
| `skills/testvm/` | agent skill documenting the VM workflow |
| `skills/transitions/` | agent skill for authoring transition cases and reading the corpus |
| [skills/network-diagnostics/](skills/network-diagnostics/SKILL.md) | choose native checks or sos evidence and interpret network findings |
| [skills/netfyr/](skills/netfyr/SKILL.md) | netfyr requirements, SpecDoc context graph and read API, contribution rules |
| [skills/netfyr-bugs/](skills/netfyr-bugs/SKILL.md) | netfyr bug reproduction, requirement lookup, and regression fixes |
| [skills/netfyr-review/](skills/netfyr-review/SKILL.md) | netfyr PR review against specs, revision history, and runtime evidence |
| `vm/nm-*.xml.in` | libvirt domain templates; render with `bin/render-vm-xml` |
| `vm/*.xml` | old domain snapshots and the `nmtest` network definition |
| `vm/scenarios/` | self-contained scenario scripts for `nm-vm scenario` (hwsim wifi APs) |
| `vm/seed/`, `vm/seed-c9s/`, `vm/seed-c10s/` | cloud-init NoCloud data for ssh access and the NetworkManager source mount |

## VM XML paths

Install gettext (`envsubst`), copy `vm/paths.conf.example` to the ignored
`vm/paths.conf`, and set absolute `VM_DIR` and `NM_SRC` paths. Set `NMSTATE_SRC`
to the writable nmstate checkout when rendering Stream 9/10 (or all domains).
The config is sourced as shell; quote spaces and use only trusted files.

```sh
cp vm/paths.conf.example vm/paths.conf
bin/render-vm-xml              # all domains
bin/render-vm-xml -d nm-c10s # only nm-c10s; repeat -d for multiple domains
virsh -c qemu:///system define vm/generated/nm-c10s.xml
```

`envsubst` does not XML-escape paths (`&`, `<`, `'` break these attributes);
unset extra placeholders become empty. Port edits from old `vm/*.xml` snapshots
to `.xml.in` before rendering.

## Docs

| doc | what |
|-----|------|
| [docs/tools.md](docs/tools.md) | discover, select and invoke an oracle, then interpret its evidence |
| [docs/vms.md](docs/vms.md) | domain inventory, IPs, ssh aliases, snapshots, scenario scripts, the Stream 11 stub |
| [docs/provisioning.md](docs/provisioning.md) | rebuilding cloud images and bringing up a fresh guest |
| [docs/transitions.md](docs/transitions.md) | harvesting state/action/next-state records, case and record format |
| [docs/tui.md](docs/tui.md) | driving nmtui against the mock service, case format, and the traps |
| [docs/diagnostics.md](docs/diagnostics.md) | native network checks, offline sos analysis, targeted sos collection and evidence limits |
| [docs/release.md](docs/release.md) | the containerized NM release process |

## Setup

- Put `bin/` on your `PATH` (symlink or copy `testvm`, `vm-test`, `nm-vm`, `nmstate-vm`,
  `nm-transitions`, `net-diagnose`).
- Add `nm-tools` by putting this checkout's `bin/` on `PATH` or symlinking the
  executable. If copying it, set `NM_DEVTOOLS` to the checkout containing its
  `tools/oracles.json`. Discovery needs Python 3.9+ and no VM or network access.
- The scripts ssh to per-domain aliases (`nm-vm`, `gnome-vm`, `c9-vm`, `c10-vm`, `c11-vm`).
  Add matching `~/.ssh/config` entries with `User root` and the domain IPs from
  [docs/vms.md](docs/vms.md).
- `release/release-container.sh` wraps NetworkManager's own
  `contrib/fedora/rpm/release.sh`; point `NM_SRC` at your NM checkout.
- Everything under `skills/` is an agent skill; install them wherever your agent loads
  skills from.
- The netfyr skills use SpecDoc's [context graph](https://specdoc.josie.cloud/context-graph/)
  and [read API](https://specdoc.josie.cloud/api/). See
  [SpecDoc access](skills/netfyr/references/specdoc.md) for setup and query guidance.
- `nm-transitions` needs no VM and no root, only unprivileged user namespaces.
- `nm-tui-drive` needs no VM and no root either; point `NM_SRC` at a built NM checkout.
- `net-diagnose` needs Python 3.9+ and benefits from iproute2 and the installed
  network clients. Start with `net-diagnose --summary`; add `--extended --logs`
  for more evidence or `--active --target APPLICATION_HOST` for requested
  connectivity probes (bare `--active` probes example.com). To analyze an existing
  extracted sos report, use `net-diagnose --from-sos /path/to/report --summary`.
  Sos modes require `tools/net_diagnose_sos.py` in the same checkout: put its
  `bin/` on `PATH` or symlink the executable instead of copying the script alone.
  Fresh collection with `--collector sos --sos-output NEWDIR` also requires the
  installed sos 4.7.2 or newer and root; see [diagnostics](docs/diagnostics.md).
