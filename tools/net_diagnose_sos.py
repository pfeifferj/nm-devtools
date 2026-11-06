"""Read selected networking evidence from an extracted sos-4 report."""

from collections import defaultdict
import errno
import ipaddress
import json
import math
import os
from pathlib import PurePosixPath
import re
import shlex
import stat
import time
import unicodedata


CORE = ("links", "addresses", "routes4", "routes6", "rules4", "rules6")
MANIFEST_LIMIT = 2 * 1024 * 1024
TOTAL_LIMIT = 32 * 1024 * 1024
ENTRY_LIMIT = 20000
PATH_DEPTH = 32
JSON_DEPTH = 32
JSON_NODE_LIMIT = 200000
FILE_IDS = {"etc/resolv.conf": "resolv-conf", "etc/nsswitch.conf": "nsswitch",
            "etc/hosts": "hosts", "etc/os-release": "os-release"}
LINK_KINDS = {"bridge", "bond", "vlan", "veth", "dummy", "vrf", "tun", "tap",
              "wireguard", "amneziawg", "gre", "gretap", "ip6gre", "ip6gretap",
              "ipip", "sit", "vti", "vti6", "vxlan", "geneve", "macvlan",
              "macvtap", "ipvlan", "ipvtap", "team", "ipoib", "bareudp",
              "erspan", "ip6erspan", "ip6tnl", "hsr", "batadv", "netdevsim",
              "nlmon", "netkit", "lowpan", "vcan", "vxcan", "can", "dsa",
              "rmnet", "wwan", "xfrm", "mctp", "amt", "ifb", "macsec", "openvswitch"}
ADDRESS_FLAGS = {"dynamic", "secondary", "primary", "temporary", "tentative",
                 "dadfailed", "deprecated", "nodad", "mngtmpaddr", "noprefixroute",
                 "autojoin", "stable-privacy", "optimistic", "permanent"}
NM_GENERAL = {"DEVICE", "TYPE", "NM-TYPE", "DBUS-PATH", "VENDOR", "PRODUCT", "DRIVER",
              "DRIVER-VERSION", "FIRMWARE-VERSION", "HWADDR", "MTU", "STATE", "REASON",
              "UDI", "IP-IFACE", "IS-SOFTWARE", "NM-MANAGED", "AUTOCONNECT",
              "FIRMWARE-MISSING", "NM-PLUGIN-MISSING", "PHYS-PORT-ID", "CONNECTION",
              "CON-UUID", "CON-PATH", "METERED"}


class SosImportError(ValueError):
    pass


class UnsafeEvidenceError(SosImportError):
    pass


class DirectoryReader:
    def __init__(self, directory, budget):
        self.deadline = time.monotonic() + budget
        self.total = 0
        path = os.fspath(directory)
        if not isinstance(path, str) or not path.strip():
            raise SosImportError("sos report directory must be a nonempty text path")
        self.path = os.path.abspath(path)
        if len(self.path) > 4096 or len(self.path.split("/")) > 64 or _path_controls(self.path):
            raise SosImportError("invalid sos report directory path")
        self.check_time()
        self.fd = os.open(self.path, os.O_PATH | os.O_DIRECTORY)

    def close(self):
        os.close(self.fd)

    def check_time(self):
        if time.monotonic() >= self.deadline:
            raise SosImportError("sos import time budget exhausted")

    def read(self, path, limit):
        self.check_time()
        if not isinstance(path, str) or not path or len(path) > 4096 or _path_controls(path):
            raise UnsafeEvidenceError("invalid sos evidence path")
        parts = path.split("/")
        if len(parts) > PATH_DEPTH or any(part in {"", ".", ".."} for part in parts):
            raise UnsafeEvidenceError("sos evidence paths must be relative and remain inside the report")
        current = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                self.check_time()
                next_fd = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=current)
                os.close(current)
                current = next_fd
            self.check_time()
            pinned = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=current)
            try:
                info = os.fstat(pinned)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise UnsafeEvidenceError("sos evidence must be a regular file without hard links: " + path)
                fd = os.open("/proc/self/fd/" + str(pinned), os.O_RDONLY | os.O_NONBLOCK)
                try:
                    opened = os.fstat(fd)
                    if (opened.st_dev, opened.st_ino, opened.st_nlink) != (info.st_dev, info.st_ino, 1):
                        raise UnsafeEvidenceError("sos evidence changed while opening: " + path)
                    chunks, retained = [], 0
                    while retained <= limit:
                        self.check_time()
                        chunk = os.read(fd, min(65536, limit + 1 - retained))
                        if not chunk:
                            break
                        self.total += len(chunk)
                        if self.total > TOTAL_LIMIT:
                            raise SosImportError("sos import aggregate byte limit exceeded")
                        chunks.append(chunk)
                        retained += len(chunk)
                    raw = b"".join(chunks)
                    return raw[:limit], len(raw) > limit
                finally:
                    os.close(fd)
            finally:
                os.close(pinned)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise UnsafeEvidenceError("sos evidence contains a symlink or invalid directory: " + path) from exc
            raise
        finally:
            os.close(current)


