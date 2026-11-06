"""Checking one run's trace against a case's `trace-rules.json` (ADR-0008).

A rules file names `calls` (matchers) and lists `rules` that use them, by name or inline.
`check` returns one verdict per rule; any fixture miss fails the run before a rule is read.
Literals written in rules are masked as `<redacted>` in every verdict.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

REDACTED = "<redacted>"


class BadRules(Exception):
    """A trace-rules file drill can't apply."""


@dataclass(frozen=True)
class Verdict:
    rule: int | None  # index into rules["rules"]; None for the fixture-miss check
    ok: bool
    what: str
    why: str = ""


def load_trace(path: Path) -> list[dict]:
    """The trace's lines, each with its 1-based `line` number added."""
    lines = Path(path).read_text().splitlines()
    return [dict(json.loads(text), line=n) for n, text in enumerate(lines, 1) if text.strip()]


def env_keep_list(rules: dict) -> list[str]:
    """The env var names the shims record: exactly those the case's `env` rules mention."""
    return sorted({k for r in rules.get("rules", []) if "env" in r for k in r["env"]})


RULE_KEYS = {"order": (), "count": (), "in-stdin": ("of",), "env": ("of",), "stop-after": ()}
MATCHER_KEYS = {"bin", "args", "arg", "exit"}


def validate(rules: dict) -> None:
    """Raise BadRules for anything `check` couldn't apply: unknown rules or call names, a missing
    `of`, an unknown matcher field or a bad `arg` regex. Run it before any run starts."""
    if not isinstance(rules, dict) or not isinstance(rules.get("rules", []), list):
        raise BadRules("a trace-rules file is an object with a rules list")
    calls = rules.get("calls", {})
    if not isinstance(calls, dict):
        raise BadRules("calls must be an object of named matchers")

    def matcher(x, where):
        if isinstance(x, str):
            if x not in calls:
                raise BadRules(f"{where}: unknown call name {x!r}")
            x = calls[x]
        if not isinstance(x, dict):
            raise BadRules(f"{where}: a call is a name or a matcher, got {x!r}")
        if extra := set(x) - MATCHER_KEYS:
            raise BadRules(f"{where}: unknown matcher field(s) {sorted(extra)}")
        if "arg" in x:
            try:
                re.compile(x["arg"])
            except (re.error, TypeError) as e:
                raise BadRules(f"{where}: bad arg regex {x['arg']!r}: {e}") from e

    for name, m in calls.items():
        matcher(m, f"call {name!r}")
    for i, r in enumerate(rules.get("rules", [])):
        kind = next((k for k in RULE_KEYS if isinstance(r, dict) and k in r), None)
        if kind is None:
            raise BadRules(f"rule {i + 1}: unknown rule {sorted(r) if isinstance(r, dict) else r!r}")
        for key in RULE_KEYS[kind]:
            if key not in r:
                raise BadRules(f"rule {i + 1}: {kind} needs {key!r}")
        targets = r["order"] if kind == "order" else [r[kind] if kind in ("count", "stop-after") else r["of"]]
        if kind == "order" and not isinstance(targets, list):
            raise BadRules(f"rule {i + 1}: order takes a list of calls")
        for t in targets:
            matcher(t, f"rule {i + 1}")
        if kind == "env" and not isinstance(r["env"], dict):
            raise BadRules(f"rule {i + 1}: env takes an object of NAME: value|null")


def _literals(rules: dict) -> list[str]:
    return [r[k] for r in rules.get("rules", []) for k in ("in-stdin", "secret") if isinstance(r.get(k), str) and r[k]]


def _subseq(want, have) -> bool:
    it = iter(have)
    return all(any(w == h for h in it) for w in want)


def _hit(m: dict, c: dict) -> bool:
    argv = c.get("argv") or []
    return (("bin" not in m or m["bin"] == c.get("bin"))
            and ("exit" not in m or m["exit"] == c.get("exit"))
            and ("arg" not in m or any(re.search(m["arg"], a) for a in argv))
            and ("args" not in m or _subseq(m["args"], argv)))


