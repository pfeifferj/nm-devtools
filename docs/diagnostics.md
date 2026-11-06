# Linux network diagnostics

`bin/net-diagnose` analyzes network evidence and produces findings linked to the
checks that support them. Use its native collector for quick Linux checks and
explicit probes, or reuse sos support reports for broader evidence. It focuses
on NetworkManager desktops, Wi-Fi and VPNs, and also works on servers, containers
and network namespaces. It requires Python 3.9+ and only the standard library.

| Need | Mode |
| --- | --- |
| Inspect the current network namespace | Native collection, the default |
| Investigate an existing support report | `--from-sos DIRECTORY`, offline |
| Collect broader support evidence on this host | `--collector sos --sos-output NEWDIR` |

Native collection works with the standalone script. Sos modes also need
`tools/net_diagnose_sos.py` beside `bin/` in the resolved checkout. Put the
checkout's `bin/` on `PATH` or symlink the executable to retain that layout.

Start with a summary, then collect a full report during the problem:

```sh
bin/net-diagnose --summary
sudo bin/net-diagnose --extended --logs -o network-report.txt
bin/net-diagnose --format json --redact -o network-report.json
```

The native collector never changes network configuration, restarts services,
requests a Wi-Fi scan, resets counters, installs packages or invokes sudo.
Default collection reads local state; DNS lookups and other generated traffic
require `--active`.
An ordinary user can collect most state. Root usually adds firewall, driver,
socket-process and journal visibility. Failed checks remain visible in the report.
Queries that would need to activate a stopped service or load diagnostic kernel
modules are skipped when their prerequisites cannot be established.

## Existing sos reports

Point at the extracted report root containing `sos_reports/manifest.json`:

```sh
bin/net-diagnose --from-sos /path/to/sosreport --summary
bin/net-diagnose --from-sos /path/to/sosreport -i eth0 \
    --format json --redact -o incident-analysis.json
```

Import reads the selected directory only. It does not run archived commands,
collect evidence from the analyst's host, start a VM, or send probes to addresses
found in the report. Compressed archives are not accepted; extraction is a
separate operation. `--active`, destination selectors and native collection
options cannot be combined with this mode. `--interface` focuses analysis of
the captured evidence.

The importer supports sos 4 manifests with per-command truncation and plugin
timeout metadata, and a narrow set of host command outputs:
links, addresses, both families' route tables and rules, and recognized
NetworkManager device fields. It uses command semantics and manifest paths,
retaining return codes, truncation, timing and source references. Named network
namespace or container commands are not merged into host snapshots. Unsupported
formats, missing commands and incomplete snapshots remain collection gaps;
they are not treated as an empty or healthy network. A source report's broader
configuration and logs remain available in the original directory.
Sos 4.0/4.1 manifests omit completeness fields used by the sos 4.2+ contract and
cannot establish a complete snapshot. Unflagged all-table route output is separated by family; ambiguous
route families remain a gap rather than being guessed.

Capture metadata belongs to the source report. Fields that cannot be established
from it remain unknown; the analyst's hostname, kernel and namespace do not
stand in for the affected system. Findings describe the captured state, not
current connectivity. Already-masked addresses may prevent a supported parser
from interpreting a snapshot.
Capture time strings retain the source's notation; their timezone is unrecorded,
so they must not be treated as UTC timestamps.

Evidence reads reject symlinks, hard links, special files and paths outside the
selected report. An unsafe optional file is recorded as unavailable while the
remaining evidence stays usable. The user-selected report root may have symlinked
ancestors; manifest-controlled paths cannot traverse symlinks. Linux
`/proc/self/fd` must be available to read pinned regular files safely.
The manifest is limited to 2 MiB, command entries to 20,000, and total
input to 32 MiB. `--max-bytes` limits each evidence file and `--budget` is checked
between import operations. A blocking filesystem read can exceed that deadline;
prefer a report extracted onto local storage. The defaults are 128 KiB per
evidence file and 120 seconds.

## Fresh sos collection

```sh
sudo bin/net-diagnose --collector sos --sos-output /var/tmp/network-support \
    --timeout 10 --budget 120 --summary
```