def _path_controls(path):
    return any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in path)


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise SosImportError("duplicate field in sos JSON: " + name)
        result[name] = value
    return result


def _bounded_json(text):
    result = json.loads(text, object_pairs_hook=_unique_object)
    pending, count = [(result, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > JSON_DEPTH:
            raise SosImportError("sos JSON nesting limit exceeded")
        if count > JSON_NODE_LIMIT:
            raise SosImportError("sos JSON node limit exceeded")
        if isinstance(value, dict):
            if count + len(pending) + 2 * len(value) > JSON_NODE_LIMIT:
                raise SosImportError("sos JSON node limit exceeded")
            pending.extend((item, depth + 1) for pair in value.items() for item in pair)
        elif isinstance(value, list):
            if count + len(pending) + len(value) > JSON_NODE_LIMIT:
                raise SosImportError("sos JSON node limit exceeded")
            pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise SosImportError("nonfinite value in sos JSON")
        elif isinstance(value, str) and any(unicodedata.category(char) == "Cs" for char in value):
            raise SosImportError("invalid Unicode in sos JSON")
    return result


def _manifest(reader):
    raw, truncated = reader.read("sos_reports/manifest.json", MANIFEST_LIMIT)
    if truncated:
        raise SosImportError("sos manifest exceeds the 2 MiB limit")
    try:
        result = _bounded_json(raw)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise SosImportError("invalid sos manifest: " + str(exc)) from exc
    if not isinstance(result, dict) or not isinstance(result.get("version"), str):
        raise SosImportError("sos manifest has no version")
    if not re.fullmatch(r"4(?:\.\d+)+(?:[-+].*)?", result["version"]):
        raise SosImportError("unsupported sos manifest version: " + result["version"])
    if not isinstance(result.get("end_time"), str) or not result["end_time"].strip():
        raise SosImportError("sos manifest has no completed capture time")
    components = result.get("components")
    report = components.get("report") if isinstance(components, dict) else None
    plugins = report.get("plugins") if isinstance(report, dict) else None
    if not isinstance(plugins, dict):
        raise SosImportError("sos manifest has no report plugin records")
    count = 0
    for name, plugin in plugins.items():
        reader.check_time()
        if not isinstance(name, str) or not isinstance(plugin, dict):
            raise SosImportError("invalid sos plugin record")
        for field in ("commands", "files"):
            rows = plugin.get(field, [])
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise SosImportError("invalid sos plugin " + field)
            count += len(rows)
            if field == "files":
                for row in rows:
                    copied = row.get("files_copied", [])
                    if not isinstance(copied, list):
                        raise SosImportError("invalid sos copied-file list")
                    count += len(copied)
            if count > ENTRY_LIMIT:
                raise SosImportError("sos manifest entry limit exceeded")
    return result, plugins


def _argv(command):
    executable, arguments = command.get("command"), command.get("parameters")
    if not isinstance(executable, str) or not executable or not isinstance(arguments, list):
        raise SosImportError("invalid sos command/parameters metadata")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in [executable, *arguments]):
        raise SosImportError("invalid sos command argument")
    recorded = [executable, *arguments]
    if "exec" in command:
        if not isinstance(command["exec"], str) or command["exec"].split(" ") != recorded:
            raise SosImportError("inconsistent sos command and exec metadata")
        text = command["exec"]
    else:
        text = " ".join(recorded)
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise SosImportError("invalid sos exec metadata") from exc
    if not argv:
        raise SosImportError("empty sos command metadata")
    return argv


def _selection(argv):
    executable = PurePosixPath(argv[0]).name
    if executable == "nmcli" and len(argv) in {3, 4} and argv[1] in {"dev", "device"} and argv[2] == "show":
        return ("nm-devices" if len(argv) == 3 else "nm-device", "nm", 0)
    if executable != "ip":
        return None
    args, options = list(argv[1:]), []
    allowed = {"-4", "-6", "-j", "-json", "-d", "-details", "-s", "-stats", "-statistics", "-o", "-oneline"}
    while args and args[0].startswith("-"):
        option = args.pop(0)
        if option not in allowed:
            return None
        options.append(option)
    if not args or ("-4" in options and "-6" in options):
        return None
    obj = args.pop(0)
    is_json = any(opt in options for opt in ("-j", "-json"))
    if obj in {"link", "addr", "address"}:
        if args not in ([], ["show"], ["list"]) or "-4" in options or "-6" in options:
            return None
        if obj == "link":
            return "links", "json" if is_json else "links", 0
        oneline = any(opt in options for opt in ("-o", "-oneline"))
        return "addresses", "json" if is_json else "addresses-one" if oneline else "addresses", 0 if is_json or oneline else 1
    family = 6 if "-6" in options else 4
    if obj == "route" and args in (["show", "table", "all"], ["list", "table", "all"]):
        if not any(opt in options for opt in ("-4", "-6")):
            return "routes-all", "json-all" if is_json else "routes-all", 1
        return "routes" + str(family), "json" if is_json else "routes", 0
    if obj == "rule" and args in ([], ["show"], ["list"]):
        return "rules" + str(family), "json" if is_json else "rules", 0
    return None


def _ip(value, family=None, network=False):
    if not isinstance(value, str) or not value:
        raise ValueError("IP values must be nonempty strings")
    try:
        address = ipaddress.ip_network(value, strict=False) if network else ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("unrecognized or obfuscated IP value: " + value) from exc
    if family is not None and address.version != family:
        raise ValueError("address family differs from captured command")
    return value


def _ifname(value):
    name, separator, peer = value.partition("@")
    if not name or any(char.isspace() for char in name):
        raise ValueError("invalid interface name")
    return name


def _link_header(line):
    match = re.fullmatch(r"(\d+):\s+([^:]+):\s+<([^>]*)>\s+(.+)", line)
    if not match:
        raise ValueError("unrecognized link header")
    index, name, flags, rest = match.groups()
    mtu = re.search(r"(?:^|\s)mtu\s+(\d+)(?:\s|$)", rest)
    if not mtu:
        raise ValueError("link header has no MTU")
    result = {"ifindex": int(index), "ifname": _ifname(name),
              "flags": flags.split(",") if flags else [], "mtu": int(mtu[1])}
    for field, pattern in (("operstate", r"(?:^|\s)state\s+(\S+)"), ("master", r"(?:^|\s)master\s+(\S+)")):
        value = re.search(pattern, rest)
        if value:
            result[field] = value[1]
    return result


def _link_detail(line, row):
    if line.startswith("link/"):
        row["link_type"] = line.split()[0].split("/", 1)[1]
        return True
    word = line.split()[0] if line else ""
    if word in LINK_KINDS:
        row.setdefault("linkinfo", {})["info_kind"] = word
        if word == "tun":
            kind = re.search(r"\btype (tun|tap)\b", line)
            if kind:
                row["linkinfo"]["info_data"] = {"type": kind[1]}
        return True
    if word.endswith("_slave") and word.removesuffix("_slave") in LINK_KINDS:
        row.setdefault("linkinfo", {})["info_slave_kind"] = word.removesuffix("_slave")
        return True
    if word in {"ingress-qos-map", "egress-qos-map"} and row.get("linkinfo", {}).get("info_kind") == "vlan":
        return bool(re.match(r"\S+\s+\{(?:\s+\d+:\d+)*\s*\}", line))
    return word in {"altname", "alias", "promiscuity", "addrgenmode", "protodown_reason", "vf", "xdp"}


def _links(text):
    rows, current, counter, vf = [], None, None, False
    for raw in text.splitlines():
        if not raw.strip():
            continue
        line = raw.strip()
        if re.match(r"^\d+: ", raw):
            if counter:
                raise ValueError("incomplete link counter row")
            current = _link_header(raw)
            rows.append(current)
            vf = False
        elif current is None:
            raise ValueError("link detail without interface")
        elif counter:
            values = line.split()
            direction, columns = counter
            if len(values) != len(columns) or not all(value.isdecimal() for value in values):
                raise ValueError("invalid link counter row")
            if not vf:
                stats = current.setdefault("stats64", {})
                if direction in stats:
                    raise ValueError("duplicate interface counter row")
                stats[direction] = dict(zip(columns, map(int, values)))
            counter = None
        elif line.startswith(("RX:", "TX:")):
            direction, _, columns = line.partition(":")
            counter = (direction.lower(), columns.split())
        elif re.match(r"vf\s+\d+\s", line):
            vf = True
        elif not _link_detail(line, current):
            raise ValueError("unsupported link detail")
    if counter:
        raise ValueError("incomplete link counter row")
    return rows


def _address(line, name):
    words = line.replace("\\", " ").split()
    if len(words) < 2 or words[0] not in {"inet", "inet6"}:
        raise ValueError("unrecognized address row")
    family = 6 if words[0] == "inet6" else 4
    local = words[1]
    if "/" not in local:
        if len(words) < 4 or words[2] != "peer" or "/" not in words[3]:
            raise ValueError("interface address has no local or peer prefix")
        peer = ipaddress.ip_interface(words[3])
        if peer.version != family:
            raise ValueError("peer address family differs from interface address")
        local += "/" + str(peer.network.prefixlen)
    try:
        address = ipaddress.ip_interface(local)
    except ValueError as exc:
        raise ValueError("invalid or obfuscated interface address") from exc
    if address.version != family:
        raise ValueError("invalid interface address prefix")
    result = {"family": words[0], "local": str(address.ip), "prefixlen": address.network.prefixlen, "flags": []}
    words = words[2:]
    while words:
        key = words.pop(0)
        if key in ADDRESS_FLAGS:
            result["flags"].append(key)
        elif key in {"scope", "brd", "peer", "valid_lft", "preferred_lft", "metric", "proto"}:
            if not words:
                raise ValueError("incomplete address field")
            value = words.pop(0)
            if key in {"brd", "peer"}:
                _ip(value, family, network="/" in value)
            if key in {"valid_lft", "preferred_lft"}:
                if value == "forever":
                    value = 4294967295
                elif re.fullmatch(r"\d+(?:sec)?", value):
                    value = int(value.removesuffix("sec"))
                else:
                    raise ValueError("invalid address lifetime")
                key = "valid_life_time" if key == "valid_lft" else "preferred_life_time"
            result[key] = value
        elif key == name or key.startswith(name + ":"):
            result["label"] = key
        else:
            raise ValueError("unsupported address field: " + key)
    return result


def _addresses(text, oneline):
    rows, pending, current = {}, None, None

    def add_pending():
        if pending:
            name, line = pending
            rows.setdefault(name, {"ifname": name, "addr_info": []})["addr_info"].append(_address(line, name))

    for raw in text.splitlines():
        if not raw.strip():
            continue
        line = raw.strip()
        if oneline:
            match = re.fullmatch(r"\d+:\s+(\S+)\s+(inet6?\s+.+)", raw)
            if not match:
                raise ValueError("unrecognized one-line address output")
            name = _ifname(match[1])
            rows.setdefault(name, {"ifname": name, "addr_info": []})["addr_info"].append(_address(match[2], name))
        elif re.match(r"^\d+: ", raw):
            add_pending()
            pending = None
            current = _link_header(raw)
            rows.setdefault(current["ifname"], {"ifname": current["ifname"], "addr_info": []})
        elif current and line.startswith(("inet ", "inet6 ")):
            add_pending()
            pending = (current["ifname"], line)
        elif pending and line.startswith("valid_lft "):
            pending = (pending[0], pending[1] + " " + line)
        elif not current or not _link_detail(line, current):
            raise ValueError("unsupported address detail")
    add_pending()
    return list(rows.values())


def _route(words, family, nexthop=False):
    result = {}
    types = {"unicast", "local", "broadcast", "multicast", "anycast", "blackhole", "unreachable", "prohibit", "throw", "nat"}
    if not nexthop:
        if words and words[0] in types:
            result["type"] = words.pop(0)
        if not words:
            raise ValueError("route has no destination")
        dst = words.pop(0)
        if dst != "default":
            _ip(dst, family, network=True)
        result["dst"] = dst
    values = {"dev", "table", "proto", "scope", "src", "from", "metric", "pref", "mtu", "advmss",
              "window", "cwnd", "ssthresh", "rtt", "rttvar", "reordering", "hoplimit", "initcwnd",
              "initrwnd", "expires", "features", "uid", "tos", "realm", "realms", "quickack", "congctl", "rto_min",
              "fastopen_no_cookie", "nhid", "weight"}
    flags = {"onlink", "linkdown", "pervasive", "notify", "dead", "offload", "trap", "cache", "offload_failed"}
    while words:
        key = words.pop(0)
        if key == "nexthop":
            if nexthop:
                raise ValueError("nested next hop")
            try:
                end = words.index("nexthop")
            except ValueError:
                end = len(words)
            result.setdefault("multipath", []).append(_route(words[:end], family, True))
            del words[:end]
        elif key in flags:
            result.setdefault("flags", []).append(key)
        elif key == "via":
            if not words:
                raise ValueError("route gateway missing")
            gateway_family = family
            typed = False
            if words[0] in {"inet", "inet6"}:
                gateway_family = 6 if words.pop(0) == "inet6" else 4
                typed = True
            if not words:
                raise ValueError("route gateway missing")
            gateway = _ip(words.pop(0), gateway_family)
            if typed:
                result["via"] = {"family": "inet6" if gateway_family == 6 else "inet", "host": gateway}
            else:
                result["gateway"] = gateway
        elif key in values:
            if not words:
                raise ValueError("route field missing")
            if words[0] == "lock":
                words.pop(0)
            if not words:
                raise ValueError("locked route field missing")
            value = words.pop(0)
            if key == "src":
                _ip(value, family)
            elif key == "from":
                _ip(value, family, network=True)
            elif key == "dev":
                value = _ifname(value)
            elif key in {"metric", "weight", "nhid"}:
                if not value.isdecimal():
                    raise ValueError("non-numeric route " + key)
                value = int(value)
            result[key] = value
        else:
            raise ValueError("unsupported route field: " + key)
    if nexthop and not (result.get("dev") or result.get("gateway") or result.get("via")):
        raise ValueError("empty next hop")
    return result


def _route_family(row):
    families = set()
    for key in ("dst", "from", "src", "prefsrc", "gateway"):
        value = row.get(key)
        if value and value not in {"default", "all"}:
            families.add(ipaddress.ip_network(value, strict=False).version)
    for field in ("multipath", "nexthops"):
        for hop in row.get(field, []):
            if hop.get("gateway"):
                families.add(ipaddress.ip_address(hop["gateway"]).version)
    if len(families) != 1:
        raise ValueError("route address family is ambiguous in an all-family capture")
    return families.pop()


def _validate_route(row, family):
    dst = row.get("dst")
    if not isinstance(dst, str):
        raise ValueError("route has no destination")
    if dst != "default":
        _ip(dst, family, network=True)
    hops = [row]
    for field in ("multipath", "nexthops"):
        if field in row:
            children = row[field]
            if not isinstance(children, list) or not children or not all(isinstance(hop, dict) for hop in children):
                raise ValueError("invalid route next hops")
            if any("multipath" in hop or "nexthops" in hop for hop in children):
                raise ValueError("nested route next hops")
            if any(not (hop.get("dev") or hop.get("gateway") or hop.get("via")) for hop in children):
                raise ValueError("empty route next hop")
            hops.extend(children)
    for hop in hops:
        for key in ("dev", "gateway", "type", "src", "prefsrc", "from"):
            if key in hop and not isinstance(hop[key], str):
                raise ValueError("invalid route " + key)
        for key in ("gateway", "src", "prefsrc"):
            if key in hop:
                _ip(hop[key], family)
        if "from" in hop:
            _ip(hop["from"], family, network=True)
        if "via" in hop:
            via = hop["via"]
            if not isinstance(via, dict) or via.get("family") not in {"inet", "inet6"}:
                raise ValueError("invalid typed route gateway")
            _ip(via.get("host"), 6 if via["family"] == "inet6" else 4)
        if "table" in hop and type(hop["table"]) not in {str, int}:
            raise ValueError("invalid route table")
        if not isinstance(hop.get("flags", []), list) or not all(isinstance(flag, str) for flag in hop.get("flags", [])):
            raise ValueError("invalid route flags")


def _routes(text, family):
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw[0].isspace():
            if not lines or raw.split()[0] not in {"nexthop", "cache", "expires"}:
                raise ValueError("unsupported route continuation")
            lines[-1] += " " + raw.strip()
        else:
            lines.append(raw)
    return [_route(line.split(), family) for line in lines]


def _rules(text, family):
    rows = []
    pairs = {"from", "to", "iif", "oif", "fwmark", "uidrange", "ipproto", "sport", "dport",
             "tun_id", "proto", "protocol", "pref", "priority", "lookup", "table", "suppress_prefixlength",
             "suppress_ifgroup", "goto", "nat", "tos", "realms", "dscp", "flowlabel"}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"(\d+):\s+(.+)", line)
        if not match:
            raise ValueError("unrecognized rule row")
        row, words = {"priority": int(match[1])}, match[2].split()
        while words:
            key = words.pop(0)
            if key in {"not", "l3mdev", "detached", "[detached]", "[unresolved]"}:
                row[key.strip("[]")] = True
            elif key in {"blackhole", "unreachable", "prohibit", "nop"}:
                row["action"] = key
            elif key in pairs:
                if not words:
                    raise ValueError("incomplete rule field")
                value = words.pop(0)
                if key in {"from", "to"} and value != "all":
                    _ip(value, family, network=True)
                if key in {"lookup", "table"}:
                    row["table"], row["action"] = value, "lookup"
                    if value == "[l3mdev-table]":
                        row["l3mdev"] = True
                else:
                    row[{"from": "src", "proto": "protocol"}.get(key, key)] = value
                    if key in {"goto", "nat"}:
                        row["action"] = key
            else:
                raise ValueError("unsupported rule field: " + key)
        if "action" not in row:
            raise ValueError("rule has no action")
        rows.append(row)
    return rows


