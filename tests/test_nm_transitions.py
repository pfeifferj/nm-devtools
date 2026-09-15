import io
import json
import runpy
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "bin" / "nm-transitions"

PROBES = (
    "reachable_before", "reachable_after",
    "established_before", "established_after",
    "bulk_before", "bulk_after",
)
MET = {
    "state_before": {}, "state_after": {}, "diff": {}, "label": True,
    "exit_code": 0, **dict.fromkeys(PROBES, True),
}
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

    def test_probe_and_exit_drift_fail_without_expectations(self):
        for field in (*PROBES, "exit_code"):
            with self.subTest(field=field), self.assertRaises(SystemExit):
                drifted = {**MET, field: 1 if field == "exit_code" else False}
                self.selftest([MET, drifted], {})

    def test_unmeasured_probe_does_not_match_a_failed_probe(self):
        with self.assertRaises(SystemExit):
            self.selftest(
                [{**MET, "established_before": None},
                 {**MET, "established_before": False}],
                {},
            )

    def test_missing_required_fields_fail_even_when_both_records_match(self):
        with self.assertRaises(SystemExit):
            self.selftest([{}, {}], {})

    def test_worker_failure_does_not_skip_remaining_cases(self):
        command = self.module["cmd_selftest"]
        broken, healthy = mock.Mock(stem="broken"), mock.Mock(stem="healthy")
        for case in (broken, healthy):
            case.read_text.return_value = "{}"

        def run(case):
            if case is broken:
                raise RuntimeError("worker failed")
            return dict(MET)

        run_once = mock.Mock(side_effect=run)
        with mock.patch.dict(command.__globals__, {
            "cases": lambda names: [broken, healthy], "run_once": run_once,
        }), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                command(SimpleNamespace(cases=[]))
        self.assertEqual(run_once.call_args_list.count(mock.call(healthy)), 2)


class CommandExecutionTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def test_shell_operators_are_literal_arguments(self):
        code = "import json, sys; print(json.dumps(sys.argv[1:]))"
        command = (
            f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} "
            '; printf substituted "$(printf expanded)" "`printf expanded`"'
        )
        result = self.module["sh"](command, check=True)
        self.assertEqual(json.loads(result.stdout), [
            ";", "printf", "substituted", "$(printf expanded)", "`printf expanded`",
        ])

    def test_nft_quoted_braces_are_one_argument(self):
        with mock.patch.object(self.module["subprocess"], "run") as run:
            self.module["sh"](
                "nft add chain inet f out '{ type filter hook output priority 0; policy drop; }'"
            )
        self.assertEqual(run.call_args.args[0], [
            "nft", "add", "chain", "inet", "f", "out",
            "{ type filter hook output priority 0; policy drop; }",
        ])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_argument_list_preserves_spaces(self):
        code = "import json, sys; print(json.dumps(sys.argv[1:]))"
        result = self.module["sh"]([sys.executable, "-c", code, "two words"], check=True)
        self.assertEqual(json.loads(result.stdout), ["two words"])

    def test_concurrent_nmstate_actions_use_private_files_and_remove_them(self):
        action = self.module["run_action"]
        barrier = threading.Barrier(2)
        paths = []
        observed = []

        def execute(command, **kwargs):
            argv = shlex.split(command) if isinstance(command, str) else command
            path = Path(argv[-1])
            paths.append(path)
            barrier.wait(timeout=5)
            observed.append(json.loads(path.read_text()))
            return SimpleNamespace(returncode=0, stderr="")

        specs = [{"interfaces": [{"name": name}]} for name in ("first", "second")]
        with mock.patch.dict(action.__globals__, {"sh": execute}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(action, {"kind": "nmstate", "spec": spec}) for spec in specs]
                for job in jobs:
                    self.assertEqual(job.result(timeout=10), (0, ""))
        self.assertEqual(len(set(paths)), 2)
        self.assertCountEqual(observed, specs)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_nmstate_action_removes_private_file_after_execution_error(self):
        action = self.module["run_action"]
        paths = []

        def execute(command, **kwargs):
            argv = shlex.split(command) if isinstance(command, str) else command
            paths.append(Path(argv[-1]))
            self.assertTrue(paths[-1].is_file())
            raise RuntimeError("execution failed")

        with mock.patch.dict(action.__globals__, {"sh": execute}):
            with self.assertRaisesRegex(RuntimeError, "execution failed"):
                action({"kind": "nmstate", "spec": {"interfaces": []}})
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())


