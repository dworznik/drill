import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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


class UnbuiltCommands(unittest.TestCase):
    def test_table_is_not_built(self):
        code, _, err = run_cli("table", "results.json")
        self.assertNotEqual(code, 0)
        self.assertIn("not built yet", err)

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


if __name__ == "__main__":
    unittest.main()
