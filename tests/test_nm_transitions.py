import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "nm-transitions"

MET = {"state_before": {}, "state_after": {}, "diff": {}, "label": True}
UNMET = {**MET, "label": False}


class SelftestExpectationsTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def selftest(self, records, expect):
        """Run cmd_selftest over one case with canned records.

        Deliberately returns nothing and swallows stdout: the verdict is the
        exit, so a caller cannot reach for the wording of a FAIL line.
        """
        cmd_selftest = self.module["cmd_selftest"]
        run = iter(records)
        case = mock.Mock(stem="case")
        case.read_text.return_value = json.dumps({"expect": expect})
        patched = {"cases": lambda names: [case], "run_once": lambda path: next(run)}
        with mock.patch.dict(cmd_selftest.__globals__, patched):
            with redirect_stdout(io.StringIO()):
                cmd_selftest(SimpleNamespace(cases=[]))

    def test_missing_expected_key_does_not_match_null(self):
        self.assertEqual(
            self.module["_unmet"]({"missing": None}, {}),
            {"missing": (None, None)},
        )

    def test_second_run_must_match_expectations(self):
        # The first run meets the expectation, so only a check of the second
        # can fail this.
        with self.assertRaises(SystemExit):
            self.selftest([MET, UNMET], {"label": True})

    def test_passes_when_both_runs_meet_expectations(self):
        self.selftest([MET, dict(MET)], {"label": True})

    def test_runs_that_disagree_fail_without_any_expectation(self):
        drifted = {**MET, "state_after": {"unstable": 1}}
        with self.assertRaises(SystemExit):
            self.selftest([MET, drifted], {})


class CaseDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def discover(self, names, *filenames):
        cases = self.module["cases"]
        with tempfile.TemporaryDirectory() as tmp:
            for name in filenames:
                (Path(tmp) / name).write_text("{}")
            with mock.patch.dict(cases.__globals__, {"CASE_DIR": Path(tmp)}):
                return [p.stem for p in cases(names)]

    def test_tui_cases_are_not_transitions(self):
        found = self.discover([], "nft-drop-peer.json", "tui-keep-selection.json")
        self.assertEqual(found, ["nft-drop-peer"])

    def test_tui_case_cannot_be_named_explicitly(self):
        with self.assertRaises(SystemExit):
            self.discover(["tui-keep-selection"], "tui-keep-selection.json")


if __name__ == "__main__":
    unittest.main()
