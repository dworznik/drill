"""`drill table`: the public per-model summary of a results file, for a skill's README (ADR-0009).

It names no case and shows nothing from one, since held-out cases stay private.
"""


def _rate(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


def _delta(x: float | None) -> str:
    if x is None:
        return "—"
    d = round(x * 100)
    return f"{'+' if d > 0 else '−' if d < 0 else '±'}{abs(d)} pp"


def render(doc: dict) -> str:
    lines = ["| model | pass | flaky | fail | mean pass rate | Δ |", "|---|---:|---:|---:|---:|---:|"]
    for m in doc.get("models", []):
        c = m["counts"]
        lines.append(f"| {m['model']} | {c['pass']} | {c['flaky']} | {c['fail']} | "
                     f"{_rate(m['meanPassRate'])} | {_delta(m['delta'])} |")
    manifest = doc["manifest"]
    skill = (manifest.get("revisions") or {}).get("skill") or {}
    sha = f"`{skill['sha'][:12]}`" if skill.get("sha") else "unknown"
    dirty = " (uncommitted changes)" if skill.get("dirty") else ""
    date = (manifest.get("date") or "unknown")[:10]
    lines += ["", f"Skill {sha}{dirty} · {date} · runner {manifest.get('runner', 'unknown')}"]
    return "\n".join(lines) + "\n"
