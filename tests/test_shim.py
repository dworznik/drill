"""The shim, called the way an agent calls it: through bin/<name> on PATH, from inside a workspace.

Seeded from the prototype's demo.sh (proto/7-shim-interface).
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from drill.lib.shim import install_shims, shim_python

DATA = Path(__file__).parent / "data" / "shim"


class Shim(unittest.TestCase):
    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp())
        self.bin = self.run_dir / "bin"
        install_shims(self.bin, ["gh"])
        self.ws = self.run_dir / "workspace"
        shutil.copytree(DATA, self.ws / ".drill" / "fixtures")
        (self.ws / ".drill" / "env-keep.json").write_text(json.dumps(["GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"]))
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin", "GH_TOKEN": "fake", "GITHUB_TOKEN": "fake2",
                    "OTHER_SECRET": "nope"}

    def gh(self, *args, cwd=None, stdin=subprocess.DEVNULL, input=None):
        kw = {"input": input} if input is not None else {"stdin": stdin}
        return subprocess.run(["gh", *args], cwd=cwd or self.ws, env=self.env, capture_output=True,
                              text=True, timeout=10, **kw)

    def trace(self):
        return [json.loads(line) for line in (self.ws / ".drill" / "trace.jsonl").read_text().splitlines()]

    def last(self):
        return self.trace()[-1]

    # 1. happy path
    def test_stderr_only_answer(self):
        r = self.gh("auth", "status")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("Logged in to github.com account octo-drill", r.stderr)
        self.assertEqual(self.last()["fixture"], 0)

    def test_json_stdout_is_printed_as_json(self):
        r = self.gh("repo", "view", "--json", "name,owner,defaultBranchRef")
        self.assertEqual(json.loads(r.stdout), {"name": "widgets", "owner": {"login": "octo-drill"},
                                                "defaultBranchRef": {"name": "main"}})
        self.assertTrue(r.stdout.endswith("\n"))
        self.assertEqual(self.last()["fixture"], 1)

    # 2. stdout_file, an exact entry beating a glob, the glob erroring
    def test_stdout_file(self):
        r = self.gh("api", "repos/octo-drill/widgets/actions/workflows")
        self.assertEqual(r.stdout, (DATA / "gh" / "workflows.json").read_text())
        self.assertEqual(self.last()["fixture"], 2)

    def test_exact_entry_above_a_glob_wins(self):
        r = self.gh("api", "repos/octo-drill/widgets/contents/README.md")
        self.assertEqual(json.loads(r.stdout), {"name": "README.md", "type": "file"})
        self.assertEqual(self.last()["fixture"], 3)

    def test_glob_star_matches_slashes(self):
        r = self.gh("api", "repos/octo-drill/widgets/contents/.github/workflows/ci.yml")
        self.assertEqual((r.returncode, r.stderr), (1, "gh: Not Found (HTTP 404)\n"))
        self.assertEqual(self.last()["fixture"], 4)

    # 3. sequence
    def test_identical_patterns_answer_in_sequence_and_the_last_sticks(self):
        outs = [self.gh("run", "view", "4242", "--json", "status").stdout for _ in range(3)]
        self.assertEqual([json.loads(o)["status"] for o in outs], ["in_progress", "completed", "completed"])
        self.assertEqual([t["fixture"] for t in self.trace()], [5, 6, 6])

    # 4. misses
    def test_extra_args_miss(self):
        self.assert_miss("repo", "view", "--json", "name,owner,defaultBranchRef", "--jq", ".name")

    def test_fewer_args_miss(self):
        self.assert_miss("repo", "view", "--json", "name")

    def test_flag_order_counts(self):
        self.assert_miss("view", "repo", "--json", "name,owner,defaultBranchRef")

    def test_unknown_command_misses(self):
        self.assert_miss("pr", "list")

    def test_binary_with_no_fixture_file_misses(self):
        install_shims(self.bin, ["gh", "rln"])
        r = subprocess.run(["rln", "params"], cwd=self.ws, env=self.env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stderr), (127, "drill-shim: no fixture for: rln params\n"))
        self.assertEqual(self.last()["fixture"], None)

    def assert_miss(self, *args):
        r = self.gh(*args)
        self.assertEqual(r.returncode, 127)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, f"drill-shim: no fixture for: gh {' '.join(args)}\n")
        self.assertEqual(self.last(), {"bin": "gh", "argv": list(args), "env": self.last()["env"],
                                       "stdin": None, "exit": 127, "fixture": None})

    # 5. stdin, from a subdirectory
    def test_piped_stdin_is_recorded_from_a_subdirectory(self):
        sub = self.ws / "sub" / "dir"
        sub.mkdir(parents=True)
        self.gh("api", "repos/octo-drill/widgets/issues", "--input", "-", cwd=sub, input='{"title":"x"}\n')
        self.assertEqual(self.last()["stdin"], '{"title":"x"}\n')

    def test_stdin_from_a_regular_file_is_recorded(self):
        f = self.run_dir / "in.txt"
        f.write_text("body")
        with f.open() as fh:
            self.gh("auth", "status", stdin=fh)
        self.assertEqual(self.last()["stdin"], "body")

    def test_empty_stdin_is_null(self):
        self.gh("auth", "status", input="")
        self.assertIsNone(self.last()["stdin"])

    def test_an_inherited_socket_is_not_read(self):
        a, b = socket.socketpair()
        try:
            r = self.gh("auth", "status", stdin=a.fileno())  # never written to, never closed: reading would hang
        finally:
            a.close(), b.close()
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(self.last()["stdin"])

    # trace line
    def test_trace_line_shape_and_env_keep_list(self):
        self.gh("auth", "status")
        self.assertEqual(self.last(), {"bin": "gh", "argv": ["auth", "status"],
                                       "env": {"GH_TOKEN": "fake", "GITHUB_TOKEN": "fake2"},
                                       "stdin": None, "exit": 0, "fixture": 0})
        self.assertEqual(list(self.last()), ["bin", "argv", "env", "stdin", "exit", "fixture"])

    def test_no_keep_list_means_no_env(self):
        (self.ws / ".drill" / "env-keep.json").unlink()
        self.gh("auth", "status")
        self.assertEqual(self.last()["env"], {})

    def test_outside_a_workspace(self):
        elsewhere = Path(tempfile.mkdtemp())
        r = self.gh("auth", "status", cwd=elsewhere)
        self.assertEqual(r.returncode, 127)
        self.assertIn("outside a drill workspace", r.stderr)

    # the interpreter is pinned
    def test_python_on_the_agents_path_cannot_swap_the_shim_interpreter(self):
        fake = self.run_dir / "fakepy"
        fake.mkdir()
        for name in ("python", "python3"):
            (fake / name).write_text("#!/bin/sh\necho hijacked; exit 9\n")
            (fake / name).chmod(0o755)
        self.env["PATH"] = f"{fake}:{self.env['PATH']}"
        r = self.gh("auth", "status")
        self.assertEqual(r.returncode, 0)

    def test_a_json_py_in_the_workspace_is_not_imported(self):
        (self.ws / "json.py").write_text("raise SystemExit(9)\n")
        self.assertEqual(self.gh("auth", "status").returncode, 0)


class ShimPython(unittest.TestCase):
    """plugin eval's sandbox denies reads under $HOME, where pyenv and uv keep their Pythons."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.home = self.root / "home"
        for d in ("home/.pyenv/bin", "opt/bin"):
            (self.root / d).mkdir(parents=True)
            py = self.root / d / "python3"
            py.write_text("#!/bin/sh\n")
            py.chmod(0o755)

    def test_an_interpreter_under_home_is_passed_over(self):
        inside, outside = self.home / ".pyenv/bin/python3", self.root / "opt/bin/python3"
        self.assertEqual(shim_python([str(inside), str(outside)], home=str(self.home)), str(outside))

    def test_a_symlink_out_of_home_is_judged_by_its_target(self):
        link = self.root / "opt/bin/python"
        link.symlink_to(self.home / ".pyenv/bin/python3")
        outside = self.root / "opt/bin/python3"
        self.assertEqual(shim_python([str(link), str(outside)], home=str(self.home)), str(outside))


if __name__ == "__main__":
    unittest.main()
