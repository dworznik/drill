"""drilling cases through `plugin eval`, without a live run.

The end-to-end tests drive drill() against tests/data/fake-claude/claude, which keeps the
same temp-dir layout and aggregate-result.json shape that plugin eval 2.1.318 does.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

from drill.cases import load_cases
from drill.cli import main
from drill.drill import drill, run_root, run_status
from drill.results import all_runs
from drill.lib.trace_rules import Verdict
from drill.rig import Refusal, load_rig
from drill.lib.http_fake import HttpFake, install_node_passthrough, register, run_env
from drill.lib.shim import shim_python
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

    def drill(self, mode, runs=None, rules=None):
        rig_dir = copy_rig()
        if rules is not None:
            (rig_dir / "cases" / "greet-ana" / "trace-rules.json").write_text(json.dumps(rules))
        rig = load_rig(rig_dir)
        cases = load_cases(rig.path / "cases")
        self.root = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"FAKE_EVAL": mode}):
            path = drill(rig, cases, skill_copy(), {"skill": {"sha": "a" * 40, "dirty": False}},
                         [Model("haiku", {})], runs=runs, claude=FAKE_CLAUDE, log=lambda *_: None, root=self.root)
        self.rig = rig.path
        return path, json.loads(path.read_text())

    def statuses(self, results):
        return {(r["arm"], r["run"]): r["status"] for r in all_runs(results)}

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
        for r in all_runs(results):
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
        self.assertEqual({r["model"] for r in all_runs(results)}, {"haiku"})
        self.assertEqual(list(root.iterdir()), [])

    def test_a_corrupt_trace_is_an_error_and_the_results_are_still_written(self):
        path, results = self.drill("corrupt")
        self.assertEqual(self.statuses(results), {("with", 1): "error", ("without", 1): "error"})
        self.assertIn("unreadable trace", all_runs(results)[0]["error"])
        self.assertTrue(path.is_file())

    def test_a_run_without_a_transcript_is_error(self):
        _, results = self.drill("notranscript")
        self.assertEqual(self.statuses(results), {("with", 1): "error", ("without", 1): "error"})
        self.assertIn("no transcript", all_runs(results)[0]["error"])

    def test_a_secret_the_agent_wrote_fails_the_run_and_is_never_printed(self):
        # the recorded with-arm agent repeats greet's output; the without-arm agent never ran greet
        rules = {"rules": [{"secret": "from greet", "label": "greeting"}]}
        path, results = self.drill("ok", rules=rules)
        self.assertEqual(self.statuses(results), {("with", 1): "fail", ("without", 1): "pass"})
        self.assertEqual(all_runs(results)[0]["trace"],
                         [{"rule": "secret <greeting>", "ok": False, "why": "assistant text"}])
        self.assertNotIn("from greet", path.read_text())
        transcript = path.with_suffix("") / "haiku" / "greet-ana" / "with-1" / "transcript.jsonl"
        self.assertIn("from greet", transcript.read_text())  # raw evidence keeps it as recorded

    def test_another_cases_secret_is_masked_in_verdicts(self):
        rig_dir = copy_rig()
        other = rig_dir / "cases" / "zz-other"
        other.mkdir()
        (other / "prompt.md").write_text("x\n")
        (other / "trace-rules.json").write_text(json.dumps({"rules": [{"secret": "Ana"}]}))
        (rig_dir / "cases" / "greet-ana" / "trace-rules.json").write_text(
            json.dumps({"rules": [{"count": {"args": ["--name", "Ana"]}, "max": 0}]}))  # rule text names Ana
        rig = load_rig(rig_dir)
        with mock.patch.dict(os.environ, {"FAKE_EVAL": "ok"}):
            path = drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("haiku", {})],
                         claude=FAKE_CLAUDE, log=lambda *_: None, root=Path(tempfile.mkdtemp()))
        self.assertNotIn("Ana", path.read_text())

    def test_a_model_error_is_masked_in_the_log_and_the_results_file(self):
        rig_dir = copy_rig()
        (rig_dir / "cases" / "greet-ana" / "trace-rules.json").write_text(
            json.dumps({"rules": [{"secret": "unknown model"}]}))  # in the fake's stderr for model "broken"
        rig = load_rig(rig_dir)
        logs = []
        path = drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("broken", {})],
                     claude=FAKE_CLAUDE, log=logs.append, root=Path(tempfile.mkdtemp()))
        self.assertNotIn("unknown model", path.read_text() + "\n".join(logs))
        self.assertIn("<redacted>", path.read_text())

    def test_the_cli_never_prints_a_secret(self):
        rig = copy_rig()
        (rig / "cases" / "greet-ana" / "trace-rules.json").write_text(
            json.dumps({"rules": [{"secret": "--name", "label": "flag"}]}))  # in greet's argv
        out = io.StringIO()
        path = f"{DATA / 'fake-claude'}{os.pathsep}{os.environ['PATH']}"
        with mock.patch.dict(os.environ, {"FAKE_EVAL": "ok", "PATH": path, "TMPDIR": tempfile.mkdtemp(dir=Path.home())}), \
                redirect_stdout(out):
            self.assertEqual(main(["run", str(rig), "--skill", str(skill_copy()), "--model", "haiku"]), 0)
        self.assertIn("FAIL secret <flag>", out.getvalue())
        self.assertNotIn("--name", out.getvalue())

    def test_a_claude_that_cannot_start_is_recorded_as_a_model_error(self):
        rig = load_rig(copy_rig())
        root = Path(tempfile.mkdtemp())
        path = drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("haiku", {})],
                     claude=str(root / "no-such-claude"), log=lambda *_: None, root=root)
        results = json.loads(path.read_text())
        self.assertIn("could not start", results["manifest"]["models"][0]["error"])
        self.assertEqual(all_runs(results), [])

    def test_every_run_is_read_back(self):
        _, results = self.drill("ok", runs=3)
        self.assertEqual(len(all_runs(results)), 6)

    def test_kept_temp_dirs_are_deleted_after_evidence_is_copied(self):
        _, results = self.drill("ok", runs=2)
        self.assertEqual(len(all_runs(results)), 4)
        self.assertEqual(list(self.root.rglob("claude-eval-*")), [])
        self.assertEqual(list(self.root.iterdir()), [])  # the shim bin/ goes too

    def test_a_sealed_kept_dir_outside_the_run_root_is_read_and_deleted(self):
        """macOS: plugin eval keeps runs in /tmp/e-*, with the workspace sealed under sealed/home/cwd."""
        kept_root = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"FAKE_SEAL": "1", "FAKE_KEPT_ROOT": str(kept_root)}):
            path, results = self.drill("ok")
        self.assertEqual(self.statuses(results), {("with", 1): "pass", ("without", 1): "pass"})
        trace = (path.with_suffix("") / "haiku" / "greet-ana" / "with-1" / "trace.jsonl").read_text()
        self.assertEqual(json.loads(trace.splitlines()[0])["argv"], ["--name", "Ana"])
        self.assertEqual(list(kept_root.iterdir()), [])


class RunLayout(unittest.TestCase):
    def test_the_env_file_is_not_beside_the_shims(self):
        """plugin eval's sandbox denies CLAUDE_ENV_FILE's directory, so bin/ must not sit in it."""
        seen = {}

        def fake_run(skill, model, bin_dir, env_file, tmp_dir, out, **_):
            seen.update(bin=bin_dir.resolve(), env=env_file.parent.resolve())
            raise RuntimeError("stop")

        rig = load_rig(copy_rig())
        with mock.patch("drill.drill.plugin_eval.run", fake_run):
            drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("haiku", {})],
                  log=lambda *_: None, root=Path(tempfile.mkdtemp()))
        self.assertFalse(seen["bin"].is_relative_to(seen["env"]))


REAL_RUN = subprocess.run


def stop_plugin_eval(seen: dict):
    """A subprocess.run that records plugin eval's command and env and stops there, running anything else (openssl)."""
    def run(cmd, *args, env=None, **kw):
        if cmd[1:3] != ["plugin", "eval"]:
            return REAL_RUN(cmd, *args, env=env, **kw)
        node = Path(env["PATH"].split(os.pathsep)[0]) / "node"
        seen.update(cmd=cmd, env=env, bundle=Path(env["SSL_CERT_FILE"]).read_text() if "SSL_CERT_FILE" in env else None,
                    node=("symlink" if node.is_symlink() else node.read_text()) if os.path.lexists(node) else None)
        raise OSError("stop")
    return mock.patch("drill.runners.plugin_eval.subprocess.run", run)


