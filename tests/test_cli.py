import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from drill.cli import main


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class DrillRunRefusesABrokenRig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_missing_rig_dir(self):
        code, _, err = run_cli("run", str(self.tmp / "nope"))
        self.assertNotEqual(code, 0)
        self.assertIn("no rig directory at", err)

    def test_missing_rig_json(self):
        (self.tmp / "cases").mkdir()
        code, _, err = run_cli("run", str(self.tmp))
        self.assertNotEqual(code, 0)
        self.assertIn("has no rig.json", err)

    def test_missing_cases(self):
        (self.tmp / "rig.json").write_text("{}")
        code, _, err = run_cli("run", str(self.tmp))
        self.assertNotEqual(code, 0)
        self.assertIn("has no cases/ directory", err)


class Verbs(unittest.TestCase):
    def test_every_call_needs_a_verb(self):
        code, _, _ = run_cli()
        self.assertNotEqual(code, 0)


class DrillRunRefusesABadPin(unittest.TestCase):
    def test_branch_pin_is_refused_before_anything_runs(self):
        rig = Path(tempfile.mkdtemp())
        (rig / "cases").mkdir()
        (rig / "rig.json").write_text('{"repo": "octo/skills", "sha": "main", "path": "skills/ci"}')
        code, _, err = run_cli("run", str(rig))
        self.assertNotEqual(code, 0)
        self.assertIn("full 40-character SHA", err)

    def test_invalid_json_is_refused(self):
        rig = Path(tempfile.mkdtemp())
        (rig / "cases").mkdir()
        (rig / "rig.json").write_text("{")
        code, _, err = run_cli("run", str(rig))
        self.assertNotEqual(code, 0)
        self.assertIn("not valid JSON", err)


class DrillRunRefusesBadHosts(unittest.TestCase):
    def rig(self, hosts):
        rig = Path(tempfile.mkdtemp())
        (rig / "cases" / "one").mkdir(parents=True)
        (rig / "cases" / "one" / "prompt.md").write_text("hi\n")
        (rig / "rig.json").write_text(json.dumps({"repo": "octo/skills", "sha": "a" * 40, "path": "s", "hosts": hosts}))
        return rig

    def test_a_url_is_not_a_host(self):
        code, _, err = run_cli("run", str(self.rig(["https://api.example.com"])), "--model", "haiku")
        self.assertNotEqual(code, 0)
        self.assertIn("hosts must be a list of host names", err)

    def test_a_rig_with_hosts_needs_openssl_before_anything_runs(self):
        with mock.patch.dict(os.environ, {"PATH": str(Path(tempfile.mkdtemp()))}):
            code, _, err = run_cli("run", str(self.rig(["api.example.com"])), "--model", "haiku")
        self.assertNotEqual(code, 0)
        self.assertIn("openssl", err)


if __name__ == "__main__":
    unittest.main()
