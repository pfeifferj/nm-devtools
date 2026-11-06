import ast
import errno
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import runpy
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "net-diagnose"
MODULE = runpy.run_path(str(SCRIPT))


def record(ident, data=None, stdout="", status="ok", **extra):
    result = {"id": ident, "title": ident, "source": {}, "status": status,
              "returncode": 0 if status == "ok" else 1, "seconds": 0.0,
              "stdout": json.dumps(data) if data is not None else stdout, "stderr": ""}
    if data is not None:
        result["data"] = data
    return dict(result, **extra)


def base_records():
    return [
        record("links", [{"ifname": "eth0", "flags": ["UP", "LOWER_UP"], "operstate": "UP", "mtu": 1500}]),
        record("addresses", [{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.0.2.2", "prefixlen": 24}]}]),
        record("routes4", [{"dst": "default", "gateway": "192.0.2.1", "dev": "eth0"}]),
        record("routes6", []), record("rules4", []), record("rules6", []),
    ]


def findings(records, interface=None):
    return MODULE["analyze"](records, {"interface": interface})


class RunnerTest(unittest.TestCase):
    def run_code(self, code, **kwargs):
        return MODULE["Runner"](**kwargs).run([sys.executable, "-c", code])

    def test_literal_arguments_and_noninteractive_environment(self):
        argument = '; touch /tmp/net-diagnose-must-not-execute "$(id)" `id`'
        result = MODULE["Runner"]().run([sys.executable, "-c",
            "import json, os, sys; print(json.dumps([sys.argv[1], os.environ['LC_ALL'], sys.stdin.read()]))", argument])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(json.loads(result["stdout"]), [argument, "C", ""])

    def test_missing_and_permission_errors_are_distinct(self):
        missing = MODULE["Runner"]().run(["/does/not/exist/net-diagnose-test"])
        denied = self.run_code("import sys; sys.stderr.write('Permission denied'); sys.exit(1)")
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(denied["status"], "denied")

    def test_noisy_command_is_stopped_at_combined_limit(self):
        result = self.run_code("import os; os.write(1, b'a' * 800); os.write(2, b'b' * 1000000)", max_bytes=1024)
        self.assertEqual(result["status"], "truncated")
        self.assertEqual(len(result["stdout"]) + len(result["stderr"]), 1024)

    def test_exact_output_limit_is_complete(self):
        result = self.run_code("import os; os.write(1, b'a' * 1024)", max_bytes=1024)
        self.assertEqual(result["status"], "ok")

    def test_timeout_preserves_output_and_whole_run_budget_skips_followups(self):
        runner = MODULE["Runner"](timeout=2, budget=0.3)
        result = runner.run([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"])
        self.assertEqual(result["status"], "budget")
        self.assertIn("started", result["stdout"])
        self.assertLess(result["seconds"], 2)
        self.assertEqual(runner.run([sys.executable, "-V"])["status"], "budget")

    def test_descendant_holding_pipes_is_killed_after_parent_exits(self):
        code = "import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); print(p.pid, flush=True)"
        result = self.run_code(code, timeout=0.4)
        self.assertEqual(result["status"], "timeout")
        pid = int(result["stdout"].strip())
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                return
            if state == "Z":
                return
            time.sleep(0.01)
        self.fail("grandchild survived process-group cleanup")

    def test_interrupt_reaps_running_process(self):
        children = []
        popen = subprocess.Popen

        def start(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child

        with mock.patch.object(MODULE["subprocess"], "Popen", side_effect=start), \
                mock.patch.object(selectors.DefaultSelector, "select", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_code("import time; time.sleep(30)")
        self.assertEqual(children[0].poll(), -signal.SIGKILL)


class CollectionTest(unittest.TestCase):
    def collector(self, argv=(), services=True):
        args = MODULE["parse_args"](list(argv))
        return MODULE["Collector"](args, {"service_queries_enabled": services})

    def test_invalid_or_partial_json_is_not_interpreted(self):
        for text, status in (("{", "ok"), ("{}", "ok"), ('[{"flags": []}]', "ok"),
                             ('[{"ifname":"eth0","linkinfo":null}]', "ok"), ("[]", "truncated")):
            with self.subTest(text=text, status=status):
                collector = self.collector()
                result = {key: value for key, value in record("unused", stdout=text, status=status).items()
                          if key not in {"id", "title", "source"}}
                with mock.patch.object(collector.runner, "run", return_value=result):
                    collected = collector.command("links", "links", ["ip"], list)
                self.assertIn(collected["status"], {"invalid", "truncated"})
                self.assertIsNone(collector.data("links"))

    def test_malformed_nested_route_fields_are_unavailable(self):
        collector = self.collector()
        malformed = record("unused", data=[{"dst": []}])
        with mock.patch.object(collector.runner, "run", return_value=malformed):
            result = collector.command("routes4", "routes4", ["ip"], list)
        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(collector.data("routes4"))

    def capture_plan(self, argv=(), services=True, overrides=None):
        collector = self.collector(argv, services)
        calls = []

        def run(command):
            calls.append(command)
            key = command[-2] if command[:4] == [sys.executable, "-I", "-c", MODULE["FILE_READER"]] else tuple(command)
            if overrides and key in overrides:
                return overrides[key]
            if key == "/proc/modules":
                out = "".join(name + " 4096 0 - Live 0x00000000\n" for name in
                              ("inet_diag", "tcp_diag", "udp_diag", "nf_tables", "ip_tables", "ip6_tables",
                               "cfg80211", "nf_conntrack_netlink", "wireguard", "xfrm_user"))
            elif isinstance(key, str) and key.endswith("/modules.builtin"):
                out = ""
            elif key in ("/proc/net/ip_tables_names", "/proc/net/ip6_tables_names"):
                out = "filter\n"
            elif command[0] == "systemctl":
                out = "\n\n".join("Id=" + unit + "\nLoadState=loaded\nActiveState=active\nSubState=running"
                                    for unit in ("systemd-resolved.service", "systemd-networkd.service"))
            elif command in (["iptables-save", "--version"], ["ip6tables-save", "--version"]):
                out = command[0] + " v1.8.13 (nf_tables)\n"
            elif command == ["ip", "-j", "-d", "-s", "link", "show"]:
                out = json.dumps([{"ifname": "wlan0", "flags": ["UP", "LOWER_UP"], "link_type": "ether", "mtu": 1500}])
            elif command == ["ip", "-j", "address", "show"]:
                out = '[{"ifname":"wlan0","addr_info":[]}]'
            elif command[:3] == ["tailscale", "status", "--json"]:
                out = '{"BackendState":"Running"}'
            elif command[:2] == ["rfkill", "--json"]:
                out = '{"rfkilldevices":[]}'
            elif command[0] == "nft":
                out = '{"nftables":[]}'
            else:
                out = "[]"
            return {"status": "ok", "returncode": 0, "stdout": out, "stderr": "", "seconds": 0.0}

        with mock.patch.object(collector.runner, "run", side_effect=run), \
                mock.patch.object(MODULE["Path"], "exists", return_value=True):
            collector.collect()
        return collector, calls

    def test_service_state_precedes_queries_that_can_activate_daemons(self):
        _, calls = self.capture_plan()
        state = next(i for i, command in enumerate(calls) if command[0] == "systemctl")
        for executable in ("resolvectl", "networkctl"):
            self.assertLess(state, next(i for i, command in enumerate(calls) if command[0] == executable))

    def test_inactive_or_unverified_services_are_not_queried(self):
        collector = self.collector()
        with mock.patch.object(collector, "command", return_value=record("services")) as command:
            collector.service_state()
        argv = tuple(command.call_args.args[2])
        for result in (
            record("services", stdout="Id=systemd-resolved.service\nLoadState=loaded\nActiveState=inactive\n\n"
                                      "Id=systemd-networkd.service\nLoadState=not-found\nActiveState=inactive\n"),
            record("services", status="denied"),
            record("services", status="truncated", stdout="Id=systemd-resolved.service\nLoadState=loaded\nActiveState=active\n"),
        ):
            with self.subTest(status=result["status"]):
                collector, calls = self.capture_plan(overrides={argv: result})
                self.assertFalse(any(c[0] in {"resolvectl", "networkctl"} for c in calls))
                for ident in ("resolved", "networkd"):
                    skipped = next(r for r in collector.records if r["id"] == ident)
                    self.assertEqual(skipped["status"], "skipped")
                    self.assertIn("service activation", skipped["stderr"])

    def test_socket_queries_require_only_the_modules_they_use(self):
        modules = "inet_diag 4096 0 - Live 0x0\ntcp_diag 4096 0 - Live 0x0\n"
        collector, calls = self.capture_plan(overrides={"/proc/modules": record("modules", stdout=modules)})
        self.assertIn(["ss", "-s"], calls)
        self.assertIn(["ss", "-H", "-n", "-t", "-o", "state", "syn-sent"], calls)
        self.assertNotIn(["ss", "-H", "-n", "-l", "-t", "-u", "-p"], calls)
        skipped = next(r for r in collector.records if r["id"] == "sockets")
        self.assertIn("udp_diag", skipped["stderr"])
        self.assertNotIn("inet_diag", skipped["stderr"])

    def test_missing_or_incomplete_module_evidence_does_not_trigger_autoload(self):
        builtin_path = "/lib/modules/" + MODULE["platform"].release() + "/modules.builtin"
        for status in ("missing", "denied", "truncated", "budget"):
            with self.subTest(status=status):
                collector, calls = self.capture_plan(overrides={
                    "/proc/modules": record("modules", status=status, stdout="nf_tables 4096 0 - Live 0x0\n"),
                    builtin_path: record("builtins", status=status),
                })
                self.assertFalse(any(c[0] == "ss" and c != ["ss", "-s"] for c in calls))
                self.assertFalse(any(c[0] == "nft" or c in (["iptables-save", "-c"], ["ip6tables-save", "-c"]) for c in calls))
                self.assertIn(["ss", "-s"], calls)
                self.assertTrue(all(next(r for r in collector.records if r["id"] == ident)["status"] == "skipped"
                                    for ident in ("sockets", "tcp-pending", "nftables", "iptables4", "iptables6")))

    def test_built_in_kernel_support_allows_queries_without_loaded_modules(self):
        builtin_path = "/lib/modules/" + MODULE["platform"].release() + "/modules.builtin"
        _, calls = self.capture_plan(overrides={
            "/proc/modules": record("modules", stdout=""),
            builtin_path: record("builtins", stdout="kernel/net/ipv4/inet_diag.ko\nkernel/net/ipv4/tcp_diag.ko\n"
                                                    "kernel/net/ipv4/udp_diag.ko\nkernel/net/netfilter/nf_tables.ko\n"),
        })
        self.assertIn(["ss", "-H", "-n", "-l", "-t", "-u", "-p"], calls)
        self.assertIn(["ss", "-H", "-n", "-t", "-o", "state", "syn-sent"], calls)
        self.assertIn(["nft", "-j", "list", "ruleset"], calls)
        self.assertIn(["iptables-save", "-c"], calls)

    def test_legacy_firewall_requires_its_own_kernel_support_and_table_inventory(self):
        versions = {tuple([name, "--version"]): record("version", stdout=name + " v1.8.13 (legacy)\n")
                    for name in ("iptables-save", "ip6tables-save")}
        _, calls = self.capture_plan(overrides=versions)
        self.assertIn(["iptables-save", "-c", "-M", "/bin/false"], calls)
        self.assertIn(["ip6tables-save", "-c", "-M", "/bin/false"], calls)
        _, calls = self.capture_plan(overrides=dict(versions, **{
            "/proc/modules": record("modules", stdout="nf_tables 4096 0 - Live 0x0\n"),
        }))
        self.assertFalse(any(c[0] in {"iptables-save", "ip6tables-save"} and "-c" in c for c in calls))
        _, calls = self.capture_plan(overrides=dict(versions, **{
            "/proc/net/ip_tables_names": record("tables", status="denied"),
        }))
        self.assertNotIn(["iptables-save", "-c", "-M", "/bin/false"], calls)
        self.assertIn(["ip6tables-save", "-c", "-M", "/bin/false"], calls)

    def test_unknown_firewall_backend_is_not_queried(self):
        collector, calls = self.capture_plan(overrides={
            ("iptables-save", "--version"): record("version", stdout="unrecognized version\n"),
            ("ip6tables-save", "--version"): record("version", status="missing"),
        })
        self.assertFalse(any(c[0] in {"iptables-save", "ip6tables-save"} and "-c" in c for c in calls))
        self.assertTrue(all(next(r for r in collector.records if r["id"] == ident)["status"] == "skipped"
                            for ident in ("iptables4", "iptables6")))

    def test_wireless_and_extended_queries_require_existing_kernel_support(self):
        collector, calls = self.capture_plan(["--extended"], overrides={"/proc/modules": record("modules", stdout="")})
        self.assertFalse(any(c[0] in {"iw", "wg", "conntrack"} or "xfrm" in c for c in calls))
        self.assertIn(["ip", "-d", "-4", "tunnel", "show"], calls)
        self.assertIn(["ip", "-d", "-6", "tunnel", "show"], calls)
        for ident in ("wireless", "wireless-regulatory", "wifi-wlan0", "wifi-power-wlan0", "stations-wlan0",
                      "xfrm-policies", "conntrack-stats", "wireguard-latest-handshakes", "wireguard-endpoints",
                      "wireguard-allowed-ips", "wireguard-transfer"):
            self.assertEqual(next(r for r in collector.records if r["id"] == ident)["status"], "skipped", ident)
        reads = [c[-2] for c in calls if c[:4] == [sys.executable, "-I", "-c", MODULE["FILE_READER"]]]
        self.assertEqual(reads.count("/proc/modules"), 1)
        self.assertEqual(sum(path.endswith("/modules.builtin") for path in reads), 1)

    def test_wireless_and_extended_queries_accept_built_in_support(self):
        builtin_path = "/lib/modules/" + MODULE["platform"].release() + "/modules.builtin"
        _, calls = self.capture_plan(["--extended"], overrides={
            "/proc/modules": record("modules", stdout=""),
            builtin_path: record("builtins", stdout="kernel/net/wireless/cfg80211.ko\n"
                                "kernel/net/netfilter/nf_conntrack_netlink.ko\nkernel/drivers/net/wireguard/wireguard.ko\n"
                                "kernel/net/xfrm/xfrm_user.ko\n"),
        })
        for command in (["iw", "dev"], ["iw", "reg", "get"], ["iw", "dev", "wlan0", "link"],
                        ["iw", "dev", "wlan0", "get", "power_save"], ["iw", "dev", "wlan0", "station", "dump"],
                        ["conntrack", "-S"], ["ip", "-s", "xfrm", "policy"]):
            self.assertIn(command, calls)
        for field in ("latest-handshakes", "endpoints", "allowed-ips", "transfer"):
            self.assertIn(["wg", "show", "all", field], calls)

    def test_passive_plan_never_sends_probes_or_changes_configuration(self):
        _, calls = self.capture_plan(["--extended", "--logs"])
        self.assertFalse(any(c[0] in {"ping", "curl", "dig", "tracepath", "sudo"} for c in calls))
        self.assertFalse(any(c[0] == "tailscale" and c[1] != "status" for c in calls))
        for command in calls:
            self.assertNotIn("--show-secrets", command)
            self.assertNotIn("showconf", command)
            self.assertNotIn("set", command)
            self.assertNotIn("connect", command)
            for action in ("check", "rescan", "disconnect", "flush", "reset", "reconnect", "enable", "disable"):
                self.assertNotIn(action, command)
        self.assertIn(["nmcli", "--colors", "no", "--wait", "2", "device", "wifi", "list", "--rescan", "no"], calls)
        self.assertIn(["iw", "dev", "wlan0", "link"], calls)
        self.assertIn(["iw", "dev", "wlan0", "get", "power_save"], calls)
        self.assertIn(["ethtool", "-S", "wlan0"], calls)
        self.assertFalse(any(c[:2] == ["wg", "show"] and c[-1] == "dump" for c in calls))

    def test_foreign_namespace_skips_host_daemons_but_keeps_kernel_state(self):
        collector, calls = self.capture_plan(["--logs", "--extended"], services=False)
        self.assertFalse(any(c[0] in {"nmcli", "networkctl", "resolvectl", "systemctl", "journalctl", "tailscale", "nym-vpnc", "nym-vpnd"} for c in calls))
        self.assertTrue(any(c[:2] == ["ip", "-j"] for c in calls))
        self.assertIn("overlay-services", [r["id"] for r in collector.records])

    def test_namespace_detection_and_explicit_service_exclusion(self):
        function = MODULE["metadata"]
        for current, initial, relation, enabled in (
            ("net:[1]", "net:[1]", "same", True),
            ("net:[2]", "net:[1]", "different", False),
            ("net:[1]", None, "unverified", False),
        ):
            with self.subTest(relation=relation), mock.patch.dict(function.__globals__, {
                    "readlink": mock.Mock(side_effect=[current, initial])}):
                data = function(MODULE["parse_args"]([]))
            self.assertEqual(data["service_namespace_comparison"], relation)
            self.assertEqual(data["service_queries_enabled"], enabled)
        with mock.patch.dict(function.__globals__, {"readlink": lambda path: "net:[1]"}):
            self.assertFalse(function(MODULE["parse_args"](["--no-services"]))["service_queries_enabled"])

    def test_passive_numeric_target_uses_only_route_lookup(self):
        collector = self.collector(["--target", "192.0.2.1"])
        with mock.patch.object(collector, "command") as command:
            collector.probes()
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[2], ["ip", "-j", "-4", "route", "get", "192.0.2.1"])

    def test_explicit_http_probe_disables_curl_config_proxy_redirects_and_other_targets(self):
        collector = self.collector(["--active", "--url", "https://192.0.2.1/", "-i", "eth0"])
        with mock.patch.object(collector, "command") as command:
            collector.probes()
        self.assertEqual(command.call_count, 1)
        argv = command.call_args.args[2]
        self.assertEqual(argv[:2], ["curl", "-q"])
        self.assertIn("--head", argv)
        self.assertIn("--globoff", argv)
        self.assertIn("--noproxy", argv)
        self.assertEqual(argv[argv.index("--interface") + 1], "if!eth0")
        self.assertNotIn("--location", argv)
        self.assertNotIn("--insecure", argv)
        self.assertEqual(collector.args.target, [])
        self.assertEqual(collector.args.dns_name, [])

    def test_bound_target_binds_route_ping_tcp_and_skips_unbound_trace(self):
        collector = self.collector(["--active", "--target", "192.0.2.1", "-i", "eth0", "--trace"])
        with mock.patch.object(collector, "command") as command:
            collector.probes()
        calls = [call.args[2] for call in command.call_args_list]
        self.assertEqual(calls[0][-2:], ["oif", "eth0"])
        self.assertIn("-I", calls[1])
        self.assertEqual(calls[2][-1], "eth0")
        self.assertEqual(collector.records[-1]["status"], "skipped")

    def test_ping_limits_transmissions_and_leaves_time_for_its_summary(self):
        for timeout in (2.5, 5):
            collector = self.collector(["--active", "--target", "192.0.2.1", "--timeout", str(timeout)])
            with mock.patch.object(collector, "command") as command:
                collector.probes()
            ping = command.call_args_list[1].args[2]
            self.assertEqual(ping[ping.index("-c") + 1], "2")
            self.assertNotIn("-w", ping)
            self.assertLess(1 + float(ping[ping.index("-W") + 1]), timeout)

    def test_short_timeouts_skip_incomplete_ping_measurements(self):
        for timeout in (0.1, 1, 2, 2.4):
            collector = self.collector(["--active", "--target", "192.0.2.1", "--timeout", str(timeout)])
            with mock.patch.object(collector, "command") as command:
                collector.probes()
            self.assertFalse(any(call.args[2][0] == "ping" for call in command.call_args_list))
            self.assertEqual(next(r for r in collector.records if r["id"] == "ping-0")["status"], "skipped")

    def test_dns_type_names_remain_literal_query_names(self):
        collector = self.collector(["--active", "--dns-name", "mx", "--family", "4"])
        with mock.patch.object(collector, "command") as command:
            collector.probes()
        self.assertEqual(command.call_args.args[2][-4:], ["-q", "mx", "-t", "A"])

    def test_numeric_nss_scope_matches_interface_name(self):
        collector = self.collector(["--active", "--target", "peer.local", "-i", "eth0"])
        with mock.patch.object(MODULE["socket"], "if_indextoname", return_value="eth0"), \
                mock.patch.object(collector, "command") as command:
            collector.destination(0, "fe80::1%2", 6, "peer.local")
        self.assertEqual(command.call_args_list[0].args[2][-2:], ["oif", "eth0"])
        self.assertEqual(command.call_count, 3)

    def test_foreign_resolved_scope_preserves_report_and_records_skip(self):
        collector = self.collector(["--active", "--target", "peer.local", "-i", "eth0"])
        with mock.patch.object(collector, "command") as command:
            collector.destination(0, "fe80::1%eth1", 6, "peer.local")
        command.assert_not_called()
        self.assertEqual(collector.records[0]["status"], "skipped")

    def test_missing_nym_client_does_not_attempt_config_reads(self):
        collector = self.collector()
        result = {key: value for key, value in record("x", status="missing").items()
                  if key not in {"id", "title", "source"}}
        with mock.patch.object(collector.runner, "run", return_value=result) as run:
            collector.overlays()
        self.assertEqual([c.args[0] for c in run.call_args_list if c.args[0][0] == "nym-vpnc"], [["nym-vpnc", "status"]])

    def test_tailscale_auth_url_and_user_record_never_persist(self):
        raw = {"BackendState": "NeedsLogin", "AuthURL": "https://example.com/SECRET",
               "User": {"1": {"LoginName": "secret-user@example.com"}},
               "Peer": {"public-key": {"Online": True, "DNSName": "peer.test", "Unrecognized": "SECRET"}}}
        item = record("tailscale", raw)
        MODULE["filter_tailscale"](item)
        self.assertNotIn("SECRET", json.dumps(item))
        self.assertNotIn("secret-user", json.dumps(item))
        self.assertIn("peer.test", item["stdout"])
        item = record("tailscale", stdout='{"AuthURL":"https://example.com/SECRET', status="truncated")
        MODULE["filter_tailscale"](item)
        self.assertNotIn("SECRET", json.dumps(item))

    def test_authentication_urls_are_filtered_from_logs_without_redact(self):
        text = ("tailscaled: control: AuthURL is https://headscale.example/register/SECRET1\n"
                "visit https://login.tailscale.com/a/SECRET2\n"
                "help https://tailscale.com/kb/1080/cli\n")
        collector = self.collector()
        with mock.patch.object(collector.runner, "run", return_value={
                "status": "ok", "stdout": text, "stderr": "", "returncode": 0, "seconds": 0}):
            item = collector.command("service-logs", "logs", ["journalctl"])
        self.assertNotIn("SECRET", json.dumps(item))
        self.assertIn("https://tailscale.com/kb/1080/cli", item["stdout"])


@unittest.skipUnless(os.environ.get("NET_DIAGNOSE_LIVE_TESTS") == "1",
                     "set NET_DIAGNOSE_LIVE_TESTS=1 to enable live loopback probes")
class LoopbackProbeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("curl"), "curl is not installed")
    def test_tcp_and_http_with_real_loopback_server(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                paths.append(self.path)
                self.send_response(405)
                self.end_headers()

            def log_message(self, *args):
                pass

        try:
            server = HTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError:
            self.skipTest("sandbox blocks loopback sockets")
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        worker.start()
        try:
            port = server.server_address[1]
            args = MODULE["parse_args"](["--active", "--target", "127.0.0.1", "--tcp-port", str(port),
                "--url", f"http://127.0.0.1:{port}/check?field[foo]=bar", "--family", "4", "--timeout", "3"])
            collector = MODULE["Collector"](args, {"service_queries_enabled": False})
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, ".curlrc").write_text('url = "http://127.0.0.1:1/extra"\n')
                with mock.patch.dict(os.environ, {"CURL_HOME": directory, "http_proxy": "http://127.0.0.1:1",
                                                  "NO_PROXY": "", "no_proxy": ""}):
                    collector.probes()
            tcp = next(r for r in collector.records if r.get("probe", {}).get("kind") == "tcp")
            http = next(r for r in collector.records if r.get("probe", {}).get("kind") == "http")
            self.assertEqual(tcp["status"], "ok", tcp)
            self.assertTrue(tcp["data"]["connected"], tcp)
            self.assertEqual(http["status"], "ok", http)
            self.assertIn("http_code=405", http["stdout"])
            self.assertEqual(paths, ["/check?field[foo]=bar"])
            self.assertEqual(next(f for f in findings(base_records() + collector.records)
                                  if f["code"] == "http-response")["severity"], "info")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


class DiagnosisTest(unittest.TestCase):
    def test_missing_core_data_does_not_become_no_routes_or_no_addresses(self):
        rows = [record(key, status="denied") for key in MODULE["CORE"]]
        self.assertEqual([f["code"] for f in findings(rows)], ["core-incomplete"])

    def test_unused_down_port_unknown_tunnel_and_policy_routes_are_not_faults(self):
        rows = base_records()
        rows[0]["data"] += [
            {"ifname": "unused0", "flags": [], "operstate": "DOWN"},
            {"ifname": "tailscale0", "flags": ["UP", "LOWER_UP"], "operstate": "UNKNOWN", "mtu": 1280,
             "linkinfo": {"info_kind": "tun", "info_data": {"type": "tun"}}},
        ]
        rows[2]["data"] = [{"dst": "default", "dev": "tailscale0", "table": 52}]
        rows[4]["data"] = [{"priority": 5270, "table": 52}]
        self.assertFalse(any(f["severity"] != "info" for f in findings(rows)))

    def test_ipv6_only_and_split_default_are_not_reported_as_outages(self):
        rows = base_records()
        rows[1]["data"][0]["addr_info"] = [{"family": "inet6", "local": "2001:db8::2", "prefixlen": 64}]
        rows[2]["data"] = []
        rows[3]["data"] = [{"dst": "::/1", "dev": "eth0"}, {"dst": "8000::/1", "dev": "eth0"}]
        self.assertFalse(any(f["severity"] != "info" for f in findings(rows)))

    def test_dad_failure_is_an_error_but_tentative_is_an_observation(self):
        rows = base_records()
        address = rows[1]["data"][0]["addr_info"][0]
        address.update(family="inet6", local="2001:db8::1", tentative=True)
        self.assertEqual(next(f for f in findings(rows) if f["code"] == "address-tentative")["severity"], "info")
        address["dadfailed"] = True
        self.assertEqual(next(f for f in findings(rows) if f["code"] == "dad-failed")["severity"], "error")

    def test_carrier_failure_on_selected_or_default_route_device_is_warning(self):
        rows = base_records()
        rows[0]["data"][0].update(flags=["UP", "NO-CARRIER"], operstate="DOWN")
        self.assertEqual(next(f for f in findings(rows) if f["code"] == "no-carrier")["severity"], "warning")

    def test_stale_neighbour_and_historic_errors_are_not_active_failures(self):
        rows = base_records()
        rows[0]["data"][0]["stats64"] = {"rx": {"errors": 100, "dropped": 999}}
        rows.append(record("neighbours4", [{"dst": "192.0.2.1", "dev": "eth0", "state": ["STALE"]}]))
        self.assertFalse(any(f["severity"] != "info" for f in findings(rows)))
        rows[-1]["data"][0]["state"] = ["FAILED"]
        self.assertEqual(next(f for f in findings(rows) if f["code"] == "neighbour-unresolved")["severity"], "warning")

    def test_effective_reverse_path_filter_uses_maximum(self):
        rows = base_records() + [record("sysctl-ipv4/conf/all/rp_filter", stdout="2"),
                                record("device-eth0-ipv4/rp_filter", stdout="1")]
        self.assertNotIn("strict-rpf", [f["code"] for f in findings(rows)])
        rows[-2]["stdout"] = "0"
        self.assertIn("strict-rpf", [f["code"] for f in findings(rows)])

    def test_nm_reason_and_wifi_signal_findings(self):
        rows = base_records() + [
            record("nm-devices", stdout="GENERAL.DEVICE:wlan0\nGENERAL.STATE:120 (failed)\nGENERAL.REASON:7 (Secrets were required)\n"),
            record("wifi-wlan0", stdout="Connected to 00:11:22:33:44:55\n\tsignal: -83 dBm\n"),
        ]
        result = findings(rows)
        self.assertIn("Secrets were required", next(f for f in result if f["code"] == "nm-device-failed")["message"])
        self.assertIn("wifi-weak-signal", [f["code"] for f in result])
        self.assertNotIn("nm-device-failed", [f["code"] for f in findings(rows, "eth0")])

    def test_tailscale_health_exit_node_and_userspace_mode(self):
        rows = base_records() + [record("tailscale", {"BackendState": "Running", "TUN": False,
            "Health": ["DNS not reachable"], "ExitNodeStatus": {"Online": False}})]
        codes = {f["code"] for f in findings(rows)}
        self.assertTrue({"tailscale-health", "tailscale-exit-offline", "tailscale-userspace"}.issubset(codes))
        self.assertNotIn("no-carrier", codes)

    def test_nym_error_and_intentional_lan_policy(self):
        rows = base_records() + [record("nym-status", stdout="State: Error: tunnel setup failed"),
                                record("nym-lan", stdout="Local network policy: block")]
        result = findings(rows)
        self.assertEqual(next(f for f in result if f["code"] == "nym-state")["severity"], "warning")
        self.assertEqual(next(f for f in result if f["code"] == "nym-lan-blocked")["severity"], "info")

    def test_http_405_is_a_response_and_dns_nxdomain_is_not_success(self):
        rows = base_records() + [
            record("http", stdout="http_code=405\n", probe={"kind": "http", "target": "https://test.example", "family": 4}),
            record("dns", stdout=";; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 1", probe={"kind": "dns", "target": "test.example", "qtype": "A"}),
        ]
        result = findings(rows)
        self.assertEqual(next(f for f in result if f["code"] == "http-response")["severity"], "info")
        self.assertIn("dns-response", [f["code"] for f in result])
        self.assertNotIn("http-failed", [f["code"] for f in result])

    def test_probe_permission_failure_is_unmeasured(self):
        rows = base_records() + [record("ping", status="denied", probe={"kind": "ping", "target": "192.0.2.1", "family": 4})]
        self.assertNotIn("icmp-no-reply", [f["code"] for f in findings(rows)])

    def test_blackhole_einval_is_a_failed_route_lookup(self):
        rows = base_records() + [record("route", status="error", returncode=2,
            stderr="RTNETLINK answers: Invalid argument\n",
            probe={"kind": "route", "target": "192.0.2.1", "family": 4})]
        self.assertIn("target-route", [f["code"] for f in findings(rows)])

    def test_prohibited_route_is_a_failed_target_lookup(self):
        rows = base_records() + [record("route", status="denied", returncode=2,
            stderr="RTNETLINK answers: Permission denied\n",
            probe={"kind": "route", "target": "192.0.2.1", "family": 4})]
        self.assertIn("target-route", [f["code"] for f in findings(rows)])

    def test_budget_exhaustion_does_not_claim_a_network_failure(self):
        rows = base_records() + [record(kind, status="budget", returncode=-9,
            probe={"kind": kind, "target": "192.0.2.1", "family": 4}) for kind in ("ping", "http")]
        self.assertFalse(any(f["severity"] != "info" for f in findings(rows)))

    def test_tcp_connect_denial_is_distinct_from_bind_permission(self):
        for operation in ("connect", "setsockopt"):
            sock = mock.MagicMock()
            sock.__enter__.return_value = sock
            getattr(sock, operation).side_effect = OSError(errno.EACCES, "Permission denied")
            output = io.StringIO()
            with mock.patch.object(MODULE["socket"], "getaddrinfo", return_value=[(2, 1, 6, "", ("192.0.2.1", 443))]), \
                    mock.patch.object(MODULE["socket"], "socket", return_value=sock), \
                    mock.patch.object(sys, "argv", ["-c", "192.0.2.1", "443", "1", "eth0"]), \
                    mock.patch.object(sys, "stdout", output):
                exec(MODULE["TCP_CONNECT"], {})
            result = json.loads(output.getvalue())
            self.assertFalse(result["connected"])
            self.assertEqual(result["permission"], operation == "setsockopt")

    def test_partial_ping_loss_is_reported_with_either_exit_code(self):
        for code in (0, 1):
            rows = base_records() + [record("ping", status="ok" if code == 0 else "error", returncode=code,
                stdout="2 packets transmitted, 1 received, 50% packet loss, time 1002ms\n",
                probe={"kind": "ping", "target": "192.0.2.1", "family": 4})]
            codes = [f["code"] for f in findings(rows)]
            self.assertIn("icmp-packet-loss", codes)
            self.assertNotIn("icmp-no-reply", codes)

    def test_zero_and_total_ping_loss_are_not_partial_loss(self):
        for loss in (0, 100):
            rows = base_records() + [record("ping", status="ok" if loss == 0 else "error",
                stdout=str(loss) + "% packet loss", probe={"kind": "ping", "target": "192.0.2.1", "family": 4})]
            codes = [f["code"] for f in findings(rows)]
            self.assertNotIn("icmp-packet-loss", codes)
            self.assertEqual("icmp-no-reply" in codes, loss == 100)


class ReportAndCliTest(unittest.TestCase):
    def test_defaults_and_explicit_selectors(self):
        parse = MODULE["parse_args"]
        self.assertEqual(parse([]).target, [])
        self.assertEqual(parse(["--active"]).target, ["example.com"])
        chosen = parse(["--active", "--target", "vpn.internal"])
        self.assertEqual(chosen.target, ["vpn.internal"])
        self.assertEqual(chosen.url, [])
        self.assertEqual(chosen.dns_name, [])
        self.assertEqual(parse(["--active", "--tailscale-netcheck"]).target, [])

    def test_invalid_inputs_fail_before_collection(self):
        invalid = [
            ["--timeout", "nan"], ["--budget", "inf"], ["--timeout", "0"],
            ["--target", "example.com"], ["--url", "https://example.com"],
            ["--active", "--url", "file:///etc/passwd"],
            ["--active", "--url", "https://user:secret@example.com/"],
            ["--active", "--url", "https://example.com:99999/"],
            ["--active", "--target=-oops"], ["--interface", "../etc"],
            ["--interface", "a\nb"], ["--tcp-port", "0"], ["--max-bytes", "0"],
            ["--target", "fe80::1"], ["--target", "fe80::1%eth0", "-i", "eth1"],
            ["--target", "2001:db8::1", "--family", "4"], ["--tailscale-netcheck"],
            ["--active", "--url", "https://[::1]/", "--family", "4"],
            ["--active", "--url", "https://example.com/", "--trace"],
        ]
        for argv in invalid:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                MODULE["parse_args"](argv)
            self.assertEqual(exc.exception.code, 2)

    def test_private_report_refuses_existing_file_and_symlink(self):
        write = MODULE["write_report"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "report")
            write(str(path), "first")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                write(str(path), "second")
            alias = Path(directory, "alias")
            alias.symlink_to(path)
            with self.assertRaises(FileExistsError):
                write(str(alias), "second")
            self.assertEqual(path.read_text(), "first")

    def test_interrupted_write_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "report")
            original = os.fdopen

            def open_failing(fd, *args, **kwargs):
                file = original(fd, *args, **kwargs)
                file.write = mock.Mock(side_effect=OSError("disk full"))
                return file

            with mock.patch.object(MODULE["os"], "fdopen", side_effect=open_failing):
                with self.assertRaises(OSError):
                    MODULE["write_report"](str(path), "data")
            self.assertFalse(path.exists())

    def test_redaction_is_consistent_across_metadata_evidence_and_findings(self):
        original = {"host": "my-laptop", "argv": ["192.0.2.1", "fe80::1%eth0"],
                    "evidence": "192.0.2.1 00:11:22:33:44:55 [2001:db8::1]:443",
                    "finding": "my-laptop 192.0.2.1 2001:0db8:0:0::1"}
        result = MODULE["Redactor"]("my-laptop").redact(original)
        text = json.dumps(result)
        for value in ("my-laptop", "192.0.2.1", "fe80::1", "2001:db8", "00:11:22:33:44:55"):
            self.assertNotIn(value, text)
        self.assertIn(result["argv"][0], result["evidence"])
        self.assertIn("%eth0", result["argv"][1])

    def test_redaction_covers_nmcli_field_values(self):
        original = "GENERAL.DEVICE:eth0\nGENERAL.HWADDR:52:54:00:12:34:56\nIP4.ADDRESS[1]:192.168.1.5/24\nIP4.DNS[1]:192.168.1.1\nIP6.ADDRESS[1]:2001:db8::5/64\nIP6.DNS[1]:fe80::1\n"
        result = MODULE["Redactor"]("host").redact(original)
        for address in ("52:54:00:12:34:56", "192.168.1.5", "192.168.1.1", "2001:db8::5", "fe80::1"):
            self.assertNotIn(address, result)
        self.assertIn("GENERAL.DEVICE:eth0\n", result)
        self.assertIn("GENERAL.HWADDR:<MAC-", result)
        self.assertIn("IP4.ADDRESS[1]:<IPv4-", result)
        self.assertIn("IP6.ADDRESS[1]:<IPv6-", result)
        self.assertTrue(MODULE["Redactor"]("host").redact("ABCD:EF01::1").startswith("<IPv6-"))
        prefixed = MODULE["Redactor"]("host").redact("peer:192.0.2.1 dns:2001:db8::1")
        self.assertNotIn("192.0.2.1", prefixed)
        self.assertNotIn("2001:db8::1", prefixed)

    def test_control_characters_cannot_change_terminal_state(self):
        text = MODULE["terminal_safe"]("wifi\x1b[2J\r\x07\u202eevil\nnext")
        for control in ("\x1b", "\r", "\x07", "\u202e"):
            self.assertNotIn(control, text)
        self.assertIn("\nnext", text)

    def test_missing_tool_cli_still_emits_parseable_partial_json(self):
        def collect(collector):
            collector.records = [record(key, status="missing", stderr="test tool is absent")
                                 for key in MODULE["CORE"]]

        stdout = io.StringIO()
        with mock.patch.dict(MODULE["main"].__globals__, {
                "metadata": lambda args: {"hostname": "fixture", "interface": None, "active": False}}), \
                mock.patch.object(MODULE["Collector"], "collect", collect), \
                mock.patch.object(MODULE["subprocess"], "Popen", side_effect=AssertionError("live command")), \
                redirect_stdout(stdout):
            code = MODULE["main"](["--format", "json", "--quiet"])
        self.assertEqual(code, 3)
        data = json.loads(stdout.getvalue())
        self.assertFalse(data["summary"]["core_complete"])
        self.assertEqual(data["schema_version"], 1)
        self.assertTrue(any(r["status"] == "missing" for r in data["evidence"]))

    def test_exit_status_distinguishes_incomplete_and_requested_failure(self):
        report = {"summary": {"core_complete": False, "findings": {"error": 0, "warning": 1}}}
        self.assertEqual(MODULE["report_exit"](report, "never"), 3)
        self.assertEqual(MODULE["report_exit"](report, "warning"), 1)
        report["summary"]["core_complete"] = True
        self.assertEqual(MODULE["report_exit"](report, "error"), 0)

    def test_python39_syntax(self):
        ast.parse(SCRIPT.read_text(), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