class HttpRouting(unittest.TestCase):
    """What drill hands plugin eval for a rig with hosts, without a live run."""

    def capture(self, rig_dir: Path, env=None):
        seen = {}
        rig = load_rig(rig_dir)
        with mock.patch.dict(os.environ, env or {}), stop_plugin_eval(seen):
            drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("haiku", {})],
                  log=lambda *_: None, root=Path(tempfile.mkdtemp()))
        return seen

    def http_rig(self) -> Path:
        rig = Path(tempfile.mkdtemp()) / "pets"
        shutil.copytree(DATA / "http-rig", rig)
        return rig

    def test_a_rig_without_hosts_runs_no_fake(self):
        seen = self.capture(copy_rig())
        for var in ("HTTPS_PROXY", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS"):
            self.assertEqual(seen["env"].get(var), os.environ.get(var), var)
        self.assertEqual(seen["cmd"][seen["cmd"].index("--allow-tools") + 1:], ["Bash"])

    def test_the_rig_hosts_are_granted_and_the_run_is_serial(self):
        cmd = self.capture(self.http_rig())["cmd"]
        self.assertEqual(cmd[cmd.index("--allow-tools") + 1:], ["Bash", "WebFetch(domain:pets.drill.test)"])
        self.assertEqual(cmd[cmd.index("--concurrency") + 1], "1")

    def test_proxies_point_at_the_fake_and_every_client_trusts_one_bundle(self):
        user_ca = Path(tempfile.mkdtemp()) / "corp.pem"
        user_ca.write_text("-----BEGIN CERTIFICATE-----\nY29ycA==\n-----END CERTIFICATE-----\n")
        env = self.capture(self.http_rig(), {"SSL_CERT_FILE": str(user_ca)})["env"]
        proxies = {env[v] for v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")}
        self.assertEqual(len(proxies), 1)
        self.assertRegex(proxies.pop(), r"^http://127\.0\.0\.1:\d+$")
        bundles = {env[v] for v in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")}
        self.assertEqual(len(bundles), 1)
        self.assertEqual(Path(bundles.pop()).name, "ca-bundle.pem")

    def test_nothing_the_run_reads_is_under_tmp(self):
        """With TMPDIR unset or under /tmp, the bundle, shims and TMPDIR all go in the user cache instead."""
        rig = self.http_rig()
        seen = {}
        with mock.patch.dict(os.environ, {"TMPDIR": "/tmp"}), stop_plugin_eval(seen):
            drill(load_rig(rig), load_cases(rig / "cases"), skill_copy(), {}, [Model("haiku", {})], log=lambda *_: None)
        env = seen["env"]
        for p in (env["SSL_CERT_FILE"], env["TMPDIR"], env["CLAUDE_ENV_FILE"], env["PATH"].split(os.pathsep)[0]):
            self.assertFalse(Path(p).resolve().is_relative_to(Path("/tmp").resolve()), p)
        self.assertIn("drill throwaway CA", seen["bundle"])

    def test_the_bundle_keeps_the_users_cas(self):
        user_ca = Path(tempfile.mkdtemp()) / "corp.pem"
        user_ca.write_text("-----BEGIN CERTIFICATE-----\nY29ycA==\n-----END CERTIFICATE-----\n")
        seen = {}
        rig = load_rig(self.http_rig())
        with mock.patch.dict(os.environ, {"SSL_CERT_FILE": str(user_ca)}), stop_plugin_eval(seen):
            drill(rig, load_cases(rig.path / "cases"), skill_copy(), {}, [Model("haiku", {})],
                  log=lambda *_: None, root=Path(tempfile.mkdtemp()))
        self.assertIn("Y29ycA==", seen["bundle"])
        self.assertIn("drill throwaway CA", seen["bundle"])

    def test_the_scaffold_registers_its_workspace_with_the_fake(self):
        with HttpFake(Path(tempfile.mkdtemp()), ["pets.drill.test"]) as fake:
            script = Path(tempfile.mkdtemp()) / SCAFFOLD
            script.write_text(scaffold_text(DATA / "http-rig" / "cases" / "adopt-rex", [], (shim_python(), fake.url)))
            ws = Path(tempfile.mkdtemp())
            # the scaffold's env is scrubbed, proxies and all
            subprocess.run(["bash", str(script)], cwd=ws, check=True, env={"PATH": "/usr/bin:/bin"})
            self.assertEqual(fake.workspace.resolve(), ws.resolve())  # macOS: $PWD is /private/var/…

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_node_fetch_reaches_the_fake_through_the_node_passthrough(self):
        run_dir = Path(tempfile.mkdtemp())
        with HttpFake(run_dir / "ca", ["pets.drill.test"]) as fake:
            bin_dir = run_dir / "bin"
            self.assertIsNotNone(install_node_passthrough(bin_dir, shim_python()))
            ws = run_dir / "ws"
            shutil.copytree(DATA / "http-rig" / "cases" / "adopt-rex" / "fixtures", ws / ".drill" / "fixtures")
            register(fake.url, ws)
            env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower() and k != "NODE_USE_ENV_PROXY"}
            env.update(run_env(fake, fake.ca), PATH=f"{bin_dir}{os.pathsep}{env['PATH']}")
            out = subprocess.run(["node", "-e", 'fetch("https://pets.drill.test/v1/adoptions/adp_01")'
                                  '.then(r => r.json()).then(j => console.log(j.status))'],
                                 cwd=ws, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(out.stdout.strip(), "adopted", out.stderr)
            trace = [json.loads(t) for t in (ws / ".drill" / "trace.jsonl").read_text().splitlines()]
            self.assertEqual([(t["method"], t["fixture"]) for t in trace], [("GET", 1)])

    def test_a_rig_that_shims_node_keeps_its_fixture_shim(self):
        rig = self.http_rig()
        config = json.loads((rig / "rig.json").read_text())
        (rig / "rig.json").write_text(json.dumps(dict(config, shims=["node"])))
        self.assertEqual(self.capture(rig)["node"], "symlink", "the fixture shim is a symlink to .drill-shim")

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_a_rig_with_hosts_gets_the_node_passthrough(self):
        self.assertIn('os.environ["NODE_USE_ENV_PROXY"] = "1"', self.capture(self.http_rig())["node"])


if __name__ == "__main__":
    unittest.main()
