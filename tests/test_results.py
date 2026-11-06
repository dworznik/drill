"""The results file (ADR-0009): c/n with Wilson intervals (ADR-0004), Δ by drill's pass rule, case hashes.

The end-to-end test builds the file from an aggregate-result.json and transcripts recorded
from a live plugin eval run (tests/data/recorded: haiku, 2 runs per arm; the with arm
greeted Ana twice, the without arm never found the skill), with no live run.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drill.cases import load_cases
from drill.drill import drill
from drill.results import case_hash, case_status, summarize, wilson
from drill.rig import load_rig
from drill.runners.plugin_eval import Model

DATA = Path(__file__).parent / "data"
RECORDED = DATA / "recorded"


class Wilson(unittest.TestCase):
    def test_three_of_three(self):
        lo, hi = wilson(3, 3)
        self.assertEqual((round(lo, 2), round(hi, 2)), (0.44, 1.00))

    def test_ten_of_ten(self):
        self.assertGreaterEqual(wilson(10, 10)[0], 0.72)

    def test_none_of_three(self):
        lo, hi = wilson(0, 3)
        self.assertEqual((round(lo, 2), round(hi, 2)), (0.0, 0.56))

    def test_one_of_two(self):
        lo, hi = wilson(1, 2)
        self.assertEqual((round(lo, 4), round(hi, 4)), (0.0945, 0.9055))

    def test_no_runs_has_no_interval(self):
        self.assertIsNone(wilson(0, 0))


class CaseStatus(unittest.TestCase):
    def test_all_passing_is_pass(self):
        self.assertEqual(case_status(3, 3), "pass")

    def test_none_passing_is_fail(self):
        self.assertEqual(case_status(0, 3), "fail")

    def test_some_passing_is_flaky(self):
        self.assertEqual(case_status(1, 3), "flaky")

    def test_no_counted_runs_is_error(self):
        self.assertEqual(case_status(0, 0), "error")


class CaseHash(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp()) / "greet-ana"
        shutil.copytree(DATA / "rig" / "cases" / "greet-ana", self.case)
        self.before = case_hash(self.case)

    def test_the_same_content_elsewhere_hashes_the_same(self):
        self.assertEqual(case_hash(DATA / "rig" / "cases" / "greet-ana"), self.before)

    def test_editing_a_nested_file_changes_it(self):
        (self.case / "fixtures" / "greet.json").write_text("[]\n")
        self.assertNotEqual(case_hash(self.case), self.before)

    def test_adding_a_file_changes_it(self):
        (self.case / "notes.md").write_text("")
        self.assertNotEqual(case_hash(self.case), self.before)

    def test_renaming_a_file_changes_it(self):
        (self.case / "prompt.md").rename(self.case / "prompt2.md")
        self.assertNotEqual(case_hash(self.case), self.before)


def run(arm, status, score=1.0, n=1, case="c1", model="haiku", trace=()):
    return {"case": case, "model": model, "arm": arm, "run": n, "score": score,
            "error": "API Error: 529" if status == "error" else None, "graders": {}, "trace": list(trace),
            "status": status}


MANIFEST = {"runner": "plugin-eval", "models": [{"id": "haiku", "settings": {}}]}


class Summary(unittest.TestCase):
    def test_error_runs_are_left_out_of_n(self):
        [row] = summarize(MANIFEST, [run("with", "pass", n=1), run("with", "error", score=0, n=2),
                                     run("with", "fail", score=0.5, n=3)], {})["results"]
        self.assertEqual((row["c"], row["n"], row["passRate"], row["status"]), (1, 2, 0.5, "flaky"))
        self.assertEqual([r["status"] for r in row["runs"]], ["pass", "error", "fail"])

    def test_all_errors_is_an_error_case_with_no_rate(self):
        [row] = summarize(MANIFEST, [run("with", "error", score=0)], {})["results"]
        self.assertEqual((row["n"], row["passRate"], row["interval"], row["status"], row["delta"]),
                         (0, None, None, "error", None))

    def test_delta_counts_a_full_score_with_a_broken_trace_rule_as_a_fail(self):
        broken = [{"rule": "count greet in 1..1", "ok": False, "why": "0 call(s): none"}]
        [row] = summarize(MANIFEST, [run("with", "pass"), run("without", "fail", score=1.0, trace=broken)], {})["results"]
        self.assertEqual((row["passRate"], row["withoutPassRate"], row["delta"]), (1.0, 0.0, 1.0))

    def test_no_without_arm_has_no_delta(self):
        doc = summarize(MANIFEST, [run("with", "pass")], {})
        self.assertIsNone(doc["results"][0]["withoutPassRate"])
        self.assertEqual((doc["models"][0]["meanPassRate"], doc["models"][0]["delta"]), (1.0, None))

    def test_model_counts_and_mean(self):
        runs = [run("with", "pass", case="c1"), run("with", "fail", case="c2", score=0),
                run("with", "pass", case="c3"), run("with", "fail", case="c3", n=2, score=0)]
        [m] = summarize(MANIFEST, runs, {})["models"]
        self.assertEqual(m["counts"], {"pass": 1, "flaky": 1, "fail": 1, "error": 0})
        self.assertEqual(m["meanPassRate"], 0.5)


def recorded_aggregate(kept_root: Path) -> dict:
    """The recorded aggregate, with each run's kept dir rebuilt under kept_root as plugin eval leaves it."""
    aggregate = json.loads((RECORDED / "aggregate-result.json").read_text())
    for case in aggregate["cases"]:
        for arm, runs in case["arms"].items():
            for i, r in enumerate(runs):
                rec = RECORDED / case["name"] / f"{arm}-{i + 1}"
                kept = kept_root / f"claude-eval-{arm}-{i + 1}"
                (kept / "out").mkdir(parents=True)
                (kept / "home" / "cwd" / ".drill").mkdir(parents=True)
                shutil.copyfile(rec / "transcript.jsonl", kept / "out" / "trace.jsonl")
                shutil.copyfile(rec / "trace.jsonl", kept / "home" / "cwd" / ".drill" / "trace.jsonl")
                r["tracePath"] = str(kept / "out" / "trace.jsonl")
    return aggregate


