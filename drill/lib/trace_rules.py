"""Checking one run's trace against a case's `trace-rules.json` (ADR-0008, ADR-0014).

The trace holds shim calls {bin, argv, env, stdin, exit, fixture} and HTTP requests
{method, url, headers, body, status, fixture}. A rules file names `calls` (matchers) and lists
`rules` that use them, by name or inline. A matcher's fields say which kind of line it hits:
bin/args/arg/exit only shim calls, method/url/status only requests, so `order` can mix both.
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


RULE_KEYS = {"order": (), "count": (), "secret": (), "in-stdin": ("of",), "in-body": ("of",), "env": ("of",),
             "header": ("of",), "stop-after": ()}
SHIM_KEYS = {"bin", "args", "arg", "exit"}
REQUEST_KEYS = {"method", "url", "status"}
MATCHER_KEYS = SHIM_KEYS | REQUEST_KEYS


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
        if set(x) & SHIM_KEYS and set(x) & REQUEST_KEYS:
            raise BadRules(f"{where}: a matcher hits a shim call or a request, not both: {sorted(x)}")
        if "method" in x and not isinstance(x["method"], str):
            raise BadRules(f"{where}: method is a string, got {x['method']!r}")
        for key in ("exit", "status"):
            if key in x and (not isinstance(x[key], int) or isinstance(x[key], bool)):
                raise BadRules(f"{where}: {key} is a number, got {x[key]!r}")
        for key in ("arg", "url"):
            if key in x:
                _regex(x[key], f"{where}: bad {key} regex")

    for name, m in calls.items():
        matcher(m, f"call {name!r}")
    for i, r in enumerate(rules.get("rules", [])):
        kind = next((k for k in RULE_KEYS if isinstance(r, dict) and k in r), None)
        if kind is None:
            raise BadRules(f"rule {i + 1}: unknown rule {sorted(r) if isinstance(r, dict) else r!r}")
        for key in RULE_KEYS[kind]:
            if key not in r:
                raise BadRules(f"rule {i + 1}: {kind} needs {key!r}")
        targets = (r["order"] if kind == "order" else [] if kind == "secret"
                   else [r[kind] if kind in ("count", "stop-after") else r["of"]])
        if kind == "order" and not isinstance(targets, list):
            raise BadRules(f"rule {i + 1}: order takes a list of calls")
        for t in targets:
            matcher(t, f"rule {i + 1}")
        if kind in ("env", "header") and not isinstance(r[kind], dict):
            raise BadRules(f"rule {i + 1}: {kind} takes an object of NAME: value|null")
        if kind == "header":
            for v in r["header"].values():
                if v is not None:
                    _regex(v, f"rule {i + 1}: bad header regex")
        if kind in ("secret", "in-stdin", "in-body") and not (isinstance(r[kind], str) and r[kind]):
            raise BadRules(f"rule {i + 1}: {kind} takes a non-empty literal")


def _regex(pattern, why: str) -> None:
    try:
        re.compile(pattern)
    except (re.error, TypeError) as e:
        raise BadRules(f"{why} {pattern!r}: {e}") from e


def secrets(rules: dict) -> list[str]:
    """The literals a rules file names, which drill masks wherever it prints."""
    return [r[k] for r in rules.get("rules", []) for k in ("in-stdin", "in-body", "secret")
            if isinstance(r.get(k), str) and r[k]]


def _subseq(want, have) -> bool:
    it = iter(have)
    return all(any(w == h for h in it) for w in want)


def _is_request(c: dict) -> bool:
    return "method" in c


def _hit(m: dict, c: dict) -> bool:
    if set(m) & REQUEST_KEYS:
        return (_is_request(c)
                and ("method" not in m or m["method"].upper() == c.get("method"))
                and ("status" not in m or m["status"] == c.get("status"))
                and ("url" not in m or re.search(m["url"], c.get("url") or "") is not None))
    if set(m) & SHIM_KEYS and _is_request(c):
        return False
    argv = c.get("argv") or []
    return (("bin" not in m or m["bin"] == c.get("bin"))
            and ("exit" not in m or m["exit"] == c.get("exit"))
            and ("arg" not in m or any(re.search(m["arg"], a) for a in argv))
            and ("args" not in m or _subseq(m["args"], argv)))


def _show(c: dict) -> str:
    if _is_request(c):
        return f"{c.get('method')} {c.get('url')}"
    return " ".join([c.get("bin", "?"), *(c.get("argv") or [])])


def _lines(xs) -> str:
    return "line " + ", ".join(str(x["line"]) for x in xs) if xs else "none"


def _text(value) -> str:
    """A stdin or body as text; a body that wasn't UTF-8 is traced as {"base64": …} and holds no literal."""
    return value if isinstance(value, str) else ""


def check(rules: dict, trace: list[dict], written: list[tuple[str, str]] = ()) -> list[Verdict]:
    """One verdict per rule. `written` is what the agent wrote, from `transcript.agent_writing`,
    which only the `secret` rule reads."""
    literals = secrets(rules)
    trace = [c if "line" in c else dict(c, line=n) for n, c in enumerate(trace, 1)]

    def mask(s: str) -> str:
        for lit in literals:
            s = s.replace(lit, REDACTED)
        return s

    misses = [c for c in trace if "fixture" in c and c["fixture"] is None]
    if misses:
        why = "; ".join(f"line {c['line']}: {_show(c)}" for c in misses)
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
        elif "secret" in r:
            # argv, request URLs and what the agent wrote in the transcript (ADR-0011); stdin, bodies and
            # headers are a secret's proper channels (ADR-0014).
            lit = r["secret"]
            where = [f"{'URL' if _is_request(c) else 'argv'} on {_lines([c])}" for c in trace
                     if (lit in (c.get("url") or "") if _is_request(c) else any(lit in a for a in c.get("argv") or []))]
            where += sorted({w for w, text in written if lit in text})
            v = Verdict(i, not where, f"secret <{label}>", "; ".join(where))
        elif "in-stdin" in r or "in-body" in r:
            key, field = ("in-stdin", "stdin") if "in-stdin" in r else ("in-body", "body")
            xs = of(r["of"])
            bad = [c for c in xs if r[key] not in _text(c.get(field))]
            v = Verdict(i, bool(xs) and not bad, f"{key} <{label}> of {name(r['of'])}",
                        f"no {name(r['of'])} call" if not xs else f"missing on {_lines(bad)}")
        elif "env" in r:
            xs = of(r["of"])
            env = r["env"]
            bad = [c for c in xs if any(k not in (c.get("env") or {}) if want is None
                                        else (c.get("env") or {}).get(k) != want for k, want in env.items())]
            what = "env " + " ".join(f"{k}={'*' if w is None else w}" for k, w in env.items())
            v = Verdict(i, not bad, f"{what} on {name(r['of'])}", f"{len(xs)} call(s), wrong on {_lines(bad)}")
        elif "header" in r:
            xs = of(r["of"])
            want = {k.lower(): w for k, w in r["header"].items()}

            def wrong(c) -> bool:
                have = c.get("headers") or {}
                return any(k not in have or (w is not None and re.search(w, str(have[k])) is None)
                           for k, w in want.items())
            bad = [c for c in xs if wrong(c)]
            what = "header " + " ".join(f"{k}={'*' if w is None else w}" for k, w in want.items())
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
