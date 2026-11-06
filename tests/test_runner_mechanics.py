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
from drill.results import all_runs
from drill.rig import load_rig
from drill.runners.plugin_eval import Model
from tests.fake_api import serve

DATA = Path(__file__).parent / "data"
REQUIRED = os.environ.get("DRILL_REQUIRE_CLAUDE") == "1"
CLAUDE = shutil.which("claude")

# Variables that would send claude somewhere other than the fake API.
ROUTING = ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
           "CLAUDE_CODE_REMOTE_SDK_URL", "SESSION_INGRESS_URL")


@unittest.skipUnless(CLAUDE or REQUIRED, "needs claude on PATH")
class RunnerMechanics(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(CLAUDE, "DRILL_REQUIRE_CLAUDE=1 but claude is not on PATH")
        self.api = serve()
        self.addCleanup(self.api.shutdown)

    def test_a_scripted_agent_drives_the_shims_through_the_real_plugin_eval(self):
        rig_dir = Path(tempfile.mkdtemp()) / "hello"
        shutil.copytree(DATA / "rig", rig_dir)
        skill = Path(tempfile.mkdtemp()) / "hello"
        shutil.copytree(DATA / "skill-hello", skill)
        env = {k: v for k, v in os.environ.items() if k not in ROUTING}
        env.update(ANTHROPIC_BASE_URL=self.api.url, ANTHROPIC_API_KEY="sk-drill-fake",
                   NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        rig = load_rig(rig_dir)
        logs = []
        with mock.patch.dict(os.environ, env, clear=True):
            path = drill(rig, load_cases(rig_dir / "cases"), skill, {}, [Model("haiku", {})], runs=1,
                         claude=CLAUDE, log=logs.append)
        results = json.loads(path.read_text())
        self.assertEqual(results["manifest"]["models"][0].get("error"), None, "\n".join(map(str, logs)))
        self.assertEqual({(r["arm"], r["status"]) for r in all_runs(results)}, {("with", "pass"), ("without", "pass")},
                         json.dumps(all_runs(results), indent=1))
        for arm in ("with-1", "without-1"):
            trace = (path.with_suffix("") / "haiku" / "greet-ana" / arm / "trace.jsonl").read_text().splitlines()
            self.assertEqual([json.loads(t)["argv"] for t in trace], [["--name", "Ana"]])
        self.assertTrue(self.api.requests, "claude never called the fake API")


if __name__ == "__main__":
    unittest.main()
