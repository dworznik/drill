"""drilling cases through `plugin eval`, without a live run.

The end-to-end tests drive drill() against tests/data/fake-claude/claude, which keeps the
same temp-dir layout and aggregate-result.json shape that plugin eval 2.1.318 does.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drill.cases import load_cases
from drill.drill import drill, run_root, run_status
from drill.lib.trace_rules import Verdict
from drill.rig import Refusal, load_rig
from drill.runners.plugin_eval import SCAFFOLD, Model, prepare, scaffold_text, with_scaffold

DATA = Path(__file__).parent / "data"
FAKE_CLAUDE = str(DATA / "fake-claude" / "claude")


def copy_rig() -> Path:
    rig = Path(tempfile.mkdtemp()) / "hello"
    shutil.copytree(DATA / "rig", rig)
    return rig


def skill_copy() -> Path:
    skill = Path(tempfile.mkdtemp()) / "hello"
    (skill / "evals" / "shipped").mkdir(parents=True)
    (skill / "evals" / "shipped" / "prompt.md").write_text("a shipped case\n")
    (skill / "SKILL.md").write_text("---\nname: hello\n---\nRun greet.\n")
    return skill


class CaseCopy(unittest.TestCase):
    def test_evals_is_emptied_and_the_held_out_cases_copied_in(self):
        skill = skill_copy()
        prepare(skill, load_cases(DATA / "rig" / "cases"))
        evals = skill / "evals"
        self.assertEqual(sorted(p.name for p in evals.iterdir()), ["greet-ana"])
        names = sorted(str(p.relative_to(evals / "greet-ana")) for p in (evals / "greet-ana").rglob("*") if p.is_file())
        self.assertEqual(names, [SCAFFOLD, "case.yaml", "fixtures/greet.json", "graders/said-hi.md",
                                 "prompt.md", "trace-rules.json"])

    def test_the_rig_is_left_untouched(self):
        rig = copy_rig()
        prepare(skill_copy(), load_cases(rig / "cases"))
        self.assertFalse((rig / "cases" / "greet-ana" / "case.yaml").exists())

    def test_a_case_with_its_own_scaffold_script_is_refused(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "case.yaml").write_text(
            'schema_version: "1.0"\ncontext:\n  scaffold_script: setup.sh\n')
        with self.assertRaisesRegex(Refusal, "sets its own scaffold_script"):
            load_cases(rig / "cases")

    def test_a_commented_out_scaffold_script_is_fine(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "case.yaml").write_text('schema_version: "1.0"\n# scaffold_script: x.sh\n')
        self.assertEqual(len(load_cases(rig / "cases")), 1)

    def test_an_inline_context_is_refused_before_anything_runs(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "case.yaml").write_text('schema_version: "1.0"\ncontext: {add_dirs: [d]}\n')
        with self.assertRaisesRegex(Refusal, "as a block"):
            load_cases(rig / "cases")

    def test_a_block_context_loads(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "case.yaml").write_text('schema_version: "1.0"\ncontext:\n  add_dirs:\n    - d\n')
        self.assertEqual(len(load_cases(rig / "cases")), 1)

    def test_an_empty_context_followed_by_a_top_level_key_loads(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "case.yaml").write_text('context:\nschema_version: "1.0"\n')
        self.assertEqual(len(load_cases(rig / "cases")), 1)

    def test_an_unknown_call_in_an_order_rule_is_refused(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "trace-rules.json").write_text('{"rules": [{"order": ["nope"]}]}')
        with self.assertRaisesRegex(Refusal, "unknown call name"):
            load_cases(rig / "cases")

    def test_unknown_trace_rules_are_refused_before_anything_runs(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "trace-rules.json").write_text('{"rules": [{"count": "nope"}]}')
        with self.assertRaisesRegex(Refusal, "unknown call name"):
            load_cases(rig / "cases")


class CaseYaml(unittest.TestCase):
    def test_no_case_yaml_gets_one(self):
        self.assertEqual(with_scaffold(None, "greet-ana"),
                         'schema_version: "1.0"\nname: "greet-ana"\ncontext:\n  scaffold_script: .drill-scaffold.sh\n')

    def test_a_case_yaml_without_context_gets_one_appended(self):
        self.assertEqual(with_scaffold('schema_version: "1.0"\nname: x', "x"),
                         'schema_version: "1.0"\nname: x\ncontext:\n  scaffold_script: .drill-scaffold.sh\n')

    def test_an_existing_context_block_gains_the_entry_at_its_indent(self):
        text = 'schema_version: "1.0"\ncontext:\n    add_dirs:\n      - data\nexecution:\n  max_turns: 4\n'
        self.assertEqual(with_scaffold(text, "x"),
                         'schema_version: "1.0"\ncontext:\n    scaffold_script: .drill-scaffold.sh\n'
                         '    add_dirs:\n      - data\nexecution:\n  max_turns: 4\n')

    def test_an_inline_context_is_refused(self):
        with self.assertRaisesRegex(ValueError, "as a block"):
            with_scaffold('context: {add_dirs: [data]}\n', "x")


class Scaffold(unittest.TestCase):
    def run_scaffold(self, case_dir: Path, keep: list[str]) -> Path:
        script = Path(tempfile.mkdtemp()) / SCAFFOLD
        script.write_text(scaffold_text(case_dir, keep))
        ws = Path(tempfile.mkdtemp())
        subprocess.run(["bash", str(script)], cwd=ws, check=True, env={"PATH": "/usr/bin:/bin"})
        return ws

    def test_it_seeds_fixtures_keep_list_and_an_empty_trace(self):
        ws = self.run_scaffold(DATA / "rig" / "cases" / "greet-ana", ["RLN_AGENT"])
        self.assertEqual(json.loads((ws / ".drill" / "fixtures" / "greet.json").read_text())[0]["argv"],
                         ["--name", "Ana"])
        self.assertEqual(json.loads((ws / ".drill" / "env-keep.json").read_text()), ["RLN_AGENT"])
        self.assertEqual((ws / ".drill" / "trace.jsonl").read_text(), "")

    def test_fixtures_come_from_an_absolute_path(self):
        text = scaffold_text(DATA / "rig" / "cases" / "greet-ana", [])
        self.assertIn(str((DATA / "rig" / "cases" / "greet-ana" / "fixtures").resolve()), text)

    def test_a_case_without_fixtures(self):
        case = Path(tempfile.mkdtemp()) / "it's odd"
        case.mkdir()
        ws = self.run_scaffold(case, [])
        self.assertEqual(sorted(p.name for p in (ws / ".drill").iterdir()), ["env-keep.json", "trace.jsonl"])


class PassRule(unittest.TestCase):
    ok = Verdict(0, True, "count greet in 1..1")
    broken = Verdict(1, False, "stop-after rln")

    def test_score_one_and_every_rule_holding_passes(self):
        self.assertEqual(run_status(1.0, None, [self.ok]), "pass")

    def test_no_rules_and_score_one_passes(self):
        self.assertEqual(run_status(1.0, None, []), "pass")

    def test_a_failed_trace_rule_is_fail_not_error(self):
        self.assertEqual(run_status(1.0, None, [self.ok, self.broken]), "fail")

    def test_a_score_below_one_fails(self):
        self.assertEqual(run_status(0.99, None, [self.ok]), "fail")

    def test_an_errored_run_is_error_whatever_its_score(self):
        self.assertEqual(run_status(0.0, "API Error: 529", [self.ok]), "error")


class RunRoot(unittest.TestCase):
    def test_a_macos_style_tmpdir_is_used(self):
        with mock.patch.dict(os.environ, {"TMPDIR": "/var/folders/xy/T"}):
            self.assertEqual(run_root(), Path("/var/folders/xy/T"))

    def test_tmpdir_under_tmp_falls_back_to_the_cache(self):
        with mock.patch.dict(os.environ, {"TMPDIR": "/tmp/x", "XDG_CACHE_HOME": "/c"}):
            self.assertEqual(run_root(), Path("/c/drill/runs"))

    def test_a_hidden_root_that_is_a_symlink_still_counts(self):
        # macOS: /tmp is a symlink to /private/tmp, so TMPDIR=/tmp/x resolves outside "/tmp"
        real = Path(tempfile.mkdtemp())
        link = Path(tempfile.mkdtemp()) / "tmp"
        link.symlink_to(real)
        with mock.patch("drill.drill.SANDBOX_HIDDEN", (link,)), \
                mock.patch.dict(os.environ, {"TMPDIR": str(link / "x"), "XDG_CACHE_HOME": "/c"}):
            self.assertEqual(run_root(), Path("/c/drill/runs"))

    def test_unset_tmpdir_falls_back_to_the_cache(self):
        env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
        with mock.patch.dict(os.environ, dict(env, XDG_CACHE_HOME="/c"), clear=True):
            self.assertEqual(run_root(), Path("/c/drill/runs"))


class ModelSpec(unittest.TestCase):
    def test_plain_id(self):
        self.assertEqual(Model.parse("haiku"), Model("haiku", {}))

    def test_settings_are_refused_until_plugin_eval_can_apply_them(self):
        with self.assertRaisesRegex(ValueError, "effort"):
            Model.parse("opus:effort=high")


class EndToEnd(unittest.TestCase):
    """drill() against the fake plugin eval."""

    def drill(self, mode, runs=None):
        rig = load_rig(copy_rig())
        cases = load_cases(rig.path / "cases")
        self.root = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"FAKE_EVAL": mode}):
            path = drill(rig, cases, skill_copy(), {"skill": {"sha": "a" * 40, "dirty": False}},
                         [Model("haiku", {})], runs=runs, claude=FAKE_CLAUDE, log=lambda *_: None, root=self.root)
        self.rig = rig.path
        return path, json.loads(path.read_text())

    def statuses(self, results):
        return {(r["arm"], r["run"]): r["status"] for r in results["runs"]}

    def test_a_passing_drill_writes_the_results_file_and_raw_evidence(self):
        path, results = self.drill("ok")
        self.assertEqual(path.parent, self.rig / "results")
        self.assertRegex(path.name, r"^\d{4}-\d\d-\d\dT\d{6}Z\.json$")
        self.assertEqual(self.statuses(results), {("with", 1): "pass", ("without", 1): "pass"})
        self.assertEqual(results["manifest"]["runner"], "plugin-eval")
        self.assertEqual(results["manifest"]["claudeVersion"], "9.9.9-fake")
        self.assertEqual(results["manifest"]["models"][0]["id"], "haiku")
        ev = path.with_suffix("")
        self.assertTrue((ev / "haiku" / "aggregate-result.json").is_file())
        self.assertTrue((ev / "haiku" / "report.html").is_file())
        for arm in ("with-1", "without-1"):
            trace = (ev / "haiku" / "greet-ana" / arm / "trace.jsonl").read_text().splitlines()
            self.assertEqual(json.loads(trace[0])["argv"], ["--name", "Ana"])
            self.assertEqual(json.loads(trace[0])["fixture"], 0)
            self.assertTrue((ev / "haiku" / "greet-ana" / arm / "transcript.jsonl").is_file())

    def test_trace_rules_run_on_both_arms_and_a_broken_rule_is_fail(self):
        _, results = self.drill("skip")
        self.assertEqual(self.statuses(results), {("with", 1): "fail", ("without", 1): "fail"})
        for r in results["runs"]:
            self.assertEqual(r["trace"], [{"rule": "count greet in 1..1", "ok": False, "why": "0 call(s): none"}])
            self.assertEqual(r["score"], 1.0)

    def test_an_api_error_run_is_error(self):
        _, results = self.drill("apierror")
        self.assertEqual(self.statuses(results), {("with", 1): "pass", ("without", 1): "error"})

    def test_a_low_score_is_fail(self):
        _, results = self.drill("low")
        self.assertEqual(self.statuses(results)[("with", 1)], "fail")

    def test_a_model_plugin_eval_cannot_run_is_recorded_and_the_others_kept(self):
        rig = load_rig(copy_rig())
        root = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"FAKE_EVAL": "ok"}):
            path = drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("broken", {}), Model("haiku", {})],
                         claude=FAKE_CLAUDE, log=lambda *_: None, root=root)
        results = json.loads(path.read_text())
        self.assertIn("left no aggregate-result.json", results["manifest"]["models"][0]["error"])
        self.assertEqual({r["model"] for r in results["runs"]}, {"haiku"})
        self.assertEqual(list(root.iterdir()), [])

    def test_a_corrupt_trace_is_an_error_and_the_results_are_still_written(self):
        path, results = self.drill("corrupt")
        self.assertEqual(self.statuses(results), {("with", 1): "error", ("without", 1): "error"})
        self.assertIn("unreadable trace", results["runs"][0]["error"])
        self.assertTrue(path.is_file())

    def test_a_claude_that_cannot_start_is_recorded_as_a_model_error(self):
        rig = load_rig(copy_rig())
        root = Path(tempfile.mkdtemp())
        path = drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("haiku", {})],
                     claude=str(root / "no-such-claude"), log=lambda *_: None, root=root)
        results = json.loads(path.read_text())
        self.assertIn("could not start", results["manifest"]["models"][0]["error"])
        self.assertEqual(results["runs"], [])

    def test_every_run_is_read_back(self):
        _, results = self.drill("ok", runs=3)
        self.assertEqual(len(results["runs"]), 6)

    def test_kept_temp_dirs_are_deleted_after_evidence_is_copied(self):
        _, results = self.drill("ok", runs=2)
        self.assertEqual(len(results["runs"]), 4)
        self.assertEqual(list(self.root.rglob("claude-eval-*")), [])
        self.assertEqual(list(self.root.iterdir()), [])  # the shim bin/ goes too


if __name__ == "__main__":
    unittest.main()
