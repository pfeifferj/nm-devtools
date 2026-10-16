"""Run with: python3 -m unittest discover -s tests"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "bin/render-vm-xml"


class RenderVmXmlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.config = self.dir / "paths.conf"
        self.output = self.dir / "generated"
        self.config.write_text('VM_DIR="/tmp/VMs with spaces"\nNM_SRC="/tmp/nm workspace"\nNMSTATE_SRC="/tmp/nmstate workspace"\n')

    def render(self, *args):
        env = os.environ.copy()
        for name in ("VM_DIR", "NM_SRC", "NMSTATE_SRC"):
            env.pop(name, None)
        env.update(VM_PATHS_CONF=str(self.config), VM_XML_OUTPUT_DIR=str(self.output))
        return subprocess.run([str(RENDER), *args], env=env, capture_output=True, text=True)

    def test_all_domains(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        files = sorted(self.output.glob("nm-*.xml"))
        self.assertEqual(len(files), 5)
        for file in files:
            root = ET.parse(file).getroot()
            self.assertEqual(root.find("./devices/filesystem/source").get("dir"), "/tmp/nm workspace")
            self.assertTrue(root.find("./devices/disk[@device='disk']/source").get("file").startswith("/tmp/VMs with spaces/"))
            shares = root.findall("./devices/filesystem")
            nmstate = [s for s in shares if s.find("target").get("dir") == "nmstate"]
            if file.stem in ("nm-c9s", "nm-c10s"):
                self.assertEqual(len(nmstate), 1)
                self.assertEqual(nmstate[0].find("source").get("dir"), "/tmp/nmstate workspace")
                self.assertIsNone(nmstate[0].find("readonly"))
                self.assertEqual(nmstate[0].find("address").get("bus"), "0x09")
                addresses = [tuple(sorted(a.attrib.items())) for a in root.findall(".//address[@type='pci']")]
                self.assertEqual(len(addresses), len(set(addresses)))
            else:
                self.assertFalse(nmstate)

    def test_selected_domain(self):
        self.assertEqual(self.render("-d", "nm-c10s").returncode, 0)
        self.assertEqual([p.name for p in self.output.iterdir()], ["nm-c10s.xml"])

    def test_domain_option_and_multiple_selections(self):
        result = self.render("-d", "nm-c10s", "-d", "nm-rawhide.xml.in")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in self.output.iterdir()),
                         ["nm-c10s.xml", "nm-rawhide.xml"])

    def test_bad_config_or_domain_fails_without_output(self):
        for config, args in (
            ("VM_DIR=relative/path\nNM_SRC=/tmp/nm\n", ("-d", "nm-c10s")),
            ("VM_DIR=/tmp/VMs\n", ("-d", "nm-c10s")),
            ("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n", ("-d", "../untracked")),
            ("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n", ("-d", "nm-c10s", "-d", "nm-unknown")),
            ("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n", ("-d",)),
            ("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n", ("nm-c10s",)),
            ("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n", ("-d", "nm-c10s", "nm-c9s")),
            ("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n", ("--domain", "nm-c10s")),
        ):
            with self.subTest(args=args):
                self.config.write_text(config)
                result = self.render(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists(), result.stdout)

    def test_stream_domains_require_nmstate_source(self):
        for domain in ("nm-c9s", "nm-c10s"):
            with self.subTest(domain=domain):
                self.config.write_text("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n")
                result = self.render("-d", domain)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.config.write_text("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\nNMSTATE_SRC=relative\n")
                result = self.render("-d", domain)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
        self.assertEqual(self.render("-d", "nm-rawhide").returncode, 0)

    def test_envsubst_does_not_escape_xml(self):
        self.config.write_text('VM_DIR="/tmp/a&b"\nNM_SRC=/tmp/nm\nNMSTATE_SRC=/tmp/nmstate\n')
        self.assertEqual(self.render("-d", "nm-c10s").returncode, 0)
        with self.assertRaises(ET.ParseError):
            ET.parse(self.output / "nm-c10s.xml")


if __name__ == "__main__":
    unittest.main()
