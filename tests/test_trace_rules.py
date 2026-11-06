"""trace rules, seeded from the prototype (proto/trace-assert) with its testnet-garden-claim traces.

The prototype's never-in-argv rule is replaced by `secret` (ADR-0011).
Expected failures are the bash prototype's own verdicts on the same files.
"""

import json
import tempfile
import unittest
from pathlib import Path

from drill.lib.trace_rules import BadRules, check, env_keep_list, load_trace, report
from drill.lib.transcript import agent_writing

DATA = Path(__file__).parent / "data" / "trace-rules"
RULES = json.loads((DATA / "trace-rules.json").read_text())
SECRET = "7Qm2XkP9fLr4TzWc8NbVh3"

# indexes into RULES["rules"]
ORDER, COUNT_START, COUNT_POLL, COUNT_ELSEWHERE, IN_STDIN, ENV, STOP_AFTER = range(7)


def failed(name):
    verdicts = check(RULES, load_trace(DATA / "traces" / f"{name}.jsonl"))
    return {v.rule for v in verdicts if not v.ok}


class PrototypeTraces(unittest.TestCase):
    def test_good_trace_passes_every_rule(self):
        verdicts = check(RULES, load_trace(DATA / "traces" / "good.jsonl"))
        self.assertEqual(len(verdicts), 7)
        self.assertTrue(all(v.ok for v in verdicts), report(verdicts))

    def test_double_claim_fails_the_start_count(self):
        self.assertEqual(failed("double-claim"), {COUNT_START})

    def test_ignoring_exit_3_fails_stop_after_and_never_reaches_balance(self):
        self.assertEqual(failed("ignores-exit-3"), {STOP_AFTER, ORDER})

    def test_secret_in_argv_is_not_on_stdin(self):
        self.assertEqual(failed("leak-argv"), {IN_STDIN})

    def test_signing_without_the_agent_env_fails_env(self):
        self.assertEqual(failed("no-agent-env"), {ENV})

    def test_sending_the_secret_elsewhere(self):
        self.assertEqual(failed("other-origin"), {ORDER, COUNT_START, COUNT_ELSEWHERE, IN_STDIN})

    def test_an_empty_trace_is_checked_like_any_other(self):
        self.assertEqual(failed("empty"), {ORDER, COUNT_START, COUNT_POLL, IN_STDIN})


class Reporting(unittest.TestCase):
    def test_literals_are_masked_in_every_line(self):
        out = report(check(RULES, load_trace(DATA / "traces" / "leak-argv.jsonl")))
        self.assertNotIn(SECRET, out)
        self.assertIn("in-stdin <link secret> of start", out)

    def test_report_lines(self):
        out = report(check(RULES, load_trace(DATA / "traces" / "double-claim.jsonl")))
        self.assertIn("FAIL count start in 1..1  (2 call(s): line 3, 4)", out.splitlines())
        self.assertIn("ok   stop-after {\"bin\":\"rln\",\"exit\":3}  (never happened)", out.splitlines())


class FixtureMisses(unittest.TestCase):
    def trace(self):
        good = load_trace(DATA / "traces" / "good.jsonl")
        miss = {"bin": "curl", "argv": ["-sS", "--data", SECRET, "https://x"], "env": {}, "stdin": None,
                "exit": 127, "fixture": None}
        return good[:2] + [dict(miss, line=3)] + good[2:]

    def test_a_fixture_miss_fails_the_run_before_any_rule_is_read(self):
        verdicts = check({"calls": {}, "rules": [{"count": "undefined-call"}]}, self.trace())
        self.assertEqual(len(verdicts), 1)
        self.assertFalse(verdicts[0].ok)
        self.assertEqual(verdicts[0].what, "no fixture misses")
        self.assertIn("line 3: curl -sS --data", verdicts[0].why)

    def test_the_miss_report_masks_literals(self):
        out = report(check(RULES, self.trace()))
        self.assertNotIn(SECRET, out)
        self.assertIn("<redacted>", out)

    def test_a_line_without_a_fixture_field_is_not_a_miss(self):
        self.assertEqual(failed("good"), set())


