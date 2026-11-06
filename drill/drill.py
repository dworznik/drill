"""One drill: every case of a rig on every model, checked, with its evidence and one results file."""

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from drill.cases import Case
from drill.lib.shim import install_shims
from drill.lib.trace_rules import Verdict, check, load_trace
from drill.rig import Rig
from drill.skill import user_cache
from drill.runners import plugin_eval


def run_status(score: float, error: str | None, verdicts: list[Verdict]) -> str:
    """drill's pass rule (ADR-0006): score 1.0 and every trace rule holds; infra errors are `error` (ADR-0004)."""
    if error:
        return "error"
    return "pass" if score == 1.0 and all(v.ok for v in verdicts) else "fail"


SANDBOX_HIDDEN = (Path("/tmp"), Path("/var/tmp"), Path("/dev/shm"))


def _hidden(path: Path) -> bool:
    """Is path under a dir the sandbox hides? Compares resolved paths, since on macOS /tmp is /private/tmp."""
    return any(path.resolve().is_relative_to(h.resolve()) for h in SANDBOX_HIDDEN)


def run_root() -> Path:
    """Where a drill's shim bin/ and plugin eval's temp dirs live: `$TMPDIR` (ADR-0003), else the user cache.

    `plugin eval`'s sandbox denies reads and writes under /tmp, so a Linux host with TMPDIR
    unset would hide the shims and make the workspace read-only. PATH dirs and the run's
    workspace under $HOME stay reachable, so the cache is the fallback.
    """
    tmp = os.environ.get("TMPDIR")
    if tmp and not _hidden(Path(tmp)):
        return Path(tmp)
    return user_cache() / "runs"


def utc_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H%M%SZ")


def drill(rig: Rig, cases: list[Case], skill: Path, revisions: dict, models: list, runs: int | None = None,
          claude: str = "claude", log=print, root: Path | None = None) -> Path:
    """Drill `skill` (a throwaway copy) over `cases` on each model; return the results file path."""
    stamp = utc_stamp()
    results = rig.path / "results"
    evidence = results / stamp
    evidence.mkdir(parents=True, exist_ok=False)
    by_name = {c.name: c for c in cases}
    plugin_eval.prepare(skill, cases)

    root = root or run_root()
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="drill-", dir=root))
    tmp_dir = run_dir / "tmp"
    tmp_dir.mkdir()
    bin_dir = run_dir / "bin"
    install_shims(bin_dir, rig.shims)
    env_file = run_dir / "env.sh"
    env_file.write_text(f'export PATH="{bin_dir}:$PATH"\n')

    run_rows, model_rows, claude_version = [], [], None
    try:
        for model in models:
            out = evidence / model.slug
            log(f"drill: running {len(cases)} case(s) on {model.id}")
            try:
                aggregate = plugin_eval.run(skill, model, bin_dir, env_file, tmp_dir, out, runs=runs, claude=claude)
            except RuntimeError as e:  # keep the other models' runs
                log(f"drill: {e}")
                model_rows.append({"id": model.id, "settings": model.settings, "error": str(e)})
                continue
            claude_version = aggregate.get("claudeVersion", claude_version)
            model_rows.append({"id": model.id, "settings": model.settings,
                               "costUsd": aggregate.get("costUsd"), "durationSeconds": aggregate.get("durationSeconds"),
                               "partial": aggregate.get("partial", False)})
            for r in plugin_eval.runs_of(aggregate):
                run_rows.append(collect(r, by_name.get(r.case), model, out))
                plugin_eval.remove_kept(r.kept)
    finally:
        plugin_eval.force_remove(run_dir)  # also any kept dir a run left without a tracePath

    manifest = {"revisions": revisions, "runner": "plugin-eval", "claudeVersion": claude_version,
                "models": model_rows, "runs": runs, "date": stamp, "rig": rig.path.name}
    path = results / f"{stamp}.json"
    path.write_text(json.dumps({"manifest": manifest, "runs": run_rows}, indent=2) + "\n")
    return path


def collect(r: plugin_eval.RunRecord, case: Case | None, model, out: Path) -> dict:
    """Copy one run's trace and transcript into raw evidence and give it drill's status."""
    dest = out / r.case / f"{r.arm}-{r.index + 1}"
    dest.mkdir(parents=True, exist_ok=True)
    error = r.error
    verdicts: list[Verdict] = []
    if r.transcript:
        shutil.copyfile(r.transcript, dest / "transcript.jsonl")
    elif not error:
        error = "drill: no transcript at plugin eval's tracePath"
    if r.trace and r.trace.is_file():
        shutil.copyfile(r.trace, dest / "trace.jsonl")
        if case is not None:
            try:
                verdicts = check(case.rules, load_trace(r.trace))
            except ValueError as e:  # the agent can write to the trace; JSONDecodeError and UnicodeDecodeError are ValueErrors
                error = error or f"drill: unreadable trace (.drill/trace.jsonl): {e}"
    elif not error:
        error = "drill: no trace (.drill/trace.jsonl) in the run's workspace"
    if case is None and not error:
        error = f"drill: plugin eval ran a case drill didn't copy: {r.case}"
    return {"case": r.case, "model": model.id, "arm": r.arm, "run": r.index + 1, "score": r.score,
            "error": error, "trace": [{"rule": v.what, "ok": v.ok, "why": v.why} for v in verdicts],
            "status": run_status(r.score, error, verdicts)}
