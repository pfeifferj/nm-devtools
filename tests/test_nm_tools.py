import contextlib
import copy
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/nm-tools"


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="nm tools ")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.netfyr = self.tmp / "netfyr ; $(literal)"
        self.netfyr.mkdir()
        self.catalog = json.loads((ROOT / "tools/oracles.json").read_text())
        for entry in self.catalog["oracles"]:
            if entry["repo"] == "netfyr":
                for name in entry["files"]:
                    path = self.netfyr / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
        self.module = runpy.run_path(str(SCRIPT))

    def invoke(self, *args, env=None):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"NM_DEVTOOLS": str(ROOT), "PATH": "", **(env or {})}, clear=True), contextlib.redirect_stdout(output):
            status = self.module["main"](["--netfyr", str(self.netfyr), *args, "--json"])
        return status, json.loads(output.getvalue())

    def test_intent_search_retains_unavailable_tools(self):
        status, data = self.invoke("search", "executor cancellation")
        self.assertEqual(status, 0)
        self.assertEqual([entry["id"] for entry in data["oracles"]], ["executor.saved", "executor.generated"])
        self.assertTrue(all(entry["readiness"] == "missing" for entry in data["oracles"]))

    def test_discovery_does_not_execute_commands(self):
        fakebin = self.tmp / "bin"
        fakebin.mkdir()
        marker = self.tmp / "invoked"
        for name in ("make", "cargo", "quint", "ssh", "virsh", "ip", "unshare", "python3", "bash", "nft", "tc", "nsenter", "nmstatectl"):
            path = fakebin / name
            path.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
            path.chmod(0o755)
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("discovery executed a process")):
            for args in (("list",), ("search", "kernel address"), ("describe", "executor.generated"), ("check", "executor.generated")):
                status, data = self.invoke(*args, env={"PATH": str(fakebin)})
                self.assertEqual(status, 0)
                self.assertTrue(data["oracles"])
        self.assertFalse(marker.exists())
        self.assertEqual(data["oracles"][0]["readiness"], "unknown")

    def test_resolved_paths_and_values_are_literal(self):
        value = "-case ; $(touch NEVER) {repo}"
        output = str(self.tmp / "out ; literal")
        status, data = self.invoke("describe", "transitions.measure", "--param", f"case={value}", "--param", f"output={output}")
        self.assertEqual(status, 0)
        invocation = data["oracles"][0]["invocation"]
        self.assertEqual(invocation["argv"][-2:], ["--", value])
        self.assertIn(output, invocation["argv"])
        self.assertEqual(invocation["cwd"], str(ROOT))

    def test_saved_command_has_no_quint_requirement(self):
        _, data = self.invoke("describe", "executor.saved")
        entry = data["oracles"][0]
        self.assertEqual(entry["invocation"]["cwd"], str(self.netfyr))
        self.assertEqual(entry["invocation"]["argv"], ["make", "test-quint-saved"])
        self.assertNotIn("quint", [command["name"] for command in entry["commands"]])

    def test_generated_parameters_and_version_requirement(self):
        _, data = self.invoke("describe", "executor.generated", "--param", "seed=42", "--param", "samples=64", "--param", "traces=32")
        entry = data["oracles"][0]
        self.assertEqual(entry["invocation"]["environment"]["QUINT_SEED"], "42")
        version = next(check for check in entry["prerequisites"] if check["kind"] == "version")
        self.assertEqual(version, {"kind": "version", "name": "quint", "required": "0.33.0", "status": "unknown"})

    def test_bad_parameters_are_errors(self):
        for params in (("bogus=x",), ("samples=0",), ("seed=-1",), ("samples=1", "traces=2"), ("seed=1", "seed=2")):
            with self.subTest(params=params):
                args = [arg for value in params for arg in ("--param", value)]
                status, data = self.invoke("describe", "executor.generated", *args)
                self.assertEqual(status, 2)
                self.assertIn("error", data)

    def test_stale_checkout_reports_missing_feature(self):
        (self.netfyr / "scripts/quint-results.py").unlink()
        status, data = self.invoke("check", "executor.saved")
        self.assertEqual(status, 1)
        self.assertIn("scripts/quint-results.py", [p.get("name") for p in data["oracles"][0]["prerequisites"] if p["status"] == "missing"])

    def test_absent_checkout_does_not_fall_back(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"NM_DEVTOOLS": str(ROOT), "PATH": ""}, clear=True), contextlib.redirect_stdout(output):
            status = self.module["main"](["describe", "executor.saved", "--json"])
        entry = json.loads(output.getvalue())["oracles"][0]
        self.assertEqual(status, 0)
        self.assertIsNone(entry["checkout"])
        self.assertIn("repo", entry["unresolved_inputs"])

    def test_case_override_is_forwarded_to_both_commands(self):
        case_root = self.tmp / "alternate cases"
        case_root.mkdir()
        _, data = self.invoke("describe", "transitions.selftest", env={"NM_TRANSITIONS_CASES": str(case_root)})
        entry = data["oracles"][0]
        self.assertEqual(entry["case_root"], str(case_root))
        for name in ("invocation", "case_discovery"):
            self.assertEqual(entry[name]["environment"]["NM_TRANSITIONS_CASES"], str(case_root))

    def test_vm_invocation_is_guest_only_and_initially_unbound(self):
        _, data = self.invoke("describe", "backend.acceptance")
        entry = data["oracles"][0]
        self.assertEqual(entry["context"], "disposable_vm")
        self.assertFalse(entry["invocation_bound"])
        self.assertEqual(entry["unresolved_inputs"], ["guest_checkout", "guest_probe", "guest_results"])
        status, _ = self.invoke("describe", "backend.acceptance", "--param", "guest_probe=relative")
        self.assertEqual(status, 2)

    def test_unknown_id_fails_and_empty_search_is_explicit(self):
        status, data = self.invoke("describe", "missing")
        self.assertEqual(status, 2)
        self.assertIn("unknown oracle", data["error"])
        status, data = self.invoke("search", "not-an-oracle")
        self.assertEqual((status, data["oracles"]), (0, []))

    def test_malformed_catalogs_fail_explicitly(self):
        variants = []
        for field, value in (("schema_version", 99), ("schema_version", True), ("oracles", [])):
            variants.append({**self.catalog, field: value})
        duplicated = copy.deepcopy(self.catalog)
        duplicated["oracles"].append(duplicated["oracles"][0])
        variants.append(duplicated)
        for field, value in (("repo", "other"), ("files", ["../escape"]), ("invocation", {"argv": "shell"}), ("commands", "cargo"), ("bogus", True)):
            catalog = copy.deepcopy(self.catalog)
            catalog["oracles"][0][field] = value
            variants.append(catalog)
        wrong_type = copy.deepcopy(self.catalog)
        wrong_type["oracles"][1]["parameters"]["seed"]["type"] = []
        variants.append(wrong_type)
        wrong_runner = copy.deepcopy(self.catalog)
        wrong_runner["oracles"][0]["evidence"]["task_context"]["runner_support"] = []
        variants.append(wrong_runner)
        path = self.tmp / "catalog.json"
        for catalog in variants:
            with self.subTest(catalog=catalog):
                path.write_text(json.dumps(catalog))
                with self.assertRaises(ValueError):
                    self.module["load_catalog"](path)
        path.write_text('{"schema_version":1,"schema_version":1,"oracles":[]}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.module["load_catalog"](path)

    def test_cli_finds_its_catalog_from_unrelated_directory_and_symlink(self):
        link = self.tmp / "tool link"
        link.symlink_to(SCRIPT)
        env = {**os.environ, "PATH": ""}
        env.pop("NM_DEVTOOLS", None)
        result = subprocess.run([sys.executable, str(link), "list", "--json"], cwd=self.tmp, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["oracles"]), 5)

    def test_explicit_nm_devtools_checkout_is_authoritative(self):
        checkout = self.tmp / "alternate tools"
        (checkout / "tools").mkdir(parents=True)
        catalog = copy.deepcopy(self.catalog)
        catalog["oracles"] = [catalog["oracles"][0]]
        (checkout / "tools/oracles.json").write_text(json.dumps(catalog))
        status, data = self.invoke("list", env={"NM_DEVTOOLS": str(checkout)})
        self.assertEqual(status, 0)
        self.assertEqual(len(data["oracles"]), 1)
        self.assertEqual(data["catalog"], str(checkout / "tools/oracles.json"))


if __name__ == "__main__":
    unittest.main()
