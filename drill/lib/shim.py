"""The one generic shim (ADR-0007) and how drill installs it.

This file is both the installer (`install_shims`) and the shim program itself: drill copies
it into the run's `bin/` behind a shebang naming its own interpreter, then symlinks each faked
binary to that copy. Below `# --- shim ---` it must stay standalone stdlib code.

At call time the shim takes its name from argv[0], walks up from the working directory to
`.drill/`, answers from `.drill/fixtures/<bin>.json` and appends one line to `.drill/trace.jsonl`:
{bin, argv, env, stdin, exit, fixture} (ADR-0008).
"""

import fnmatch
import json
import os
import stat
import sys

SHIM_NAME = ".drill-shim"


def install_shims(bin_dir, names, python=None):
    """Write the shim into bin_dir and symlink bin_dir/<name> to it for each faked binary."""
    os.makedirs(bin_dir, exist_ok=True)
    python = python or os.path.realpath(sys.executable)
    with open(__file__, encoding="utf-8") as f:
        source = f.read()
    shim = os.path.join(bin_dir, SHIM_NAME)
    # -I: ignore PYTHON* env vars and keep the cwd off sys.path, so the agent can't hijack imports.
    with open(shim, "w", encoding="utf-8") as f:
        f.write(f"#!{python} -I\n{source}")
    os.chmod(shim, 0o755)
    for name in names:
        link = os.path.join(bin_dir, name)
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(SHIM_NAME, link)
    return shim


# --- shim ---

def find_drill_dir(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".drill")):
            return os.path.join(d, ".drill")
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def read_stdin():
    """stdin only from a pipe or a regular file: a terminal or an inherited socket could hang."""
    try:
        mode = os.fstat(0).st_mode
    except OSError:
        return None
    if not (stat.S_ISFIFO(mode) or stat.S_ISREG(mode)):
        return None
    data = sys.stdin.buffer.read()
    return data.decode("utf-8", errors="replace") or None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def pick(table, argv, prior_calls):
    """Index of the answering entry, or None. First match wins; identical patterns form a sequence."""
    hits = [i for i, e in enumerate(table)
            if len(e.get("argv", [])) == len(argv)
            and all(fnmatch.fnmatchcase(a, p) for p, a in zip(e["argv"], argv))]
    if not hits:
        return None
    seq = [i for i in hits if table[i]["argv"] == table[hits[0]]["argv"]]
    return seq[min(prior_calls, len(seq) - 1)]


def prior_calls(trace_path, name, argv):
    n = 0
    try:
        with open(trace_path, encoding="utf-8") as f:
            for line in f:
                t = json.loads(line)
                n += t.get("bin") == name and t.get("argv") == argv
    except FileNotFoundError:
        pass
    return n


def main(argv0, argv):
    name = os.path.basename(argv0)
    drill = find_drill_dir(os.getcwd())
    if drill is None:
        sys.stderr.write(f"drill-shim: {name} called outside a drill workspace (no .drill/ above {os.getcwd()})\n")
        return 127
    trace_path = os.path.join(drill, "trace.jsonl")
    fixtures = os.path.join(drill, "fixtures")
    stdin = read_stdin()
    keep = load_json(os.path.join(drill, "env-keep.json"), [])
    env = {k: os.environ[k] for k in keep if k in os.environ}
    table = load_json(os.path.join(fixtures, f"{name}.json"), [])
    index = pick(table, argv, prior_calls(trace_path, name, argv))
    entry = table[index] if index is not None else None
    code = 127 if entry is None else int(entry.get("exit", 0))
    line = {"bin": name, "argv": argv, "env": env, "stdin": stdin, "exit": code, "fixture": index}
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    if entry is None:
        sys.stderr.write(f"drill-shim: no fixture for: {' '.join([name, *argv])}\n")
        return 127
    out = sys.stdout.buffer
    if "stdout_file" in entry:
        with open(os.path.join(fixtures, entry["stdout_file"]), "rb") as f:
            out.write(f.read())
    else:
        value = entry.get("stdout", "")
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n"
        out.write(text.encode())
    out.flush()
    sys.stderr.write(entry.get("stderr", ""))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[0], sys.argv[1:]))
