"""Exercise namespace fixture guards without creating a namespace or interface."""

from pathlib import Path
import runpy
import unittest


MODULE = runpy.run_path(str(Path(__file__).with_name("e2e_net_diagnose.py")))


class NamespaceGuardTest(unittest.TestCase):
    def test_loopback_and_inactive_fallback_tunnels_are_accepted(self):
        self.assertTrue(MODULE["fresh_namespace_links"]([{"ifname": "lo"}]))
        self.assertTrue(MODULE["fresh_namespace_links"]([
            {"ifname": "lo"}, {"ifname": "gre0", "flags": [], "linkinfo": {"info_kind": "gre"}},
            {"ifname": "ip6tnl0", "flags": [], "linkinfo": {"info_kind": "ip6tnl"}},
        ]))

    def test_active_or_unexpected_links_are_rejected(self):
        for row in ({"ifname": "eth0"},
                    {"ifname": "gre0", "flags": ["UP"], "linkinfo": {"info_kind": "gre"}},
                    {"ifname": "gre0", "flags": [], "linkinfo": {"info_kind": "veth"}}):
            with self.subTest(row=row):
                self.assertFalse(MODULE["fresh_namespace_links"]([{"ifname": "lo"}, row]))

    def test_loopback_is_required_and_names_must_be_unique(self):
        for links in ([], [{"ifname": "lo"}, {"ifname": "lo"}]):
            self.assertFalse(MODULE["fresh_namespace_links"](links))
