"""`drill compare` on hand-written results files (ADR-0009)."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.test_cli import run_cli


def row(case, model, c, n, interval, hash_=None, status=None):
    return {"case": case, "runner": "plugin-eval", "model": model, "c": c, "n": n,
            "passRate": c / n if n else None, "interval": interval,
            "status": status or ("error" if n == 0 else "pass" if c == n else "fail" if c == 0 else "flaky"),
            "runs": [], "withoutRuns": [], "withoutPassRate": None, "delta": None}


def results(rows, skill="a" * 40, hashes=None):
    hashes = hashes or {}
    return {"manifest": {"revisions": {"skill": {"sha": skill, "dirty": False}}, "runner": "plugin-eval",
                         "models": [{"id": m} for m in dict.fromkeys(r["model"] for r in rows)]},
            "cases": {r["case"]: {"hash": hashes.get(r["case"], "sha256:1")} for r in rows},
            "models": [], "results": rows}


ALL3 = [0.4385, 1.0]   # 3/3
NONE3 = [0.0, 0.5615]  # 0/3
TWO3 = [0.2077, 0.9385]  # 2/3
ALL10 = [0.7225, 1.0]  # 10/10
NONE10 = [0.0, 0.2775]  # 0/10


class Compare(unittest.TestCase):
    def setUp(self):
        self.rig = Path(tempfile.mkdtemp())
        (self.rig / "results").mkdir()

    def write(self, name, doc):
        path = self.rig / "results" / name
        path.write_text(json.dumps(doc))
        return path

    def table_rows(self, out):
        return [l for l in out.splitlines() if l.startswith("| ") and not l.startswith("| case")]

    def test_with_no_arguments_it_picks_the_two_newest_files(self):
        self.write("2026-11-01T000000Z.json", results([row("greet", "haiku", 0, 3, NONE3)]))
        self.write("2026-11-02T000000Z.json", results([row("greet", "haiku", 2, 3, TWO3)]))
        self.write("2026-11-03T000000Z.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        (self.rig / "results" / "2026-11-04T000000Z").mkdir()  # raw evidence dir, not a results file
        cwd = os.getcwd()
        os.chdir(self.rig)
        try:
            code, out, err = run_cli("compare")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, err)
        self.assertIn("`2026-11-02T000000Z.json` → `2026-11-03T000000Z.json`", out)
        self.assertEqual(self.table_rows(out),
                         ["| greet | plugin-eval | haiku | flaky 2/3 | pass 3/3 | 67% → 100% (+33 pp) |  |  |"])

    def test_the_rig_can_be_named(self):
        self.write("2026-11-01T000000Z.json", results([row("greet", "haiku", 0, 10, NONE10)]))
        self.write("2026-11-02T000000Z.json", results([row("greet", "haiku", 10, 10, ALL10)]))
        code, out, _ = run_cli("compare", "--rig", str(self.rig))
        self.assertEqual(code, 0)
        self.assertIn("| greet | plugin-eval | haiku | fail 0/10 | pass 10/10 | 0% → 100% (+100 pp) | moved |  |", out)
        self.assertIn("1 of 1 row(s) moved", out)

    def test_fewer_than_two_results_files_is_refused(self):
        self.write("2026-11-01T000000Z.json", results([]))
        code, _, err = run_cli("compare", "--rig", str(self.rig))
        self.assertNotEqual(code, 0)
        self.assertIn("needs two results files", err)

    def test_overlapping_intervals_are_shown_but_not_moved(self):
        a = self.write("a.json", results([row("greet", "haiku", 2, 3, TWO3)]))
        b = self.write("b.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        code, out, _ = run_cli("compare", str(a), str(b))
        self.assertEqual(code, 0)
        self.assertEqual(self.table_rows(out),
                         ["| greet | plugin-eval | haiku | flaky 2/3 | pass 3/3 | 67% → 100% (+33 pp) |  |  |"])
        self.assertIn("0 of 1 row(s) moved", out)

    def test_even_all_to_none_over_three_runs_is_not_moved(self):
        a = self.write("a.json", results([row("greet", "haiku", 0, 3, NONE3)]))
        b = self.write("b.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        _, out, _ = run_cli("compare", str(a), str(b))
        self.assertIn("| fail 0/3 | pass 3/3 | 0% → 100% (+100 pp) |  |", out)

    def test_a_regression_exits_zero(self):
        a = self.write("a.json", results([row("greet", "haiku", 10, 10, ALL10)]))
        b = self.write("b.json", results([row("greet", "haiku", 0, 10, NONE10)]))
        code, out, _ = run_cli("compare", str(a), str(b))
        self.assertEqual(code, 0)
        self.assertIn("| pass 10/10 | fail 0/10 | 100% → 0% (−100 pp) | moved |", out)

    def test_a_changed_case_hash_is_flagged_on_its_row(self):
        a = self.write("a.json", results([row("greet", "haiku", 3, 3, ALL3), row("bye", "haiku", 3, 3, ALL3)]))
        b = self.write("b.json", results([row("greet", "haiku", 3, 3, ALL3), row("bye", "haiku", 3, 3, ALL3)],
                                         hashes={"greet": "sha256:2"}))
        _, out, _ = run_cli("compare", str(a), str(b))
        rows = {r.split(" | ")[0][2:]: r for r in self.table_rows(out)}
        self.assertTrue(rows["greet"].endswith("| case changed |"), rows["greet"])
        self.assertTrue(rows["bye"].endswith("|  |"), rows["bye"])

    def test_a_changed_skill_sha_is_flagged_on_every_row(self):
        a = self.write("a.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        b = self.write("b.json", results([row("greet", "haiku", 3, 3, ALL3)], skill="b" * 40))
        _, out, _ = run_cli("compare", str(a), str(b))
        self.assertTrue(self.table_rows(out)[0].endswith("| skill SHA changed |"))

    def test_a_dirty_skill_is_flagged_even_on_the_same_sha(self):
        a = self.write("a.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        doc = results([row("greet", "haiku", 3, 3, ALL3)])
        doc["manifest"]["revisions"]["skill"]["dirty"] = True
        b = self.write("b.json", doc)
        _, out, _ = run_cli("compare", str(a), str(b))
        self.assertTrue(self.table_rows(out)[0].endswith("| skill SHA changed |"))

    def test_rows_in_only_one_file(self):
        a = self.write("a.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        b = self.write("b.json", results([row("greet", "sonnet", 3, 3, ALL3)]))
        _, out, _ = run_cli("compare", str(a), str(b))
        self.assertEqual(sorted(self.table_rows(out)),
                         ["| greet | plugin-eval | haiku | pass 3/3 | — | — |  |  |",
                          "| greet | plugin-eval | sonnet | — | pass 3/3 | — |  |  |"])

    def test_an_error_case_has_no_rate(self):
        a = self.write("a.json", results([row("greet", "haiku", 0, 0, None)]))
        b = self.write("b.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        _, out, _ = run_cli("compare", str(a), str(b))
        self.assertEqual(self.table_rows(out), ["| greet | plugin-eval | haiku | error 0/0 | pass 3/3 | — |  |  |"])

    def test_two_models_in_one_file(self):
        f = self.write("a.json", results([row("greet", "haiku", 0, 10, NONE10), row("greet", "opus", 10, 10, ALL10),
                                          row("greet", "sonnet", 2, 3, TWO3)]))
        code, out, err = run_cli("compare", str(f), "--model", "haiku", "--model", "opus")
        self.assertEqual(code, 0, err)
        self.assertIn("`haiku` → `opus` in `a.json`", out)
        self.assertEqual(self.table_rows(out), ["| greet | plugin-eval | fail 0/10 | pass 10/10 | 0% → 100% (+100 pp) | moved |"])

    def test_comparing_models_needs_exactly_two(self):
        f = self.write("a.json", results([]))
        code, _, err = run_cli("compare", str(f), "--model", "haiku")
        self.assertNotEqual(code, 0)
        self.assertIn("one results file and two --model", err)

    def test_a_model_not_in_the_file_is_refused(self):
        f = self.write("a.json", results([row("greet", "haiku", 3, 3, ALL3)]))
        code, _, err = run_cli("compare", str(f), "--model", "haiku", "--model", "opsu")
        self.assertNotEqual(code, 0)
        self.assertIn("no results for model opsu", err)

    def test_a_file_that_is_not_a_results_file_is_refused(self):
        a = self.write("a.json", {"manifest": {}})
        code, _, err = run_cli("compare", str(a), str(a))
        self.assertNotEqual(code, 0)
        self.assertIn("not a results file", err)


if __name__ == "__main__":
    unittest.main()
