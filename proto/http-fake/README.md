# PROTOTYPE: HTTP fixture and trace format (drill#21)

Throwaway. Lives on `proto/21-http-fake`, never merged. Python stdlib plus the `openssl` CLI.

**Question:** what do the HTTP fake's fixture and its trace lines look like, and do the `testnet-garden-claim`
rules still work once its `curl` calls are requests instead of shim calls?

**Run it:** `./demo.sh` (or `./demo.sh good-python`). Captured output: [`demo.out`](demo.out).
It drives scripted stand-ins for the agent (`testnet-garden-claim/agents/`) through the fake with
`HTTPS_PROXY` and the drill CA, the way #20 proved works under the sandbox. It doesn't run `plugin eval`.

| File | What it is |
| :- | :- |
| [`fake.py`](fake.py) | the HTTP fake: a CONNECT proxy that terminates TLS for faked hosts |
| [`mkca`](mkca) | a throwaway CA plus one leaf cert per faked host |
| [`shim`](shim) | the #7 shim ported to Python, only so `rln` lines land in the same trace |
| [`trace_assert.py`](trace_assert.py) | #6's `trace-assert` ported to Python and extended to requests |
| [`testnet-garden-claim/`](testnet-garden-claim) | the claim rig: `fixtures/http.json`, `fixtures/rln.json`, `trace-rules.json` |

## Fixture: `fixtures/http.json`

One table for all faked hosts, the same shape as a shim fixture:

```json
{"method": "POST", "url": "https://testnet.garden/api/v1/acme/claims", "status": 201,
 "headers": {"x-request-id": "r1"}, "body": {"id": "clm_01", "message": "…"}}
```

- **Match** on `method` (default `GET`) and `url`, a glob over the whole URL with scheme, host and query
  (`https://testnet.garden/api/v1/acme/claims/*`). No request-body matching: the trace rules check bodies.
- **Answer** with `status` (default 200), `headers`, and `body` (a string is sent as `text/plain`, any other
  JSON value as `application/json`) or `body_file` (a file under `fixtures/`).
- **First match wins**; later entries with the identical `(method, url)` pattern form a sequence, the last one
  sticks (polling `claims/clm_01` until `funded`). Same rule as the shims.
- **A miss** answers `502 Bad Gateway`, header `x-drill-fixture: miss`, body
  `drill-http: no fixture for: GET https://…`, and traces `"fixture": null`, which fails the run (#6).

## Trace line

Requests go into the same `.drill/trace.jsonl` as shim calls, in call order:

```json
{"method":"POST","url":"https://testnet.garden/api/v1/acme/claims","headers":{"user-agent":"curl/8.5.0","accept":"*/*","content-type":"application/json"},"body":"{\"secret\":\"7Qm2…\",\"recipient\":\"0x1111…\"}","status":201,"fixture":1}
```

| Field | Holds |
| :- | :- |
| `method`, `url` | as the client sent them; `url` is absolute, the port only when it isn't the default |
| `headers` | every request header, names lower-cased, minus hop-by-hop and proxy ones (`connection`, `proxy-*`, `host`, `content-length`, …) |
| `body` | the request body as text, `null` when empty, `{"base64": …}` when it isn't UTF-8 |
| `status` | what the fake answered |
| `fixture` | the entry's index, `null` on a miss |

Headers are kept whole, unlike the shims' `env` keep-list: they hold only what the agent sent, never host secrets.

## Rules

`trace-rules.json` is unchanged in shape. What's new:

- **Matchers** take `method` (equal), `url` (a regex, like `arg`) and `status`. A matcher with `bin`/`args`/`arg`/`exit`
  hits only shim calls, one with `method`/`url`/`status` only requests, so `order` mixes both kinds.
- **`in-body`** is `in-stdin` for requests. **`header`** is `env` for requests, with regex values
  (`"content-type": "^application/json"`), since clients append `; charset=…`.
- **`secret`** fails on a shim argv or a request **URL** holding the literal (plus the transcript, per #8). Bodies and
  headers are the channels a secret is meant to travel in; `in-body` and an `elsewhere` count check where it went.

## The claim rig, ported

`curl` is no longer shimmed; its five rules became request matchers, and the `rln` rules hold beside them
([`trace-rules.json`](testnet-garden-claim/trace-rules.json)). In the demo:

- `good-curl` and `good-python` (urllib) pass all ten checks with identical traces bar `user-agent` and JSON spacing.
- `good-node` reaches the fake with `NODE_USE_ENV_PROXY=1`.
- `secret-in-url` fails `secret` (masked) and is also a fixture miss, since the query string is part of the URL.
- `unfaked-path` sees the 502 and fails the run.
- `no-content-type` (`curl --data` without `-H`) fails `header`.

## What it surfaced

- **Which run a request belongs to.** The fake is one process on the host and `plugin eval` makes each run's workspace
  itself. Proposal: the case's scaffold (which drill writes) `POST`s its `$PWD` to `http://127.0.0.1:<port>/_drill/run`,
  and the fake writes that run's lines into `$PWD/.drill/trace.jsonl` and reads `.drill/fixtures/http.json` from there,
  just as a shim does. Probed with `plugin eval --scaffold` (Linux, Claude Code 2.1.318, 2 runs): the scaffold runs once
  per run, its cwd is the workspace (`…/home/cwd`), it reaches the host's `127.0.0.1`, and its env is scrubbed
  (`HTTPS_PROXY` and a custom var were unset), so drill bakes the port into the scaffold. This needs `--concurrency 1`,
  the default. Not probed on macOS.
- **Python 3.13 rejects a bare CA.** Its default context is X509-strict, so the CA needs `keyUsage` and the leaf needs
  `authorityKeyIdentifier`; curl and node didn't mind. [`mkca`](mkca) has the working extensions.
- **The CA needs the `openssl` CLI.** Python's stdlib can load certs but not mint them. It's on every Mac and Linux box,
  like `curl`, but it's drill's first non-Python dependency.
- **Request-body matching would be brittle.** curl sent `{"secret":"…"}`, urllib's `json.dumps` sent `{"secret": "…"}`.
  Matching bodies as text would make fixtures client-specific, which is what the fake exists to avoid.
- **`curl`'s argv drops out of the trace.** A secret pasted into `curl --data '…'` is now caught only by #8's transcript
  check (the Bash tool input), not by a shim argv.
- **Unfaked hosts are tunnelled, not recorded.** `claude`'s own API traffic comes through the fake; the sandbox already
  refuses the agent's commands any host the rig didn't grant.
