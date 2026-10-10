"""Run with: python3 -m unittest discover -s tests"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "bin/render-vm-xml"


@unittest.skipUnless(shutil.which("envsubst"), "gettext envsubst is required")
class RenderVmXmlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.config = self.dir / "paths.conf"
        self.output = self.dir / "generated"
        self.config.write_text('VM_DIR="/tmp/VMs with spaces"\nNM_SRC="/tmp/nm workspace"\n')

    def render(self, *domains):
        env = os.environ.copy()
        env.pop("VM_DIR", None)
        env.pop("NM_SRC", None)
        env.update(VM_PATHS_CONF=str(self.config), VM_XML_OUTPUT_DIR=str(self.output))
        return subprocess.run([str(RENDER), *domains], env=env, capture_output=True, text=True)

    def test_all_domains(self):
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        files = sorted(self.output.glob("nm-*.xml"))
        self.assertEqual(len(files), 5)
        for file in files:
            root = ET.parse(file).getroot()
            self.assertEqual(root.find("./devices/filesystem/source").get("dir"), "/tmp/nm workspace")
            self.assertTrue(root.find("./devices/disk[@device='disk']/source").get("file").startswith("/tmp/VMs with spaces/"))

    def test_selected_domain(self):
        self.assertEqual(self.render("nm-c10s").returncode, 0)
        self.assertEqual([p.name for p in self.output.iterdir()], ["nm-c10s.xml"])

    def test_bad_config_or_domain_fails_without_output(self):
        for config, domain in (
            ("VM_DIR=relative/path\nNM_SRC=/tmp/nm\n", "nm-c10s"),
            ("VM_DIR=/tmp/VMs\n", "nm-c10s"),
            ("VM_DIR=/tmp/VMs\nNM_SRC=/tmp/nm\n", "../untracked"),
        ):
            self.config.write_text(config)
            result = self.render(domain)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.output.exists(), result.stdout)

    def test_envsubst_does_not_escape_xml(self):
        self.config.write_text('VM_DIR="/tmp/a&b"\nNM_SRC=/tmp/nm\n')
        self.assertEqual(self.render("nm-c10s").returncode, 0)
        with self.assertRaises(ET.ParseError):
            ET.parse(self.output / "nm-c10s.xml")


if __name__ == "__main__":
    unittest.main()
