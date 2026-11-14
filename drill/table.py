"""`drill table`: the public per-model summary of a results file, for a skill's README (ADR-0009).

It names no case and shows nothing from one, since held-out cases stay private.
"""

from drill.results import pct, pp, skill_revision


def render(doc: dict) -> str:
    lines = ["| model | pass | flaky | fail | mean pass rate | Δ |", "|---|---:|---:|---:|---:|---:|"]
    for m in doc.get("models", []):
        c = m["counts"]
        lines.append(f"| {m['model']} | {c['pass']} | {c['flaky']} | {c['fail']} | "
                     f"{pct(m['meanPassRate'])} | {pp(m['delta'])} |")
    manifest = doc["manifest"]
    skill = skill_revision(doc)
    sha = f"`{skill['sha'][:12]}`" if skill.get("sha") else "unknown"
    dirty = " (uncommitted changes)" if skill.get("dirty") else ""
    date = (manifest.get("date") or "unknown")[:10]
    lines += ["", f"Skill {sha}{dirty} · {date} · runner {manifest.get('runner', 'unknown')}"]
    return "\n".join(lines) + "\n"
