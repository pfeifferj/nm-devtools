"""Collector boundaries exercised with fake records and owned Python children."""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "bin/net-diagnose"
MODULE = runpy.run_path(str(SCRIPT))


def stopped(pid):
    try:
        return Path("/proc", str(pid), "stat").read_text().rsplit(") ", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        return True


class CollectorBoundaryTest(unittest.TestCase):
    def collector(self, *options):
        return MODULE["Collector"](MODULE["parse_args"](list(options)),
                                   {"service_queries_enabled": False, "kernel": "fixture"})

    def test_module_evidence_omits_addresses_even_when_truncated(self):
        for status in ("ok", "truncated"):
            collector = self.collector()
            loaded = {"status": status, "stdout": "rfkill 4096 0 - Live 0xffffffffc1a3c000\n",
                      "source": {"path": "/proc/modules"}}
            builtins = {"status": "ok", "stdout": "kernel/net/core/tcp_diag.ko\n"}
            with mock.patch.object(collector, "file", side_effect=[loaded, builtins]):
                modules = collector.kernel_modules()
            self.assertEqual(loaded["stdout"], "rfkill\n")
            self.assertNotIn("0xffffffff", json.dumps(loaded))
            self.assertEqual("rfkill" in modules, status == "ok")
            self.assertIn("tcp_diag", modules)

    def test_rfkill_and_daemons_are_skipped_without_confirmed_support(self):
        collector = self.collector()
        calls = []

        def command(ident, title, argv, shape=None, **kwargs):
            calls.append(argv)
            record = {"id": ident, "status": "ok", "stdout": "", "stderr": ""}
            if shape:
                record["data"] = shape()
            collector.records.append(record)
            return record

        with mock.patch.object(collector, "command", side_effect=command), \
                mock.patch.object(collector, "kernel_modules", return_value=set()), \
                mock.patch.object(MODULE["subprocess"], "Popen", side_effect=AssertionError("live command")):
            collector.collect()
        self.assertFalse(any(argv[0] in {"rfkill", "nmcli", "systemctl", "tailscale"} for argv in calls))
        self.assertEqual(next(row for row in collector.records if row["id"] == "rfkill")["status"], "skipped")

    def test_http_deadline_leaves_time_for_curl_output(self):
        collector = self.collector("--active", "--url", "https://192.0.2.1/", "--timeout", "10")
        collector.runner.deadline = time.monotonic() + 1
        with mock.patch.object(collector, "command") as command:
            collector.probes()
        argv = command.call_args.args[2]
        self.assertGreater(float(argv[argv.index("--max-time") + 1]), 0)
        self.assertLess(float(argv[argv.index("--max-time") + 1]), 0.81)
        self.assertEqual(command.call_args.kwargs["probe"]["family_selection"], "both")

    def test_unknown_ipv6_scope_is_not_probed_when_link_snapshot_is_complete(self):
        collector = self.collector("--target", "fe80::1%typo0")
        collector.records = [{"id": "links", "status": "ok", "data": [{"ifname": "eth0"}]}]
        with mock.patch.object(collector, "command", side_effect=AssertionError("unexpected route lookup")):
            collector.probes()
        self.assertEqual(collector.records[-1]["status"], "skipped")
        self.assertIn("scope is absent", collector.records[-1]["stderr"])


class ProcessBoundaryTest(unittest.TestCase):
    def test_runner_uses_explicit_working_directory(self):
        result = MODULE["Runner"]().run([sys.executable, "-B", "-c", "import os; print(os.getcwd())"], cwd="/")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stdout"].strip(), "/")

    def test_cleanup_wait_is_bounded_if_sigkill_cannot_finish_child(self):
        proc = mock.Mock(pid=123456789)
        proc.wait.side_effect = subprocess.TimeoutExpired("fixture", 1)
        with mock.patch.object(MODULE["os"], "killpg"), \
                mock.patch.object(MODULE["os"], "scandir", return_value=nullcontext([])):
            error = MODULE["stop_process_group"](proc)
        self.assertIn("did not exit after SIGKILL", error)
        self.assertLessEqual(proc.wait.call_args.kwargs["timeout"], 1)

    def test_new_process_group_in_owned_session_is_stopped(self):
        child = "import os,time; os.setpgid(0,0); print(os.getpid(),flush=True); time.sleep(30)"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-B','-c',sys.argv[1]]); time.sleep(30)"
        result = MODULE["Runner"](timeout=0.5).run([sys.executable, "-B", "-c", parent, child])
        self.assertEqual(result["status"], "timeout")
        pid = int(result["stdout"].strip())
        try:
            if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
                limit = time.monotonic() + 1
                while not stopped(pid) and time.monotonic() < limit:
                    time.sleep(0.01)
                self.assertTrue(stopped(pid))
            else:
                self.assertIn("pidfd support", result.get("cleanup_error", ""))
        finally:
            if not stopped(pid):
                os.kill(pid, signal.SIGKILL)

    def test_cleanup_never_signals_an_unrelated_session(self):
        proc = mock.Mock(pid=123456789)
        scandir = os.scandir
        with tempfile.TemporaryDirectory() as directory:
            for pid, group, session in ((10, 20, proc.pid), (11, 21, 999999999)):
                path = Path(directory, str(pid))
                path.mkdir()
                (path / "stat").write_text(str(pid) + " (name ) with punctuation) S 1 " + str(group) + " " + str(session))
            with mock.patch.object(MODULE["os"], "scandir", side_effect=lambda path: scandir(directory)), \
                    mock.patch.object(MODULE["os"], "killpg") as kill, \
                    mock.patch.object(MODULE["os"], "pidfd_open", return_value=99, create=True) as pidfd, \
                    mock.patch.object(MODULE["os"], "close"), \
                    mock.patch.object(MODULE["signal"], "pidfd_send_signal", create=True) as pidfd_signal:
                MODULE["stop_process_group"](proc)
        self.assertEqual({call.args[0] for call in kill.call_args_list}, {proc.pid})
        self.assertEqual({call.args[0] for call in pidfd.call_args_list}, {10})
        self.assertEqual({call.args for call in pidfd_signal.call_args_list}, {(99, signal.SIGKILL)})

    def test_termination_signals_unwind_runner_and_preserve_exit_signal(self):
        code = """
import runpy, sys
module = runpy.run_path(sys.argv[1])
child = 'import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)'
def work(argv):
    module['Runner'](timeout=10).run([sys.executable, '-B', '-c', child, sys.argv[2]])
    return 0
module['cli'].__globals__['main'] = work
sys.exit(module['cli']([]))
"""
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signum=signum), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory, "child-pid")
                process = subprocess.Popen([sys.executable, "-B", "-c", code, str(SCRIPT), str(marker)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                pid = None
                try:
                    limit = time.monotonic() + 3
                    while not marker.exists() and process.poll() is None and time.monotonic() < limit:
                        time.sleep(0.01)
                    self.assertTrue(marker.exists())
                    pid = int(marker.read_text())
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 128 + signum, stderr)
                    self.assertIn("interrupted", stderr)
                    self.assertTrue(stopped(pid))
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=3)
                    if pid is not None and not stopped(pid):
                        os.kill(pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
