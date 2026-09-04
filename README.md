# nm-devtools

NetworkManager and nmstate development tooling: test VM wrappers, the testvm
agent skill, libvirt definitions, and the containerized NM release process.
Assumes a Linux host with libvirt/qemu; the guests are Fedora and CentOS Stream.

## Layout

| path | what |
|------|------|
| `bin/testvm` | lifecycle for the nmtest VMs: up/down/status/rollback/snapshot, `-d` or `$TESTVM_DOMAIN` picks the domain |
| `bin/nm-vm` | install build dependencies and build/deploy/run NetworkManager inside the VM |
| `bin/nmstate-vm` | cargo-build nmstatectl on the host, scp to the VM, apply YAML states |
| `bin/nm-transitions` | harvest (state, action, next-state) records in a rootless netns; no VM involved |
| `bin/nm-tui-drive` | drive nmtui against the NM mock service over a pty; asserts on activations, never on the screen |
| `cases/*.json` | transition case definitions consumed by `nm-transitions` |
| `cases/tui-*.json` | nmtui case definitions consumed by `nm-tui-drive` |
| `release/release-container.sh` | run NM's release.sh inside a Fedora container so releases work from any host distro; host gpg-agent socket for tag signing |
| `skills/testvm/` | agent skill documenting the VM workflow |
| `skills/transitions/` | agent skill for authoring transition cases and reading the corpus |
| `vm/*.xml` | libvirt domain and network definitions (`virsh dumpxml` snapshots) |
| `vm/seed/`, `vm/seed-c9s/`, `vm/seed-c10s/` | cloud-init NoCloud data for ssh access and the NetworkManager source mount |

## Docs

| doc | what |
|-----|------|
| [docs/vms.md](docs/vms.md) | domain inventory, IPs, ssh aliases, snapshots, scenario scripts, the Stream 11 stub |
| [docs/provisioning.md](docs/provisioning.md) | rebuilding cloud images and bringing up a fresh guest |
| [docs/transitions.md](docs/transitions.md) | harvesting state/action/next-state records, case and record format |
| [docs/tui.md](docs/tui.md) | driving nmtui against the mock service, case format, and the traps |
| [docs/release.md](docs/release.md) | the containerized NM release process |

## Setup

- Put `bin/` on your `PATH` (symlink or copy `testvm`, `nm-vm`, `nmstate-vm`,
  `nm-transitions`).
- The scripts ssh to per-domain aliases (`nm-vm`, `gnome-vm`, `c9-vm`, `c10-vm`, `c11-vm`).
  Add matching `~/.ssh/config` entries with `User root` and the domain IPs from
  [docs/vms.md](docs/vms.md).
- `release/release-container.sh` wraps NetworkManager's own
  `contrib/fedora/rpm/release.sh`; point `NM_SRC` at your NM checkout.
- `skills/testvm/` and `skills/transitions/` are agent skills; install them wherever your agent loads skills from.
- `nm-transitions` needs no VM and no root, only unprivileged user namespaces.
- `nm-tui-drive` needs no VM and no root either; point `NM_SRC` at a built NM checkout.
