"""Offline structured-evidence guards shared by native and imported reports."""

from pathlib import Path
import runpy
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class EvidenceSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("executed a process")):
            cls.module = runpy.run_path(str(ROOT / "bin/net-diagnose"))

    def validate(self, data, ident="routes4"):
        self.module["validate_data"](ident, data, None)

    def test_flat_hops_and_cross_family_via_are_supported(self):
        for field in ("nexthops", "multipath"):
            self.validate([{"dst": "192.0.2.0/24", "table": 100, field: [
                {"gateway": "192.0.2.1", "dev": "eth0"},
                {"via": {"family": "inet6", "host": "fe80::1"}, "dev": "eth1"},
            ]}])

    def test_malformed_nested_hops_cannot_reach_the_analyzer(self):
        for children in ({"dev": "eth0"}, [], [{}], ["eth0"], [{"gateway": []}],
                         [{"dev": "eth0", "nexthops": [{"dev": "eth1"}]}],
                         [{"dev": "eth0", "flags": [False]}],
                         [{"via": {"family": "inet6", "host": "192.0.2.1"}}]):
            for field in ("nexthops", "multipath"):
                with self.subTest(field=field, children=children), self.assertRaises(ValueError):
                    self.validate([{"dst": "default", field: children}])
        for via in ([], {"family": "inet6", "host": []}, {"family": [], "host": "fe80::1"}):
            with self.subTest(via=via), self.assertRaises(ValueError):
                self.validate([{"dst": "default", "via": via}])

    def test_unconsumed_json_fields_still_have_finite_structure(self):
        nested = []
        for _ in range(40):
            nested = [nested]
        for extra in (nested, [None] * 200000, float("inf"), "\ud800"):
            with self.subTest(kind=type(extra).__name__), self.assertRaises(ValueError):
                self.validate([{"dst": "default", "dev": "eth0", "extra": extra}])


if __name__ == "__main__":
    unittest.main()
