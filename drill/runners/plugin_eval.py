"""The `claude plugin eval` runner (ADR-0005).

Inputs: the throwaway skill copy, the rig's cases, the shim bin/ and its env file, one model,
an optional run count and an output dir. drill empties the copy's `evals/`, copies the cases
in with drill's own scaffold, runs `plugin eval --keep-temp`, then reads each run back.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from drill.cases import Case
from drill.lib.trace_rules import env_keep_list

SCAFFOLD = ".drill-scaffold.sh"


def scaffold_text(case_dir: Path, keep: list[str]) -> str:
    """drill's scaffold: seed `.drill/` in the run's workspace from absolute paths (ADR-0003)."""
    fixtures = case_dir.resolve() / "fixtures"
    lines = [
        "#!/bin/bash",
        "# Written by drill. Seeds the run's workspace for the shims.",
        "set -euo pipefail",
        'mkdir -p "$PWD/.drill"',
    ]
    if fixtures.is_dir():
        lines.append(f'cp -R {shlex.quote(str(fixtures))} "$PWD/.drill/fixtures"')
    lines += [
        f'printf %s {shlex.quote(json.dumps(keep))} > "$PWD/.drill/env-keep.json"',
        ': > "$PWD/.drill/trace.jsonl"',
        "",
    ]
    return "\n".join(lines)


def with_scaffold(case_yaml: str | None, name: str) -> str:
    """case.yaml text with drill's scaffold_script set. A case that sets its own is refused earlier."""
    entry = f"scaffold_script: {SCAFFOLD}"
    if case_yaml is None:
        return f'schema_version: "1.0"\nname: {json.dumps(name)}\ncontext:\n  {entry}\n'
    lines = case_yaml.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"context\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest and not rest.startswith("#"):
            raise ValueError(f"case {name}: write case.yaml's context as a block, not inline, so drill can add its scaffold")
        indent = next((re.match(r"\s*", l).group(0) for l in lines[i + 1:] if l.strip() and not l.lstrip().startswith("#")), "")
        indent = indent or "  "
        lines.insert(i + 1, f"{indent}{entry}")
        return "\n".join(lines) + "\n"
    return case_yaml.rstrip("\n") + f"\ncontext:\n  {entry}\n"


def prepare(skill: Path, cases: list[Case]) -> None:
    """Empty the skill copy's evals/ and copy the rig's cases in, each with drill's scaffold."""
    evals = skill / "evals"
    shutil.rmtree(evals, ignore_errors=True)
    evals.mkdir()
    for case in cases:
        dest = evals / case.name
        shutil.copytree(case.path, dest, symlinks=False)
        (dest / SCAFFOLD).write_text(scaffold_text(dest, env_keep_list(case.rules)))
        (dest / SCAFFOLD).chmod(0o755)
        yaml = dest / "case.yaml"
        yaml.write_text(with_scaffold(yaml.read_text() if yaml.is_file() else None, case.name))


@dataclass(frozen=True)
class Model:
    id: str
    settings: dict

    @classmethod
    def parse(cls, spec: str) -> "Model":
        """`<id>[:key=val,…]`."""
        mid, _, rest = spec.partition(":")
        if not mid:
            raise ValueError(f"--model needs an id, got {spec!r}")
        settings = {}
        for pair in filter(None, rest.split(",")):
            k, eq, v = pair.partition("=")
            if not eq or not k:
                raise ValueError(f"--model settings are key=val, got {pair!r}")
            settings[k] = v
        if settings:
            raise ValueError(f"plugin eval has no way to apply model settings yet: {', '.join(settings)}")
        return cls(mid, settings)

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", self.id)


def run(skill: Path, model: Model, bin_dir: Path, env_file: Path, tmp_dir: Path, out_dir: Path,
        runs: int | None = None, claude: str = "claude") -> dict:
    """Run every case `runs` times on one model; return plugin eval's aggregate result.

    plugin eval keeps each run's temp dir under TMPDIR, so tmp_dir must sit outside /tmp,
    which its sandbox makes read-only.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [claude, "plugin", "eval", str(skill), "--trust-plugin", "--scaffold", "--keep-temp",
           "--no-publish", "--concurrency", "1", "--allow-tools", "Bash", "--model", model.id,
           "--output-dir", str(out_dir), "--report", str(out_dir / "report.html")]
    if runs is not None:
        cmd += ["--runs", str(runs)]
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", CLAUDE_ENV_FILE=str(env_file),
               TMPDIR=str(tmp_dir))
    try:
        proc = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, text=True, capture_output=True)
    except OSError as e:
        raise RuntimeError(f"could not start {claude} for {model.id}: {e}") from e
    aggregate = out_dir / "aggregate-result.json"
    if not aggregate.is_file():
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-15:])
        raise RuntimeError(f"plugin eval on {model.id} left no aggregate-result.json (exit {proc.returncode}):\n{tail}")
    (out_dir / "plugin-eval.log").write_text(proc.stdout + proc.stderr)
    return json.loads(aggregate.read_text())


@dataclass(frozen=True)
class RunRecord:
    """One plugin eval run, read back from its kept temp dir."""
    case: str
    arm: str
    index: int
    score: float
    error: str | None
    transcript: Path | None
    trace: Path | None
    kept: Path | None


def runs_of(aggregate: dict) -> list[RunRecord]:
    records = []
    for c in aggregate.get("cases", []):
        name = Path(c.get("dir") or c["name"]).name
        for arm, arm_runs in c.get("arms", {}).items():
            for i, r in enumerate(arm_runs):
                tp = Path(r["tracePath"]) if r.get("tracePath") else None
                kept = tp.parent.parent if tp else None
                records.append(RunRecord(
                    case=name, arm=arm, index=i, score=float(r.get("score") or 0), error=r.get("error"),
                    transcript=tp if tp and tp.is_file() else None,
                    trace=(kept / "home" / "cwd" / ".drill" / "trace.jsonl") if kept else None,
                    kept=kept))
    return records


def force_remove(path: Path) -> None:
    """Delete a tree holding plugin eval's kept temp dirs, whose modes it closes: open them first."""
    if not path.is_dir() or path.is_symlink():
        return
    os.chmod(path, 0o700)
    for root, dirs, _ in os.walk(path):
        for d in dirs:
            p = os.path.join(root, d)
            if not os.path.islink(p):
                os.chmod(p, 0o700)
    shutil.rmtree(path, ignore_errors=True)


def remove_kept(kept: Path | None) -> None:
    """Delete one run's kept temp dir once its evidence is copied."""
    if kept and kept.name.startswith("claude-eval-"):
        force_remove(kept)
