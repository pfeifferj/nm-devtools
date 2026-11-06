"""Analyzer and privacy regressions use synthetic evidence without collection."""

import json
from pathlib import Path
import runpy
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin/net-diagnose"
MODULE = runpy.run_path(str(SCRIPT))


def record(ident, data=None, **fields):
    row = {"id": ident, "title": ident, "source": {}, "status": "ok", "returncode": 0,
           "stdout": "", "stderr": "", "seconds": 0.0}
    if data is not None:
        row["data"] = data
    row.update(fields)
    return row


def snapshot():
    return [
        record("links", [{"ifname": "eth0", "flags": ["UP", "LOWER_UP"], "mtu": 1500}]),
        record("addresses", [{"ifname": "eth0", "addr_info": [
            {"family": "inet6", "local": "2001:db8::5", "prefixlen": 64}]}]),
        record("routes4", [{"dst": "default", "gateway": "192.0.2.1", "dev": "eth0"}]),
        record("routes6", [{"dst": "default", "gateway": "2001:db8::1", "dev": "eth0"}]),
        record("rules4", []), record("rules6", []),
    ]


def analyze(rows, **metadata):
    return MODULE["analyze"](rows, {"interface": None, **metadata})


def codes(rows, **metadata):
    return {item["code"]: item for item in analyze(rows, **metadata)}


class AddressAndRouteRegressionTest(unittest.TestCase):
    def test_address_flags_survive_unavailable_link_snapshot(self):
        for flag, code in (("dadfailed", "dad-failed"), ("tentative", "address-tentative"),
                           ("deprecated", "address-deprecated")):
            for status in ("missing", "invalid", "truncated"):
                with self.subTest(flag=flag, status=status):
                    rows = snapshot()
                    rows[0] = record("links", status=status)
                    rows[1]["data"][0]["addr_info"][0]["flags"] = [flag]
                    result = codes(rows)
                    self.assertIn(code, result)
                    self.assertNotIn("ipv6-mtu", result)
                    self.assertNotIn(code, codes(rows, interface="other0"))

    def test_mtu_findings_require_link_evidence_and_are_per_interface(self):
        rows = snapshot()
        rows[0]["data"][0]["mtu"] = 1200
        rows[1]["data"][0]["addr_info"].append({"family": "inet6", "local": "2001:db8::6"})
        self.assertEqual(sum(item["code"] == "ipv6-mtu" for item in analyze(rows)), 1)

    def test_multipath_defaults_keep_device_and_gateway_severity(self):
        for hop_key in ("nexthops", "multipath"):
            for gateway_key, gateway in (("gateway", "192.0.2.1"),
                                         ("via", {"family": "inet6", "host": "2001:db8::1"})):
                with self.subTest(hop_key=hop_key, gateway=gateway):
                    rows = snapshot()
                    rows[0]["data"][0].update(flags=["UP", "NO-CARRIER"], operstate="DOWN")
                    rows[2]["data"] = [{"dst": "default", hop_key: [{"dev": "eth0", gateway_key: gateway}]}]
                    rows[3]["data"] = []
                    family = "4" if gateway_key == "gateway" else "6"
                    address = gateway if gateway_key == "gateway" else gateway["host"]
                    rows.append(record("neighbours" + family, [{"dev": "eth0", "dst": address, "state": ["FAILED"]}]))
                    result = codes(rows)
                    self.assertEqual(result["no-carrier"]["severity"], "warning")
                    self.assertEqual(result["neighbour-unresolved"]["severity"], "warning")

    def test_local_tunnel_routes_do_not_suggest_multiple_routed_tunnels(self):
        rows = snapshot()
        rows[0]["data"] = [{"ifname": name, "flags": ["UP"], "linkinfo": {"info_kind": "wireguard"}}
                           for name in ("wg0", "wg1")]
        rows[2]["data"] = []
        rows[3]["data"] = [
            {"dev": "wg0", "dst": "ff00::/8", "table": "local", "type": "multicast"},
            {"dev": "wg1", "dst": "2001:db8::5", "table": 255, "type": "local"},
        ]
        self.assertNotIn("multiple-tunnels", codes(rows))
        rows[2]["data"] = [{"dst": "default", "table": 52,
                            "nexthops": [{"dev": "wg0"}, {"dev": "wg1"}]}]
        result = analyze(rows)
        self.assertIn("multiple-tunnels", {item["code"] for item in result})
        self.assertTrue(all("route tables 52" in item["message"] for item in result if item["code"] == "tunnel-state"))

    def test_incomplete_advice_matches_available_failure_causes(self):
        rows = snapshot()
        rows[0] = record("links", status="truncated")
        rows[1] = record("addresses", status="timeout")
        advice = codes(rows)["core-incomplete"]["next_step"]
        self.assertIn("--max-bytes", advice)
        self.assertIn("--timeout", advice)
        self.assertNotIn("Install iproute2", advice)
        self.assertIn("original report", codes(rows, source_kind="sos")["core-incomplete"]["next_step"])