def _lines(xs) -> str:
    return "line " + ", ".join(str(x["line"]) for x in xs) if xs else "none"


def check(rules: dict, trace: list[dict]) -> list[Verdict]:
    literals = _literals(rules)
    trace = [c if "line" in c else dict(c, line=n) for n, c in enumerate(trace, 1)]

    def mask(s: str) -> str:
        for lit in literals:
            s = s.replace(lit, REDACTED)
        return s

    misses = [c for c in trace if "fixture" in c and c["fixture"] is None]
    if misses:
        why = "; ".join(f"line {c['line']}: {' '.join([c.get('bin', '?'), *(c.get('argv') or [])])}" for c in misses)
        return [Verdict(None, False, "no fixture misses", mask(why))]
    validate(rules)

    calls = rules.get("calls", {})

    def m(x) -> dict:
        if isinstance(x, str):
            if x not in calls:
                raise BadRules(f"unknown call name {x!r}")
            return calls[x]
        if isinstance(x, dict):
            return x
        raise BadRules(f"a call is a name or a matcher, got {x!r}")

    def name(x) -> str:
        return x if isinstance(x, str) else json.dumps(x, separators=(",", ":"))

    def of(x) -> list[dict]:
        matcher = m(x)
        return [c for c in trace if _hit(matcher, c)]

    verdicts = []
    for i, r in enumerate(rules.get("rules", [])):
        label = r.get("label", "literal")
        if "order" in r:
            want, k = r["order"], 0
            for c in trace:
                if k < len(want) and _hit(m(want[k]), c):
                    k += 1
            ok = k == len(want)
            why = "" if ok else f"no {name(want[k])} after {'the start' if k == 0 else name(want[k - 1])}"
            v = Verdict(i, ok, "order: " + " → ".join(map(name, want)), why)
        elif "count" in r:
            xs = of(r["count"])
            lo, hi = r.get("min", 0), r.get("max")
            ok = len(xs) >= lo and (hi is None or len(xs) <= hi)
            v = Verdict(i, ok, f"count {name(r['count'])} in {lo}..{'∞' if hi is None else hi}",
                        f"{len(xs)} call(s): {_lines(xs)}")
        elif "in-stdin" in r:
            xs = of(r["of"])
            bad = [c for c in xs if r["in-stdin"] not in (c.get("stdin") or "")]
            v = Verdict(i, bool(xs) and not bad, f"in-stdin <{label}> of {name(r['of'])}",
                        f"no {name(r['of'])} call" if not xs else f"missing on {_lines(bad)}")
        elif "env" in r:
            xs = of(r["of"])
            env = r["env"]
            bad = [c for c in xs if any(k not in (c.get("env") or {}) if want is None
                                        else (c.get("env") or {}).get(k) != want for k, want in env.items())]
            what = "env " + " ".join(f"{k}={'*' if w is None else w}" for k, w in env.items())
            v = Verdict(i, not bad, f"{what} on {name(r['of'])}", f"{len(xs)} call(s), wrong on {_lines(bad)}")
        elif "stop-after" in r:
            first = next((c["line"] for c in of(r["stop-after"])), None)
            after = [c for c in trace if first is not None and c["line"] > first]
            v = Verdict(i, not after, f"stop-after {name(r['stop-after'])}",
                        "never happened" if first is None else f"line {first}, then {_lines(after)}")
        else:
            raise BadRules(f"unknown rule {sorted(r)}")
        verdicts.append(Verdict(v.rule, v.ok, mask(v.what), mask(v.why)))
    return verdicts


def report(verdicts: list[Verdict]) -> str:
    """One line per verdict, the prototype's format."""
    return "\n".join(f"{'ok  ' if v.ok else 'FAIL'} {v.what}{f'  ({v.why})' if v.why else ''}" for v in verdicts)
