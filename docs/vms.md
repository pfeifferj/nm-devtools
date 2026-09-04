# Test VMs

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

## Scenario scripts

`nm-vm scenario <script> [args...]` scps a host-side script into the VM and runs
it there. Network reproducers (netns and mac80211_hwsim topologies, e.g. the ones
in [bengal/scripts](https://github.com/bengal/scripts): NAT64/CLAT, DHCPv6-PD,
802.1X, WireGuard, Wi-Fi roaming) tear down connections, load kernel modules and
install packages, so run them against a snapshotted guest:

```sh
testvm -d nm-rawhide rollback baseline-known-good
nm-vm scenario ~/src/bengal-scripts/test-prefix-delegation.sh dhcp-stateful
```

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
