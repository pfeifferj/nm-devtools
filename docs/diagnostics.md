# Linux network diagnostics

`bin/net-diagnose` collects network state and produces findings linked to the
checks that support them. It focuses on NetworkManager desktops, Wi-Fi and VPNs,
and also works on servers, containers and network namespaces. It is a standalone
Python 3.9+ script using only the standard library.

Start with a summary, then collect a full report during the problem:

```sh
bin/net-diagnose --summary
sudo bin/net-diagnose --extended --logs -o network-report.txt
bin/net-diagnose --format json --redact -o network-report.json
```

The script never changes network configuration, restarts services, requests a
Wi-Fi scan, resets counters, installs packages or invokes sudo. Default collection
reads local state; DNS lookups and other generated traffic require `--active`.
An ordinary user can collect most state. Root usually adds firewall, driver,
socket-process and journal visibility. Failed checks remain visible in the report.

## NetworkManager and Wi-Fi

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

Default evidence includes IPv4/IPv6 addresses, all route tables and rules,
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
`--interface` to focus a large host. Descendants are killed on timeout,
interruption or excess output. Truncated/malformed JSON is never used as a
complete snapshot. A snapshot is not atomic, and counter changes need repeated
reports to establish a rate or persistent fault.

## Namespaces

The script inspects its current network namespace. Use the standard Linux tools
to select another one:

```sh
sudo ip netns exec vpn /absolute/path/to/net-diagnose --summary
sudo nsenter --target 1234 --net /absolute/path/to/net-diagnose --no-services --summary
```

The report records current and PID 1 namespace identities and the resolved path
of `/etc/resolv.conf`. A known namespace mismatch disables host service queries,
VPN daemon queries and journal collection. If PID 1's identity is unreadable,
local service queries remain enabled and their namespace comparison is labeled
**unverified**. Even matching identities do not establish who owns a mounted
D-Bus/daemon socket inside a container. Use `--no-services` to exclude it.

`ip netns exec` applies `/etc/netns/NAME/` configuration overrides when present.
`nsenter --net` does not change resolver files, filesystem or NSS delegation.
Check those contexts before interpreting a namespace's DNS results.

## Reports and exit status

Text output starts with interface state, findings, evidence references and
suggested next checks, followed by collection gaps and raw evidence. `--summary`
omits raw evidence. `--format json` includes schema version 1, metadata, summary,
findings and evidence. Every check carries an ID, source command/path,
status, exit code, duration and captured output. Parsed complete JSON is also
available in `data`. Tailscale status is explicitly marked as filtered.

Statuses distinguish `ok`, `missing`, `denied`, `error`, `timeout`, `truncated`,
`invalid`, `budget` and intentional `skipped` checks. Core completeness covers
link/address snapshots and both families' route/rule snapshots; it does not imply
that every optional collector or active probe succeeded.

`-o PATH` exclusively creates a mode-0600 file and refuses existing files and
symlinks. Existing reports are not overwritten. `-o -` writes to stdout; shell
redirection permissions depend on your shell's umask.

`--redact` replaces recognized IPv4/IPv6 addresses, MAC addresses and the local
hostname with consistent per-report tokens after diagnosis. This is **partial
pseudonymization**, not full anonymization: SSIDs, DNS names, profile names,
public keys, arbitrary comments/logs and URL parameters can remain identifying.
Review reports before sharing, particularly when including logs.

| Exit | Meaning |
| --- | --- |
| `0` | Report produced with core state collected; optional checks may still be unavailable. |
| `1` | Findings meet `--fail-on warning` or `--fail-on error`. |
| `2` | Invalid input, unsupported platform or failure to produce/write the report. |
| `3` | Report produced but core collection is incomplete. `--fail-on` takes precedence. |
| `130` | Interrupted. |

The default `--fail-on never` does not fail because of diagnostic findings.
Missing optional tools alone do not make the whole command fail.

## References and development

Command semantics follow the upstream [NetworkManager nmcli manual](https://networkmanager.dev/docs/api/latest/nmcli.html),
[Linux IP sysctl documentation](https://docs.kernel.org/networking/ip-sysctl.html),
[Tailscale CLI reference](https://tailscale.com/kb/1080/cli) and
[Nym CLI source](https://github.com/nymtech/nym-vpn-client/tree/develop/nym-vpn-core/crates/nym-vpnc/src).
The effective reverse-path filter is the maximum of the `all` and per-interface
values; strict mode is contextual information because it can interact with VPN
and asymmetric routing.

Run the regression suite without root or external network access (one test uses
local loopback sockets):

```sh
python3 -m unittest discover -s tests -v
```

Tests cover bounded subprocesses and descendant cleanup, incomplete evidence,
passive mode, namespace scope, target/probe isolation, policy/IPv6 routing,
NetworkManager/Wi-Fi/VPN findings, Tailscale authorization-field omission, private
output creation and pseudonymization.

Run the full CLI against real, isolated network faults:

```sh
python3 tests/e2e_net_diagnose.py
# Preserve reports in a chosen empty directory:
python3 tests/e2e_net_diagnose.py --output-dir /tmp/net-diagnose-e2e
```

This requires Linux user/network/mount namespaces, IPv6, veth, TUN/TAP and
nftables support, plus `unshare`, `nsenter`, `mount`, `ip`, `nft`, `ping`, `curl`,
`dig` and `openssl`. Unprivileged user namespaces must be permitted by the host;
root is not otherwise required.

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