class RecordedDrill(unittest.TestCase):
    def setUp(self):
        rig_dir = Path(tempfile.mkdtemp()) / "hello"
        shutil.copytree(DATA / "rig", rig_dir)
        rig = load_rig(rig_dir)
        kept_root = Path(tempfile.mkdtemp())

        def fake_run(skill, model, bin_dir, env_file, tmp_dir, out, **_):
            out.mkdir(parents=True, exist_ok=True)
            return recorded_aggregate(kept_root)

        skill = Path(tempfile.mkdtemp()) / "hello"
        shutil.copytree(DATA / "skill-hello", skill)
        revisions = {"skill": {"sha": "a" * 40, "dirty": False}, "drill": {"sha": "b" * 40, "dirty": True},
                     "rig": {"sha": "c" * 40, "dirty": False}}
        with mock.patch("drill.drill.plugin_eval.run", fake_run):
            path = drill(rig, load_cases(rig_dir / "cases"), skill, revisions, [Model("haiku", {})],
                         log=lambda *_: None, root=Path(tempfile.mkdtemp()))
        self.results = json.loads(path.read_text())
        [self.row] = self.results["results"]

    def test_the_manifest(self):
        m = self.results["manifest"]
        self.assertEqual(m["revisions"]["drill"], {"sha": "b" * 40, "dirty": True})
        self.assertEqual((m["runner"], m["claudeVersion"], m["judgeModel"], m["runs"]), ("plugin-eval", "2.1.318", "haiku", 2))
        self.assertEqual(m["models"], [{"id": "haiku", "settings": {}, "costUsd": 0.01166406, "durationSeconds": 33,
                                        "partial": False}])
        self.assertIs(m["partial"], False)
        self.assertRegex(m["date"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_the_run_vector(self):
        self.assertEqual((self.row["case"], self.row["runner"], self.row["model"]), ("greet-ana", "plugin-eval", "haiku"))
        self.assertEqual([r["status"] for r in self.row["runs"]], ["pass", "pass"])
        self.assertEqual(self.row["runs"][0]["graders"], {"said-hi": True})
        self.assertEqual(self.row["runs"][0]["trace"], [{"rule": "count greet in 1..1", "ok": True, "why": "1 call(s): line 1"}])
        self.assertEqual([r["status"] for r in self.row["withoutRuns"]], ["fail", "fail"])

    def test_c_of_n_interval_and_status(self):
        self.assertEqual((self.row["c"], self.row["n"], self.row["passRate"], self.row["status"]), (2, 2, 1.0, "pass"))
        self.assertEqual([round(x, 2) for x in self.row["interval"]], [0.34, 1.0])

    def test_delta_uses_drills_pass_rule_on_both_arms(self):
        self.assertEqual((self.row["withoutPassRate"], self.row["delta"]), (0.0, 1.0))

    def test_the_per_model_summary(self):
        self.assertEqual(self.results["models"], [{"model": "haiku", "meanPassRate": 1.0, "meanWithoutPassRate": 0.0,
                                                   "delta": 1.0, "counts": {"pass": 1, "flaky": 0, "fail": 0, "error": 0}}])

    def test_each_case_has_a_content_hash(self):
        self.assertEqual(self.results["cases"], {"greet-ana": {"hash": case_hash(DATA / "rig" / "cases" / "greet-ana")}})


if __name__ == "__main__":
    unittest.main()
