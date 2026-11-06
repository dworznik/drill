# PROTOTYPE: trace format and `trace-assert`

Throwaway, for the wayfinder ticket "Trace format and assertion rules" (dworznik/drill#6). Never merged.
Bash + jq only. Run it:

```sh
./testnet-garden-claim/make-traces      # rewrites the hand-written traces
for t in testnet-garden-claim/traces/*.jsonl; do echo "== $t"; ./trace-assert testnet-garden-claim/trace-rules.json "$t"; done
```

The traces are written by hand from the `testnet-garden-claim` skill (`dworznik/testnet-garden`,
`packages/web/agent-skills/testnet-garden-claim/SKILL.md`): its shimmed tools are `curl` and `rln`
(`jq`, `cat`, `printenv` stay real). `good.jsonl` passes; each other trace breaks one of the skill's rules.

## One trace line per shim call

```json
{"bin":"curl","argv":["-sS","-o","/tmp/testnet-garden-claim.json","-w","%{http_code}\\n","-H","content-type: application/json","--data","@-","https://testnet.garden/api/v1/acme/claims"],"env":{},"stdin":"{\"secret\":\"7Qm2…\",\"recipient\":\"0x1111…\"}","exit":0}
```

| Field | Holds |
| :- | :- |
| `bin` | the shim's name (`curl`), not its path |
| `argv` | the arguments after `argv[0]`, verbatim |
| `env` | only the variables on the shim's keep-list, by name; a variable not set is absent, not `null` |
| `stdin` | everything the shim read on stdin, or `null` when stdin was a TTY or empty |
| `exit` | the exit status the shim answered with (from the fixture) |

The file's line order is the call order. No timestamps, sequence numbers, cwd or stdout: nothing a rule reads.

## `trace-rules.json`

Two parts: named **calls** (matchers) and a list of **rules** that use them.

A matcher has any of: `bin` (equal), `args` (these strings appear in `argv` in this order, not necessarily
adjacent, so `["account","sign"]` survives global flags), `arg` (a regex some argv element matches), `exit`.
All given fields must hold. A rule may also take a matcher inline instead of a name.

| Rule | Holds when |
| :- | :- |
| `{"order": [a, b, …]}` | the calls occur in this order, others allowed between (a subsequence) |
| `{"count": a, "min": n, "max": m}` | the number of `a` calls is in `n..m`; `max: 0` is "never" |
| `{"never-in-argv": "<literal>"}` | no argv element of any shim call contains the literal |
| `{"in-stdin": "<literal>", "of": a}` | there is at least one `a` call, and every one got the literal on stdin |
| `{"env": {"K": "v"}, "of": a}` | every `a` call had `K=v`; `"K": null` means only "is set" |
| `{"stop-after": a}` | if an `a` call happens, it is the last call in the trace |

The ticket's `stdin-not-argv` is `never-in-argv` plus `in-stdin` on the same literal, and `env-present` is `env`
with a `null` value. A rule may carry a `label`; literals are printed by label and masked as `<redacted>` in
failure output, so the report never repeats a secret.

## What it showed

- The good trace passes all 8 rules; each bad trace fails the rule it was written to break, plus the
  ones it knocks on (sending the secret to another origin also fails `order` and `count start`).
- `never-in-argv` alone does not catch the other-origin leak: the secret went through stdin, as the skill says.
  The `elsewhere` count rule catches it. Leaks through unshimmed commands (`echo $TESTNET_GARDEN_LINK`) or the
  agent's own messages are not in the trace at all; that is "Secret-leak check as a first-class assertion".
- An empty trace (no shim ever called) fails `order`, the `min` counts and `in-stdin`, and passes the rest.
- The secret literal is written in both the fixture and the rules file. If the fixture changes alone,
  `never-in-argv` passes vacuously, but `in-stdin` fails, which flags the drift.