def _nm(text):
    lines, device, devices = [], None, set()
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError("unsupported nmcli device output")
        key, value = key.strip(), value.strip()
        if key == "GENERAL.DEVICE":
            if not value or value in devices:
                raise ValueError("empty or duplicate NetworkManager device identity")
            device = value
            devices.add(device)
        allowed = key.startswith("GENERAL.") and key[8:] in NM_GENERAL
        allowed = allowed or bool(re.fullmatch(r"IP[46]\.(?:ADDRESS|GATEWAY|ROUTE|DNS|DOMAIN|SEARCH|WINS)(?:\[\d+\])?", key))
        allowed = allowed or bool(re.fullmatch(r"DHCP[46]\.OPTION\[\d+\]", key))
        if allowed:
            if device is None:
                raise ValueError("NetworkManager fields without a device identity")
            if key == "GENERAL.STATE" and not re.match(r"^\d+(?:\s|$)", value):
                raise ValueError("invalid NetworkManager device state")
            lines.append(key + ":" + value)
    return "\n".join(lines) + ("\n" if lines else "")


def _normalize(ident, text, syntax):
    if syntax == "nm":
        return _nm(text)
    mixed_routes = syntax in {"json-all", "routes-all"}
    if syntax in {"json", "json-all"}:
        result = _bounded_json(text)
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            raise ValueError("expected an array of network records")
        # The same adapters validate identities before structured evidence is used.
        for row in result:
            if ident in {"links", "addresses"}:
                if not isinstance(row.get("ifname"), str):
                    raise ValueError("network record has no interface identity")
                _ifname(row["ifname"])
                if not isinstance(row.get("flags", []), list) or not all(isinstance(flag, str) for flag in row.get("flags", [])):
                    raise ValueError("invalid interface flags")
            if ident == "links":
                if "flags" not in row or type(row.get("mtu")) is not int:
                    raise ValueError("incomplete link record")
                info = row.get("linkinfo", {})
                if not isinstance(info, dict) or not isinstance(info.get("info_data", {}), dict):
                    raise ValueError("invalid link information")
                if "info_kind" in info and not isinstance(info["info_kind"], str):
                    raise ValueError("invalid link kind")
                stats = row.get("stats64", row.get("stats", {}))
                if not isinstance(stats, dict) or any(not isinstance(stats.get(key, {}), dict) for key in ("rx", "tx")):
                    raise ValueError("invalid link counters")
            elif ident == "addresses":
                if not isinstance(row.get("addr_info"), list):
                    raise ValueError("incomplete address record")
                for address in row["addr_info"]:
                    if not isinstance(address, dict) or address.get("family") not in {"inet", "inet6"}:
                        raise ValueError("invalid address family")
                    family = 6 if address["family"] == "inet6" else 4
                    _ip(address.get("local", ""), family)
                    prefix = address.get("prefixlen")
                    if type(prefix) is not int or not 0 <= prefix <= (128 if family == 6 else 32):
                        raise ValueError("invalid address prefix")
                    if not isinstance(address.get("flags", []), list):
                        raise ValueError("invalid address flags")
            elif ident.startswith("routes"):
                _validate_route(row, None if mixed_routes else int(ident[-1]))
            elif ident.startswith("rules"):
                if type(row.get("priority")) is not int:
                    raise ValueError("rule has no numeric priority")
        if mixed_routes:
            families = [_route_family(row) for row in result]
            return [row for row, family in zip(result, families) if family == int(ident[-1])]
        return result
    if syntax == "links":
        return _links(text)
    if syntax in {"addresses", "addresses-one"}:
        return _addresses(text, syntax == "addresses-one")
    if syntax in {"routes", "routes-all"}:
        result = _routes(text, None if mixed_routes else int(ident[-1]))
        for row in result:
            _validate_route(row, None if mixed_routes else int(ident[-1]))
        if mixed_routes:
            families = [_route_family(row) for row in result]
            return [row for row, family in zip(result, families) if family == int(ident[-1])]
        return result
    return _rules(text, int(ident[-1]))


