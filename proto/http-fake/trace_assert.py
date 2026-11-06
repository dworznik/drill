#!/usr/bin/env python3
"""PROTOTYPE (drill#21): the #6 trace-assert ported to Python, extended to HTTP lines.
usage: trace_assert.py <trace-rules.json> <trace.jsonl>   (exit 1 on any FAIL)

A trace line is a shim call {bin, argv, env, stdin, exit, fixture} or a
request {method, url, headers, body, status, fixture}. A matcher's keys say
which kind it hits: bin/args/arg/exit for shim calls, method/url/status for
requests. Any line with "fixture": null fails the run (#6's addendum)."""
import json, re, sys


def subseq(want, have):
    i = 0
    for x in have:
        if i < len(want) and want[i] == x:
            i += 1
    return i == len(want)


def hit(c, t):
    if any(k in c for k in ("bin", "args", "arg", "exit")):
        if "bin" not in t:
            return False
        return (c.get("bin") in (None, t["bin"]) and c.get("exit") in (None, t["exit"])
                and ("arg" not in c or any(re.search(c["arg"], a) for a in t["argv"]))
                and ("args" not in c or subseq(c["args"], t["argv"])))
    if any(k in c for k in ("method", "url", "status")):
        if "method" not in t:
            return False
        return (c.get("method") in (None, t["method"]) and c.get("status") in (None, t["status"])
                and ("url" not in c or re.search(c["url"], t["url"])))
    return True


def main(rules_path, trace_path):
    R = json.load(open(rules_path))
    T = [dict(json.loads(l), line=n) for n, l in enumerate(open(trace_path), 1) if l.strip()]
    m = lambda x: R["calls"][x] if isinstance(x, str) else x
    name = lambda x: x if isinstance(x, str) else json.dumps(x)
    lines = lambda xs: "line " + ", ".join(str(t["line"]) for t in xs) if xs else "none"
    show = lambda t: f'{t["bin"]} {" ".join(t["argv"])}' if "bin" in t else f'{t["method"]} {t["url"]}'
    out = []

    misses = [t for t in T if t.get("fixture", 0) is None]
    out.append((not misses, "every call answered by a fixture", "; ".join(f"line {t['line']}: {show(t)}" for t in misses)))

    for r in R["rules"]:
        if "order" in r:
            k = 0
            for t in T:
                if k < len(r["order"]) and hit(m(r["order"][k]), t):
                    k += 1
            ok = k == len(r["order"])
            out.append((ok, "order: " + " → ".join(r["order"]),
                        "" if ok else f"no {r['order'][k]} after {r['order'][k-1] if k else 'the start'}"))
        elif "count" in r:
            xs = [t for t in T if hit(m(r["count"]), t)]
            lo, hi = r.get("min", 0), r.get("max", float("inf"))
            out.append((lo <= len(xs) <= hi, f"count {name(r['count'])} in {lo}..{'∞' if hi == float('inf') else hi}",
                        f"{len(xs)} call(s): {lines(xs)}"))
        elif "secret" in r:
            s = r["secret"]
            mask = lambda v: v.replace(s, "<redacted>")
            xs = [t for t in T if ("bin" in t and any(s in a for a in t["argv"])) or ("method" in t and s in t["url"])]
            out.append((not xs, f"secret <{r.get('label', 'literal')}> not in argv or URL",
                        "; ".join(f"line {t['line']}: {mask(show(t))}" for t in xs)))
        elif "in-stdin" in r or "in-body" in r:
            key, field = ("in-stdin", "stdin") if "in-stdin" in r else ("in-body", "body")
            of = [t for t in T if hit(m(r["of"]), t)]
            xs = [t for t in of if r[key] not in (t.get(field) or "")]
            out.append((of and not xs, f"{key} <{r.get('label', 'literal')}> of {name(r['of'])}",
                        f"no {name(r['of'])} call" if not of else f"missing on {lines(xs)}"))
        elif "env" in r or "header" in r:
            key, field = ("env", "env") if "env" in r else ("header", "headers")
            of = [t for t in T if hit(m(r["of"]), t)]
            def bad(t):
                for k, v in r[key].items():
                    have = t.get(field, {}).get(k.lower() if key == "header" else k)
                    if have is None or (v is not None and (have != v if key == "env" else not re.search(v, have))):
                        return True
            xs = [t for t in of if bad(t)]
            out.append((not xs, f"{key} {' '.join(f'{k}={v or '*'}' for k, v in r[key].items())} on {name(r['of'])}",
                        f"{len(of)} call(s), wrong on {lines(xs)}"))
        elif "stop-after" in r:
            at = next((t["line"] for t in T if hit(m(r["stop-after"]), t)), None)
            xs = [t for t in T if at is not None and t["line"] > at]
            out.append((not xs, f"stop-after {name(r['stop-after'])}",
                        "never happened" if at is None else f"line {at}, then {lines(xs)}"))
        else:
            sys.exit(f"unknown rule {list(r)}")

    for ok, what, why in out:
        print(f"{'ok  ' if ok else 'FAIL'} {what}" + (f"  ({why})" if why else ""))
    return 0 if all(o for o, _, _ in out) else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
