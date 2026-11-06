"""`drill compare`: a markdown diff of two results files, or of two models in one (ADR-0009).

Rows are keyed by (case, runner, model). A change counts as moved only when the two Wilson
intervals don't overlap. Rows whose case hash or skill SHA differ are flagged. There is no
gating: a regression is printed like any other change.
"""

from pathlib import Path

from drill.results import pct, pp, skill_revision
from drill.rig import Refusal


def newest_two(rig: Path) -> tuple[Path, Path]:
    """The rig's two newest results files; their UTC-stamp names sort by time."""
    files = sorted(p for p in (Path(rig) / "results").glob("*.json") if p.is_file())
    if len(files) < 2:
        raise Refusal(f"compare needs two results files; {Path(rig) / 'results'} has {len(files)}")
    return files[-2], files[-1]


def _cell(row: dict | None) -> str:
    return "—" if row is None else f"{row['status']} {row['c']}/{row['n']}"


def _change(a: dict | None, b: dict | None) -> str:
    if a is None or b is None or a["passRate"] is None or b["passRate"] is None:
        return "—"
    return f"{pct(a['passRate'])} → {pct(b['passRate'])} ({pp(b['passRate'] - a['passRate'])})"


def moved(a: dict | None, b: dict | None) -> bool:
    """Do the two Wilson intervals not overlap?"""
    if not a or not b or not a.get("interval") or not b.get("interval"):
        return False
    (alo, ahi), (blo, bhi) = a["interval"], b["interval"]
    return ahi < blo or bhi < alo


def _hash(doc: dict, case: str) -> str | None:
    return (doc.get("cases", {}).get(case) or {}).get("hash")


def _table(header: list[str], lines: list[list[str]], title: str, moves: int) -> str:
    out = [title, "", "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(cells) + " |" for cells in lines]
    out += ["", f"{moves} of {len(lines)} row(s) moved (Wilson 95% intervals don't overlap)."]
    return "\n".join(out) + "\n"


def compare_files(a: dict, b: dict, a_name: str, b_name: str) -> str:
    key = lambda r: (r["case"], r["runner"], r["model"])
    before, after = {key(r): r for r in a["results"]}, {key(r): r for r in b["results"]}
    sa, sb = skill_revision(a), skill_revision(b)
    # a dirty skill may differ from its SHA, so two runs of one dirty SHA can't be called the same skill
    skill_changed = sa.get("sha") != sb.get("sha") or sa.get("dirty") or sb.get("dirty")
    lines, moves = [], 0
    for k in sorted(before.keys() | after.keys()):
        x, y = before.get(k), after.get(k)
        flags = []
        if x and y and _hash(a, k[0]) != _hash(b, k[0]):
            flags.append("case changed")
        if x and y and skill_changed:
            flags.append("skill SHA changed")
        m = moved(x, y)
        moves += m
        lines.append([*k, _cell(x), _cell(y), _change(x, y), "moved" if m else "", ", ".join(flags)])
    return _table(["case", "runner", "model", "before", "after", "pass rate", "moved", "flags"], lines,
                  f"Comparing `{a_name}` → `{b_name}`", moves)


def compare_models(doc: dict, first: str, second: str, name: str) -> str:
    def rows(model):
        return {(r["case"], r["runner"]): r for r in doc["results"] if r["model"] == model}
    before, after = rows(first), rows(second)
    lines, moves = [], 0
    for k in sorted(before.keys() | after.keys()):
        x, y = before.get(k), after.get(k)
        m = moved(x, y)
        moves += m
        lines.append([*k, _cell(x), _cell(y), _change(x, y), "moved" if m else ""])
    return _table(["case", "runner", first, second, "pass rate", "moved"], lines,
                  f"Comparing `{first}` → `{second}` in `{name}`", moves)