def _record(ident, title, source):
    return {"id": ident, "title": title, "source": source, "status": "missing",
            "returncode": None, "seconds": 0.0, "stdout": "", "stderr": ""}


def _capture(reader, ident, syntax, plugin_name, plugin, command, argv, max_bytes):
    source = {"kind": "sos", "plugin": plugin_name, "archive_path": command.get("filepath"),
              "argv": argv, "capture_started": command.get("start_time", plugin.get("start_time")),
              "capture_finished": command.get("end_time", plugin.get("end_time")),
              "capture_timezone": "unrecorded (source wall-clock timestamps)"}
    if syntax in {"routes-all", "json-all"}:
        source["family_filter"] = "IPv" + ident[-1] + " from an all-family capture"
    record = _record(ident, "Archived " + " ".join(argv), source)
    code, duration = command.get("return_code"), command.get("run_time")
    record["returncode"] = code if type(code) is int else None
    if type(duration) in {int, float} and math.isfinite(duration) and duration >= 0:
        record["seconds"] = duration
    failure = {124: ("timeout", "Archived command exceeded its timeout."),
               126: ("denied", "Archived command could not be executed."),
               127: ("missing", "Command was unavailable during sos collection.")}.get(code) if type(code) is int else None
    if not isinstance(source["archive_path"], str):
        status, reason = failure or ("invalid", "Command has no archive path.")
        record.update(status=status, stderr=reason)
        return record
    try:
        raw, limited = reader.read(source["archive_path"], max_bytes)
    except FileNotFoundError:
        status, reason = failure or ("missing", "Manifest-listed evidence file is missing.")
        record.update(status=status, stderr=reason)
        return record
    except PermissionError:
        record.update(status="denied", stderr="Cannot read manifest-listed evidence file.")
        return record
    record["stdout"] = raw.decode("utf-8", "replace")
    if failure:
        record.update(status=failure[0], stderr=failure[1])
    elif plugin.get("timeout_hit") is True:
        record.update(status="timeout", stderr="The sos plugin did not finish within its deadline.")
    elif limited or command.get("truncated") is True:
        record.update(status="truncated", stderr="Captured output or import read limit is incomplete.")
    elif type(code) is not int or type(command.get("truncated")) is not bool or plugin.get("timeout_hit") is not False:
        record.update(status="invalid", stderr="Manifest cannot establish command success and completeness: integer return_code, boolean truncated (sos 4.2+), and plugin timeout_hit=false are required.")
    elif code:
        denied = re.search(r"permission denied|operation not permitted|access denied|not authorized", record["stdout"], re.I)
        record.update(status="denied" if denied else "error", stderr="Archived command returned a nonzero exit status.")
    else:
        try:
            text = raw.decode("utf-8")
            normalized = _normalize(ident, text, syntax)
            if syntax == "nm" and len(argv) == 4:
                devices = [line.partition(":")[2] for line in normalized.splitlines() if line.startswith("GENERAL.DEVICE:")]
                if devices != [argv[3]]:
                    raise ValueError("NetworkManager device output does not match the captured command's scope")
            record["status"] = "ok"
            if syntax == "nm":
                record["stdout"] = normalized
            else:
                record["data"] = normalized
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            record.update(status="invalid", parse_error=str(exc), stderr="Captured output is not a complete supported snapshot.")
    if syntax == "nm":
        source["filtered"] = "Known device runtime fields only; configuration and credential fields excluded."
        if record["status"] != "ok":
            record["stdout"] = ""
    return record


