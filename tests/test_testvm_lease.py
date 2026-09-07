import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

TESTVM = Path(__file__).parents[1] / "bin" / "testvm"


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A fake virsh so a gated command can prove it never got past the claim.
        fake = Path(self.tmp.name, "virsh")
        fake.write_text("#!/bin/sh\ntouch \"$VIRSH_MARK\"\n")
        fake.chmod(0o755)
        self.mark = Path(self.tmp.name, "virsh-ran")

    def run_as(self, owner, *args, **overrides):
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("TESTVM_", "CLAUDE_"))
        }
        env.update(
            XDG_RUNTIME_DIR=self.tmp.name,
            CLAUDE_PID=str(os.getpid()),
            PATH=f"{self.tmp.name}:{os.environ['PATH']}",
            VIRSH_MARK=str(self.mark),
        )
        if owner is not None:
            env["TESTVM_OWNER"] = owner
        env.update(overrides)
        return subprocess.run(
            [TESTVM, "-d", "dom", *args], env=env, capture_output=True, text=True
        )

    def lease(self):
        return dict(
            line.split("=", 1)
            for line in Path(self.tmp.name, "testvm", "dom.lease").read_text().splitlines()
        )

    def test_second_session_is_refused(self):
        self.assertEqual(self.run_as("a", "claim", "demo").returncode, 0)
        self.assertEqual(self.run_as("b", "claim").returncode, 1)
        self.assertEqual(self.lease()["owner"], "a")
        self.assertEqual(self.lease()["reason"], "demo")

    def test_owner_reclaims_and_releases(self):
        self.run_as("a", "claim", "first")
        lease = Path(self.tmp.name, "testvm", "dom.lease")
        lease.write_text(re.sub(r"since=\d+", "since=1", lease.read_text()))
        self.assertEqual(self.run_as("a", "claim", "second").returncode, 0)
        self.assertEqual(self.lease()["reason"], "second")
        self.assertEqual(self.lease()["since"], "1")
        self.assertEqual(self.run_as("b", "release").returncode, 1)
        self.assertEqual(self.lease()["owner"], "a")
        self.assertEqual(self.run_as("a", "release").returncode, 0)
        self.assertEqual(self.run_as("b", "claim").returncode, 0)

    def test_dead_session_is_taken_over(self):
        dead = subprocess.run(["sh", "-c", "echo $$"], capture_output=True, text=True)
        self.run_as("a", "claim", CLAUDE_PID=dead.stdout.strip())
        self.assertEqual(self.run_as("b", "claim").returncode, 0)
        self.assertEqual(self.lease()["owner"], "b")

    def test_force_overrides(self):
        self.run_as("a", "claim")
        self.assertEqual(self.run_as("b", "claim", TESTVM_FORCE="1").returncode, 0)
        self.assertEqual(self.lease()["owner"], "b")
        self.assertEqual(self.run_as("a", "release", TESTVM_FORCE="1").returncode, 0)
        self.assertEqual(self.run_as("a", "claim").returncode, 0)

    def test_gated_commands_stop_before_virsh(self):
        self.run_as("a", "claim")
        for cmd in ("up", "down", "rollback", "snapshot"):
            self.assertEqual(self.run_as("b", cmd, "x").returncode, 1)
            self.assertFalse(self.mark.exists())
        self.run_as("b", "snapshots")
        self.assertTrue(self.mark.exists())

    def test_owner_defaults_to_session_id(self):
        self.run_as(None, "claim", CLAUDE_CODE_SESSION_ID="sess")
        self.assertEqual(self.lease()["owner"], "sess")

    def test_reason_stays_on_one_line(self):
        self.run_as("a", "claim", "two\nlines")
        self.assertEqual(self.lease()["reason"], "two lines")


if __name__ == "__main__":
    unittest.main()