class CaptureFailureTest(unittest.TestCase):
    SOURCES = {
        "nmstatectl -q show -k --json": {},
        "ip -j route show table all": [],
        "nft -j list ruleset": {},
        "tc -j qdisc show": [],
        "ip -j addr show": [],
    }

    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def read_source(self, source, *, stdout="", returncode=0):
        def execute(command, check=False, **kwargs):
            code = returncode if command == source else 0
            output = stdout if command == source else json.dumps(self.SOURCES[command])
            if check and code:
                raise subprocess.CalledProcessError(code, command, output=output)
            return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

        reader = self.module["_dad_pending" if source == "ip -j addr show" else "capture"]
        with mock.patch.dict(reader.__globals__, {"sh": execute}):
            return reader()

    def test_failed_capture_or_dad_command_cannot_become_empty_state(self):
        for source in self.SOURCES:
            with self.subTest(source=source), self.assertRaises(subprocess.CalledProcessError):
                self.read_source(source, returncode=1)

    def test_invalid_or_empty_capture_or_dad_json_is_rejected(self):
        for source in self.SOURCES:
            for output in ("", "invalid json"):
                with self.subTest(source=source, output=output), self.assertRaises(json.JSONDecodeError):
                    self.read_source(source, stdout=output)


class PeerStartupTest(unittest.TestCase):
    PEER = {"subject_ip": "10.5.5.1/24", "peer_ip": "10.5.5.2/24"}

    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))
        self.proc = mock.Mock(pid=4321)
        self.proc.poll.return_value = None

    def start(self, stat, command):
        start_peer = self.module["start_peer"]
        with mock.patch.object(self.module["os"], "stat", side_effect=stat), \
                mock.patch.object(self.module["subprocess"], "Popen", return_value=self.proc), \
                mock.patch.object(self.module["time"], "sleep"), \
                mock.patch.dict(start_peer.__globals__, {"sh": command}):
            return start_peer(self.PEER)

    def test_veth_moves_only_after_child_enters_a_distinct_namespace(self):
        child_polls = 0
        ready = False
        moves = []

        def stat(path):
            nonlocal child_polls, ready
            if str(path) == f"/proc/{self.proc.pid}/ns/net":
                child_polls += 1
                ready = child_polls >= 3
                return SimpleNamespace(st_ino=2 if ready else 1)
            self.assertEqual(str(path), "/proc/self/ns/net")
            return SimpleNamespace(st_ino=1)

        def execute(command, **kwargs):
            argv = shlex.split(command) if isinstance(command, str) else command
            if argv[:4] == ["ip", "link", "set", "veth1"]:
                self.assertTrue(ready, "veth moved while the child still shared the subject namespace")
                moves.append((argv, kwargs.get("check", False)))
            return SimpleNamespace(returncode=0, stderr="")

        self.assertIs(self.start(stat, execute), self.proc)
        self.assertEqual(moves, [(["ip", "link", "set", "veth1", "netns", "4321"], True)])
        self.proc.kill.assert_not_called()
        self.proc.wait.assert_not_called()

    def test_child_exit_before_namespace_creation_is_reaped(self):
        self.proc.poll.return_value = 1
        command = mock.Mock(return_value=SimpleNamespace(returncode=0, stderr=""))
        with self.assertRaises(RuntimeError):
            self.start(lambda path: SimpleNamespace(st_ino=1), command)
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once()
        for call in command.call_args_list:
            argv = shlex.split(call.args[0]) if isinstance(call.args[0], str) else call.args[0]
            self.assertNotEqual(argv[:4], ["ip", "link", "set", "veth1"])

    def test_namespace_readiness_timeout_kills_and_reaps_peer(self):
        command = mock.Mock(return_value=SimpleNamespace(returncode=0, stderr=""))
        with self.assertRaises(RuntimeError):
            self.start(lambda path: SimpleNamespace(st_ino=1), command)
        command.assert_not_called()
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once()

    def test_setup_failure_kills_and_reaps_peer(self):
        def stat(path):
            return SimpleNamespace(st_ino=1 if str(path) == "/proc/self/ns/net" else 2)

        error = subprocess.CalledProcessError(1, "ip link add")
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.start(stat, mock.Mock(side_effect=error))
        self.assertIs(raised.exception, error)
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once()


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def run_worker(self, case, action_error=None):
        worker = self.module["worker"]
        events = []
        peer = SimpleNamespace(pid=321, kill=mock.Mock(), wait=mock.Mock())
        echo = SimpleNamespace(kill=mock.Mock(), wait=mock.Mock())
        flow = SimpleNamespace(close=mock.Mock())

        def mark(event, result):
            def call(*args):
                events.append(event)
                return result
            return call

        def command(cmd, **kwargs):
            events.append(shlex.split(cmd) if isinstance(cmd, str) else cmd)
            return SimpleNamespace(returncode=0, stderr="")

        patched = {
            "start_peer": lambda config: peer,
            "start_echo": lambda pid: echo,
            "wait_echo": lambda config: True,
            "sh": command,
            "open_flow": mark("open_flow", flow),
            "settled_capture": mark("capture", {}),
            "reachable": mark("reachable", True),
            "flow_alive": mark("established", True),
            "bulk_ok": mark("bulk", True),
            "run_action": mark("action", (0, "")),
            "versions": lambda: {},
        }
        if action_error is not None:
            patched["run_action"] = mock.Mock(side_effect=action_error)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "case.json")
            path.write_text(json.dumps(case))
            try:
                with mock.patch.dict(worker.__globals__, patched), redirect_stdout(io.StringIO()) as out:
                    worker(path)
            finally:
                if case.get("peer"):
                    for proc in (peer, echo):
                        proc.kill.assert_called_once()
                        proc.wait.assert_called_once()
        return json.loads(out.getvalue()), events

    def test_peer_setup_precedes_flow_probes_and_action_and_preserves_metadata(self):
        case = {
            "name": "link-peer-context", "peer": {"probe": "10.5.5.2"},
            "setup": ["ip link set veth0 mtu 1450"],
            "peer_setup": ["tc qdisc add dev veth1 root netem loss 100%"],
            "action": {"kind": "shell", "spec": "ip link set veth0 up"},
            "slice": "link-up", "origin": "generated", "ceiling": True,
            "split": "holdout",
        }
        record, events = self.run_worker(case)
        local = events.index(shlex.split(case["setup"][0]))
        remote = events.index(["nsenter", "-t", "321", "-n", *shlex.split(case["peer_setup"][0])])
        self.assertLess(local, remote)
        for event in ("open_flow", "capture", "reachable", "established", "bulk", "action"):
            self.assertLess(remote, events.index(event))
        self.assertLess(events.index("open_flow"), events.index("action"))
        for field in ("slice", "origin", "ceiling", "split", "setup", "peer_setup", "action"):
            self.assertEqual(record[field], case[field])

    def test_metadata_defaults(self):
        record, _ = self.run_worker({
            "name": "route-example", "action": {"kind": "shell", "spec": "true"},
        })
        self.assertEqual(record["slice"], "route")
        self.assertEqual(record["origin"], "handwritten")
        self.assertEqual(record["split"], "dev")
        self.assertIs(record["ceiling"], False)
        self.assertEqual(record["peer_setup"], [])

    def test_peer_setup_requires_a_peer(self):
        with self.assertRaisesRegex(RuntimeError, "peer_setup needs a peer"):
            self.run_worker({
                "name": "invalid", "peer_setup": ["ip link set lo up"],
                "action": {"kind": "shell", "spec": "true"},
            })

    def test_action_error_still_kills_and_reaps_peer_processes(self):
        with self.assertRaisesRegex(RuntimeError, "action failed"):
            self.run_worker({
                "name": "broken", "peer": {"probe": "10.5.5.2"},
                "action": {"kind": "shell", "spec": "true"},
            }, action_error=RuntimeError("action failed"))


class WorkerProcessTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))

    def test_interruption_and_timeout_kill_the_worker_group_and_reap_it(self):
        timeout = subprocess.TimeoutExpired("worker", 300)
        for error, expected in ((KeyboardInterrupt(), KeyboardInterrupt),
                                (timeout, RuntimeError)):
            with self.subTest(error=type(error).__name__):
                proc = mock.Mock(pid=321)
                proc.communicate.side_effect = [error, ("", "")]
                with mock.patch.object(self.module["subprocess"], "Popen", return_value=proc), \
                     mock.patch.object(self.module["os"], "killpg") as kill:
                    with self.assertRaises(expected):
                        self.module["run_once"](Path("case.json"))
                kill.assert_called_once_with(321, self.module["signal"].SIGKILL)
                self.assertEqual(proc.communicate.call_args_list,
                                 [mock.call(timeout=300), mock.call()])

    def test_already_exited_group_does_not_hide_interruption(self):
        proc = mock.Mock(pid=321)
        proc.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        with mock.patch.object(self.module["subprocess"], "Popen", return_value=proc), \
             mock.patch.object(self.module["os"], "killpg", side_effect=ProcessLookupError):
            with self.assertRaises(KeyboardInterrupt):
                self.module["run_once"](Path("case.json"))
        self.assertEqual(proc.communicate.call_count, 2)


