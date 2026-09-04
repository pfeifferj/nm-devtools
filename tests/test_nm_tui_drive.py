import io
import runpy
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "nm-tui-drive"

A = ["11111111-1111-1111-1111-111111111111"]
B = ["33333333-3333-3333-3333-333333333333"]


class RunCaseTest(unittest.TestCase):
    """The verdict is the boolean, so a caller cannot key off the wording."""

    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def run_case(self, *passes):
        """Drive run_case with canned pass results instead of a real nmtui."""
        fake = mock.Mock(side_effect=list(passes))
        globs = self.module["run_case"].__globals__
        original = globs["run_pass"]
        globs["run_pass"] = fake
        try:
            ok, _ = self.module["run_case"]({}, None, None)
        finally:
            globs["run_pass"] = original
        return ok, fake

    def test_same_profile_both_passes_passes(self):
        ok, _ = self.run_case(B, B)
        self.assertTrue(ok)

    def test_selection_moving_to_another_profile_fails(self):
        ok, _ = self.run_case(B, A)
        self.assertFalse(ok)

    def test_reference_pass_without_activation_fails(self):
        ok, _ = self.run_case([], B)
        self.assertFalse(ok)

    def test_injected_pass_without_activation_fails(self):
        ok, _ = self.run_case(B, [])
        self.assertFalse(ok)

    def test_a_dead_reference_pass_skips_the_injected_one(self):
        _, fake = self.run_case([], B)
        self.assertEqual(fake.call_count, 1)

    def test_the_injected_pass_is_the_one_that_injects(self):
        _, fake = self.run_case(B, B)
        self.assertEqual([c.kwargs["inject"] for c in fake.call_args_list], [False, True])


class SelftestTest(unittest.TestCase):
    """selftest is the harness's own falsifier, so its verdict is the exit code."""

    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def selftest(self, *passes):
        cmd = self.module["cmd_selftest"]
        patched = {
            "_nm_src": lambda: None,
            "_resolve_nmtui": lambda args, nm_src: None,
            "load_cases": lambda names: {"tui-x": {"keys": ["down"]}},
            "run_pass": mock.Mock(side_effect=list(passes)),
        }
        with mock.patch.dict(cmd.__globals__, patched):
            with redirect_stdout(io.StringIO()):
                return cmd(SimpleNamespace())

    def test_moving_activation_passes(self):
        self.assertEqual(self.selftest(B, A), 0)

    def test_activation_that_ignores_the_keystrokes_fails(self):
        self.assertEqual(self.selftest(A, A), 1)

    def test_a_run_reaching_no_profile_fails(self):
        self.assertEqual(self.selftest(B, []), 1)

    def test_needs_a_case_with_keystrokes(self):
        cmd = self.module["cmd_selftest"]
        patched = {
            "_nm_src": lambda: None,
            "_resolve_nmtui": lambda args, nm_src: None,
            "load_cases": lambda names: {"tui-x": {"keys": []}},
        }
        with mock.patch.dict(cmd.__globals__, patched):
            with self.assertRaises(SystemExit):
                cmd(SimpleNamespace())


class ResolveNmtuiTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def test_rejects_a_binary_not_named_nmtui(self):
        args = SimpleNamespace(nmtui="/tmp/nmtui-unfixed")
        with mock.patch("os.access", return_value=True):
            with self.assertRaises(SystemExit):
                self.module["_resolve_nmtui"](args, Path("/nm"))

    def test_accepts_a_binary_named_nmtui(self):
        args = SimpleNamespace(nmtui="/tmp/unfixed/nmtui")
        with mock.patch("os.access", return_value=True):
            self.assertEqual(
                self.module["_resolve_nmtui"](args, Path("/nm")), "/tmp/unfixed/nmtui"
            )


if __name__ == "__main__":
    unittest.main()