This explicitly invokes installed sos 4.7.2 or newer. Compatibility is checked
during preparation, using the plugin-discovery header when the version command
is unavailable, before capture starts. It requires root and a
verified match between the current and PID 1 network namespaces; it never
invokes sudo itself. `--sos-output` must name a new directory. The wrapper keeps
the capture there with private permissions, then imports the completed report.
It does not fall back to native collection if sos is missing or fails.
Matching PID 1 inside a container does not establish the physical host's
namespace or ownership of mounted daemon sockets. Run fresh collection in the
intended system context. Imported evidence is not supplemented with native
checks on the machine performing the analysis.

The backend lists plugins and retains the result in `sos-plugins.txt`. It
requires `networking` to be listed as enabled and includes `networkmanager` only
when sos lists it as enabled. Availability depends on the sos version and
distribution; the report records selected plugins in `collection_plugins` and
notes absent NetworkManager coverage.

Discovery and capture use the same settings to disable automatic presets,
local configuration files, environment-variable collection and container runtime
use. Capture also disables named-namespace collection by the networking plugin.
Sos startup can still [inspect container runtimes](https://github.com/sosreport/sos/blob/main/sos/policies/distros/__init__.py)
and [enumerate devices inside named network namespaces](https://github.com/sosreport/sos/blob/main/sos/report/__init__.py)
before applying those options. Traceroute and
NIC EEPROM dumps are disabled. Secret postprocessing stays enabled and the
backend does not request an upload.

Sos collects broader support data, including configuration and logs. Its privacy
and collection scope differ from the native collector. `--interface` only filters
our analysis; sos still captures host-wide evidence. Probes and native
`--extended`, `--logs` and `--no-services` options are rejected in this mode.
Use a separate native run when explicit destination probes are needed.

`--timeout` sets individual sos command deadlines, rounded up to whole seconds
and capped by the plugin deadline. Version
checking, plugin discovery, capture and import share one `--budget`. The wrapper
reserves time for postprocessing and import and shortens plugin/command deadlines
to fit; an insufficient remaining budget prevents capture from starting.
The reserve does not guarantee slow postprocessing will finish before the overall
deadline. Plugin deadlines and 1 MiB log/journal limits reduce
collection work, but these are not a total disk-space cap. `--max-bytes` bounds
imported evidence and captured command output, not every file sos writes.

Only a successfully completed capture is imported. After a timeout, interruption
or failed run, retained files may not have passed sos secret postprocessing.
Treat that private directory as incomplete and review it before sharing.
`--redact` affects the resulting net-diagnose report, not the retained sos tree.

## NetworkManager and Wi-Fi

The following collection and probe options apply to the native collector.

```sh
bin/net-diagnose -i wlp1s0 --summary
sudo bin/net-diagnose -i wlp1s0 --extended --logs --since=-30min -o wifi-report.txt
bin/net-diagnose --active --target example.com --url https://example.com/ --summary
```

The report includes device state and activation reason, DHCP-provided settings,
addresses and DNS, active connections, radio switches, cached connectivity,
cached access points, rfkill state, regulatory settings, current association and
power saving. `--extended` adds station and driver counters, offloads and ring
settings. `--logs` adds the last 200 matching service journal entries and the last
200 kernel warnings from the current boot within `--since` (default: one hour).

Findings flag failed activations, failed duplicate-address detection, missing
carrier on a selected/default-route device, weak Wi-Fi signal, unresolved gateway
neighbors and near-full connection tracking. Radio blocks, unused down ports,
tentative addresses and historical error counters are observations. Cached
NetworkManager connectivity is not treated as a fresh Internet test.

## TUN/TAP, Tailscale, Nym and other VPNs

```sh
bin/net-diagnose -i tailscale0 --extended --summary
sudo bin/net-diagnose --extended --logs -o vpn-report.txt
bin/net-diagnose --target 100.64.0.10 --summary
bin/net-diagnose --active --target 100.64.0.10 --tcp-port 22 --summary
bin/net-diagnose --active --tailscale-netcheck --timeout 15 --summary
```

TUN/TAP inspection includes device type, flags, ownership reported by `ip tuntap`,
MTU, addressing, counters, `/dev/net/tun` availability and route tables. `UNKNOWN`
operstate is normal on many tunnels. An enabled tunnel is not proof that its
remote transport works. Multiple routed tunnels produce a reminder to inspect
rule priorities, marks, split defaults, DNS routing and kill-switch rules.

Tailscale collection reads local `status --json`: backend state, health reports,
selected exit-node availability, userspace-networking mode and peer connection
metadata. Authorization URLs and user records are excluded, including incomplete
responses that cannot be filtered. Recognizable authentication URLs in captured
output, including journal entries, are also removed independently of `--redact`.
Relay use is reported as context, not failure.
`--tailscale-netcheck` explicitly enables Tailscale STUN/relay traffic and requires
`--active`. It uses host routing and is skipped with `--interface` or when service
queries are disabled. Selecting it alone does not also probe example.com.

Nym collection uses `nym-vpnc status`, followed by `tunnel get`, `dns get` and
`lan get` when status succeeds, plus the installed `nym-vpnd` version. It shows
the tunnel mode, IPv6 setting, custom DNS and LAN policy. Systemd status and
optional logs include `nym-vpnd` and `tailscaled`. If the GUI/daemon is installed
without `nym-vpnc`, the missing CLI is recorded; kernel, service and firewall
evidence remains available. No account, mnemonic, credential or private-key
configuration is read. There is no Nym reconnect, built-in diagnostic run or
automatic mixnet traffic test. Use an explicit application destination and a
longer timeout when investigating a high-latency mixnet connection.

Other tunnels, VLANs, VRFs and bridges appear in detailed link/routing snapshots.
`--extended` adds IPv4/IPv6 tunnel configuration, XFRM policies and selected
WireGuard handshake, endpoint, allowed-IP and transfer fields. It never runs
`wg show ... dump`, `wg showconf` or an XFRM state dump containing encryption keys.

## Destination probes

`--active` with no destination selectors tests **example.com**: system NSS
resolution, up to two addresses per family, local route lookup, two ICMP echo
requests, a TCP connection to port 443, DNS A/AAAA queries and an HTTPS HEAD
request over each family. Providing any `--target`, `--dns-name`, `--url` or
`--tailscale-netcheck` replaces those defaults with exactly the selected work.

ICMP needs at least 2.5 seconds available to send both requests and collect the
summary. With a shorter timeout or remaining budget, it is recorded as skipped.

```sh
# Local route lookup only: no packets or DNS queries.
bin/net-diagnose --target 192.0.2.10 --summary

# Application-oriented checks, without a public default destination.
bin/net-diagnose --active --target intranet.example --tcp-port 443 \
    --dns-name intranet.example --url https://intranet.example/ --summary

# Query a chosen resolver; the DNS server must be numeric.
bin/net-diagnose --active --dns-name intranet.example --dns-server 192.0.2.53

# Optional UDP path/path-MTU discovery, with at most 12 hops.
bin/net-diagnose --active --target 192.0.2.10 --trace --timeout 15

# A link-local IPv6 destination needs a scope.
bin/net-diagnose --active --target fe80::1%eth0 --family 6
```

| Option | Scope |
| --- | --- |
| `--target HOST_OR_IP` | Local route lookup; with `--active`, NSS if needed, ICMP and TCP. Repeat up to 16 times. |
| `--dns-name NAME` | `dig` queries for A and/or AAAA. Repeat up to 8 times. |
| `--url URL` | Direct HTTP(S) HEAD requests. Repeat up to 8 times. |
| `--family 4`, `6`, `both` | Probe address families; snapshots always include both. Default: both. |
| `--tcp-port PORT` | Target TCP port; default 443. Connect and close without application payload. |
| `--interface NAME` | Focus device details/findings; bind route lookup, ICMP, TCP and HTTP to that interface. |
| `--trace` | Add bounded tracepath probes to selected targets; skipped when interface binding is required. |

NSS and explicit DNS queries retain their own resolver/routing context, including
when `--interface` is set. `dig` reads `/etc/resolv.conf` or uses `--dns-server`;
it does not implement systemd-resolved's per-domain routing. NSS may resolve via
files, mDNS or a host service instead of sending a DNS packet from this namespace.
An AAAA query does not itself imply IPv6 transport to the DNS server.

HTTP probes treat URLs literally, ignore curl's user configuration, bypass proxies, verify TLS, do not
follow redirects and discard response headers/body. HTTP 403/405 still records
that a server responded; it does not become a transport failure. Only the names
of configured proxy environment variables are collected, not their values.
ICMP failures do not prove TCP failures. TCP refusals do not prove an unreachable
host. Probe failures are scoped to their protocol, family, destination and budget.

## Collection coverage and limits

Native default evidence includes IPv4/IPv6 addresses, all route tables and rules,
ARP/NDP, resolver/NSS/hosts files, NetworkManager and networkd/resolved state,
nftables and iptables counters, listening sockets and pending TCP connections,
bridge links/VLANs, queueing disciplines, kernel network counters, forwarding,
reverse-path filtering, IPv6 settings and connection-tracking capacity.

No optional tool is mandatory for producing a partial report. `ip` from iproute2
with JSON support is needed for core diagnosis. Common optional tools are
`nmcli`, `resolvectl`, `networkctl`, `ss`, `iw`, `rfkill`, `ethtool`, `nft`,
`iptables-save`, `ip6tables-save`, `bridge`, `tc`, `journalctl`, `tailscale`,
`nym-vpnc`, `nym-vpnd`, `wg` and `conntrack`. Active checks additionally use
`ping`, `dig`, `curl` and optionally `tracepath`.

Each check has a five-second timeout and a combined stdout/stderr limit of
128 KiB. The overall collection budget is 120 seconds. Override with `--timeout`,
`--max-bytes` and `--budget`. Commands run sequentially; later checks may be skipped
if the budget is exhausted. Checks interrupted by the whole-run deadline are
marked `budget`, not network failures. Extra device checks cover at most 32 interfaces;
global snapshots still cover all interfaces within their output limits. Use
`--interface` to focus a large host. Cleanup on timeout, interruption or excess
output targets processes in the command's session with bounded waits. A process
that creates another session can escape that cleanup; this is not process
containment. Safe cleanup of descendants that change process group requires
pidfd support in both Python and the kernel; missing support is recorded as a
cleanup gap. Truncated/malformed JSON is never used as a
complete snapshot. A snapshot is not atomic, and counter changes need repeated
reports to establish a rate or persistent fault.

## Namespaces

The native collector inspects its current network namespace. Use the standard
Linux tools to select another one:

```sh
sudo ip netns exec vpn /absolute/path/to/net-diagnose --summary
sudo nsenter --target 1234 --net /absolute/path/to/net-diagnose --no-services --summary
```

The report records current and PID 1 namespace identities and the resolved path
of `/etc/resolv.conf`. A namespace mismatch or an unverified comparison disables
host service queries, VPN daemon queries and journal collection. Even matching
identities do not establish who owns a mounted
D-Bus/daemon socket inside a container. Use `--no-services` to exclude it.

`ip netns exec` applies `/etc/netns/NAME/` configuration overrides when present.
`nsenter --net` does not change resolver files, filesystem or NSS delegation.
Check those contexts before interpreting a namespace's DNS results.

## Reports and exit status

Text output starts with interface state, findings, evidence references and
suggested next checks, followed by collection gaps and raw evidence. `--summary`
omits raw evidence. `--format json` includes schema version 1, metadata, summary,
findings and evidence. Every check carries an ID, source command/path,
status, exit code, duration and captured output. Supported complete snapshots
are normalized in `data`. Sos evidence retains its archive source and capture
metadata; Tailscale status is explicitly marked as filtered.

Statuses distinguish `ok`, `missing`, `denied`, `error`, `timeout`, `truncated`,
`invalid`, `budget` and intentional `skipped` checks. Core completeness covers
link/address snapshots and both families' route/rule snapshots; it does not imply
that every optional collector or active probe succeeded.

`-o PATH` exclusively creates a mode-0600 file and refuses existing files and
symlinks. Existing reports are not overwritten. `-o -` writes to stdout; shell
redirection permissions depend on your shell's umask.

`--redact` replaces recognized IPv4/IPv6 addresses, hardware addresses, DHCP
identifiers and known hostname forms with consistent per-report tokens after
diagnosis. Sos source-directory paths are tokenized independently of hostname
knowledge; offline import does not infer a hostname from a report's filename.
This is **partial
pseudonymization**, not full anonymization: SSIDs, DNS names, profile names,
public keys, arbitrary comments/logs and URL parameters can remain identifying.
Review reports before sharing, particularly when including logs.

| Exit | Meaning |
| --- | --- |
| `0` | Report produced with core state collected; optional checks may still be unavailable. |
| `1` | Findings meet `--fail-on warning` or `--fail-on error`. |
| `2` | Invalid input, unsupported platform or failure to produce/write the report. |
| `3` | Report produced but core collection is incomplete. `--fail-on` takes precedence. |
| `129` | Interrupted by SIGHUP. |
| `130` | Interrupted by SIGINT. |
| `143` | Interrupted by SIGTERM. |

The default `--fail-on never` does not fail because of diagnostic findings.
Missing optional tools alone do not make the whole command fail.

## References and development

Command semantics follow the upstream [NetworkManager nmcli manual](https://networkmanager.dev/docs/api/latest/nmcli.html),
[Linux IP sysctl documentation](https://docs.kernel.org/networking/ip-sysctl.html),
[Tailscale CLI reference](https://tailscale.com/kb/1080/cli) and
[Nym CLI source](https://github.com/nymtech/nym-vpn-client/tree/develop/nym-vpn-core/crates/nym-vpnc/src).
Sos integration follows its [report options](https://github.com/sosreport/sos/blob/main/man/en/sos-report.1),
[manifest format](https://github.com/sosreport/sos/wiki#can-i-see-what-it-is-collecting)
and [networking collector](https://github.com/sosreport/sos/blob/main/sos/report/plugins/networking.py).
The effective reverse-path filter is the maximum of the `all` and per-interface
values; strict mode is contextual information because it can interact with VPN
and asymmetric routing.

Run the diagnostics regression suite without root, live host collection or
network probes:

```sh
python3 -B -m unittest discover -s tests -p 'test_net_diagnose*.py' -v
```

Tests cover bounded subprocesses and descendant cleanup, incomplete evidence,
passive mode, namespace scope, target/probe isolation, policy/IPv6 routing,
NetworkManager/Wi-Fi/VPN findings, Tailscale authorization-field omission, private
output creation and pseudonymization.
Sos tests use synthetic extracted reports and a fake collection command to
exercise parsing, evidence gaps, bounded directory reads and backend failures
without collecting a real host's support data. An optional loopback test requires
`NET_DIAGNOSE_LIVE_TESTS=1`; it invokes real network tools and local probes.

Run the full CLI against real network faults in a disposable VM:

```sh
python3 tests/e2e_net_diagnose.py
# Preserve reports in a chosen empty directory:
python3 tests/e2e_net_diagnose.py --output-dir /tmp/net-diagnose-e2e
```

This requires Linux user/network/mount namespaces, IPv6, veth, TUN/TAP and
nftables support, plus `unshare`, `nsenter`, `mount`, `ip`, `nft`, `ping`, `curl`,
`dig` and `openssl`. Unprivileged user namespaces must be permitted by the host;
root is not otherwise required.
Network namespaces share the host kernel: creating links and firewall rules can
autoload host modules even from a user namespace. Use a disposable VM when that
host change is unacceptable. The harness accepts inactive kernel fallback tunnel
interfaces already present in a fresh namespace.

The harness creates disposable namespaces with a veth peer, private resolver
files and local DNS/HTTP/HTTPS servers. It invokes the complete script for IPv4,
bound IPv6, NSS, DNS/NXDOMAIN, TLS trust, HTTP HEAD restrictions, ICMP filtering
and partial loss, TCP refusal/timeouts, blackhole and policy routes, link/carrier
failures, TUN/TAP, redaction, missing tools, output limits, time budgets and
exclusive report creation. Passive collection is checked for unchanged links,
routes and server request logs. It removes its temporary fixtures and retains
private per-case JSON reports plus `results.json` in the printed output directory.

Host service queries are disabled in this suite. NetworkManager daemon state,
physical Wi-Fi behavior and authenticated VPN connections need separate host or
VM tests; namespace fixtures do not establish those behaviors.
