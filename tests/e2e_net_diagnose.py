#!/usr/bin/env python3
"""Run the complete diagnostic CLI against disposable Linux network namespaces."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest


SELF = Path(__file__).resolve()
SCRIPT = SELF.parents[1] / "bin" / "net-diagnose"
OPTIONS = None
IP4, IP6 = "192.0.2.2", "2001:db8:1::2"


def run(*argv, **kwargs):
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=20, check=True, **kwargs)


def fresh_namespace_links(links):
    fallback = {"tunl0": "ipip", "sit0": "sit", "ip6tnl0": "ip6tnl",
                "gre0": "gre", "gretap0": "gretap", "erspan0": "erspan",
                "ip6gre0": "ip6gre", "ip6gretap0": "ip6gretap", "ip6erspan0": "ip6erspan",
                "ip_vti0": "vti", "ip6_vti0": "vti6"}
    names = [row.get("ifname") for row in links]
    if names.count("lo") != 1 or len(set(names)) != len(names):
        return False
    for row in links:
        if row.get("ifname") == "lo":
            continue
        if (row.get("ifname") not in fallback or "UP" in row.get("flags", []) or
                row.get("linkinfo", {}).get("info_kind") != fallback[row["ifname"]]):
            return False
    return True


def peer_server(directory):
    log = Path(directory, "requests.log")

    def record(line):
        with log.open("a") as stream:
            stream.write(line + "\n")

    class HTTP(BaseHTTPRequestHandler):
        def do_HEAD(self):
            record("HTTP " + self.path)
            self.send_response(405 if self.path == "/head-disallowed" else 200)
            self.end_headers()

        def log_message(self, *args):
            pass

    class HTTPServer(ThreadingHTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name = self.server_address[0]
            self.server_port = self.server_address[1]

    class HTTP6(HTTPServer):
        address_family = socket.AF_INET6

    class DNS(socketserver.BaseRequestHandler):
        def handle(self):
            packet, sock = self.request
            if len(packet) < 12:
                return
            end, labels = 12, []
            while end < len(packet) and packet[end]:
                size = packet[end]
                labels.append(packet[end + 1:end + 1 + size].decode("ascii"))
                end += size + 1
            end += 1
            if end + 4 > len(packet):
                return
            name = ".".join(labels)
            kind = struct.unpack("!H", packet[end:end + 2])[0]
            record("DNS " + name + " " + str(kind))
            payload = b""
            if name != "missing.test" and kind in (1, 28):
                address = socket.inet_pton(socket.AF_INET if kind == 1 else socket.AF_INET6,
                                          IP4 if kind == 1 else IP6)
                payload = b"\xc0\x0c" + struct.pack("!HHIH", kind, 1, 60, len(address)) + address
            flags = 0x8183 if name == "missing.test" else 0x8180
            response = packet[:2] + struct.pack("!HHHHH", flags, 1, bool(payload), 0, 0)
            sock.sendto(response + packet[12:end + 4] + payload, self.client_address)

    servers = [HTTPServer(("0.0.0.0", 18080), HTTP), HTTP6(("::", 18081), HTTP),
               socketserver.UDPServer(("0.0.0.0", 53), DNS)]
    tls = HTTPServer(("0.0.0.0", 18443), HTTP)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(Path(directory, "cert.pem")), str(Path(directory, "key.pem")))
    tls.socket = context.wrap_socket(tls.socket, server_side=True)
    servers.append(tls)
    for server in servers:
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    print(os.getpid(), flush=True)
    threading.Event().wait()


class NetworkE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.readlink("/proc/self/ns/net") == OPTIONS.parent_netns:
            raise RuntimeError("Refusing to change the caller's network namespace")
        initial = json.loads(run("ip", "-j", "-d", "link", "show").stdout)
        if not fresh_namespace_links(initial):
            raise RuntimeError("Expected a new network namespace with loopback and inactive kernel fallback tunnels")
        run("mount", "--make-rprivate", "/")
        run("mount", "-t", "sysfs", "-o", "ro,nosuid,nodev,noexec", "sysfs", "/sys")
        cls.fixtures = tempfile.TemporaryDirectory(prefix="fixtures-", dir=OPTIONS.output_dir)
        cls.addClassCleanup(cls.fixtures.cleanup)
        directory = Path(cls.fixtures.name)
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(directory / "key.pem"), "-out", str(directory / "cert.pem"),
            "-subj", "/CN=diag.test", "-addext", "subjectAltName=IP:192.0.2.2,DNS:diag.test")
        cls.peer_log = (directory / "peer-stderr.log").open("w")
        cls.addClassCleanup(cls.peer_log.close)
        cls.peer = subprocess.Popen(["unshare", "--net", "--", sys.executable, str(SELF),
                                     "--peer", str(directory)], stdout=subprocess.PIPE,
                                    stderr=cls.peer_log, text=True)
        cls.addClassCleanup(cls.stop_peer)
        if not select.select([cls.peer.stdout], [], [], 10)[0]:
            raise RuntimeError("Peer server did not start: " + (directory / "peer-stderr.log").read_text())
        line = cls.peer.stdout.readline().strip()
        if not line.isdecimal():
            raise RuntimeError("Peer server failed: " + (directory / "peer-stderr.log").read_text())
        cls.peer_pid = line
        run("ip", "link", "set", "lo", "up")
        run("ip", "link", "add", "diag0", "type", "veth", "peer", "name", "diag-peer")
        run("ip", "link", "set", "diag-peer", "netns", cls.peer_pid)
        run("ip", "address", "add", "192.0.2.1/24", "dev", "diag0")
        run("ip", "-6", "address", "add", "2001:db8:1::1/64", "dev", "diag0", "nodad")
        run("ip", "link", "set", "diag0", "up")
        cls.peer_cmd("ip", "link", "set", "lo", "up")
        cls.peer_cmd("ip", "address", "add", IP4 + "/24", "dev", "diag-peer")
        cls.peer_cmd("ip", "-6", "address", "add", IP6 + "/64", "dev", "diag-peer", "nodad")
        cls.peer_cmd("ip", "link", "set", "diag-peer", "up")
        for filename, contents in (
            ("hosts", "127.0.0.1 localhost\n::1 localhost\n192.0.2.2 diag.test\n2001:db8:1::2 diag.test\n"),
            ("resolv.conf", "nameserver 192.0.2.2\noptions timeout:1 attempts:1\n"),
            ("nsswitch.conf", "hosts: files dns\n"),
        ):
            path = directory / filename
            path.write_text(contents)
            run("mount", "--bind", str(path), "/etc/" + filename)
        run("ping", "-n", "-c", "1", "-W", "2", IP4)
        run("ping", "-n", "-6", "-c", "1", "-W", "2", IP6)
        cls.outcomes = []

    @classmethod
    def stop_peer(cls):
        cls.peer.terminate()
        try:
            cls.peer.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.peer.kill()
            cls.peer.wait(timeout=3)
        cls.peer.stdout.close()

    @classmethod
    def peer_cmd(cls, *argv):
        return run("nsenter", "--target", cls.peer_pid, "--net", "--", *argv)

    def report(self, name, *args, expected=0, env=None):
        path = Path(OPTIONS.output_dir, name + ".json")
        command = [sys.executable, str(SCRIPT), "--quiet", "--no-services", "--format", "json",
                   "--timeout", "2.5", "--budget", "20", "--output", str(path), *args]
        started = time.monotonic()
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
        self.outcomes.append({"name": name, "exit_code": result.returncode,
                              "seconds": round(time.monotonic() - started, 3), "report": str(path)})
        self.assertEqual(result.returncode, expected, result.stderr)
        report = json.loads(path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len({r["id"] for r in report["evidence"]}), len(report["evidence"]))
        self.assertFalse(any(r["status"] == "invalid" for r in report["evidence"]),
                         [(r["id"], r.get("parse_error")) for r in report["evidence"] if r["status"] == "invalid"])
        return report

    def evidence(self, report, ident):
        return next(r for r in report["evidence"] if r["id"] == ident)

    def has_finding(self, report, code):
        self.assertIn(code, {f["code"] for f in report["findings"]})

    def nft_drop(self, expression):
        self.peer_cmd("nft", "add", "table", "inet", "diag_test")
        self.addCleanup(self.peer_cmd, "nft", "delete", "table", "inet", "diag_test")
        self.peer_cmd("nft", "add", "chain", "inet", "diag_test", "input",
                      "{ type filter hook input priority 0; policy accept; }")
        self.peer_cmd("nft", "add", "rule", "inet", "diag_test", "input", *expression)

    def test_passive(self):
        requests = Path(self.fixtures.name, "requests.log")
        before = requests.read_text() if requests.exists() else ""
        links = run("ip", "-j", "link", "show").stdout
        routes = run("ip", "-j", "route", "show", "table", "all").stdout
        report = self.report("passive", "--extended", "--logs")
        self.assertTrue(report["summary"]["core_complete"])
        self.assertEqual(self.evidence(report, "nftables")["status"], "ok")
        self.assertFalse(any(r.get("probe") for r in report["evidence"]))
        self.assertEqual(requests.read_text() if requests.exists() else "", before)
        self.assertEqual(run("ip", "-j", "link", "show").stdout, links)
        self.assertEqual(run("ip", "-j", "route", "show", "table", "all").stdout, routes)

    def test_ipv4_application(self):
        report = self.report("ipv4", "--active", "--target", IP4, "--tcp-port", "18080",
                             "--url", "http://192.0.2.2:18080/", "--family", "4")
        self.assertTrue(self.evidence(report, "tcp-0")["data"]["connected"])
        self.assertEqual(self.evidence(report, "ping-0")["returncode"], 0)
        self.has_finding(report, "http-response")

    def test_ipv6_bound_application(self):
        report = self.report("ipv6-bound", "--active", "--target", IP6, "--tcp-port", "18081",
                             "--url", "http://[2001:db8:1::2]:18081/", "--family", "6", "-i", "diag0")
        self.assertTrue(self.evidence(report, "tcp-0")["data"]["connected"])
        self.assertEqual(self.evidence(report, "ping-0")["returncode"], 0)
        self.has_finding(report, "http-response")

    def test_nss_over_dns(self):
        report = self.report("nss-dns", "--active", "--target", "dns.test", "--tcp-port", "18080", "--family", "4")
        self.assertEqual(self.evidence(report, "resolve-0")["data"]["addresses"], [{"address": IP4, "family": 4}])
        self.assertTrue(self.evidence(report, "tcp-0")["data"]["connected"])

    def test_dns_success_and_nxdomain(self):
        report = self.report("dns", "--active", "--dns-name", "healthy.test", "--dns-name", "missing.test",
                             "--dns-server", IP4, "--fail-on", "warning", expected=1)
        self.assertEqual(self.evidence(report, "dns-0-A")["status"], "ok")
        self.assertIn(IP4, self.evidence(report, "dns-0-A")["stdout"])
        self.assertIn(IP6, self.evidence(report, "dns-0-AAAA")["stdout"])
        self.has_finding(report, "dns-response")

    def test_tls_verification_and_success(self):
        url = "https://192.0.2.2:18443/"
        rejected = self.report("tls-untrusted", "--active", "--url", url, "--family", "4", "--fail-on", "warning", expected=1)
        self.assertEqual(self.evidence(rejected, "http-0-4")["returncode"], 60)
        self.has_finding(rejected, "http-failed")
        env = dict(os.environ, CURL_CA_BUNDLE=str(Path(self.fixtures.name, "cert.pem")))
        trusted = self.report("tls-trusted", "--active", "--url", url, "--family", "4", env=env)
        self.has_finding(trusted, "http-response")

    def test_http_405(self):
        report = self.report("http-405", "--active", "--url", "http://192.0.2.2:18080/head-disallowed", "--family", "4")
        self.has_finding(report, "http-response")
        self.assertNotIn("http-failed", {f["code"] for f in report["findings"]})

    def test_tcp_refused(self):
        report = self.report("tcp-refused", "--active", "--target", IP4, "--tcp-port", "18082",
                             "--fail-on", "warning", expected=1)
        self.assertEqual(self.evidence(report, "ping-0")["returncode"], 0)
        self.has_finding(report, "tcp-connect-failed")

    def test_icmp_filter_does_not_hide_working_tcp(self):
        self.nft_drop(["ip", "protocol", "icmp", "icmp", "type", "echo-request", "drop"])
        report = self.report("icmp-filtered", "--active", "--target", IP4, "--tcp-port", "18080",
                             "--fail-on", "warning", expected=1)
        self.has_finding(report, "icmp-no-reply")
        self.assertTrue(self.evidence(report, "tcp-0")["data"]["connected"])

    def test_partial_icmp_loss(self):
        self.nft_drop(["ip", "protocol", "icmp", "icmp", "type", "echo-request",
                       "numgen", "inc", "mod", "2", "==", "0", "drop"])
        report = self.report("icmp-partial-loss", "--active", "--target", IP4, "--tcp-port", "18080",
                             "--fail-on", "warning", expected=1)
        self.assertEqual(self.evidence(report, "ping-0")["returncode"], 1)
        self.assertIn("50% packet loss", self.evidence(report, "ping-0")["stdout"])
        self.has_finding(report, "icmp-packet-loss")

    def test_tcp_filter_timeout(self):
        self.nft_drop(["tcp", "dport", "18080", "drop"])
        report = self.report("tcp-filtered", "--active", "--target", IP4, "--tcp-port", "18080",
                             "--fail-on", "warning", expected=1)
        self.assertEqual(self.evidence(report, "ping-0")["returncode"], 0)
        self.has_finding(report, "tcp-connect-failed")

    def test_blackhole_route(self):
        run("ip", "route", "add", "blackhole", IP4 + "/32")
        self.addCleanup(run, "ip", "route", "del", "blackhole", IP4 + "/32")
        report = self.report("blackhole", "--target", IP4, "--fail-on", "warning", expected=1)
        self.assertNotEqual(self.evidence(report, "route-0")["returncode"], 0)
        self.has_finding(report, "target-route")

    def test_policy_routing_without_main_table_route(self):
        run("ip", "route", "del", "192.0.2.0/24", "dev", "diag0")
        self.addCleanup(run, "ip", "route", "add", "192.0.2.0/24", "dev", "diag0")
        run("ip", "route", "add", "table", "100", "192.0.2.0/24", "dev", "diag0")
        self.addCleanup(run, "ip", "route", "del", "table", "100", "192.0.2.0/24", "dev", "diag0")
        run("ip", "rule", "add", "priority", "100", "to", IP4, "lookup", "100")
        self.addCleanup(run, "ip", "rule", "del", "priority", "100")
        report = self.report("policy-routing", "--active", "--target", IP4, "--tcp-port", "18080")
        self.assertTrue(self.evidence(report, "tcp-0")["data"]["connected"])
        self.has_finding(report, "policy-routing-v4")

    def test_link_down(self):
        run("ip", "link", "set", "diag0", "down")
        self.addCleanup(run, "ip", "-6", "address", "replace", "2001:db8:1::1/64", "dev", "diag0", "nodad")
        self.addCleanup(run, "ip", "link", "set", "diag0", "up")
        report = self.report("link-down", "-i", "diag0", "--fail-on", "warning", expected=1)
        self.has_finding(report, "link-down")

    def test_missing_carrier(self):
        self.peer_cmd("ip", "link", "set", "diag-peer", "down")
        self.addCleanup(self.peer_cmd, "ip", "-6", "address", "replace", IP6 + "/64", "dev", "diag-peer", "nodad")
        self.addCleanup(self.peer_cmd, "ip", "link", "set", "diag-peer", "up")
        report = self.report("no-carrier", "-i", "diag0", "--fail-on", "warning", expected=1)
        self.has_finding(report, "no-carrier")

    def test_tun_tap_and_redaction(self):
        for name, mode in (("diag-tun", "tun"), ("diag-tap", "tap")):
            run("ip", "tuntap", "add", "dev", name, "mode", mode)
            self.addCleanup(run, "ip", "link", "del", name)
            run("ip", "link", "set", name, "up")
        report = self.report("tun-tap", "--extended", "--redact")
        kinds = {r["ifname"]: r.get("linkinfo", {}).get("info_data", {}).get("type")
                 for r in self.evidence(report, "links")["data"]}
        self.assertEqual((kinds["diag-tun"], kinds["diag-tap"]), ("tun", "tap"))
        self.has_finding(report, "tunnel-state")
        self.assertNotIn(IP4, json.dumps(report))
        self.assertNotIn(IP6, json.dumps(report))

    def test_output_limit_and_budget(self):
        limited = self.report("output-limit", "--max-bytes", "1024", expected=3)
        self.assertFalse(limited["summary"]["core_complete"])
        self.assertTrue(any(r["status"] == "truncated" for r in limited["evidence"]))
        budget = self.report("budget", "--budget", "0.001", expected=3)
        self.assertTrue(any(r["status"] == "budget" for r in budget["evidence"]))

    def test_missing_tools(self):
        path = Path(self.fixtures.name, "empty-path")
        path.mkdir(exist_ok=True)
        report = self.report("missing-tools", env=dict(os.environ, PATH=str(path)), expected=3)
        self.assertEqual(self.evidence(report, "links")["status"], "missing")
        self.has_finding(report, "core-incomplete")

    def test_output_refuses_overwrite(self):
        path = Path(OPTIONS.output_dir, "existing.txt")
        path.write_text("keep me")
        result = subprocess.run([sys.executable, str(SCRIPT), "--output", str(path)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(path.read_text(), "keep me")


def main():
    global OPTIONS, SCRIPT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, default=SCRIPT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--parent-netns", help=argparse.SUPPRESS)
    parser.add_argument("--peer", help=argparse.SUPPRESS)
    OPTIONS = parser.parse_args()
    os.umask(0o077)
    if OPTIONS.peer:
        peer_server(OPTIONS.peer)
        return 0
    SCRIPT = OPTIONS.script.resolve()
    if OPTIONS.worker:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(NetworkE2E)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        summary = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                   "skipped": result.skipped, "runs": getattr(NetworkE2E, "outcomes", [])}
        Path(OPTIONS.output_dir, "results.json").write_text(json.dumps(summary, indent=2) + "\n")
        return 0 if result.wasSuccessful() else 1
    for command in ("unshare", "nsenter", "ip", "nft", "mount", "ping", "curl", "dig", "openssl"):
        if not shutil.which(command):
            parser.error("required test dependency is missing: " + command)
    output = OPTIONS.output_dir or Path(tempfile.mkdtemp(prefix="net-diagnose-e2e-"))
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if list(output.iterdir()):
        parser.error("output directory must be empty")
    print("Reports: " + str(output), flush=True)
    command = ["unshare", "--user", "--map-root-user", "--net", "--mount", "--",
               sys.executable, str(SELF), "--worker", "--parent-netns", os.readlink("/proc/self/ns/net"),
               "--script", str(SCRIPT), "--output-dir", str(output.resolve())]
    process = subprocess.Popen(command, start_new_session=True)
    try:
        return process.wait(timeout=240)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
