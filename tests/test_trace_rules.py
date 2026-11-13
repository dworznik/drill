"""trace rules, seeded from the prototype (proto/trace-assert) with its testnet-garden-claim traces.

The prototype's never-in-argv rule is replaced by `secret` (ADR-0011).
The HTTP rules are seeded from proto/21-http-fake, whose scripted agents run through the HTTP fake here.
Expected failures are the bash prototype's own verdicts on the same files.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from drill.lib.trace_rules import BadRules, check, env_keep_list, load_trace, report
from drill.lib.transcript import agent_writing
from tests import test_http_fake

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
        with self.assertRaisesRegex(BadRules, "secret takes a non-empty literal"):
            check({"rules": [{"secret": ""}]}, [])


def shim(**kw):
    return {"bin": "rln", "argv": [], "env": {}, "stdin": None, "exit": 0, "fixture": 0, **kw}


def request(**kw):
    return {"method": "GET", "url": "https://testnet.garden/api", "headers": {}, "body": None, "status": 200,
            "fixture": 0, **kw}


def holds(rule, trace, calls=None):
    """The one verdict of a single rule on trace."""
    [v] = check({"calls": calls or {}, "rules": [rule]}, trace)
    return v.ok


class HttpMatchers(unittest.TestCase):
    """Request matchers and rules, each passing and failing on a trace that mixes shim and HTTP lines."""

    MIXED = [request(url="https://testnet.garden/api/v1/acme"),
             shim(argv=["params"]),
             request(method="POST", url="https://testnet.garden/api/v1/acme/claims", status=201,
                     headers={"content-type": "application/json; charset=utf-8"}, body='{"secret":"%s"}' % SECRET),
             shim(argv=["account", "sign"], stdin="msg")]

    def test_method(self):
        self.assertTrue(holds({"count": {"method": "POST"}, "min": 1, "max": 1}, self.MIXED))
        self.assertFalse(holds({"count": {"method": "DELETE"}, "min": 1}, self.MIXED))

    def test_url_is_a_regex(self):
        self.assertTrue(holds({"count": {"url": "/claims$"}, "min": 1, "max": 1}, self.MIXED))
        self.assertFalse(holds({"count": {"url": "^http://"}, "min": 1}, self.MIXED))

    def test_status(self):
        self.assertTrue(holds({"count": {"status": 201}, "min": 1, "max": 1}, self.MIXED))
        self.assertFalse(holds({"count": {"status": 404}, "min": 1}, self.MIXED))

    def test_a_request_matcher_never_hits_a_shim_line_nor_a_shim_matcher_a_request(self):
        self.assertTrue(holds({"count": {"status": 200}, "min": 1, "max": 1}, self.MIXED))  # shim lines have no status
        self.assertTrue(holds({"count": {"exit": 0}, "min": 2, "max": 2}, self.MIXED))  # requests have no exit

    def test_order_mixes_both_kinds(self):
        calls = {"tenant": {"method": "GET"}, "params": {"bin": "rln", "args": ["params"]},
                 "start": {"method": "POST", "url": "/claims$"}, "sign": {"bin": "rln", "args": ["sign"]}}
        self.assertTrue(holds({"order": ["tenant", "params", "start", "sign"]}, self.MIXED, calls))
        self.assertFalse(holds({"order": ["params", "tenant"]}, self.MIXED, calls))

    def test_in_body(self):
        self.assertTrue(holds({"in-body": SECRET, "of": {"method": "POST"}}, self.MIXED))
        self.assertFalse(holds({"in-body": "nope", "of": {"method": "POST"}}, self.MIXED))
        self.assertFalse(holds({"in-body": SECRET, "of": {"method": "DELETE"}}, self.MIXED), "no such call")

    def test_header_values_are_regexes_and_names_any_case(self):
        self.assertTrue(holds({"header": {"Content-Type": "^application/json"}, "of": {"method": "POST"}}, self.MIXED))
        self.assertFalse(holds({"header": {"content-type": "^text/"}, "of": {"method": "POST"}}, self.MIXED))

    def test_header_null_means_only_present(self):
        self.assertTrue(holds({"header": {"content-type": None}, "of": {"method": "POST"}}, self.MIXED))
        self.assertFalse(holds({"header": {"authorization": None}, "of": {"method": "POST"}}, self.MIXED))

    def test_a_request_miss_names_the_request(self):
        [v] = check({"rules": []}, self.MIXED + [request(url="https://testnet.garden/health", status=502, fixture=None)])
        self.assertEqual((v.ok, v.why), (False, "line 5: GET https://testnet.garden/health"))

    def test_a_matcher_mixing_shim_and_request_fields_is_an_error(self):
        with self.assertRaisesRegex(BadRules, "a shim call or a request, not both"):
            check({"rules": [{"count": {"bin": "curl", "url": "x"}}]}, [])

    def test_a_bad_url_or_header_regex_is_an_error(self):
        with self.assertRaisesRegex(BadRules, "bad url regex"):
            check({"rules": [{"count": {"url": "("}}]}, [])
        with self.assertRaisesRegex(BadRules, "bad header regex"):
            check({"rules": [{"header": {"a": "("}, "of": {"method": "GET"}}]}, [])


class SecretInRequests(unittest.TestCase):
    RULE = {"secret": SECRET, "label": "link secret"}

    def test_a_secret_in_a_shim_argv_fails(self):
        [v] = check({"rules": [self.RULE]}, [request(), shim(argv=["--secret", SECRET])])
        self.assertEqual((v.ok, v.why), (False, "argv on line 2"))

    def test_a_secret_in_a_request_url_fails_and_is_masked(self):
        trace = [request(method="POST", url=f"https://testnet.garden/claims?secret={SECRET}")]
        [v] = check({"rules": [self.RULE]}, trace)
        self.assertFalse(v.ok)
        self.assertEqual((v.what, v.why), ("secret <link secret>", "URL on line 1"))

    def test_the_same_secret_in_a_body_or_header_passes(self):
        trace = [request(method="POST", body=SECRET, headers={"authorization": f"Bearer {SECRET}"}),
                 shim(stdin=SECRET)]
        self.assertTrue(holds(self.RULE, trace))


HTTP_RULES = json.loads((Path(__file__).parent / "data" / "http-claim" / "trace-rules.json").read_text())
# indexes into HTTP_RULES["rules"]: the claim rig's rules ported from curl argv to requests
H_ORDER, H_START, H_POLL, H_ELSEWHERE, H_SECRET, H_IN_BODY, H_HEADER, H_ENV, H_STOP_AFTER = range(9)


class ClaimAgents(unittest.TestCase):
    """The prototype's scripted agents, run through the HTTP fake, checked with the ported claim rules."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("curl"):
            raise unittest.SkipTest("needs curl")

    def verdicts(self, agent):
        driver = test_http_fake.ScriptedAgents(methodName="run")
        driver.setUp()
        self.addCleanup(driver.doCleanups)
        _, trace = driver.drive(agent)
        return check(HTTP_RULES, trace)

    def failed(self, agent):
        return {v.rule for v in self.verdicts(agent) if not v.ok}

    def test_the_good_agents_pass_every_rule(self):
        for agent in ("good-curl", "good-python"):
            with self.subTest(agent):
                verdicts = self.verdicts(agent)
                self.assertEqual(len(verdicts), 9)
                self.assertTrue(all(v.ok for v in verdicts), report(verdicts))

    def test_a_secret_in_the_url_fails_secret(self):
        self.assertEqual(self.failed("secret-in-url"), {H_SECRET, H_ORDER, H_START, H_POLL, H_IN_BODY})
        self.assertNotIn(SECRET, report(self.verdicts("secret-in-url")))

    def test_posting_without_a_content_type_fails_header(self):
        self.assertEqual(self.failed("no-content-type"), {H_HEADER, H_ORDER, H_POLL})

    def test_an_unfaked_path_fails_the_run_on_its_miss(self):
        [v] = self.verdicts("unfaked-path")
        self.assertEqual((v.rule, v.ok, v.why), (None, False, "line 1: GET https://testnet.garden/api/v1/acme/health"))


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
