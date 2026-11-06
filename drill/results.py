"""The results file's computed numbers (ADR-0009): per case × model c/n, Wilson interval, status
and Δ (ADR-0004); per model the mean pass rate and status counts; per case a content hash.

Every number comes from drill's own run status (ADR-0006), on both arms. `error` runs are
left out of n. plugin eval's own scores and deltas stay in the raw evidence.
"""

import hashlib
import json
import math
from pathlib import Path

from drill.rig import Refusal

Z95 = 1.959963984540054
STATUSES = ("pass", "flaky", "fail", "error")


def wilson(c: int, n: int, z: float = Z95) -> list[float] | None:
    """The Wilson score interval for c passes of n runs; None when n is 0."""
    if n == 0:
        return None
    p = c / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def case_status(c: int, n: int) -> str:
    """`pass` when c = n, `fail` when c = 0, `flaky` otherwise; `error` when no run counted."""
    if n == 0:
        return "error"
    return "pass" if c == n else "fail" if c == 0 else "flaky"


def case_hash(case_dir: Path) -> str:
    """sha256 over every file's path and bytes in the case folder, so any edit, addition or rename changes it."""
    h = hashlib.sha256()
    case_dir = Path(case_dir)
    for f in sorted(p for p in case_dir.rglob("*") if p.is_file()):
        rel = f.relative_to(case_dir).as_posix().encode()
        data = f.read_bytes()
        h.update(len(rel).to_bytes(8, "big") + rel + len(data).to_bytes(8, "big") + data)
    return "sha256:" + h.hexdigest()


def _rate(runs: list[dict]) -> tuple[int, int, float | None]:
    counted = [r for r in runs if r["status"] != "error"]
    c = sum(r["status"] == "pass" for r in counted)
    return c, len(counted), (c / len(counted) if counted else None)


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def summarize(manifest: dict, runs: list[dict], case_hashes: dict[str, str]) -> dict:
    """The whole results file from the manifest, every run row and each case's hash."""
    runner = manifest["runner"]
    rows = []
    for model in [m["id"] for m in manifest["models"]]:
        for case in sorted({r["case"] for r in runs if r["model"] == model}):
            mine = [r for r in runs if r["model"] == model and r["case"] == case]
            strip = lambda r: {k: v for k, v in r.items() if k not in ("case", "model", "arm")}
            with_runs = [strip(r) for r in mine if r["arm"] == "with"]
            without_runs = [strip(r) for r in mine if r["arm"] == "without"]
            c, n, rate = _rate(with_runs)
            _, _, without = _rate(without_runs)
            rows.append({"case": case, "runner": runner, "model": model, "runs": with_runs,
                         "c": c, "n": n, "passRate": rate, "interval": wilson(c, n), "status": case_status(c, n),
                         "withoutRuns": without_runs, "withoutPassRate": without,
                         "delta": None if rate is None or without is None else rate - without})
    models = []
    for m in manifest["models"]:
        mine = [r for r in rows if r["model"] == m["id"]]
        both = [r for r in mine if r["delta"] is not None]
        models.append({"model": m["id"], "meanPassRate": _mean(r["passRate"] for r in mine),
                       "meanWithoutPassRate": _mean(r["withoutPassRate"] for r in both),
                       "delta": _mean(r["delta"] for r in both),
                       "counts": {s: sum(r["status"] == s for r in mine) for s in STATUSES}})
    return {"manifest": manifest, "cases": {k: {"hash": v} for k, v in sorted(case_hashes.items())},
            "models": models, "results": rows}


def all_runs(doc: dict) -> list[dict]:
    """Every run in a results file, flat, each with its case, model and arm."""
    return [dict(r, case=row["case"], model=row["model"], arm=arm)
            for row in doc["results"] for arm, key in (("with", "runs"), ("without", "withoutRuns")) for r in row[key]]


def load(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Refusal(f"can't read {path}: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("results"), list) or "manifest" not in doc:
        raise Refusal(f"{path} is not a results file (no manifest and results)")
    return doc


def skill_revision(doc: dict) -> dict:
    """The manifest's skill revision, `{sha, dirty}`, or {} when it has none."""
    return (doc["manifest"].get("revisions") or {}).get("skill") or {}


def pct(rate: float | None) -> str:
    """A pass rate as a whole percent, or `—` when there is none."""
    return "—" if rate is None else f"{round(rate * 100)}%"


def pp(change: float | None) -> str:
    """A pass-rate change in signed percentage points, or `—` when there is none."""
    if change is None:
        return "—"
    d = round(change * 100)
    return f"{'+' if d > 0 else '−' if d < 0 else '±'}{abs(d)} pp"
