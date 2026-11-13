"""drill's runner against the real `claude plugin eval`, with a scripted model (tests/fake_api.py).

This checks the mechanics a fake plugin eval can't: the real sandbox reaching the shims and
their Python, drill's scaffold running, and drill finding each run's workspace and trace in
the kept temp dir. It needs `claude` on PATH (on Linux, also bubblewrap and socat) and skips
without it, unless DRILL_REQUIRE_CLAUDE=1, as in CI.
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
from drill.drill import drill
from drill.rig import load_rig
from drill.runners.plugin_eval import Model
from tests.fake_api import serve

DATA = Path(__file__).parent / "data"
REQUIRED = os.environ.get("DRILL_REQUIRE_CLAUDE") == "1"
CLAUDE = shutil.which("claude")

# Variables that would send claude somewhere other than the fake API.
ROUTING = ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
           "CLAUDE_CODE_REMOTE_SDK_URL", "SESSION_INGRESS_URL")


def drill_rig(api, rig_name: str, skill_name: str):
    """Drill tests/data/<rig_name> on tests/data/skill-<skill_name> with real plugin eval and the scripted API."""
    rig_dir = Path(tempfile.mkdtemp()) / rig_name
    shutil.copytree(DATA / rig_name, rig_dir)
    skill = Path(tempfile.mkdtemp()) / skill_name
    shutil.copytree(DATA / f"skill-{skill_name}", skill)
    env = {k: v for k, v in os.environ.items() if k not in ROUTING}
    env.update(ANTHROPIC_BASE_URL=api.url, ANTHROPIC_API_KEY="sk-drill-fake",
               NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    rig = load_rig(rig_dir)
    logs = []
    with mock.patch.dict(os.environ, env, clear=True):
        path = drill(rig, load_cases(rig_dir / "cases"), skill, {}, [Model("haiku", {})], runs=1,
                     claude=CLAUDE, log=logs.append)
    return path, json.loads(path.read_text()), logs


@unittest.skipUnless(CLAUDE or REQUIRED, "needs claude on PATH")
class RunnerMechanics(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(CLAUDE, "DRILL_REQUIRE_CLAUDE=1 but claude is not on PATH")
        self.api = serve()
        self.addCleanup(self.api.shutdown)

    def test_a_scripted_agent_drives_the_shims_through_the_real_plugin_eval(self):
        path, results, logs = drill_rig(self.api, "rig", "hello")
        self.assertEqual(results["manifest"]["models"][0].get("error"), None, "\n".join(map(str, logs)))
        self.assertEqual({(r["arm"], r["status"]) for r in results["runs"]}, {("with", "pass"), ("without", "pass")},
                         json.dumps(results["runs"], indent=1))
        for arm in ("with-1", "without-1"):
            trace = (path.with_suffix("") / "haiku" / "greet-ana" / arm / "trace.jsonl").read_text().splitlines()
            self.assertEqual([json.loads(t)["argv"] for t in trace], [["--name", "Ana"]])
        self.assertTrue(self.api.requests, "claude never called the fake API")


@unittest.skipUnless(CLAUDE or REQUIRED, "needs claude on PATH")
class HttpRouting(unittest.TestCase):
    """A skill that POSTs to an API, drilled through the HTTP fake under the real sandbox."""

    COMMANDS = [
        "curl -sS -H 'content-type: application/json' --data '{\"name\":\"Rex\"}' https://pets.drill.test/v1/adoptions",
        "node -e 'fetch(\"https://pets.drill.test/v1/adoptions/adp_01\").then(r => r.json()).then(j => console.log(j.status))'",
        "curl -sS -m 20 https://ungranted.drill.test/ || echo UNREACHABLE",
    ]

    def setUp(self):
        self.assertIsNotNone(CLAUDE, "DRILL_REQUIRE_CLAUDE=1 but claude is not on PATH")
        self.api = serve(self.COMMANDS, "Rex is adopted.")
        self.addCleanup(self.api.shutdown)

    def test_a_skill_that_posts_to_an_api_drills_end_to_end(self):
        path, results, logs = drill_rig(self.api, "http-rig", "pets")
        self.assertEqual(results["manifest"]["models"][0].get("error"), None, "\n".join(map(str, logs)))
        self.assertEqual({(r["arm"], r["status"]) for r in results["runs"]}, {("with", "pass"), ("without", "pass")},
                         json.dumps(results["runs"], indent=1))
        for arm in ("with-1", "without-1"):
            evidence = path.with_suffix("") / "haiku" / "adopt-rex" / arm
            trace = [json.loads(t) for t in (evidence / "trace.jsonl").read_text().splitlines()]
            self.assertEqual([(t["method"], t["url"], t["status"], t["fixture"]) for t in trace],
                             [("POST", "https://pets.drill.test/v1/adoptions", 201, 0),
                              ("GET", "https://pets.drill.test/v1/adoptions/adp_01", 200, 1)])
            self.assertEqual(json.loads(trace[0]["body"]), {"name": "Rex"})
            self.assertTrue(trace[1]["headers"]["user-agent"].startswith("node"), "node fetch went through the node passthrough")
            results_text = tool_results(evidence / "transcript.jsonl")
            self.assertIn("adopted", results_text[1])
            self.assertNotIn("ungranted.drill.test", " ".join(logs), "the ungranted host reached the fake")
            # the sandbox refuses it before any proxy: its 403, and its own violation report
            self.assertIn("CONNECT tunnel failed, response 403", results_text[2])
            self.assertIn("deny network-outbound ungranted.drill.test:443", results_text[2])


def tool_results(transcript: Path) -> list[str]:
    """The text of each tool result in a stream-json transcript, in order."""
    out = []
    for line in transcript.read_text().splitlines():
        msg = json.loads(line)
        if msg.get("type") != "user":
            continue
        for block in msg.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                c = block.get("content")
                out.append(c if isinstance(c, str) else " ".join(b.get("text", "") for b in c or []))
    return out


if __name__ == "__main__":
    unittest.main()
