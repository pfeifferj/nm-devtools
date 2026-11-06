"""Synthetic sos-4 manifest fixtures using upstream command-output formats."""

import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/net_diagnose_sos.py"
FIXTURES = ROOT / "tests/fixtures/sos"
SPEC = importlib.util.spec_from_file_location("net_diagnose_sos_test", MODULE_PATH)
SOS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOS)

LINKS = """1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00 promiscuity 0 allmulti 0 minmtu 0 maxmtu 0
2: eth0@if3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP mode DEFAULT group default qlen 1000
    link/ether 02:00:00:00:00:01 brd ff:ff:ff:ff:ff:ff link-netnsid 0 promiscuity 0 allmulti 0 minmtu 68 maxmtu 65535
    veth addrgenmode eui64 numtxqueues 1 numrxqueues 1 gso_max_size 65536 gso_max_segs 65535
    RX:  bytes packets errors dropped missed mcast
         1000 10       0      2       0      0
    TX:  bytes packets errors dropped carrier collsns
         2000 20       0      0       0       0
"""
ADDRESSES = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
1: lo    inet6 ::1/128 scope host \\       valid_lft forever preferred_lft forever
2: eth0    inet 192.0.2.2/24 brd 192.0.2.255 scope global dynamic eth0\\       valid_lft 300sec preferred_lft 200sec
2: eth0    inet6 2001:db8::2/64 scope global tentative \\       valid_lft forever preferred_lft forever
"""
RULES = """0: from all lookup local
32766: from all lookup main
32767: from all lookup default
"""


class SosImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="net-diagnose-sos-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "report"
        self.root.mkdir()
        (self.root / "sos_reports").mkdir()
        self.manifest_path = self.root / "sos_reports/manifest.json"
        self.manifest = {
            "version": "4.12.1", "policy": "Fedora", "start_time": "2026-11-01 12:00:00",
            "end_time": "2026-11-01 12:00:05", "components": {"report": {"plugins": {}}},
        }
        self.counter = 0

    def plugin(self, name):
        return self.manifest["components"]["report"]["plugins"].setdefault(name, {
            "start_time": "2026-11-01 12:00:01", "end_time": "2026-11-01 12:00:04",
            "timeout_hit": False, "commands": [], "files": [],
        })

    def command(self, command, text="", plugin="networking", **overrides):
        # sos stores split(' ') tokens, including quote characters and empty tokens.
        raw = command.split(" ")
        path = "sos_commands/" + plugin + "/capture-" + str(self.counter)
        self.counter += 1
        output = self.root / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
        entry = {"command": raw[0], "parameters": raw[1:], "exec": command,
                 "filepath": path, "return_code": 0, "run_time": 0.1, "truncated": False}
        entry.update(overrides)
        self.plugin(plugin)["commands"].append(entry)
        return entry

    def core(self):
        return [
            self.command("ip -s -d link", LINKS),
            self.command("ip -o addr", ADDRESSES),
            self.command("ip route show table all", "default via 192.0.2.1 dev eth0 proto dhcp metric 100\n192.0.2.0/24 dev eth0 proto kernel scope link src 192.0.2.2\nlocal 192.0.2.2 dev eth0 table local proto kernel scope host src 192.0.2.2\n"),
            self.command("ip -6 route show table all", "default via fe80::1 dev eth0 proto ra metric 100 pref medium\n2001:db8::/64 dev eth0 proto kernel metric 256 pref medium\n"),
            self.command("ip -4 rule list", RULES),
            self.command("ip -6 rule list", RULES),
        ]

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def load(self, **kwargs):
        self.write_manifest()
        return SOS.import_report(self.root, **kwargs)

    def records(self, **kwargs):
        return {record["id"]: record for record in self.load(**kwargs)["evidence"]}

    def test_standard_core_snapshots_preserve_capture_provenance(self):
        self.core()
        report = self.load()
        records = {record["id"]: record for record in report["evidence"]}
        self.assertTrue(all(records[ident]["status"] == "ok" for ident in SOS.CORE))
        self.assertEqual(records["links"]["data"][1]["ifname"], "eth0")
        self.assertEqual(records["links"]["data"][1]["stats64"]["rx"]["dropped"], 2)
        self.assertEqual(records["addresses"]["data"][1]["addr_info"][0]["preferred_life_time"], 200)
        self.assertIn("tentative", records["addresses"]["data"][1]["addr_info"][1]["flags"])
        self.assertEqual(records["routes4"]["data"][0]["gateway"], "192.0.2.1")
        self.assertEqual(records["rules6"]["data"][1]["priority"], 32766)
        self.assertEqual(records["links"]["source"]["capture_started"], "2026-11-01 12:00:01")
        self.assertEqual(records["links"]["stdout"], LINKS)
        self.assertEqual(report["metadata"]["started"], "2026-11-01 12:00:00")
        self.assertEqual(report["metadata"]["hostname"], "unknown")
        self.assertIsNone(report["metadata"]["network_namespace"])

    def test_import_does_not_collect_from_analysis_host(self):
        self.core()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("executed a process")), \
                mock.patch("socket.gethostname", side_effect=AssertionError("read analyst identity")), \
                mock.patch("platform.release", side_effect=AssertionError("read analyst kernel")), \
                mock.patch("os.readlink", side_effect=AssertionError("read analyst namespace")):
            report = self.load()
        self.assertFalse(report["metadata"]["service_queries_enabled"])
        self.assertFalse(report["metadata"]["active"])

    def test_partial_failures_are_not_empty_successful_snapshots(self):
        entries = self.core()
        entries[0].pop("truncated")
        entries[1]["return_code"] = True
        entries[2]["truncated"] = True
        entries[3]["return_code"] = 1
        (self.root / entries[4]["filepath"]).unlink()
        (self.root / entries[5]["filepath"]).write_text("unrecognized rule output\n")
        records = self.records()
        expected = ["invalid", "invalid", "truncated", "error", "missing", "invalid"]
        self.assertEqual([records[ident]["status"] for ident in SOS.CORE], expected)
        self.assertTrue(all("data" not in records[ident] for ident in SOS.CORE))

    def test_timeout_and_unavailable_command_codes_survive_missing_paths(self):
        for code, status in ((124, "timeout"), (126, "denied"), (127, "missing")):
            with self.subTest(code=code):
                self.plugin("networking")["commands"].clear()
                self.command("ip -s -d link", return_code=code, filepath=None)
                self.assertEqual(self.records()["links"]["status"], status)
        self.plugin("networking")["commands"].clear()
        self.command("ip -s -d link", LINKS)
        self.plugin("networking")["timeout_hit"] = True
        self.assertEqual(self.records()["links"]["status"], "timeout")

    def test_unsupported_or_obfuscated_rows_invalidate_entire_snapshot(self):
        for command, output, ident in (
            ("ip -s -d link", LINKS + "    unfamiliar link extension\n", "links"),
            ("ip -o addr", ADDRESSES + "2: eth0 inet <IPv4-1>/24 scope global\n", "addresses"),
            ("ip route show table all", "default via 192.0.2.1 dev eth0\nnot-a-route\n", "routes4"),
            ("ip -6 route show table all", "default via 192.0.2.1 dev eth0\n", "routes6"),
            ("ip -4 rule list", RULES + "100: from all unfamiliar-selector x lookup 100\n", "rules4"),
        ):
            with self.subTest(command=command):
                self.plugin("networking")["commands"].clear()
                self.command(command, output)
                record = self.records()[ident]
                self.assertEqual(record["status"], "invalid")
                self.assertNotIn("data", record)

    def test_policy_routes_and_multipath_are_retained(self):
        self.command("ip route show table all", "blackhole 198.51.100.0/24 table 100\ndefault proto static metric 100\n    nexthop via 192.0.2.1 dev eth0 weight 1\n    nexthop via 192.0.2.5 dev eth1 weight 2\n")
        self.command("ip -4 rule list", "100: not from 192.0.2.0/24 fwmark 0x100/0xff00 lookup 100 suppress_prefixlength 0\n200: from all unreachable\n")
        records = self.records()
        self.assertEqual(records["routes4"]["status"], "ok")
        self.assertEqual(records["routes4"]["data"][0]["type"], "blackhole")
        self.assertEqual(len(records["routes4"]["data"][1]["multipath"]), 2)
        self.assertEqual(records["rules4"]["data"][0]["fwmark"], "0x100/0xff00")
        self.assertEqual(records["rules4"]["data"][1]["action"], "unreachable")

    def test_filtered_or_namespace_commands_cannot_establish_host_core(self):
        for command in ("ip -n vpn -s -d link", "ip netns exec vpn ip -o addr",
                        "ip route show table main", "ip -4 address show", "ip -6 rule list dev eth0"):
            self.command(command, "[]\n", tags=["ip_addr", "ip_route"])
        self.command("ip -s -d link", LINKS, container="container-1")
        records = self.records()
        self.assertTrue(all(records[ident]["status"] == "missing" for ident in SOS.CORE))

    def test_one_line_addresses_are_preferred_and_duplicate_core_is_ambiguous(self):
        self.command("ip -d address", "unrecognized fallback")
        self.command("ip -o addr", ADDRESSES)
        self.command("ip -s -d link", LINKS)
        self.command("ip -s -d link", LINKS)
        records = self.records()
        self.assertEqual(records["addresses"]["status"], "ok")
        self.assertEqual(records["links"]["status"], "invalid")
        self.assertEqual(len(records["links"]["source"]["archive_paths"]), 2)

    def test_multiline_addresses_work_when_no_one_line_capture_exists(self):
        self.command("ip -d address", "2: eth0@if3: <BROADCAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default\n    link/ether 02:00:00:00:00:01 brd ff:ff:ff:ff:ff:ff\n    inet 192.0.2.2/24 brd 192.0.2.255 scope global dynamic eth0\n       valid_lft 300sec preferred_lft 200sec\n")
        record = self.records()["addresses"]
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["data"][0]["addr_info"][0]["local"], "192.0.2.2")

    def test_quoted_nm_devices_keep_scope_and_exclude_unknown_secret_fields(self):
        self.command('nmcli  dev show "eth0"', "GENERAL.DEVICE:                    eth0\nGENERAL.STATE:                     120 (failed)\nGENERAL.REASON:                    5 (IP configuration unavailable)\nIP4.GATEWAY:                       192.0.2.1\n802-1x.password: super-secret\nGENERAL.UNKNOWN-SECRET: other-secret\n", plugin="networkmanager")
        self.plugin("unrelated")["commands"].append({"exec": "command 'quoted value'", "command": "command"})
        record = self.records()["nm-device-0"]
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["source"]["argv"], ["nmcli", "dev", "show", "eth0"])
        self.assertIn("GENERAL.STATE:120 (failed)", record["stdout"])
        self.assertNotIn("secret", record["stdout"])
        self.assertIn("filtered", record["source"])

    def test_nm_scope_mismatch_and_duplicates_remain_unavailable(self):
        self.command('nmcli dev show "eth0"', "GENERAL.DEVICE:eth1\nGENERAL.STATE:120 (failed)\n", plugin="networkmanager")
        record = self.records()["nm-device-0"]
        self.assertEqual(record["status"], "invalid")
        self.assertEqual(record["stdout"], "")
        self.command('nmcli dev show "eth0"', "GENERAL.DEVICE:eth0\nGENERAL.STATE:100 (connected)\n", plugin="networkmanager")
        record = self.records()["nm-device-0"]
        self.assertEqual(record["status"], "invalid")
        self.assertEqual(len(record["source"]["archive_paths"]), 2)

    def test_global_nm_snapshot_takes_precedence_over_device_captures(self):
        self.command('nmcli dev show "eth0"', "GENERAL.DEVICE:eth0\nGENERAL.STATE:120 (failed)\n", plugin="networkmanager")
        self.command("nmcli dev show", "GENERAL.DEVICE:eth0\nGENERAL.STATE:100 (connected)\n", plugin="networkmanager")
        records = self.records()
        self.assertIn("nm-devices", records)
        self.assertNotIn("nm-device-0", records)

    def test_json_variants_reject_malformed_and_obfuscated_fields(self):
        self.command("ip -j -d -s link show", '[{"ifname":"eth0","flags":["UP"],"mtu":1500}]')
        self.command("ip -j address show", '[{"ifname":"eth0","addr_info":[{"family":"inet","local":"<IPv4-1>","prefixlen":24}]}]')
        self.command("ip -j -4 route show table all", '[{"dst":"default","gateway":[]}]')
        records = self.records()
        self.assertEqual(records["links"]["status"], "ok")
        self.assertEqual(records["addresses"]["status"], "invalid")
        self.assertEqual(records["routes4"]["status"], "invalid")
        address = self.plugin("networking")["commands"][1]
        (self.root / address["filepath"]).write_text('[{"ifname":"eth0","addr_info":[{"family":"inet","local":3221225986,"prefixlen":24}]}]')
        self.assertEqual(self.records()["addresses"]["status"], "invalid")

    def test_supplementary_files_do_not_establish_absent_resolver_configuration(self):
        path = self.root / "etc/resolv.conf"
        path.parent.mkdir()
        path.write_text("# no nameserver in this possibly truncated copy\n")
        self.plugin("networking")["files"].append({"specification": "/etc/resolv.conf", "files_copied": ["etc/resolv.conf"]})
        record = self.records()["resolv-conf"]
        self.assertEqual(record["status"], "skipped")
        self.assertIn("possibly truncated", record["stdout"])

    def test_per_file_and_aggregate_byte_limits(self):
        self.command("ip -s -d link", LINKS)
        self.assertEqual(self.records(max_bytes=100)["links"]["status"], "truncated")
        self.write_manifest()
        cap = self.manifest_path.stat().st_size + 10
        with mock.patch.object(SOS, "TOTAL_LIMIT", cap), self.assertRaisesRegex(ValueError, "aggregate"):
            SOS.import_report(self.root)

    def test_manifest_entry_and_time_limits(self):
        self.core()
        with mock.patch.object(SOS, "ENTRY_LIMIT", 2), self.assertRaisesRegex(ValueError, "entry limit"):
            self.load()
        with mock.patch.object(SOS.time, "monotonic", side_effect=[0, 2]), self.assertRaisesRegex(ValueError, "time budget"):
            SOS.import_report(self.root, budget=1)
        self.manifest["components"]["report"]["plugins"] = {}
        self.plugin("networking")["files"].append({"files_copied": ["x"] * 3})
        with mock.patch.object(SOS, "ENTRY_LIMIT", 2), self.assertRaisesRegex(ValueError, "entry limit"):
            self.load()

    def test_manifest_completion_version_and_duplicate_fields(self):
        for mutate, pattern in ((lambda: self.manifest.pop("end_time"), "completed"),
                                (lambda: self.manifest.update(version="5.0"), "unsupported")):
            with self.subTest(pattern=pattern):
                original = dict(self.manifest)
                mutate()
                with self.assertRaisesRegex(ValueError, pattern):
                    self.load()
                self.manifest = original
        self.manifest_path.write_text('{"version":"4.12.1","version":"4.12.1"}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            SOS.import_report(self.root)
        self.manifest_path.write_text(" " * (SOS.MANIFEST_LIMIT + 1))
        with self.assertRaisesRegex(ValueError, "2 MiB"):
            SOS.import_report(self.root)

    def test_inconsistent_recorded_exec_is_rejected(self):
        self.command("ip -s -d link", LINKS, exec="ip netns exec vpn ip -s -d link")
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            self.load()

    def test_paths_cannot_escape_or_hide_control_characters(self):
        entry = self.command("ip -s -d link", LINKS)
        for path in ("../outside", "/etc/passwd", "sos_commands//capture", "./capture",
                     "sos_commands/line\nbreak", "sos_commands/zero\u200bwidth", "/".join(["x"] * 33)):
            with self.subTest(path=path):
                entry["filepath"] = path
                with self.assertRaises(ValueError):
                    self.load()

    def test_symlinks_hardlinks_and_special_files_are_rejected(self):
        entry = self.command("ip -s -d link", LINKS)
        path = self.root / entry["filepath"]
        outside = Path(self.temp.name) / "outside"
        outside.write_text(LINKS)
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, path)
                else:
                    os.mkfifo(path)
                with self.assertRaises(ValueError):
                    self.load()
        path.unlink()
        path.write_text(LINKS)
        alias = self.root / "alias"
        alias.symlink_to(path.parent, target_is_directory=True)
        entry["filepath"] = "alias/" + path.name
        with self.assertRaises(ValueError):
            self.load()

    def test_root_symlink_and_missing_evidence_are_distinct(self):
        entry = self.command("ip -s -d link", LINKS)
        (self.root / entry["filepath"]).unlink()
        self.assertEqual(self.records()["links"]["status"], "missing")
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        report = SOS.import_report(alias)
        self.assertEqual(report["evidence"][0]["status"], "missing")
        self.assertEqual(report["metadata"]["source_directory"], str(alias))

    def test_mixed_route_families_keep_provenance_and_typed_gateways(self):
        entry = self.command("ip route show table all", "default via 192.0.2.1 dev eth0\n2001:db8::/64 dev eth0 pref medium\n10.60.0.0/16 via inet6 fe80::1 dev eth0\n")
        records = self.records()
        self.assertEqual(records["routes4"]["status"], "ok")
        self.assertEqual(records["routes6"]["status"], "ok")
        self.assertEqual([row["dst"] for row in records["routes4"]["data"]], ["default", "10.60.0.0/16"])
        self.assertEqual([row["dst"] for row in records["routes6"]["data"]], ["2001:db8::/64"])
        self.assertEqual(records["routes4"]["data"][1]["via"], {"family": "inet6", "host": "fe80::1"})
        for ident in ("routes4", "routes6"):
            self.assertEqual(records[ident]["source"]["archive_path"], entry["filepath"])
            self.assertEqual(records[ident]["source"]["argv"], ["ip", "route", "show", "table", "all"])
            self.assertIn("all-family", records[ident]["source"]["family_filter"])

    def test_ambiguous_defaults_and_unsupported_foreign_rows_are_unavailable(self):
        for output in ("default dev eth0\n", "unreachable default table 100\n",
                       "default via inet6 fe80::1 dev eth0\n", "192.0.2.0/24 dev eth0\n2001:db8::/64 unsupported field\n",
                       (FIXTURES / "ip-routes-all.txt").read_text()):
            with self.subTest(output=output[:70]):
                self.plugin("networking")["commands"].clear()
                self.command("ip route show table all", output)
                records = self.records()
                for ident in ("routes4", "routes6"):
                    self.assertEqual(records[ident]["status"], "invalid")
                    self.assertNotIn("data", records[ident])

    def test_explicit_route_family_is_strict_and_preferred(self):
        self.command("ip route show table all", "default dev eth0\n")
        self.command("ip -4 route show table all", "default dev eth0\n")
        self.command("ip -6 route show table all", "default dev eth0 pref medium\n")
        records = self.records()
        self.assertTrue(all(records[ident]["status"] == "ok" for ident in ("routes4", "routes6")))
        self.assertNotIn("family_filter", records["routes4"]["source"])
        explicit = self.plugin("networking")["commands"][1]
        (self.root / explicit["filepath"]).write_text("2001:db8::/64 dev eth0\n")
        self.assertEqual(self.records()["routes4"]["status"], "invalid")

    def test_json_all_family_routes_and_malformed_hops(self):
        rows = [{"dst": "192.0.2.0/24", "multipath": [{"via": {"family": "inet6", "host": "fe80::1"}, "dev": "eth0"}]},
                {"dst": "2001:db8::/64", "nexthops": [{"gateway": "fe80::1", "dev": "eth0"}]}]
        entry = self.command("ip -j route show table all", json.dumps(rows))
        records = self.records()
        self.assertEqual(len(records["routes4"]["data"]), 1)
        self.assertEqual(len(records["routes6"]["data"]), 1)
        for malformed in ({"multipath": ["bad"]}, {"nexthops": [{}]},
                          {"multipath": [{"nexthops": [{"dev": "eth0"}]}]},
                          {"via": {"family": "inet6", "host": "192.0.2.1"}}):
            with self.subTest(malformed=malformed):
                (self.root / entry["filepath"]).write_text(json.dumps([dict(dst="192.0.2.0/24", **malformed)]))
                self.assertEqual(self.records()["routes4"]["status"], "invalid")

    def test_saved_links_preserve_primary_slave_and_vf_counter_scope(self):
        self.command("ip -s -d link", (FIXTURES / "ip-link.txt").read_text())
        record = self.records()["links"]
        self.assertEqual(record["status"], "ok")
        rows = {row["ifname"]: row for row in record["data"]}
        self.assertEqual(len(rows), 29)
        self.assertEqual(rows["veth0"]["linkinfo"], {"info_kind": "veth", "info_slave_kind": "bridge"})
        self.assertEqual(rows["dum2"]["linkinfo"], {"info_kind": "dummy", "info_slave_kind": "bond"})
        self.assertEqual(rows["dum3"]["linkinfo"], {"info_kind": "dummy", "info_slave_kind": "vrf"})
        for name in ("erspan0", "ip6tnl0", "ifb0", "xfrm0"):
            self.assertIn("info_kind", rows[name]["linkinfo"])
        row = SOS._links((FIXTURES / "ip-link-vf.txt").read_text())[0]
        self.assertEqual(row["stats64"]["rx"]["errors"], 42)
        self.assertEqual(row["stats64"]["rx"]["dropped"], 1000)
        self.assertEqual(row["stats64"]["tx"]["packets"], 765432)
        vlan = SOS._links((FIXTURES / "ip-link-vlan-qos.txt").read_text())[0]
        self.assertEqual(vlan["linkinfo"]["info_kind"], "vlan")
        row = {"linkinfo": {"info_kind": "veth"}}
        self.assertTrue(SOS._link_detail("openvswitch_slave", row))
        self.assertEqual(row["linkinfo"], {"info_kind": "veth", "info_slave_kind": "openvswitch"})

    def test_saved_peer_addresses_keep_local_address_and_prefix(self):
        self.command("ip -o addr", (FIXTURES / "ip-address.txt").read_text())
        record = self.records()["addresses"]
        self.assertEqual(record["status"], "ok")
        rows = {row["ifname"]: row for row in record["data"]}
        addresses = rows["tun0"]["addr_info"]
        self.assertEqual([(item["local"], item["prefixlen"]) for item in addresses], [("10.8.0.1", 32), ("2001:db8:8::1", 128)])
        self.assertEqual(addresses[0]["peer"], "10.8.0.2/32")
        for line in ("inet 10.8.0.1 scope global", "inet 10.8.0.1 peer 2001:db8::2/128 scope global"):
            with self.assertRaises(ValueError):
                SOS._address(line, "tun0")

    def test_saved_rule_and_route_fields_are_preserved(self):
        self.command("ip -4 rule list", (FIXTURES / "ip-rules4.txt").read_text() + "120: from all iif old0 [detached] goto 121 [unresolved]\n")
        record = self.records()["rules4"]
        self.assertEqual(record["status"], "ok")
        rules = {row["priority"]: row for row in record["data"]}
        self.assertEqual(rules[101]["protocol"], "static")
        self.assertEqual(rules[107]["tos"], "0x10")
        self.assertEqual(rules[110]["action"], "nop")
        self.assertEqual(rules[113]["realms"], "1/2")
        self.assertTrue(rules[120]["detached"])
        self.assertTrue(rules[120]["unresolved"])
        self.assertTrue(rules[1000]["l3mdev"])
        routes = SOS._routes((FIXTURES / "ip-routes-all.txt").read_text(), None)
        self.assertEqual(len(routes), 82)
        self.assertEqual(next(row for row in routes if row["dst"] == "10.42.0.0/16")["rto_min"], "50ms")
        self.assertEqual(next(row for row in routes if row["dst"] == "2001:db8:400::/48")["from"], "2001:db8:1::/48")
        self.assertEqual(SOS._routes("192.0.2.0/24 dev eth0 realm 5\n", 4)[0]["realm"], "5")

    def test_unsafe_optional_files_preserve_core_evidence(self):
        self.core()
        path = self.root / "etc/resolv.conf"
        path.parent.mkdir()
        outside = Path(self.temp.name) / "outside"
        outside.write_text("nameserver 192.0.2.250\n")
        self.plugin("networking")["files"].append({"specification": "/etc/resolv.conf", "files_copied": ["etc/resolv.conf"]})
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    path.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, path)
                else:
                    os.mkfifo(path)
                records = self.records()
                self.assertTrue(all(records[ident]["status"] == "ok" for ident in SOS.CORE))
                self.assertEqual(records["resolv-conf"]["status"], "skipped")
                self.assertEqual(records["resolv-conf"]["stdout"], "")
                path.unlink()
        path.write_text("x" * 100)
        self.write_manifest()
        cap = self.manifest_path.stat().st_size + sum((self.root / entry["filepath"]).stat().st_size for entry in self.plugin("networking")["commands"]) + 10
        with mock.patch.object(SOS, "TOTAL_LIMIT", cap), self.assertRaisesRegex(ValueError, "aggregate"):
            SOS.import_report(self.root)

    def test_selected_root_is_pinned_through_symlink_and_search_only_parent(self):
        self.command("ip -s -d link", LINKS)
        self.write_manifest()
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        reader = SOS.DirectoryReader(alias, 1)
        self.addCleanup(reader.close)
        alias.unlink()
        alias.symlink_to("/", target_is_directory=True)
        raw, truncated = reader.read("sos_reports/manifest.json", SOS.MANIFEST_LIMIT)
        self.assertFalse(truncated)
        self.assertEqual(json.loads(raw)["version"], "4.12.1")
        parent = Path(self.temp.name)
        parent.chmod(0o111)
        try:
            self.assertEqual(SOS.import_report(self.root)["evidence"][0]["status"], "ok")
        finally:
            parent.chmod(0o700)
        for blank in ("", " "):
            with self.assertRaisesRegex(ValueError, "nonempty"):
                SOS.import_report(blank)

    def test_special_file_is_only_opened_with_o_path(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        reader = SOS.DirectoryReader(self.root, 1)
        self.addCleanup(reader.close)
        with mock.patch.object(SOS.os, "open", wraps=os.open) as opened:
            with self.assertRaises(SOS.UnsafeEvidenceError):
                reader.read("fifo", 1024)
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(opened.call_args.args[1] & os.O_PATH)

    def test_json_structure_is_bounded_before_returning_evidence(self):
        nested = []
        for _ in range(40):
            nested = [nested]
        for extra, reason in ((nested, "nesting"), (float("nan"), "nonfinite"), ("\ud800", "Unicode")):
            with self.subTest(reason=reason):
                self.plugin("networking")["commands"].clear()
                self.command("ip -j -d -s link show", json.dumps([{"ifname": "eth0", "flags": [], "mtu": 1500, "extra": extra}]))
                record = self.records()["links"]
                self.assertEqual(record["status"], "invalid")
                self.assertIn(reason, record["parse_error"])
                self.assertNotIn("data", record)
        with mock.patch.object(SOS, "JSON_NODE_LIMIT", 20), self.assertRaisesRegex(ValueError, "node limit"):
            SOS._normalize("links", json.dumps([{"ifname": "eth0", "flags": [], "mtu": 1500, "extra": [None] * 20}]), "json")

    def test_old_manifest_completeness_and_source_timezone_are_explicit(self):
        self.manifest["version"] = "4.1"
        entry = self.command("ip -s -d link", LINKS)
        entry.pop("truncated")
        report = self.load()
        record = report["evidence"][0]
        self.assertEqual(record["status"], "invalid")
        self.assertIn("sos 4.2+", record["stderr"])
        self.assertIn("unrecorded", record["source"]["capture_timezone"])
        self.assertIn("unrecorded", report["metadata"]["capture_timezone"])
        self.assertIn("source wall-clock", " ".join(report["notes"]))

    def test_present_nm_plugin_without_supported_device_capture_is_a_gap(self):
        self.command("nmcli connection show", "unrelated capture", plugin="networkmanager")
        record = self.records()["nm-devices"]
        self.assertEqual(record["status"], "missing")
        self.assertIn("no supported device capture", record["stderr"])

    def test_python39_syntax(self):
        ast.parse(MODULE_PATH.read_text(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