class ProbeRegressionTest(unittest.TestCase):
    def test_denied_dns_query_is_visible_without_asserting_network_cause(self):
        row = record("dns", status="denied", returncode=9,
                     stderr="UDP setup failed: permission denied. No servers could be reached.",
                     probe={"kind": "dns", "target": "app.example", "qtype": "AAAA"})
        finding = codes(snapshot() + [row])["dns-query-failed"]
        self.assertEqual(finding["severity"], "warning")
        self.assertIn("privilege", finding["next_step"])
        row["returncode"] = None
        result = codes(snapshot() + [row])
        self.assertNotIn("dns-query-failed", result)
        self.assertEqual(result["probe-denied"]["severity"], "info")

    def test_ping_permission_failure_is_an_unmeasured_observation(self):
        row = record("ping", status="denied", returncode=2,
                     probe={"kind": "ping", "target": "192.0.2.1", "family": 4})
        result = codes(snapshot() + [row])
        self.assertNotIn("icmp-no-reply", result)
        self.assertEqual(result["probe-denied"]["severity"], "info")

    def test_one_missing_http_family_is_observation_only_with_sibling_success(self):
        url = "https://app.example/"
        failed = record("http6", status="error", returncode=6, stdout="http_code=000\n",
                        probe={"kind": "http", "target": url, "family": 6, "family_selection": "both"})
        success = record("http4", stdout="http_code=405\n",
                         probe={"kind": "http", "target": url, "family": 4, "family_selection": "both"})
        result = codes(snapshot() + [failed, success])
        self.assertEqual(result["http-family-unresolved"]["severity"], "info")
        self.assertNotIn("http-failed", result)
        self.assertEqual(codes(snapshot() + [failed])["http-failed"]["severity"], "warning")
        success["probe"]["target"] = "https://different.example/"
        self.assertIn("http-failed", codes(snapshot() + [failed, success]))
        success["probe"]["target"] = url
        failed["probe"]["family_selection"] = "6"
        self.assertIn("http-failed", codes(snapshot() + [failed, success]))
        failed["probe"]["family_selection"] = "both"
        failed["returncode"] = 7
        self.assertIn("http-failed", codes(snapshot() + [failed, success]))

    def test_ipv6_tcp_endpoint_preserves_address_and_port_redaction(self):
        address = "2001:db8::5"
        row = record("tcp", {"connected": False, "error": "Connection refused"},
                     probe={"kind": "tcp", "target": address, "family": 6, "port": 443})
        finding = codes(snapshot() + [row])["tcp-connect-failed"]
        self.assertIn("[" + address + "]:443", finding["message"])
        redactor = MODULE["Redactor"]("")
        result = redactor.redact({"address": address, "message": finding["message"]})
        self.assertIn("[" + result["address"] + "]:443", result["message"])


class PrivacyRegressionTest(unittest.TestCase):
    def test_ipv6_sentence_punctuation_keeps_one_address_token(self):
        redactor = MODULE["Redactor"]("")
        address = "2001:db8::5"
        expected = redactor.redact(address)
        for suffix in (".", ":", "...", ".:"):
            with self.subTest(suffix=suffix):
                self.assertEqual(redactor.redact(address + suffix), expected + suffix)
        self.assertEqual(redactor.redact("dns:" + address + "."), "dns:" + expected + ".")
        shortened = redactor.redact("2001:db8::")
        self.assertEqual(redactor.redact("2001:db8::."), shortened + ".")
        self.assertEqual(redactor.redact("[" + address + "]:443"), "[" + expected + "]:443")

    def test_dhcp_and_large_hardware_identifiers_do_not_reveal_mac_tokens(self):
        mac = "52:54:00:12:34:56"
        text = ("GENERAL.HWADDR:" + mac + "\n"
                "DHCP4.OPTION[1]:dhcp_client_identifier = 01:" + mac + "\n"
                "DHCP6.OPTION[1]:dhcp6_client_id = 00:03:00:01:" + mac + "\n"
                "DHCP6.OPTION[2]:pxe_client_id = 00:01:00:01:12:34:56:78:" + mac + "\n")
        result = MODULE["Redactor"]("").redact(text)
        self.assertNotIn(mac, result)
        self.assertIn("GENERAL.HWADDR:<MAC-1>", result)
        self.assertIn("dhcp_client_identifier = <DHCP-ID-1>", result)
        hardware = "80:00:00:48:fe:80:00:00:00:00:00:00:00:02:c9:03:00:0b:7b:d1"
        self.assertNotIn(hardware, MODULE["Redactor"]("").redact("link/infiniband " + hardware))
        eui64 = "52:54:00:ff:fe:12:34:56"
        self.assertNotIn(eui64, MODULE["Redactor"]("").redact("GENERAL.HWADDR:" + eui64))

    def test_hostname_variants_and_source_paths_are_pseudonymized(self):
        redactor = MODULE["Redactor"]("MyHost.example.com")
        source = "/home/analyst/cases/sosreport-myhost-20261106-abcdef"
        result = redactor.redact({"metadata": {"source_directory": source, "hostname": "MYHOST.example.com"},
                                 "text": "myhost myhost.example.com MyHost.tailnet.example unrelated-myhost"})
        self.assertEqual(result["metadata"]["source_directory"], "<source-path-1>")
        self.assertEqual(result["metadata"]["hostname"], "<hostname>")
        self.assertEqual(result["text"], "<hostname> <hostname> <hostname>.tailnet.example unrelated-myhost")
        self.assertEqual(MODULE["Redactor"]("").redact("DHCP4.OPTION[1]:host_name = incident-host"),
                         "DHCP4.OPTION[1]:host_name = <hostname-1>")

    def test_tailscale_login_name_is_excluded_from_tailnet_metadata(self):
        row = record("tailscale", {"BackendState": "Running", "CurrentTailnet": {
            "Name": "private-user@example.com", "MagicDNSSuffix": "tail123.ts.net", "MagicDNSEnabled": True}})
        MODULE["filter_tailscale"](row)
        self.assertNotIn("private-user", json.dumps(row))
        self.assertEqual(row["data"]["CurrentTailnet"], {"MagicDNSSuffix": "tail123.ts.net", "MagicDNSEnabled": True})


if __name__ == "__main__":
    unittest.main()
