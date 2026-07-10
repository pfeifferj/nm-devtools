---
name: testvm
description: "Operate the nmtest libvirt VMs used for NetworkManager and nmstate integration testing. Covers the testvm lifecycle wrapper (up/rollback/snapshot), the nm-vm and nmstate-vm deploy wrappers, the four VM domains (rawhide, rawhide-gnome, CentOS Stream 10/11), and how to reach them."
when-to-use: "When deploying or testing NetworkManager or nmstate against a real VM, rolling back the VM, applying nmstate config states, needing a GNOME desktop rawhide VM, testing on CentOS Stream, or any mention of nm-vm, nm-rawhide, nm-c10s, the test VM, or 'the rawhide vm'."
allowed-tools: [Bash, Read]
context: inline
---

# testvm

Operational reference for the libvirt test VMs and their helper scripts. All run
against `qemu:///system`; the user is in the `libvirt` group, so no `sudo` is
needed for virsh/qemu-img. There is no passwordless sudo and no guestfs-tools.

## The VMs

Domains on the `nmtest` NAT network (`198.51.100.0/24`). Each has its own
MAC, a pinned DHCP IP, and an ssh alias, so they run concurrently.

| domain | IP / ssh alias | what it is | use for |
|--------|----------------|-----------|---------|
| `nm-rawhide` | `198.51.100.16` / `nm-vm` | headless Fedora rawhide, patched NetworkManager dev build (meson-installed) + host-deployed `nmstatectl` | NM/nmstate integration testing (default) |
| `nm-rawhide-gnome` | `198.51.100.17` / `gnome-vm` | fresh Fedora Cloud rawhide + GNOME Workstation, stock NM | GUI testing (nm-applet, connection editor), desktop repro |
| `nm-c10s` | `198.51.100.18` / `c10-vm` | headless CentOS Stream 10, in-VM NM meson build (same workflow as nm-rawhide) | NM/nmstate testing on the RHEL 10 target |
| `nm-c11s` | `198.51.100.19` / `c11-vm` | stub: XML + DHCP + ssh alias wired, no image yet (Stream 11 unreleased) | future RHEL 11 target |

- Root, key auth via `~/.ssh/id_ed25519`. IPs are pinned via `ip-dhcp-host`
  entries in the `nmtest` network.
- Each active VM has a `baseline-known-good` snapshot for fast rollback.
- Drive the CentOS VM through the same wrappers: `TESTVM_DOMAIN=nm-c10s nm-vm build`,
  `testvm -d nm-c10s rollback`. A host-built `nmstatectl` may not run on Stream's
  older glibc; build nmstate in-VM there if `nmstate-vm deploy` fails at runtime.

## testvm (VM lifecycle, tool-agnostic)

`~/.local/bin/testvm` controls start/stop/snapshot. It knows nothing about NM or
nmstate. Pick a domain with `-d <name>` or `$TESTVM_DOMAIN` (default `nm-rawhide`);
it routes ssh to the right alias per domain.

```
testvm up                      # start default VM, wait for ssh
testvm -d nm-rawhide-gnome up  # start the GNOME VM (runs alongside nm-rawhide)
testvm down                    # graceful shutdown
testvm status                  # domain state + ssh reachability
testvm domains                 # list all domains, state, and ssh alias
testvm rollback [snap]         # revert to snapshot (default baseline-known-good)
testvm snapshot <name>         # snapshot (disk+RAM) before destructive tests
testvm snapshots               # list snapshots
testvm console                 # serial console (exit Ctrl+])
testvm -d nm-rawhide-gnome ssh # ssh into the chosen domain
```

Typical loop: `testvm snapshot before-test` -> run a test -> `testvm rollback before-test`.

## nmstate-vm (build + deploy + apply nmstate)

`~/.local/bin/nmstate-vm`. A host-built `nmstatectl` usually runs on Fedora
as-is, so deploy is just `cargo build` + `scp` (no in-VM build).

```
nmstate-vm build           # cargo build --release nmstatectl on the host
nmstate-vm deploy          # build + scp to the VM's /usr/local/bin, print version
nmstate-vm apply <state>   # apply a YAML: a local path or a name in nmstate/examples/
nmstate-vm show [iface]    # nmstatectl show on the VM
nmstate-vm status          # deployed nmstatectl version
```

`apply` resolves `<state>` as a literal path, else `examples/<state>`, else
`examples/<state>.yml` (repo at `~/rh-src/nmstate`). The repo ships ~50 example
states (bonds, bridges, VLANs, routes, DNS, SR-IOV, ipsec).

## nm-vm (build + deploy NetworkManager)

`~/.local/bin/nm-vm`. Unlike nmstate, NetworkManager must be built **inside** the
VM (the host can't produce runnable Fedora binaries). The host source tree
is virtiofs-mounted read-only at `/mnt/nmsrc`; the build dir is `/root/nm-build`.

```
nm-vm build            # meson setup (if needed) + ninja in the VM
nm-vm install          # meson install + restart NetworkManager (full)
nm-vm deploy           # fast selective copy of changed binaries + restart
nm-vm run [args]       # run the built daemon in foreground (--debug)
nm-vm test [args]      # run the NM test suite in the VM
nm-vm status           # running daemon version
nm-vm console|view     # serial / graphical console
nm-vm ssh [cmd]        # ssh into the VM
```

## Recovering a locked VM (no shell, no password)

If a domain has no ssh key and no known password, the qemu guest agent still
works: `virsh -c qemu:///system set-user-password <dom> root <pw>`, or
`qemu-agent-command` with `guest-exec`. Note the agent runs SELinux-confined
(`virt_qemu_ga_t`): it cannot write `/root`, edit config, run setenforce, or
start services. To seed ssh access into a fresh image cleanly, prefer a
cloud-init NoCloud seed ISO (build with `xorriso -as mkisofs -V CIDATA ...`,
attach as a cdrom) rather than fighting the agent.
