import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]


class DoctorTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workspace doctor ")
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.checkout = self.tmp / "chosen ; $(literal)"
        self.checkout.mkdir()
        self.git = shutil.which("git")
        self.run_git(self.checkout, "init", "--initial-branch=main")
        (self.checkout / "requirements.md").write_text("# Pinned requirement\n")
        blob = self.fixture_object("blob", b"# Pinned requirement\n")
        tree = self.fixture_object("tree", b"100644 requirements.md\0" + bytes.fromhex(blob))
        self.revision = self.fixture_object("commit", f"tree {tree}\nauthor Fixture <fixture@example.invalid> 1 +0000\ncommitter Fixture <fixture@example.invalid> 1 +0000\n\nFixture baseline\n".encode())
        (self.checkout / ".git/refs/heads/main").write_text(self.revision + "\n")
        self.run_git(self.checkout, "read-tree", "HEAD")
        self.config = self.tmp / "workspace.json"
        self.data = {"schema_version": 1, "checkouts": {"selected": {"path": str(self.checkout), "required": True}},
                     "merged_specs": {"checkout": "selected", "ref": "refs/heads/main", "paths": ["requirements.md"]}}
        self.main = runpy.run_path(str(ROOT / "bin/nm-tools"))["main"]

    def fixture_object(self, kind, content):
        # Static fixture objects avoid creating commits through user hooks/signers.
        encoded = f"{kind} {len(content)}\0".encode() + content
        oid = hashlib.sha1(encoded).hexdigest()
        path = self.checkout / ".git/objects" / oid[:2] / oid[2:]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(zlib.compress(encoded))
        return oid

    def run_git(self, root, *args):
        return subprocess.run([self.git, "-C", str(root), *args], check=True, capture_output=True, text=True).stdout

    def invoke(self, data=None, config=None, env=None):
        self.config.write_text(json.dumps(self.data if data is None else data))
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"NM_DEVTOOLS": str(ROOT), **(env or {})}), contextlib.redirect_stdout(output):
            status = self.main(["doctor", "--config", str(config or self.config), "--json"])
        return status, json.loads(output.getvalue())

    def test_identity_dirty_and_untracked_paths(self):
        (self.checkout / "requirements.md").write_text("edited\n")
        (self.checkout / "untracked\nname").write_text("never indexed\n")
        status, result = self.invoke()
        self.assertEqual(status, 0)
        checkout = result["checkouts"][0]
        self.assertEqual(checkout["head"], self.revision)
        self.assertEqual(checkout["branch"], "main")
        self.assertTrue(checkout["dirty"])
        self.assertEqual(checkout["untracked"], ["untracked\nname"])
        self.assertEqual(checkout["tracked_changes"], [{"status": " M", "path": "requirements.md"}])
        source = result["merged_specs"]["sources"][0]
        self.assertEqual(source["sha256"], hashlib.sha256(b"# Pinned requirement\n").hexdigest())
        self.assertEqual(source["revision"], self.revision)
        self.assertEqual(result["status"], "unknown")

    def test_linked_worktree_is_explicit(self):
        worktree = self.tmp / "linked"
        worktree.mkdir()
        administrative = self.checkout / ".git/worktrees/linked"
        administrative.mkdir(parents=True)
        (administrative / "commondir").write_text("../..\n")
        (administrative / "HEAD").write_text(self.revision + "\n")
        (administrative / "gitdir").write_text(str(worktree / ".git") + "\n")
        (worktree / ".git").write_text("gitdir: " + str(administrative) + "\n")
        (worktree / "requirements.md").write_bytes((self.checkout / "requirements.md").read_bytes())
        self.run_git(worktree, "read-tree", "HEAD")
        self.data["checkouts"]["selected"]["path"] = str(worktree)
        self.data.pop("merged_specs")
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(result["checkouts"][0]["path"], str(worktree))
        self.assertIsNone(result["checkouts"][0]["branch"])
        self.assertEqual(result["checkouts"][0]["head"], self.revision)

    def test_no_implicit_fallback_for_missing_or_nested_checkout(self):
        self.data["checkouts"]["selected"]["path"] = str(self.tmp / "absent")
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertIsNone(result["checkouts"][0]["head"])
        (self.checkout / "nested").mkdir()
        self.data["checkouts"]["selected"]["path"] = str(self.checkout / "nested")
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertEqual(result["checkouts"][0]["status"], "missing")

    def test_required_and_optional_capabilities(self):
        self.data["capabilities"] = [
            {"id": "available", "required": True, "checkout": "selected", "files": ["requirements.md"]},
            {"id": "optional", "required": False, "checkout": "selected", "files": ["missing"]}]
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual([c["status"] for c in result["capabilities"]], ["unknown", "missing"])
        self.data["capabilities"][1]["required"] = True
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn("capability:optional", result["missing_required"])

    def test_invalid_sources_cannot_be_reported_as_pinned(self):
        self.data["merged_specs"]["expected_commit"] = "0" * 40
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertFalse(result["merged_specs"]["expected_commit_matches"])
        self.data["merged_specs"].pop("expected_commit")
        self.data["merged_specs"]["paths"] = ["missing.md"]
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertIsNone(result["merged_specs"]["sources"][0]["sha256"])

    def test_ambient_git_selection_cannot_override_explicit_checkout(self):
        status, result = self.invoke(env={"GIT_DIR": str(self.tmp / "missing"), "GIT_WORK_TREE": str(self.tmp), "GIT_INDEX_FILE": str(self.tmp / "not-an-index")})
        self.assertEqual(status, 0)
        self.assertEqual(result["checkouts"][0]["path"], str(self.checkout))
        self.assertEqual(result["checkouts"][0]["head"], self.revision)

    def test_doctor_never_runs_project_commands_or_fsmonitor(self):
        marker = self.tmp / "executed"
        monitor = self.tmp / "monitor"
        monitor.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        monitor.chmod(0o755)
        self.run_git(self.checkout, "config", "core.fsmonitor", str(monitor))
        self.data["capabilities"] = [{"id": "probe", "required": True, "checkout": "selected", "commands": ["monitor"]}]
        before = (self.checkout / ".git/index").read_bytes()
        calls = []
        original = subprocess.run

        def guarded(argv, **kwargs):
            self.assertEqual(argv[0], self.git)
            self.assertIn("core.fsmonitor=false", argv)
            calls.append(argv)
            return original(argv, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=guarded):
            status, _ = self.invoke(env={"PATH": str(self.tmp) + os.pathsep + os.environ["PATH"]})
        self.assertEqual(status, 0)
        self.assertTrue(calls)
        self.assertFalse(marker.exists())
        self.assertEqual(before, (self.checkout / ".git/index").read_bytes())

    def test_missing_partial_clone_object_does_not_trigger_fetch(self):
        blob = self.run_git(self.checkout, "rev-parse", "HEAD:requirements.md").strip()
        (self.checkout / ".git/objects" / blob[:2] / blob[2:]).unlink()
        marker = self.tmp / "fetched"
        upload_pack = self.tmp / "upload-pack"
        upload_pack.write_text(f"#!/bin/sh\n: > {shlex.quote(str(marker))}\nexit 1\n")
        upload_pack.chmod(0o755)
        self.run_git(self.checkout, "config", "remote.origin.url", str(self.checkout))
        self.run_git(self.checkout, "config", "remote.origin.uploadpack", shlex.quote(str(upload_pack)))
        self.run_git(self.checkout, "config", "remote.origin.promisor", "true")
        self.run_git(self.checkout, "config", "remote.origin.partialclonefilter", "blob:none")

        status, result = self.invoke()

        self.assertFalse(marker.exists())
        self.assertEqual(status, 1)
        self.assertEqual(result["merged_specs"]["sources"][0]["status"], "missing")

    def test_static_engine_comparison_without_loading_packages(self):
        cli = self.checkout / "cli"
        mcp = self.checkout / "mcp"
        (cli / "lib/context").mkdir(parents=True)
        (cli / "bin").mkdir()
        mcp.mkdir()
        (cli / "lib/context/runtime.js").write_text("throw Error('never load')\n")
        (cli / "bin/specdoc.js").write_text("never execute\n")
        (mcp / "server.js").write_text("never execute\n")
        (cli / "package.json").write_text(json.dumps({"name": "fixture-cli", "version": "1", "optionalDependencies": {"tree-sitter": "^1"}}))
        (mcp / "node_modules").mkdir()
        (mcp / "node_modules/specdoc-cli").symlink_to(cli, target_is_directory=True)
        for name in ("tree-sitter", "tree-sitter-rust"):
            installed = cli / "node_modules" / name
            installed.mkdir(parents=True)
            (installed / "package.json").write_text(json.dumps({"version": "1", "scripts": {"install": "never run"}}))
        self.data["specdoc"] = {"checkout": "selected", "require_mcp": True}
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertTrue(result["specdoc"]["same_engine_bytes"])
        self.assertEqual(result["specdoc"]["parsers"][0]["runtime_support"], "unknown")
        (mcp / "node_modules/specdoc-cli").unlink()
        installed = mcp / "node_modules/specdoc-cli"
        (installed / "lib/context").mkdir(parents=True)
        (installed / "package.json").write_text('{}')
        (installed / "lib/context/runtime.js").write_text("different engine\n")
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn("specdoc:shared-engine", result["missing_required"])

    def test_manifest_identity_is_checked_without_claiming_git_verification(self):
        manifest = self.tmp / "task.manifest.json"
        value = {"schemaVersion": 1, "kind": "specdoc-task-context", "sources": [], "requiredGaps": []}
        value["id"] = hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()
        manifest.write_text(json.dumps(value))
        self.data["task_manifest"] = str(manifest)
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertTrue(result["task_manifest"]["identity_matches"])
        self.assertFalse(result["task_manifest"]["content_verified"])
        value["requiredGaps"] = ["changed"]
        manifest.write_text(json.dumps(value))
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertFalse(result["task_manifest"]["identity_matches"])

    def test_relative_paths_and_unknown_config_fields_fail(self):
        for data in ({**self.data, "commands": ["never execute"]},
                     {**self.data, "checkouts": {"selected": {"path": "../guess", "required": True}}}):
            status, result = self.invoke(data)
            self.assertEqual(status, 2)
            self.assertIn("error", result)
        status, _ = self.invoke(config=Path("relative.json"))
        self.assertEqual(status, 2)


if __name__ == "__main__":
    unittest.main()