class CorpusOutputTest(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(SCRIPT))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outdir = Path(self.tmp.name)
        self.case = Path("case.json")
        self.dest = self.outdir / "case.jsonl"
        self.original = '{"previous": true}\n'
        self.dest.write_text(self.original)

    def run_cases(self, run_once, repeat=2):
        command = self.module["cmd_run"]
        with mock.patch.dict(command.__globals__, {
            "cases": lambda names: [self.case], "run_once": run_once,
        }), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            command(SimpleNamespace(cases=[], output=self.outdir, repeat=repeat))

    def assert_original_preserved(self):
        self.assertEqual(self.dest.read_text(), self.original)
        self.assertEqual(list(self.outdir.iterdir()), [self.dest])

    def test_success_replaces_previous_records_after_all_repetitions(self):
        records = [{"sample": 1}, {"sample": 2}]
        pending = iter(records)

        def run_once(path):
            self.assertEqual(self.dest.read_text(), self.original)
            return next(pending)

        self.run_cases(run_once)
        self.assertEqual([json.loads(line) for line in self.dest.read_text().splitlines()], records)
        self.assertEqual(list(self.outdir.iterdir()), [self.dest])

    def test_failed_repetition_preserves_previous_records(self):
        for successful in (0, 1):
            with self.subTest(successful=successful):
                results = [dict(MET)] * successful + [RuntimeError("worker failed")]
                with self.assertRaises(SystemExit):
                    self.run_cases(mock.Mock(side_effect=results))
                self.assert_original_preserved()

    def test_interruption_preserves_previous_records(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_cases(mock.Mock(side_effect=[dict(MET), KeyboardInterrupt()]))
        self.assert_original_preserved()

    def test_failure_creates_no_empty_corpus_file(self):
        self.dest.unlink()
        with self.assertRaises(SystemExit):
            self.run_cases(mock.Mock(side_effect=RuntimeError("worker failed")))
        self.assertEqual(list(self.outdir.iterdir()), [])

    def test_cli_rejects_nonpositive_repetitions_before_changing_output(self):
        main = self.module["main"]
        for repeat in (0, -1):
            with self.subTest(repeat=repeat):
                run_once = mock.Mock(return_value=dict(MET))
                with mock.patch.dict(main.__globals__, {
                    "cases": lambda names: [self.case], "run_once": run_once,
                }), mock.patch.object(sys, "argv", [
                    "nm-transitions", "run", "-n", str(repeat), "-o", str(self.outdir),
                ]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        main()
                self.assertNotEqual(error.exception.code, 0)
                run_once.assert_not_called()
                self.assert_original_preserved()


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
