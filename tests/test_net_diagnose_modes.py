"""CLI tests use synthetic incident evidence and a fake sos, never host capture."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/net-diagnose"
MODULE = runpy.run_path(str(SCRIPT))


def fixture_files():
    samples = [
        ("ip -s -d link", "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode DEFAULT group default qlen 1000\n"
         "    link/ether 52:54:00:12:34:56 brd ff:ff:ff:ff:ff:ff\n"),
        ("ip -o addr", "2: eth0    inet 192.0.2.2/24 brd 192.0.2.255 scope global eth0\\       valid_lft forever preferred_lft forever\n"),
        ("ip route show table all", "default via 192.0.2.1 dev eth0 proto dhcp metric 100\n"),
        ("ip -6 route show table all", ""),
        ("ip -4 rule list", "0: from all lookup local\n32766: from all lookup main\n32767: from all lookup default\n"),
        ("ip -6 rule list", "0: from all lookup local\n32766: from all lookup main\n"),
    ]
    files, commands = {}, []
    for index, (command, output) in enumerate(samples):
        path = "sos_commands/networking/sample-" + str(index)
        files[path] = output
        words = command.split()
        commands.append({"command": words[0], "parameters": words[1:], "exec": command,
                         "filepath": path, "return_code": 0, "truncated": False,
                         "start_time": 1700000000.0, "end_time": 1700000000.1,
                         "run_time": 0.1, "tags": []})
    manifest = {"version": "4.12.1", "start_time": "2023-11-14 22:13:20", "end_time": "2023-11-14 22:14:00",
                "policy": "Fedora", "components": {"report": {"sysroot": "/", "plugins": {
                    "networking": {"timeout_hit": False, "commands": commands, "files": []}}}}}
    files["sos_reports/manifest.json"] = json.dumps(manifest)
    return files


def write_fixture(directory):
    for name, content in fixture_files().items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


class SosModeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.source = self.path / "incident"
        write_fixture(self.source)

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = MODULE["main"](list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_offline_analysis_uses_no_live_metadata_or_subprocesses(self):
        with mock.patch.object(MODULE["subprocess"], "Popen", side_effect=AssertionError("live command")), \
                mock.patch.dict(MODULE["main"].__globals__, {"metadata": mock.Mock(side_effect=AssertionError("live metadata")),
                                                          "readlink": mock.Mock(side_effect=AssertionError("live namespace"))}):
            code, stdout, stderr = self.invoke("--from-sos", str(self.source), "--format", "json")
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["metadata"]["source_kind"], "sos")
        self.assertEqual(report["metadata"]["hostname"], "unknown")
        self.assertEqual(report["metadata"]["started"], "2023-11-14 22:13:20")
        self.assertIn("analyzed_at", report["metadata"])
        self.assertTrue(report["summary"]["core_complete"])
        finding = next(item for item in report["findings"] if item["code"] == "no-default-v6")
        self.assertIn("affected system", finding["next_step"])
        self.assertNotIn("--target", finding["next_step"])

    def test_offline_summary_and_redaction_keep_source_context(self):
        code, stdout, stderr = self.invoke("--from-sos", str(self.source), "--summary", "--redact")
        self.assertEqual(code, 0, stderr)
        self.assertIn("offline analysis", stdout)
        self.assertNotIn("local inspection", stdout)
        self.assertNotIn("192.0.2.2", stdout)
        self.assertIn("eth0", stdout)
        self.assertIn("collection gaps remain unknown", stdout)

    def test_empty_source_never_falls_back_to_live_collection(self):
        for options in (["--from-sos", ""], ["--sos-output", ""],
                        ["--collector", "sos", "--sos-output", ""]):
            with self.subTest(options=options), \
                    mock.patch.object(MODULE["Collector"], "collect", side_effect=AssertionError("live collection")), \
                    self.assertRaises(SystemExit) as error:
                self.invoke(*options)
            self.assertEqual(error.exception.code, 2)

    def test_missing_selected_interface_is_scoped_to_saved_evidence(self):
        code, stdout, stderr = self.invoke("--from-sos", str(self.source), "-i", "absent0")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("saved link snapshot", stderr)

    def test_missing_core_evidence_returns_incomplete_report(self):
        manifest_path = self.source / "sos_reports/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["components"]["report"]["plugins"]["networking"]["commands"].pop()
        manifest_path.write_text(json.dumps(manifest))
        code, stdout, stderr = self.invoke("--from-sos", str(self.source), "--format", "json")
        self.assertEqual(code, 3, stderr)
        report = json.loads(stdout)
        self.assertFalse(report["summary"]["core_complete"])
        finding = next(item for item in report["findings"] if item["code"] == "core-incomplete")
        self.assertIn("original report", finding["next_step"])

    def test_modes_reject_ignored_or_live_options(self):
        for extra in (["--active"], ["--target", "192.0.2.1"], ["--logs"], ["--timeout", "2"],
                      ["--family", "both"], ["--since=-30min"], ["--act"], ["--collector", "native"]):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                MODULE["parse_args"](["--from-sos", str(self.source), *extra])
        for args in (["--collector", "sos"], ["--sos-output", "new"],
                     ["--collector", "sos", "--sos-output", "new", "--active"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                MODULE["parse_args"](args)

    def test_symlink_installation_finds_checkout_helper(self):
        link = self.path / "net-diagnose"
        link.symlink_to(SCRIPT)
        result = subprocess.run([sys.executable, "-B", str(link), "--from-sos", str(self.source), "--summary"],
                                cwd=self.path, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("offline analysis", result.stdout)

    def fake_sos(self, behavior="success"):
        executable = self.path / "sos"
        executable.write_text(
            "#!" + sys.executable + " -B\n"
            "import json, pathlib, sys, time\n"
            "behavior = " + repr(behavior) + "\n"
            "if '--version' in sys.argv:\n"
            "    if behavior == 'legacy-version':\n"
            "        print('sos: error: the following arguments are required: component', file=sys.stderr)\n"
            "        sys.exit(2)\n"
            "    print('sos version ' + ('4.7.1' if behavior == 'old-version' else '4.12.1'))\n"
            "    sys.exit(0)\n"
            "destination = pathlib.Path(sys.argv[sys.argv.index('--tmp-dir') + 1])\n"
            "if '--list-plugins' in sys.argv:\n"
            "    print('sosreport (version ' + ('4.7.2' if behavior == 'legacy-version' else '4.12.1') + ')')\n"
            "    print('The following plugins are currently enabled:')\n"
            "    print(' networking Network configuration')\n"
            "    if behavior != 'no-nm': print(' networkmanager NetworkManager configuration')\n"
            "    print('The following plugins are currently disabled:')\n"
            "    if behavior == 'no-nm': print(' networkmanager inactive NetworkManager configuration')\n"
            "    sys.exit(0)\n"
            "(destination / 'argv.json').write_text(json.dumps(sys.argv[1:]))\n"
            "(destination / 'cwd.txt').write_text(str(pathlib.Path.cwd()))\n"
            "if behavior == 'timeout': time.sleep(30)\n"
            "if behavior == 'failure':\n"
            "    (destination / 'unfinished').write_text('not postprocessed')\n"
            "    sys.exit(1)\n"
            "files = " + repr(fixture_files()) + "\n"
            "for name, content in files.items():\n"
            "    path = destination / 'sosreport-sample' / name\n"
            "    path.parent.mkdir(parents=True, exist_ok=True)\n"
            "    path.write_text(content)\n"
            "print('Saved report')\n"
        )
        executable.chmod(0o755)
        return str(executable)

    def backend(self, behavior="success", budget="10", options=()):
        executable = self.fake_sos(behavior)
        destination = self.path / "capture"
        with mock.patch.object(MODULE["os"], "geteuid", return_value=0), \
                mock.patch.object(MODULE["shutil"], "which", return_value=executable), \
                mock.patch.dict(MODULE["collect_sos"].__globals__, {"readlink": lambda path: "net:[42]"}):
            result = self.invoke("--collector", "sos", "--sos-output", str(destination),
                                 "--budget", budget, "--format", "json", *options)
        return destination, result

    def test_explicit_backend_keeps_private_artifacts_and_imports_finished_report(self):
        destination, (code, stdout, stderr) = self.backend()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertTrue(json.loads(stdout)["summary"]["core_complete"])
        self.assertEqual((destination / "cwd.txt").read_text(), "/")
        meta = json.loads(stdout)["metadata"]
        self.assertEqual(meta["service_namespace_comparison"], "same")
        self.assertEqual(meta["network_namespace"], "net:[42]")
        self.assertNotIn("Offline analysis did not execute", stdout)
        self.assertIn("Identity-cleaning state is unrecorded", stdout)
        argv = json.loads((destination / "argv.json").read_text())
        self.assertEqual(argv[argv.index("--preset") + 1], "none")
        self.assertEqual(argv[argv.index("--config-file") + 1], "/dev/null")
        self.assertEqual(argv[argv.index("--only-plugins") + 1], "networking,networkmanager")
        self.assertIn("--no-env-vars", argv)
        self.assertIn("networking.namespace-pattern=^$", argv)
        self.assertNotIn("--upload", argv)
        self.assertNotIn("--no-postproc", argv)
        self.assertLess(int(argv[argv.index("--plugin-timeout") + 1]), 8)
        self.assertLessEqual(int(argv[argv.index("--cmd-timeout") + 1]),
                             int(argv[argv.index("--plugin-timeout") + 1]))

    def test_fresh_source_hostname_is_available_for_redaction(self):
        with mock.patch.object(MODULE["socket"], "gethostname", return_value="captured-host.test"):
            _, (code, stdout, stderr) = self.backend(options=["--redact"])
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("captured-host.test", stdout)
        self.assertEqual(json.loads(stdout)["metadata"]["hostname"], "<hostname>")

    def test_incompatible_version_stops_before_plugin_discovery(self):
        destination, (code, stdout, stderr) = self.backend("old-version")
        self.assertEqual(code, 2)
        self.assertIn("sos >= 4.7.2", stderr)
        self.assertIn("collection was not started", stderr)
        self.assertFalse((destination / "sos-plugins.txt").exists())
        self.assertFalse((destination / "argv.json").exists())

    def test_sos_472_version_is_read_from_discovery_header(self):
        destination, (code, stdout, stderr) = self.backend("legacy-version")
        self.assertEqual(code, 0, stderr)
        self.assertIn("sosreport (version 4.7.2)", (destination / "sos-plugins.txt").read_text())
        self.assertTrue((destination / "argv.json").exists())

    def test_too_little_budget_does_not_start_collection(self):
        destination, (code, stdout, stderr) = self.backend(budget="1.5")
        self.assertEqual(code, 2)
        self.assertIn("increase --budget", stderr)
        self.assertIn("collection was not started", stderr)
        self.assertFalse((destination / "argv.json").exists())

    def test_invalid_report_parent_prevents_collection(self):
        with mock.patch.object(MODULE["Collector"], "collect", side_effect=AssertionError("live collection")), \
                mock.patch.dict(MODULE["main"].__globals__, {"analyze_sos": mock.Mock(side_effect=AssertionError("sos collection"))}):
            code, stdout, stderr = self.invoke("--collector", "sos", "--sos-output", str(self.path / "capture"),
                                               "-o", str(self.path / "missing" / "report"))
        self.assertEqual(code, 2)
        self.assertIn("Report parent directory", stderr)

    def test_backend_omits_unavailable_networkmanager_plugin_with_explicit_gap(self):
        destination, (code, stdout, stderr) = self.backend("no-nm")
        self.assertEqual(code, 0, stderr)
        argv = json.loads((destination / "argv.json").read_text())
        self.assertEqual(argv[argv.index("--only-plugins") + 1], "networking")
        report = json.loads(stdout)
        self.assertEqual(report["metadata"]["collection_plugins"], ["networking"])
        self.assertTrue(any("NetworkManager plugin" in note for note in report["notes"]))

    def test_failed_or_interrupted_backend_never_imports_partial_evidence(self):
        for behavior in ("failure", "timeout"):
            with self.subTest(behavior=behavior):
                destination, (code, stdout, stderr) = self.backend(behavior, budget="3")
                self.assertEqual(code, 2, stderr)
                self.assertEqual(stdout, "")
                self.assertIn("secret postprocessing may not have completed", stderr)
                self.assertTrue(destination.is_dir())
                # Each attempt needs a new output path, as in the real CLI.
                destination.rename(self.path / behavior)

    def test_backend_requires_root_and_verified_namespace_before_writing(self):
        destination = self.path / "capture"
        for uid, namespace in ((1000, "net:[42]"), (0, None)):
            with self.subTest(uid=uid), mock.patch.object(MODULE["os"], "geteuid", return_value=uid), \
                    mock.patch.dict(MODULE["collect_sos"].__globals__, {"readlink": lambda path: namespace}):
                code, _, _ = self.invoke("--collector", "sos", "--sos-output", str(destination))
            self.assertEqual(code, 2)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
