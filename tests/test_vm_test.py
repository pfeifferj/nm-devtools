import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

VM_TEST = Path(__file__).parents[1] / "bin" / "vm-test"

# The guest is the local machine: ssh and scp act on local paths under the
# test's temporary directory, and testvm only logs what it was asked to do.
FAKES = {
    "testvm": '#!/bin/sh\necho "$*" >> "$FAKE_LOG"\n[ "$1" != host ] || echo guest\n',
    "ssh": '#!/bin/sh\nwhile [ "$1" != guest ]; do shift; done\nshift\nexec sh -c "$*"\n',
    "scp": '#!/bin/sh\ncp -p "$3" "${4#guest:}"\n[ -z "$FAKE_CORRUPT" ] || echo x >> "${4#guest:}"\n',
    "timedatectl": "#!/bin/sh\n",
    "chronyc": "#!/bin/sh\n",
}


class VmTestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        fakes = self.tmp / "fakes"
        fakes.mkdir()
        for name, body in FAKES.items():
            (fakes / name).write_text(body)
            (fakes / name).chmod(0o755)
        # vm-test runs the testvm in its own directory
        self.vm_test = fakes / "vm-test"
        shutil.copy(VM_TEST, self.vm_test)
        self.log = self.tmp / "testvm.log"
        self.out = self.tmp / "evidence"
        self.guest = self.tmp / "guest"
        self.exe = self.tmp / "probe"
        self.exe.write_text(
            f'#!/bin/sh\necho ran\necho done > {self.guest}/result\nexit 3\n'
        )
        self.exe.chmod(0o755)
        self.env = dict(
            os.environ,
            PATH=f"{fakes}:{os.environ['PATH']}",
            FAKE_LOG=str(self.log),
            TESTVM_DOMAIN="dom",
        )

    def run_vm_test(self, *args, **env):
        return subprocess.run(
            [self.vm_test, "-o", str(self.out), *args],
            env={**self.env, **env},
            capture_output=True,
            text=True,
        )

    def meta(self):
        return dict(
            line.split("=", 1) for line in (self.out / "meta").read_text().splitlines()
        )

    def calls(self):
        return self.log.read_text().splitlines()

    def test_runs_and_collects_evidence(self):
        dst = self.guest / "bin" / "probe"
        r = self.run_vm_test(
            "-s", "none",
            "-f", f"{self.exe}:{dst}",
            "-e", str(self.guest / "result"),
            "--", str(dst),
        )
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.meta()["exit"], "3")
        self.assertEqual(self.meta()["domain"], "dom")
        self.assertEqual((self.out / "stdout").read_text(), "ran\n")
        want = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.assertIn(f"{want}  {dst}", (self.out / "hashes.sha256").read_text())
        self.assertTrue(os.access(dst, os.X_OK))
        collected = self.out / "files" / str(self.guest / "result").lstrip("/")
        self.assertEqual(collected.read_text(), "done\n")
        self.assertFalse(any(c.startswith("rollback") for c in self.calls()))

    def test_hash_mismatch_stops_before_the_command(self):
        dst = self.guest / "probe"
        self.guest.mkdir()
        r = self.run_vm_test("-f", f"{self.exe}:{dst}", "--", str(dst), FAKE_CORRUPT="1")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.meta()["exit"], "setup-failed")
        self.assertFalse((self.guest / "result").exists())
        rollbacks = [c for c in self.calls() if c.startswith("rollback")]
        self.assertEqual(rollbacks, ["rollback baseline-known-good"] * 2)

    def test_failed_deploy_stops_before_the_command(self):
        self.guest.mkdir()
        r = self.run_vm_test("-s", "none", "-f", f"{self.tmp}/missing:{self.guest}/x",
                             "--", str(self.exe))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.meta()["exit"], "setup-failed")
        self.assertFalse((self.guest / "result").exists())

    def test_keep_skips_the_final_rollback(self):
        self.guest.mkdir()
        r = self.run_vm_test("-s", "snap", "-k", "--", str(self.exe))
        self.assertEqual(r.returncode, 3)
        rollbacks = [c for c in self.calls() if c.startswith("rollback")]
        self.assertEqual(rollbacks, ["rollback snap"])
        self.assertIn("claim", self.calls()[1])


if __name__ == "__main__":
    unittest.main()