def import_report(directory, max_bytes=131072, budget=120.0):
    if type(max_bytes) is not int or not 1 <= max_bytes <= 16 * 1024 * 1024:
        raise SosImportError("invalid per-evidence import byte limit")
    if type(budget) not in {int, float} or not math.isfinite(budget) or budget <= 0:
        raise SosImportError("invalid sos import time budget")
    reader = DirectoryReader(directory, budget)
    try:
        manifest, plugins = _manifest(reader)
        candidates, device_candidates, files = defaultdict(list), [], defaultdict(list)
        for name, plugin in plugins.items():
            reader.check_time()
            if name not in {"networking", "networkmanager"}:
                continue
            for command in plugin.get("commands", []):
                reader.check_time()
                argv = _argv(command)
                selection = _selection(argv)
                if selection is None:
                    continue
                if any(command.get(key) for key in ("namespace", "container", "container_id")):
                    continue
                ident, syntax, priority = selection
                value = (priority, syntax, name, plugin, command, argv)
                if ident == "nm-device":
                    device_candidates.append(value)
                elif ident == "routes-all":
                    candidates["routes4"].append(value)
                    candidates["routes6"].append(value)
                else:
                    candidates[ident].append(value)
            for entry in plugin.get("files", []):
                specification, copied = entry.get("specification"), entry.get("files_copied", [])
                if not isinstance(copied, list):
                    raise SosImportError("invalid sos copied-file list")
                for path in copied:
                    if isinstance(path, str) and path in FILE_IDS and specification == "/" + path:
                        files[FILE_IDS[path]].append((name, plugin, path))
        records = []
        for ident in (*CORE, "nm-devices"):
            options = candidates.get(ident, [])
            if ident in {"addresses", "routes4", "routes6"} and options:
                best = min(value[0] for value in options)
                options = [value for value in options if value[0] == best]
            if not options:
                if ident in CORE or (ident == "nm-devices" and "networkmanager" in plugins and not device_candidates):
                    item = _record(ident, ident, {"kind": "sos"})
                    item["stderr"] = "No supported unscoped command appears in the sos manifest." if ident in CORE else "The NetworkManager plugin has no supported device capture in the manifest."
                    records.append(item)
                continue
            if len(options) != 1:
                item = _record(ident, ident, {"kind": "sos", "archive_paths": [value[4].get("filepath") for value in options]})
                item.update(status="invalid", stderr="Several captures match this snapshot; their ordering and equivalence are ambiguous.")
                records.append(item)
                continue
            _, syntax, name, plugin, command, argv = options[0]
            records.append(_capture(reader, ident, syntax, name, plugin, command, argv, max_bytes))
        if not candidates.get("nm-devices"):
            devices = defaultdict(list)
            for option in device_candidates:
                devices[option[-1][-1]].append(option)
            for index, (device, options) in enumerate(devices.items()):
                ident = "nm-device-" + str(index)
                if len(options) != 1:
                    item = _record(ident, "Archived NetworkManager device " + device,
                                   {"kind": "sos", "archive_paths": [value[4].get("filepath") for value in options]})
                    item.update(status="invalid", stderr="Several captures describe this device; their ordering and equivalence are ambiguous.")
                    records.append(item)
                    continue
                _, syntax, name, plugin, command, argv = options[0]
                records.append(_capture(reader, ident, syntax, name, plugin, command, argv, max_bytes))
        for ident, options in files.items():
            if len(options) != 1:
                item = _record(ident, ident, {"kind": "sos"})
                item.update(status="invalid", stderr="Several manifest file entries match this source.")
                records.append(item)
                continue
            name, plugin, path = options[0]
            item = _record(ident, path, {"kind": "sos", "plugin": name, "archive_path": path})
            try:
                raw, limited = reader.read(path, max_bytes)
                item["stdout"] = raw.decode("utf-8", "replace")
                if limited:
                    item.update(status="truncated", stderr="Imported file exceeds its read limit.")
                else:
                    item.update(status="skipped", stderr="Supplementary file only: the manifest does not establish whether source file collection was truncated.")
            except FileNotFoundError:
                item["stderr"] = "Manifest-listed evidence file is missing."
            except PermissionError:
                item.update(status="denied", stderr="Cannot read manifest-listed evidence file.")
            except UnsafeEvidenceError as exc:
                item.update(status="skipped", stderr="Unsafe supplementary file was not read: " + str(exc))
            records.append(item)
        reader.check_time()
        return {
            "metadata": {
                "source_kind": "sos", "source_directory": reader.path,
                "started": manifest.get("start_time") if isinstance(manifest.get("start_time"), str) else "unknown",
                "capture_finished": manifest["end_time"], "sos_version": manifest["version"],
                "capture_timezone": "unrecorded (source wall-clock timestamps)",
                "sos_policy": manifest.get("policy") if isinstance(manifest.get("policy"), str) else "unknown",
                "hostname": "unknown", "kernel": "unknown", "architecture": "unknown", "euid": None,
                "network_namespace": None, "pid1_network_namespace": None,
                "service_namespace_comparison": "unverified", "service_queries_enabled": False,
                "resolver_path": "unknown", "interface": None, "active": False, "family": "both",
                "scope": "sos collection namespace (identity unrecorded)", "cleaning": "unknown",
            },
            "evidence": records,
            "notes": [
                "Offline analysis did not execute archived commands or query this computer's network.",
                "Only supported manifest-listed host command forms are normalized; missing, ambiguous or incomplete snapshots remain unavailable.",
                "Capture namespace identity and identity-cleaning state are unrecorded; addresses may be pseudonyms and are never probe targets.",
                "Capture timestamps are source wall-clock values with an unrecorded timezone; they are not analysis-host UTC timestamps.",
                "Imported output follows sos's collection contract; it does not establish the native collector's passive behavior or privacy limits.",
            ],
        }
    finally:
        reader.close()
