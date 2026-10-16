"""CLI routing tests; no guest or source checkout required."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/nmstate-vm"


class NmstateVmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        for name in ("ssh", "testvm", "cargo", "scp"):
            stub = self.path / name
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$0 $*\" >> \"$CALL_LOG\"\n"
                "if [[ ${0##*/} == ssh ]]; then\n"
                "  cat > \"$SSH_INPUT\"\n"
                "  if [[ -n ${SSH_FAIL_ON:-} && $* == *\"$SSH_FAIL_ON\"* ]]; then exit 1; fi\n"
                "fi\n"
            )
            stub.chmod(0o755)
        self.log = self.path / "calls"
        self.script = self.path / "remote"
        self.env = dict(os.environ, PATH=f"{self.path}:{os.environ['PATH']}",
                        CALL_LOG=str(self.log), SSH_INPUT=str(self.script),
                        NMSTATE_REPO="/nonexistent/nmstate")

    def run_script(self, domain, *args):
        return subprocess.run([str(SCRIPT), *args], env=dict(self.env, TESTVM_DOMAIN=domain),
                              capture_output=True, text=True)

    def test_stream_build_and_deploy_run_in_guest(self):
        for domain, host in (("nm-c9s", "c9-vm"), ("nm-c10s", "c10-vm")):
            for action in ("build", "deploy", "test"):
                with self.subTest(domain=domain, action=action):
                    self.log.unlink(missing_ok=True)
                    self.script.unlink(missing_ok=True)
                    args = ("-k", "foo") if action == "test" else ()
                    result = self.run_script(domain, action, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    calls = self.log.read_text()
                    self.assertIn(f"testvm -d {domain} claim nmstate-vm {action}", calls)
                    self.assertIn(f"ssh {host} mkdir -p /mnt/nmstate", calls)
                    self.assertIn("mount -t virtiofs nmstate /mnt/nmstate", calls)
                    self.assertIn(f"ssh {host} cd /mnt/nmstate && CARGO_TARGET_DIR=/mnt/nmstate/rust/vm-target cargo build", calls)
                    self.assertNotIn("/root/nmstate-target", calls)
                    self.assertNotIn("\ncargo ", calls)
                    self.assertNotIn("\nscp ", calls)
                    if action == "test":
                        self.assertIn("pytest tests/integration -k foo", calls)
                    if action == "deploy":
                        self.assertIn("install -m755 /mnt/nmstate/rust/vm-target/release/nmstatectl", calls)

    def test_deps_is_not_a_command(self):
        result = self.run_script("nm-c9s", "deps")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_missing_guest_source_stops_build(self):
        self.env["SSH_FAIL_ON"] = "findmnt -rn"
        result = self.run_script("nm-c10s", "build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot use /mnt/nmstate", result.stderr)
        self.assertNotIn("cargo build", self.log.read_text())

    def test_rawhide_still_builds_on_host(self):
        result = self.run_script("nm-rawhide", "build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cargo build --release --bin nmstatectl", self.log.read_text())
        self.assertNotIn("ssh ", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