class Matchers(unittest.TestCase):
    def line(self, **kw):
        return {"bin": "gh", "argv": [], "env": {}, "stdin": None, "exit": 0, "fixture": 0, **kw}

    def test_args_is_a_subsequence_and_survives_global_flags(self):
        t = [self.line(argv=["--repo", "o/r", "pr", "--draft", "create"])]
        rules = {"rules": [{"count": {"args": ["pr", "create"]}, "min": 1},
                           {"count": {"args": ["create", "pr"]}, "max": 0}]}
        self.assertTrue(all(v.ok for v in check(rules, t)))

    def test_exit_matcher(self):
        t = [self.line(exit=1), self.line(exit=0)]
        self.assertEqual(check({"rules": [{"count": {"exit": 1}, "min": 1, "max": 1}]}, t)[0].ok, True)

    def test_env_null_means_only_set(self):
        rules = {"rules": [{"env": {"GH_TOKEN": None}, "of": {"bin": "gh"}}]}
        self.assertTrue(check(rules, [self.line(env={"GH_TOKEN": "x"})])[0].ok)
        self.assertFalse(check(rules, [self.line(env={})])[0].ok)

    def test_label_is_used_in_place_of_the_literal(self):
        rules = {"rules": [{"in-stdin": "hunter2", "of": {"bin": "gh"}, "label": "pw"}]}
        v = check(rules, [self.line(stdin="xx")])[0]
        self.assertEqual((v.ok, v.what), (False, "in-stdin <pw> of {\"bin\":\"gh\"}"))

    def test_an_unknown_call_name_is_an_error(self):
        with self.assertRaisesRegex(BadRules, "unknown call name 'nope'"):
            check({"rules": [{"count": "nope"}]}, [])

    def test_an_unknown_call_name_in_order_is_an_error_even_on_an_empty_trace(self):
        with self.assertRaisesRegex(BadRules, "unknown call name 'nope'"):
            check({"rules": [{"order": ["nope"]}]}, [])

    def test_a_rule_missing_its_of_is_an_error(self):
        with self.assertRaisesRegex(BadRules, "in-stdin needs 'of'"):
            check({"rules": [{"in-stdin": "x"}]}, [])

    def test_a_bad_arg_regex_is_an_error(self):
        with self.assertRaisesRegex(BadRules, "bad arg regex"):
            check({"rules": [{"count": {"arg": "("}}]}, [])

    def test_an_unknown_rule_is_an_error(self):
        with self.assertRaisesRegex(BadRules, "unknown rule"):
            check({"rules": [{"never-in-argv": "x"}]}, [])


RECORDED = Path(__file__).parent / "data" / "recorded" / "greet-ana"


class Secret(unittest.TestCase):
    """The `secret` rule over the trace's argv and what the agent wrote in a recorded transcript (ADR-0011)."""

    def verdict(self, literal, trace=(), written=None):
        written = agent_writing(RECORDED / "with-1" / "transcript.jsonl") if written is None else written
        [v] = check({"rules": [{"secret": literal, "label": "key"}]}, list(trace), written)
        return v

    def test_a_secret_in_a_shim_argv_fails(self):
        v = self.verdict(SECRET, load_trace(DATA / "traces" / "leak-argv.jsonl"))
        self.assertFalse(v.ok)
        self.assertEqual(v.what, "secret <key>")
        self.assertIn("argv on line 3", v.why)

    def test_a_secret_on_stdin_only_passes(self):
        self.assertTrue(self.verdict(SECRET, load_trace(DATA / "traces" / "good.jsonl")).ok)

    def test_a_secret_in_the_agents_reply_fails(self):
        v = self.verdict("from greet")  # the agent repeats greet's output in its reply
        self.assertFalse(v.ok)
        self.assertIn("assistant text", v.why)

    def test_a_secret_in_a_bash_command_fails(self):
        v = self.verdict("Run greet tool for Ana")  # the Bash call's description
        self.assertFalse(v.ok)
        self.assertIn("Bash input", v.why)

    def test_a_secret_in_a_writes_contents_fails(self):
        path = Path(tempfile.mkdtemp()) / "transcript.jsonl"
        write = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "Write",
             "input": {"file_path": "/w/.env", "content": "KEY=sk-live-123\n"}}]}}
        path.write_text((RECORDED / "with-1" / "transcript.jsonl").read_text() + json.dumps(write) + "\n")
        v = self.verdict("sk-live-123", written=agent_writing(path))
        self.assertFalse(v.ok)
        self.assertIn("Write input", v.why)

    def test_a_secret_only_in_a_tool_result_passes(self):
        self.assertTrue(self.verdict(".bashrc: Permission denied\n").ok)  # only in greet's Bash result
        self.assertTrue(self.verdict("Launching skill").ok)

    def test_text_the_harness_injected_as_the_user_passes(self):
        self.assertTrue(self.verdict("reply with its output verbatim").ok)  # SKILL.md, loaded by the Skill tool

    def test_the_literal_is_masked(self):
        v = self.verdict("from greet")
        self.assertNotIn("from greet", v.what + v.why)
        self.assertNotIn(SECRET, report(check({"rules": [{"secret": SECRET}]},
                                               load_trace(DATA / "traces" / "leak-argv.jsonl"))))

    def test_the_label_defaults_to_literal(self):
        [v] = check({"rules": [{"secret": "x"}]}, [])
        self.assertEqual((v.ok, v.what), (True, "secret <literal>"))

    def test_a_secret_needs_a_non_empty_literal(self):
        with self.assertRaisesRegex(BadRules, "secret takes a non-empty string"):
            check({"rules": [{"secret": ""}]}, [])


class EnvKeepList(unittest.TestCase):
    def test_exactly_the_names_the_env_rules_mention(self):
        rules = {"rules": [{"env": {"RLN_AGENT": "1"}, "of": "rln"},
                           {"env": {"GH_TOKEN": None, "RLN_AGENT": "1"}, "of": {"bin": "gh"}},
                           {"count": {"bin": "gh"}, "min": 1}]}
        self.assertEqual(env_keep_list(rules), ["GH_TOKEN", "RLN_AGENT"])

    def test_the_prototype_rules(self):
        self.assertEqual(env_keep_list(RULES), ["RLN_AGENT"])

    def test_no_env_rules(self):
        self.assertEqual(env_keep_list({"rules": []}), [])


if __name__ == "__main__":
    unittest.main()
