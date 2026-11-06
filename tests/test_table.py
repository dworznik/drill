"""`drill table` on a hand-written results file (ADR-0009)."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.test_cli import run_cli

DOC = {
    "manifest": {"revisions": {"skill": {"sha": "0123456789abcdef" * 2 + "01234567", "dirty": False}},
                 "runner": "plugin-eval", "date": "2026-11-06T12:09:21Z",
                 "models": [{"id": "haiku"}, {"id": "opus"}, {"id": "broken", "error": "plugin eval failed"}]},
    "cases": {"claim-secret-ana": {"hash": "sha256:1"}, "claim-twice-bob": {"hash": "sha256:2"}},
    "models": [
        {"model": "haiku", "meanPassRate": 0.5, "meanWithoutPassRate": 0.25, "delta": 0.25,
         "counts": {"pass": 1, "flaky": 0, "fail": 1, "error": 0}},
        {"model": "opus", "meanPassRate": 0.8333, "meanWithoutPassRate": 0.9, "delta": -0.0667,
         "counts": {"pass": 1, "flaky": 1, "fail": 0, "error": 0}},
        {"model": "broken", "meanPassRate": None, "meanWithoutPassRate": None, "delta": None,
         "counts": {"pass": 0, "flaky": 0, "fail": 0, "error": 0}},
    ],
    "results": [
        {"case": "claim-secret-ana", "runner": "plugin-eval", "model": "haiku", "status": "pass",
         "runs": [{"trace": [{"rule": "order: tenant → start", "ok": True, "why": ""}]}]},
        {"case": "claim-twice-bob", "runner": "plugin-eval", "model": "haiku", "status": "fail", "runs": []},
    ],
}


class Table(unittest.TestCase):
    def render(self, doc=DOC):
        path = Path(tempfile.mkdtemp()) / "2026-11-06T120921Z.json"
        path.write_text(json.dumps(doc))
        code, out, err = run_cli("table", str(path))
        self.assertEqual(code, 0, err)
        return out

    def test_the_markdown_table_and_footer(self):
        self.assertEqual(self.render(), "\n".join([
            "| model | pass | flaky | fail | mean pass rate | Δ |",
            "|---|---:|---:|---:|---:|---:|",
            "| haiku | 1 | 0 | 1 | 50% | +25 pp |",
            "| opus | 1 | 1 | 0 | 83% | −7 pp |",
            "| broken | 0 | 0 | 0 | — | — |",
            "",
            "Skill `0123456789ab` · 2026-11-06 · runner plugin-eval",
            "",
        ]))

    def test_no_case_name_or_content_appears(self):
        out = self.render()
        for leak in ("claim", "ana", "bob", "tenant", "order"):
            self.assertNotIn(leak, out)

    def test_a_dirty_skill_says_so(self):
        doc = json.loads(json.dumps(DOC))
        doc["manifest"]["revisions"]["skill"]["dirty"] = True
        self.assertIn("Skill `0123456789ab` (uncommitted changes)", self.render(doc))

    def test_a_file_that_is_not_a_results_file_is_refused(self):
        path = Path(tempfile.mkdtemp()) / "x.json"
        path.write_text("{}")
        code, _, err = run_cli("table", str(path))
        self.assertNotEqual(code, 0)
        self.assertIn("not a results file", err)


if __name__ == "__main__":
    unittest.main()
