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

## Leases

A domain in use carries a lease at `$XDG_RUNTIME_DIR/testvm/<domain>.lease`
naming the session (`$TESTVM_OWNER`, else `$CLAUDE_CODE_SESSION_ID`, else
Codex's `$CODEX_THREAD_ID`, else user@host), its pid (`$CLAUDE_PID`, the
`codex` ancestor process, else the caller), and what it is doing. `testvm up|down|rollback|snapshot`
and every `nm-vm` and `nmstate-vm` command that changes the guest claim it
first and fail while another live session holds it. `testvm claim [why]` and `testvm release` manage
it by hand; `testvm status` and `testvm domains` show it. A lease whose pid is
gone is taken over; `TESTVM_FORCE=1` overrides a live one.

`testvm domains` and `status` report a domain as `undefined` only when
libvirt answers that it does not exist; a connection or permission failure
prints libvirt's error and shows `error`. `snapshot` and `rollback` print
elapsed time every 5 seconds.

## vm-test

`vm-test [options] -- cmd [args...]` runs one command against a known guest
state and writes everything needed to judge the run to an evidence directory
(default `~/.local/state/vm-test/<domain>-<UTC time>`):

1. claim the domain and roll back to `-s SNAP` (default `baseline-known-good`;
   `none` uses the guest as it is), stepping the guest clock afterwards
2. `dnf install` each missing `-p PKG`
3. scp each `-f SRC[:DEST]` (default DEST `/usr/local/bin/<name>`) and compare
   its sha256 on both ends; a mismatch stops before the command runs
4. run the command; record stdout, stderr, exit status, and the sha256 of the
   executable it resolved to
5. collect the guest journal for the run and each `-e PATH` under `files/`
6. roll back to SNAP again, also on failure or interrupt (`-k` skips this)

```sh
TESTVM_DOMAIN=nm-c10s vm-test -p tcpdump -f ./repro.sh -e /var/log/repro -- repro.sh --iterations 20
```

`meta` records the domain, snapshot, lease, times, command, and exit
(`setup-failed` or `interrupted` when the command did not finish). vm-test
exits with the command's status, or 2 when setup failed.

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

`nm-vm scenario` copies a single file, so multi-file scenarios (e.g. hostapd
configs next to the script) need to be self-contained. `vm/scenarios/hwsim-ap.sh`
is the in-repo wifi entry point: two mac80211_hwsim APs (open + WPA2-PSK, both
with DHCP) with `wlan0` left NM-managed as the client. They supply addresses
without an upstream route or DNS. Snapshot the guest's current state before
running it and restore that snapshot afterwards; rerunning with existing
hwsim radios is refused.
The APs and client share one network namespace. Use this fixture for scanning,
authentication, DHCP and UI tests.

## CentOS Stream 11

`vm/nm-c11s.xml.in`, the `c11-vm` ssh alias (198.51.100.19), and the DHCP
reservation are pre-wired. Stream 11 GenericCloud images are not published yet
(cloud.centos.org has only 8/9/10-stream). To activate when an image exists:

```sh
# drop the base image at ~/VMs/centos11-stream-base.qcow2, then:
bin/render-vm-xml nm-c11s
qemu-img create -f qcow2 -F qcow2 -b ~/VMs/centos11-stream-base.qcow2 ~/VMs/nm-c11s.qcow2 30G
mkdir -p vm/seed-c11s && sed 's/c10s/c11s/;s/Stream 10/Stream 11/' vm/seed-c10s/meta-data > vm/seed-c11s/meta-data && cp vm/seed-c10s/user-data vm/seed-c11s/user-data
xorriso -as mkisofs -V CIDATA -J -r -o ~/VMs/seed-c11s.iso vm/seed-c11s/user-data vm/seed-c11s/meta-data
virsh -c qemu:///system define vm/generated/nm-c11s.xml
```
