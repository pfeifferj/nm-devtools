# nm-devtools

NetworkManager and nmstate development tooling: test VM wrappers, the testvm
agent skill, libvirt definitions, and the containerized NM release process.
Assumes a Linux host with libvirt/qemu; the guests are Fedora and CentOS Stream.

## Layout

| path | what |
|------|------|
| `bin/testvm` | lifecycle for the nmtest VMs: up/down/status/rollback/snapshot, `-d` or `$TESTVM_DOMAIN` picks the domain |
| `bin/nm-vm` | build/deploy/run NetworkManager inside the VM (source virtiofs-mounted at /mnt/nmsrc, build on VM disk) |
| `bin/nmstate-vm` | cargo-build nmstatectl on the host, scp to the VM, apply YAML states |
| `release/release-container.sh` | run NM's release.sh inside a Fedora container so releases work from any host distro; host gpg-agent socket for tag signing |
| `skills/testvm/` | agent skill documenting the VM workflow |
| `vm/*.xml` | libvirt domain and network definitions (`virsh dumpxml` snapshots) |
| `vm/seed/`, `vm/seed-c10s/` | cloud-init NoCloud seed data (ssh public key only): GNOME rawhide and CentOS Stream 10 |

## Setup

- Put `bin/` on your `PATH` (symlink or copy `testvm`, `nm-vm`, `nmstate-vm`).
- The scripts ssh to per-domain aliases (`nm-vm`, `gnome-vm`, `c10-vm`, `c11-vm`).
  Add matching `~/.ssh/config` entries with `User root` and the domain IPs below.
- `release/release-container.sh` wraps NetworkManager's own
  `contrib/fedora/rpm/release.sh`; point `NM_SRC` at your NM checkout.
- `skills/testvm/` is an agent skill; install it wherever your agent loads skills from.

## VM inventory

Libvirt domains (qemu:///system) on the `nmtest` NAT network
(198.51.100.0/24, bridge virbr-nmtest), pinned by MAC in the network DHCP.
Target a domain with `-d <name>` / `$TESTVM_DOMAIN` (testvm) or `$TESTVM_DOMAIN`
(nm-vm, nmstate-vm):

| domain | IP | ssh alias | image |
|--------|----|-----------|-------|
| nm-rawhide | 198.51.100.16 | `nm-vm` | `~/VMs/nm-rawhide.qcow2` (backing: `fedora-rawhide-base.qcow2`) |
| nm-rawhide-gnome | 198.51.100.17 | `gnome-vm` | `~/VMs/fedora-cloud-rawhide.qcow2` |
| nm-c10s | 198.51.100.18 | `c10-vm` | `~/VMs/nm-c10s.qcow2` (backing: `centos10-stream-base.qcow2`) |
| nm-c11s | 198.51.100.19 | `c11-vm` | stub: no image yet (CentOS Stream 11 unreleased) |

Each is expected to carry a `baseline-known-good` snapshot. Images stay in
`~/VMs/` (not tracked; see .gitignore). The headless CentOS/rawhide VMs share the
`nm-vm` in-VM build workflow: `TESTVM_DOMAIN=nm-c10s nm-vm build`, etc.

Rebuild from scratch. First edit the placeholders: the `vm/*.xml` disk and
virtiofs paths use `/home/user/...`, and the `vm/seed*/user-data` files ship a
dummy `ssh-ed25519 AAAA...` key. Point both at your own home and ssh public key.

```sh
virsh -c qemu:///system net-define vm/nmtest-network.xml && virsh -c qemu:///system net-autostart nmtest && virsh -c qemu:///system net-start nmtest
virsh -c qemu:///system define vm/nm-rawhide.xml          # disk paths in the XML point at ~/VMs
genisoimage -output ~/VMs/seed-gnome.iso -volid cidata -joliet -rock vm/seed/user-data vm/seed/meta-data
virsh -c qemu:///system define vm/nm-rawhide-gnome.xml
# CentOS Stream 10 (use xorriso if genisoimage is unavailable):
qemu-img create -f qcow2 -F qcow2 -b ~/VMs/centos10-stream-base.qcow2 ~/VMs/nm-c10s.qcow2 30G
xorriso -as mkisofs -V CIDATA -J -r -o ~/VMs/seed-c10s.iso vm/seed-c10s/user-data vm/seed-c10s/meta-data
virsh -c qemu:///system define vm/nm-c10s.xml
```

The gnome VM boots a fresh Fedora Cloud rawhide qcow2 with the seed ISO
attached; cloud-init injects the ssh key from `vm/seed/user-data`. The CentOS
VM works the same way (`vm/seed-c10s/`). First boot needs NM build deps:
`dnf config-manager --set-enabled crb && dnf -y builddep --skip-unavailable NetworkManager`,
then `libndp-devel` from CentOS koji (not in public repos), plus
`libbpf-devel nss-devel clang llvm bpftool`. Mount the NM source with
`echo 'nmsrc /mnt/nmsrc virtiofs defaults 0 0' >> /etc/fstab && mount /mnt/nmsrc`.

## CentOS Stream 11

`vm/nm-c11s.xml`, the `c11-vm` ssh alias (198.51.100.19), and the DHCP
reservation are pre-wired. Stream 11 GenericCloud images are not published yet
(cloud.centos.org has only 8/9/10-stream). To activate when an image exists:

```sh
# drop the base image at ~/VMs/centos11-stream-base.qcow2, then:
qemu-img create -f qcow2 -F qcow2 -b ~/VMs/centos11-stream-base.qcow2 ~/VMs/nm-c11s.qcow2 30G
mkdir -p vm/seed-c11s && sed 's/c10s/c11s/;s/Stream 10/Stream 11/' vm/seed-c10s/meta-data > vm/seed-c11s/meta-data && cp vm/seed-c10s/user-data vm/seed-c11s/user-data
xorriso -as mkisofs -V CIDATA -J -r -o ~/VMs/seed-c11s.iso vm/seed-c11s/user-data vm/seed-c11s/meta-data
virsh -c qemu:///system define vm/nm-c11s.xml
```

## Release container

```sh
NM_RELEASE_TOKEN=<gitlab-api-token> release/release-container.sh rc1
```

Dry run by default (builds RPMs + tarball in the container, no push); add
`--no-test` for the real thing. `NM_SRC` overrides the NetworkManager checkout
(default `~/rh-src/NetworkManager`). Signing uses the host gpg-agent via a
mounted socket; no keys enter the container. See the script header for all
env overrides.
