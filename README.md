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
| `release/release-container.sh` | run NM's release.sh inside a Fedora container so releases work from any host distro; host gpg-agent socket for tag signing |
| `skills/testvm/` | agent skill documenting the VM workflow |
| `vm/*.xml` | libvirt domain and network definitions (`virsh dumpxml` snapshots) |
| `vm/seed/`, `vm/seed-c9s/`, `vm/seed-c10s/` | cloud-init NoCloud data for ssh access and the NetworkManager source mount |

## Setup

- Put `bin/` on your `PATH` (symlink or copy `testvm`, `nm-vm`, `nmstate-vm`).
- The scripts ssh to per-domain aliases (`nm-vm`, `gnome-vm`, `c9-vm`, `c10-vm`, `c11-vm`).
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
| nm-c9s | 198.51.100.20 | `c9-vm` | `~/VMs/nm-c9s.qcow2` (backing: `centos9-stream-base.qcow2`) |
| nm-c10s | 198.51.100.18 | `c10-vm` | `~/VMs/nm-c10s.qcow2` (backing: `centos10-stream-base.qcow2`) |
| nm-c11s | 198.51.100.19 | `c11-vm` | stub: no image yet (CentOS Stream 11 unreleased) |

Each active VM should carry a `baseline-known-good` snapshot. Images stay in
`~/VMs/` (not tracked; see `.gitignore`). Virtiofs source content is not part of
the snapshot, so rollback does not restore the host NetworkManager checkout.

## Rebuild cloud-image VMs

Download the current x86_64 cloud images and verify their vendor checksums before
putting them under `~/VMs/`:

- Fedora Rawhide: the `Fedora-Cloud-Base-Generic-Rawhide-*.x86_64.qcow2` image
  and matching `CHECKSUM` from the [Rawhide Cloud image directory](https://download.fedoraproject.org/pub/fedora/linux/development/rawhide/Cloud/x86_64/images/).
  Save a writable copy as `fedora-cloud-rawhide.qcow2` and install the desktop
  environment there before creating the GNOME baseline.
- CentOS Stream 9: [GenericCloud latest](https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2)
  and its [SHA-256 file](https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2.SHA256SUM),
  saved as `centos9-stream-base.qcow2` after verification.
- CentOS Stream 10: [GenericCloud latest](https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2)
  and its [SHA-256 file](https://cloud.centos.org/centos/10-stream/x86_64/images/CentOS-Stream-GenericCloud-10-latest.x86_64.qcow2.SHA256SUM),
  saved as `centos10-stream-base.qcow2` after verification.

Fedora's `CHECKSUM` is GPG-signed, so verify the signature as well. The CentOS
`SHA256SUM` files sit next to the images on the same host and only catch
transfer corruption, not a compromised mirror.

The headless `nm-rawhide` domain expects an already-prepared
`fedora-rawhide-base.qcow2`; the tracked NoCloud data initializes only the GNOME
Rawhide and CentOS images.
Before defining the cloud-image domains, replace `/home/user/...` in their XML
files and both copies of the dummy ssh key in each `vm/seed*/user-data`.

```sh
virsh -c qemu:///system net-define vm/nmtest-network.xml && virsh -c qemu:///system net-autostart nmtest && virsh -c qemu:///system net-start nmtest
virsh -c qemu:///system define vm/nm-rawhide.xml   # expects a prepared fedora-rawhide-base.qcow2 in ~/VMs
genisoimage -output ~/VMs/seed-gnome.iso -volid cidata -joliet -rock vm/seed/user-data vm/seed/meta-data
virsh -c qemu:///system define vm/nm-rawhide-gnome.xml
# CentOS Stream 9:
qemu-img create -f qcow2 -F qcow2 -b ~/VMs/centos9-stream-base.qcow2 ~/VMs/nm-c9s.qcow2 30G
xorriso -as mkisofs -V CIDATA -J -r -o ~/VMs/seed-c9s.iso vm/seed-c9s/user-data vm/seed-c9s/meta-data
virsh -c qemu:///system define vm/nm-c9s.xml
# CentOS Stream 10 (use xorriso if genisoimage is unavailable):
qemu-img create -f qcow2 -F qcow2 -b ~/VMs/centos10-stream-base.qcow2 ~/VMs/nm-c10s.qcow2 30G
xorriso -as mkisofs -V CIDATA -J -r -o ~/VMs/seed-c10s.iso vm/seed-c10s/user-data vm/seed-c10s/meta-data
virsh -c qemu:///system define vm/nm-c10s.xml
```

Cloud-init injects the root ssh key and mounts the read-only `nmsrc` virtiofs
export at `/mnt/nmsrc`. `testvm up` waits for both ssh and cloud-init on these
seed-managed domains. On a fresh guest, install deps and run a build before
taking the baseline snapshot:

```sh
testvm -d nm-c9s up
TESTVM_DOMAIN=nm-c9s nm-vm deps
TESTVM_DOMAIN=nm-c9s nm-vm build
testvm -d nm-c9s snapshot baseline-known-good
```

`nm-vm deps` is safe to rerun. On Fedora it uses DNF5; on Stream 9/10 it enables
CRB, installs a pinned `libndp` pair from CentOS Koji, then pulls the rest via
`builddep`. The Koji RPMs are unsigned, so the script checks them against
SHA-256 hashes committed in `bin/nm-vm`. Run `nm-vm reconf` if Meson was
configured before the dependencies were installed.

NoCloud applies `mounts` and `runcmd` once per instance. Rebuilding a seed ISO
with the same `instance-id` does not update an initialized disk or snapshot; use
a fresh overlay or change the seed's instance ID, then recreate the baseline
after `virsh snapshot-delete`-ing the old one; duplicate snapshot names fail.

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
