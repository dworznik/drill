# How an agent's HTTP reaches drill's fake under the sandbox

Research for [#20](https://github.com/dworznik/drill/issues/20) (map [#1](https://github.com/dworznik/drill/issues/1), decided by [#11](https://github.com/dworznik/drill/issues/11)). Researched 2026-11-06.

**Question.** Under `claude plugin eval`, shell commands run in Claude Code's OS sandbox and network is "limited to domains you grant with `WebFetch(domain:…)`". drill ships one generic HTTP fake in `lib/` that must catch requests from any client (`curl`, Python `urllib`/`requests`, `node fetch`, `wget`). How does the agent's HTTP reach it, does an unfaked request fail fast, and does `curl -o /tmp/…` work?

**Sources.** [Sandboxing](https://code.claude.com/docs/en/sandboxing), [Test plugins with evals](https://code.claude.com/docs/en/plugin-evals), [Environment variables](https://code.claude.com/docs/en/env-vars), and the open-source runtime Claude Code builds its sandbox on, [`anthropic-experimental/sandbox-runtime`](https://github.com/anthropic-experimental/sandbox-runtime) at commit `27fce0d` (2026-11-06), read as README plus `src/sandbox/*.ts`. The sandboxing page says "Claude Code builds the sandbox on the open source `@anthropic-ai/sandbox-runtime` package", so the runtime's source is primary for mechanism; where Claude Code wires it (which settings load, which env reaches the child) the docs are primary. I could not run `claude` from this session, so everything about `plugin eval` specifically is marked **inferred** and has a probe in [Local probe](#local-probe).

## Answer in one line

Run drill's fake as an HTTP CONNECT proxy on the host, export `HTTPS_PROXY`/`HTTP_PROXY` to it plus a drill CA via the trust env vars before launching `claude plugin eval`, and grant `WebFetch(domain:testnet.garden)`: the sandbox proxy accepts the hostname, then tunnels every allowed connection to the upstream named by `HTTPS_PROXY`, which is drill. No `localhost` base URL, no `/etc/hosts`, no real server. Anything drill doesn't fake gets a `403` from a proxy and never reaches the internet. `curl -o /tmp/testnet-garden-claim.json` fails: `/tmp` itself is not writable, only `$TMPDIR` (`/tmp/claude…`) and the workspace are.

## Summary table

| # | Question | Answer | Status |
| :- | :- | :- | :- |
| 1 | How is the network limit enforced, which env vars are set, does host `HTTPS_PROXY` survive? | Linux: `bwrap --unshare-net` plus socat relays over Unix sockets; macOS: Seatbelt allows outbound only to the proxy port. The child gets `HTTP_PROXY`/`HTTPS_PROXY`/`http_proxy`/`https_proxy`/`ALL_PROXY`/`GRPC_PROXY`=`http://localhost:<port>`, `NO_PROXY`=`localhost,127.0.0.1,::1,` private ranges, `TMPDIR`, `SANDBOX_RUNTIME=1`. **Host `HTTPS_PROXY` is overwritten in the child but consumed by the sandbox proxy as its upstream.** `SSL_CERT_FILE`/`NODE_EXTRA_CA_CERTS`/`CURL_CA_BUNDLE`/`REQUESTS_CA_BUNDLE` are inherited untouched unless `tlsTerminate` is on. | Source-verified |
| 2 | Can the sandbox be pointed at a drill proxy, and does `plugin eval` honor it? | Two mechanisms: `network.httpProxyPort` (replaces the built-in proxy, needs a settings file) and `HTTPS_PROXY` in the launch env (chains behind the built-in proxy, needs no settings). `plugin eval` loads no user or project settings, so `httpProxyPort`, `allowLocalBinding`, `allowUnixSockets` are out of reach (managed settings aside); `HTTPS_PROXY` is on its env allowlist ("proxy and certificate settings"). | Docs; `plugin eval` behavior inferred |
| 3 | Does `localhost:<port>` reach a host server? Smallest route? | Linux: **no**, the sandbox has its own network namespace and `localhost` is private to the command. macOS: no unless `allowLocalBinding: true`, which `plugin eval` can't set. **Route (a)**, the upstream-proxy variant, wins. (b) is dead on Linux and under `plugin eval`; (c) trips the loopback check; (d) rejected. | Docs + source; ranking inferred |
| 4 | Does an unfaked request fail fast, and how does it look? | Yes, twice over. A host not granted: the sandbox proxy answers the CONNECT/request with `HTTP/1.1 403 Forbidden`, header `X-Proxy-Error: blocked-by-allowlist`, body `Connection blocked by network allowlist`, and Claude Code names the denied host in the tool result. A granted host drill doesn't fake: drill's proxy answers 403 itself; nothing is dialed. Clients that ignore proxy vars (plain `node fetch` without `NODE_USE_ENV_PROXY`, `ssh`, DB drivers) can't leave at all: `Network is unreachable` / `Temporary failure in name resolution` on Linux, `Operation not permitted` on macOS. | Source + docs |
| 5 | Does `curl -o /tmp/testnet-garden-claim.json` succeed? | **No.** Writes are allowed to the workspace, `$TMPDIR` (set to `/tmp/claude` or `$CLAUDE_CODE_TMPDIR/claude-<uid>`), and nowhere else by default; `/tmp` as such isn't in the write set. Expect `curl: (23) Failure writing output to destination` (Linux: `Read-only file system`, macOS: `Operation not permitted`). | Source; exact error text inferred |

## 1. How the sandbox enforces its network limit

### Mechanism

From the sandboxing page: "A sandboxed command has no direct route to the network: Linux and WSL2: the command runs in a separate network namespace that has no connection to your network; macOS: the Seatbelt sandbox framework by default blocks connections other than the one to the sandbox proxy. Claude Code runs the sandbox proxy on your machine, outside the sandbox, and directs commands to it with `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and related environment variables. The proxy checks the hostname of each connection against your allowed and denied domains."

The runtime source matches:

- **Linux.** `linux-sandbox-utils.ts` pushes `--unshare-net` and documents the design: "Linux network sandboxing uses bwrap --unshare-net which creates a completely isolated network namespace with NO network access. To enable network access, we: 1. Host side: Run socat bridges that listen on Unix sockets and forward to host proxy servers … 2. Sandbox side: Bind the Unix sockets into the isolated namespace and run socat listeners: HTTP listener on port 3128 … SOCKS listener on port 1080 … 3. Configure environment: `HTTP_PROXY=http://localhost:3128`". The Unix sockets live in the host's `tmpdir()` as `claude-http-<id>.sock` and are `--bind`-mounted in. Domain filtering "happens at the host proxy level, not the sandbox boundary".
- **macOS.** `macos-sandbox-utils.ts` writes a Seatbelt profile that, under network restriction, allows `network-outbound` only to `(remote ip "localhost:<httpProxyPort>")` and `(remote ip "localhost:<socksProxyPort>")`, plus `localhost:*` when `allowLocalBinding` is set.
- Both platforms need the same host-side proxy: `http-proxy.ts` (HTTP and CONNECT) and `socks-proxy.ts`, multiplexed on one port (`mux-proxy.ts`).

### Env vars injected into the child

`generateProxyEnvVars()` in `sandbox-utils.ts` is the single source. For a sandbox with an HTTP proxy port it emits, in this order:

```
SANDBOX_RUNTIME=1
TMPDIR=${CLAUDE_CODE_TMPDIR || CLAUDE_TMPDIR || /tmp/claude}     # unless filesystem isolation is off
NO_PROXY=localhost,127.0.0.1,::1,169.254.0.0/16,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
no_proxy=<same>
HTTP_PROXY=http://[user:token@]localhost:<port>
HTTPS_PROXY=http://[user:token@]localhost:<port>
http_proxy=… https_proxy=…
ALL_PROXY=http://…  all_proxy=…
GRPC_PROXY=http://…  grpc_proxy=…
```

and, when a SOCKS port exists too, `GIT_SSH_COMMAND`, `FTP_PROXY`, `RSYNC_PROXY`, `DOCKER_HTTP_PROXY`, `DOCKER_HTTPS_PROXY`, `CLOUDSDK_PROXY_*`. On Linux these are applied with `bwrap --setenv` (port 3128/1080, the in-sandbox socat listeners), and `CLAUDE_CODE_HOST_HTTP_PROXY_PORT`/`CLAUDE_CODE_HOST_SOCKS_PROXY_PORT` are added "for debugging/transparency". On macOS they are prepended as `env VAR=… ` before `sandbox-exec`. JVMs get `JAVA_TOOL_OPTIONS` pointing at a proxy agent jar.

The env-vars page confirms the overwrite from the docs side: under the subprocess env scrub table, "`HTTP_PROXY`, `HTTPS_PROXY`: Leaves it in place … The sandbox can set these variables itself for sandboxed commands."

**Note on `NO_PROXY`.** It always lists `localhost`, so a client asked for `http://localhost:8080` bypasses the proxy and dials direct. The docs say the same: "Claude Code sets `NO_PROXY` for sandboxed commands so that they connect to `localhost` directly instead of through the proxy." That matters for route (b) below.

### Certificate env vars

`CA_TRUST_VARS` (`NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `PIP_CERT`, `GIT_SSL_CAINFO`, `AWS_CA_BUNDLE`, `CARGO_HTTP_CAINFO`, `DENO_CERT`, `CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE`, `NIX_SSL_CERT_FILE`) are set **only** `if (caCertPath)`, i.e. when the experimental `network.tlsTerminate` is configured. Otherwise the child inherits whatever the parent had. The README says the same for the terminate case: the trust env vars SRT sets "_replace_ each tool's own trust configuration". Claude Code doesn't turn `tlsTerminate` on by default (the docs call it experimental and opt-in), so **drill's CA env vars survive into the sandboxed command.**

### Does a host-set `HTTPS_PROXY` survive?

Two answers, both from source:

- **In the child's environment: no.** It is overwritten with the sandbox proxy URL. `parent-proxy.ts`: "SRT later overwrites HTTP_PROXY etc. in the *sandboxed child's* environment to point at itself".
- **As the sandbox proxy's upstream: yes.** `resolveParentProxy()` reads `cfg.http ?? process.env.HTTP_PROXY ?? process.env.http_proxy`, same for HTTPS (falling back to `HTTP_PROXY`), and `NO_PROXY`. `sandbox-manager.ts` calls it before starting listeners: "Resolve parent/upstream proxy from config or HTTP_PROXY env before we start our own listeners (which will later shadow those vars in the child)". `http-proxy.ts` then, for an allowed host not matched by the parent's `NO_PROXY`, does `connectViaParentProxy(parentUrl, hostname, port)` instead of dialing. The config schema describes `network.parentProxy` as "Falls back to HTTP_PROXY/HTTPS_PROXY/NO_PROXY env vars if unset."

The docs describe this as the corporate-proxy path: "set `HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY` … in the environment you launch Claude Code from. Claude Code enforces the domain allowlist and then tunnels allowed connections through that upstream proxy. `http://` and `https://` proxy URLs work".

Two consequences for drill:

1. The allowlist still applies: `testnet.garden` must be granted, or the sandbox proxy answers 403 before the parent is ever contacted.
2. The resolved-address check is skipped on this path (README: "The check is not evaluated for connections routed through `parentProxy` (including one picked up from `HTTP_PROXY` / `HTTPS_PROXY` in srt's own environment)"; docs: "The proxy also skips the check for connections it sends through an upstream corporate proxy, because that proxy resolves the name"). The sandbox proxy sends `CONNECT testnet.garden:443` to drill and never resolves the name. **drill needs no DNS or `/etc/hosts` trick at all.**

## 2. Pointing the sandbox at a drill proxy, and what `plugin eval` honors

### The settings route: `httpProxyPort` / `socksProxyPort`

Docs, "Custom proxy configuration": "To inspect, filter, or log sandbox traffic with your own tooling, replace the built-in sandbox proxy with a proxy you run on the same machine … set the localhost ports it listens on in sandbox settings: `{"sandbox":{"network":{"httpProxyPort":8080,"socksProxyPort":8081}}}`". Then "your proxy is responsible for filtering everything sent to it. Claude Code's own network controls, such as `allowedDomains`, `deniedDomains`, `strictAllowlist`, approval prompts, and the local-address check, stop applying to that traffic." Source agrees: the schema says "When provided, the library will skip starting its own HTTP proxy and use this port. The external proxy must handle domain filtering."

Caveat from the docs: "If you set a port and also set `HTTPS_PROXY` or `HTTP_PROXY`, Claude Code doesn't forward what sandboxed commands send to your proxy on to the proxy those variables name." So the two mechanisms don't stack; pick one.

Which files may set the port: "`allowManagedDomainsOnly` is on: managed settings only; the sandbox is admin-required, or a narrower network lock applies: managed settings, `--settings`, and user settings; otherwise: any settings file."

### `allowLocalBinding` and `allowUnixSockets`

- `allowLocalBinding` (schema): "macOS: allows binding and accepting on any local port and connecting to localhost on any port … Linux: ignored — the sandbox has its own network namespace, whose loopback is always usable and is not the host one." The Seatbelt profile adds `(allow network-outbound (remote ip "localhost:*"))` when set.
- `allowUnixSockets` (schema): "macOS only: Unix socket paths to allow. Ignored on Linux (seccomp cannot filter by path)." `allowAllUnixSockets` opens every socket on both platforms. Neither helps HTTP: no mainstream HTTP client speaks to a Unix socket unless told to (`curl --unix-socket`), and drill can't control the agent's command line.

### Does `plugin eval` honor any of this? (inferred)

The plugin-evals page: "Each run gets a temporary home directory, working directory, and Claude Code configuration … Nothing personal or project-level loads. Your user settings, hooks, `CLAUDE.md` files, MCP servers, other installed plugins, memory, and skills are absent. Project-scoped configuration isn't read anywhere either: no `.claude/` directory, `CLAUDE.md`, or `.mcp.json` loads from above the workspace or inside it, even one a `scaffold_script` wrote". And: "Managed policy can still restrict a run. Restrictions in managed settings an administrator deployed to the machine apply inside a run".

The CLI flag table for `claude plugin eval` has `--allow-tools`, `--keep-temp`, `--scaffold`, `--mocks`, `--allow-real-servers`, `--tag`, `--json` and similar. **No `--settings` flag is listed.**

So, inferred:

- `sandbox.network.httpProxyPort`, `allowLocalBinding`, `allowUnixSockets` cannot be supplied to a `plugin eval` run from user or project settings. The only file that still loads is managed settings (`/etc/claude-code/managed-settings.json` or the macOS equivalent), which needs admin rights and is global to the machine. Not a route drill should depend on.
- `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` do reach the run: the `env` field doc says "The run inherits only an allowlist from your shell: basics such as `PATH` and locale, **proxy and certificate settings**, the variables that select and authenticate your model provider, most `ANTHROPIC_*` and `CLAUDE_CODE_*` configuration, and `EVAL_*`". The variable names behind "certificate settings" aren't listed. `NODE_EXTRA_CA_CERTS` is a safe bet (Claude Code's own TLS uses it; the env-vars page mentions it under `CLAUDE_CODE_GZIP_REQUEST_BODIES`); `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `REQUESTS_CA_BUNDLE` are **unverified**. The probe below checks each.
- Domain grants: "network access is limited to domains you grant with `--allow-tools "WebFetch(domain:example.com)"`". So `testnet.garden` is granted on the command line; no settings file needed.

## 3. Does `localhost:<port>` reach a host server, and which route is smallest?

### Direct `localhost`

Docs, "A command fails to reach a server on localhost": "By default, a sandboxed command can't connect directly to a server that's running on your machine outside the sandbox … macOS: set `network.allowLocalBinding` to `true` … Linux and WSL2: a sandboxed command's `localhost` is private to that command. The command can listen on a port and reach servers it started itself. A direct connection to `localhost` or `127.0.0.1` doesn't reach servers on the host, and `allowLocalBinding` has no effect."

Also: "An `allowedDomains` entry for `localhost` applies to connections that go through the proxy, so it doesn't change a direct connection. Claude Code sets `NO_PROXY` for sandboxed commands so that they connect to `localhost` directly instead of through the proxy."

So a `localhost` base URL is dead on Linux outright, and dead on macOS under `plugin eval` because `allowLocalBinding` can't be set there. It only works in a loop drill runs itself (where drill controls settings) on macOS.

### Routes, ranked

**(a) HTTPS proxy with a drill CA, upstream-proxy variant: recommended.**

What drill does per run:

1. Start the fake as an HTTP proxy on `127.0.0.1:P` that accepts `CONNECT testnet.garden:443`, terminates TLS with a leaf for `testnet.garden` signed by a drill CA, answers from fixtures, records the request into the trace, and answers plain `GET http://…` absolute-form requests the same way. Any `CONNECT`/request for a host with no fixture gets `403`.
2. Export, before launching: `HTTPS_PROXY=http://127.0.0.1:P`, `HTTP_PROXY=http://127.0.0.1:P`, and the CA path in `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS` (and `GIT_SSL_CAINFO` if a skill uses git over HTTPS). Put the CA file **outside `$HOME`** (the workspace, or `$TMPDIR`): under `plugin eval` "your home directory … [is] unreadable".
3. Run `claude plugin eval <rig> --allow-tools Bash "WebFetch(domain:testnet.garden)"`.

Why this is the smallest: no settings file, no root, no DNS, no change to the skill, and the sandbox's allowlist plus drill's own 403 give two independent leak guards. The parent-proxy path is explicitly supported by both the docs and the runtime, and it skips the loopback check that kills (c).

Unverified pieces: that `plugin eval`'s allowlist passes the CA variables (certain for `HTTPS_PROXY`, likely for `NODE_EXTRA_CA_CERTS`, unknown for the rest); that `WebFetch(domain:…)` grants feed the sandbox allowlist in a `plugin eval` run the same way the docs describe for ordinary sessions ("Claude Code also pre-allows domains from `WebFetch(domain:...)` allow rules"). Both are in the probe.

Client coverage on this route:

| Client | Reads `HTTPS_PROXY`? | Trusts drill CA via | Notes |
| :- | :- | :- | :- |
| `curl` | yes | `CURL_CA_BUNDLE` or `SSL_CERT_FILE` | `--proxy-insecure`/`-k` not needed if the CA is trusted |
| `wget` | yes (`https_proxy`) | `SSL_CERT_FILE` (OpenSSL) or `--ca-certificate` | |
| Python `urllib`, `requests` | yes (`getproxies()`) | `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` | `requests` prefers `REQUESTS_CA_BUNDLE`, then `CURL_CA_BUNDLE` |
| `node fetch` (undici) | **only with `NODE_USE_ENV_PROXY=1`** | `NODE_EXTRA_CA_CERTS` | see below |
| `gh`, other Go tools | yes | system trust store, not env | macOS needs `enableWeakerNetworkIsolation` for Go TLS through a MITM; out of scope |

**`node fetch` caveat.** Node's CLI docs: `NODE_USE_ENV_PROXY=1` (added v24.0.0, v22.21.0) and `--use-env-proxy` (v24.5.0, v22.21.0): "When enabled, Node.js parses the `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` environment variables during startup, and routes requests through the specified proxy." Without it, `fetch()` ignores the proxy variables and dials direct, which the sandbox blocks (the runtime itself sets `NODE_USE_ENV_PROXY=1` only in its Windows path). drill can export `NODE_USE_ENV_PROXY=1` on the host, but whether `plugin eval`'s allowlist passes a `NODE_*` variable is **unverified**. If it doesn't, a `node -e 'fetch(…)'` from the agent fails with a network error rather than reaching the fake; the failure is still fail-closed, and the trace shows no request. Same for `NODE_EXTRA_CA_CERTS` if that isn't passed.

**(a′) same thing via `httpProxyPort`.** Replaces the sandbox proxy with drill's; the allowlist stops applying. Needs a settings file, which `plugin eval` doesn't load. Use only in drill's own `claude -p` loop, and even there (a) is simpler because it keeps the sandbox's allowlist as a second guard.

**(b) `localhost` base URL when the skill takes one.** Dead on Linux (private namespace), dead on macOS under `plugin eval` (`allowLocalBinding` unsettable), and it forces a skill contract change. Also dead through the proxy: `NO_PROXY=localhost` makes every client dial direct. Drop it.

**(c) `/etc/hosts` or DNS redirection.** Needs root on the host, and doesn't even work: the sandbox proxy resolves the name on the host, sees `127.0.0.1`, and refuses ("The command sees a `403` response whose body names the kind of address, such as `Connection to myapp.test blocked: resolved to a loopback address`"). The carve-out is an IP literal with port in `allowedDomains` (`"127.0.0.1:443"`), which `plugin eval` can only take as a `WebFetch(domain:127.0.0.1:443)` grant, and whether an IP:port literal in a `WebFetch` rule reaches the sandbox allowlist is undocumented. And drill would still need the CA. Strictly worse than (a).

**(d) `WebFetch(domain:testnet.garden)` grant plus a real server.** Rejected by #11: drill never touches the real network.

## 4. Does an unfaked request fail fast, and how does it look?

Three layers, from the outside in:

1. **Host not granted.** The sandbox proxy's `filter()` says no. For CONNECT, `http-proxy.ts` writes `rawDenied('Connection blocked by network allowlist', 'blocked-by-allowlist', reason)`, which `request-filter.ts` renders as `HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nX-Proxy-Error: blocked-by-allowlist\r\n\r\nConnection blocked by network allowlist`. For a plain-HTTP request, `respondDenied(res, …)` sends the same 403 with the reason as body. The docs add that "When the sandbox blocks a network connection, Claude Code names the denied host in the command's result, so Claude sees what was blocked", and "the tool receives a `403` response from the proxy". What the agent sees from `curl -v https://other.example/`: `CONNECT other.example:443 HTTP/1.1` then `< HTTP/1.1 403 Forbidden`, exit 56 `Received HTTP code 403 from proxy after CONNECT`. Python `requests` raises `ProxyError('Tunnel connection failed: 403 Forbidden')`. (Exact client messages are from memory, not from these sources.)
   - In a `plugin eval` run there is no one to prompt ("Runs never stop to ask for permission"), so an ungranted host is refused rather than asked about. **Inferred**: the docs' table says a `dontAsk` session refuses and a `bypassPermissions` session allows; `plugin eval` says network is "limited to domains you grant", which matches refuse. The probe checks it.
2. **Host granted, drill has no fixture.** The sandbox proxy forwards the CONNECT to drill (upstream), drill answers 403 (or whatever drill chooses), and nothing is ever dialed to the internet because the sandbox proxy only ever talks to drill on this path. This is drill's own guard and is the one that makes a leak impossible even if someone grants `*`.
3. **Client that ignores proxy variables.** Docs: "Tools that ignore the proxy variables: plain `ssh`, most database drivers, and similar tools can't connect, even to an allowed host"; "The failure looks like a network or name resolution error: macOS: `Operation not permitted`, or … `Could not resolve host`; Linux and WSL2: `Network is unreachable`, or … `Temporary failure in name resolution`". `node fetch` without `NODE_USE_ENV_PROXY` lands here.

There is no fourth path: UDP, QUIC and ICMP "can't leave the sandbox", and on Linux the namespace has no interface but loopback and the two socat listeners.

One more backstop worth knowing: the host `NO_PROXY` the parent-proxy logic reads is the one drill's launcher exports. drill should export `NO_PROXY=` (empty) or leave it unset, so that nothing bypasses drill's proxy on the parent hop.

## 5. Does `curl -o /tmp/testnet-garden-claim.json` succeed?

**No, as written.** Evidence:

- Docs: "By default, commands inside the sandbox can write to the working directory, a per-user temp directory, and any directories you've added"; "Unless you disable filesystem isolation, Claude Code sets `$TMPDIR` to this directory for sandboxed commands". `plugin eval` says "Writes are confined to the run's workspace".
- Source: `SANDBOX_OWN_WRITE_PATHS` is `/dev/stdout`, `/dev/stderr`, `/dev/null`, `/dev/tty`, `/dev/dtracehelper`, `/dev/autofs_nowait`, `/tmp/claude`, `/private/tmp/claude`, plus `~/.npm/_logs` and `~/.claude/debug`. `generateProxyEnvVars` sets `TMPDIR` to `$CLAUDE_CODE_TMPDIR || $CLAUDE_TMPDIR || /tmp/claude`. The env-vars page says Claude Code "appends `/claude-{uid}/`" to `CLAUDE_CODE_TMPDIR`, whose default is "`/tmp` on macOS, `os.tmpdir()` on Linux", so the writable temp dir is `/tmp/claude-<uid>/` or similar; either way it's a subdirectory, and `/tmp` itself is read-only (Linux: `--ro-bind / /` then selective `--bind` of write paths; macOS: Seatbelt `deny file-write*` outside the allowed subpaths).

So `curl -o /tmp/testnet-garden-claim.json` fails at open: curl prints `curl: (23) Failure writing output to destination` (Linux underlying `EROFS`, macOS `EPERM: Operation not permitted`; **exact wording inferred**). The request itself is still made and still lands in drill's trace, so a grader that checks the HTTP request will pass while one that checks the file will not, and the agent will probably retry with another path.

What drill can do: nothing on the drill side without `allowWrite`, which `plugin eval` can't load. The fix belongs to the skill: `-o "${TMPDIR:-/tmp}/testnet-garden-claim.json"` or a workspace-relative path. drill's rig can assert on the request instead of the file. Worth flagging to the `testnet-garden-claim` skill owner as a real portability bug, not just an eval artifact: any user with Claude Code's sandbox on hits it.

## Recommendation

1. **Wire `lib/`'s HTTP fake as an HTTP CONNECT proxy, and make `drill run` set `HTTPS_PROXY`/`HTTP_PROXY` to it plus the drill CA in `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS` (and `NODE_USE_ENV_PROXY=1`) before spawning any runner.** Grant each rig's hosts with `--allow-tools "WebFetch(domain:<host>)"` under `plugin eval`; in drill's own loop, the same env plus `sandbox.network.allowedDomains` in `--settings`. One mechanism for every runner.
2. **The fake answers 403 for any host or path without a fixture.** That, not the sandbox allowlist, is drill's leak guard; the allowlist is the second one.
3. **No base URL in the skill contract.** Route (a) works with the skill's real hostname. Skills don't need to know drill exists.
4. **Record the CA path and proxy port in the trace header** so a grader can tell a run where the env didn't reach the agent (every request missing) from a run where the agent never called the API.
5. **Tell the claim skill owner about `/tmp`.** Suggest `${TMPDIR:-/tmp}` in the skill. drill's rig should grade the request, not the file.

## Local probe

Run on a machine with `claude`, `bubblewrap` and `socat` (Linux) or on macOS. It uses `mitmdump` as a stand-in for drill's proxy (any CONNECT proxy with its own CA works; drill's eventual fake replaces it).

```bash
# 1. a MITM proxy with a CA, outside $HOME so the sandbox can read it
pipx install mitmproxy            # or: brew install mitmproxy
PROBE=/tmp/drill-probe; mkdir -p "$PROBE"
mitmdump --set confdir="$PROBE/mitm" -p 18080 \
  --map-local '|testnet.garden|'"$PROBE"'/fixtures' &   # serves $PROBE/fixtures/<path>
mkdir -p "$PROBE/fixtures" && echo '{"ok":true}' > "$PROBE/fixtures/index.html"
sleep 2; CA="$PROBE/mitm/mitmproxy-ca-cert.pem"

# 2. a throwaway plugin with one eval case
P="$PROBE/plugin"; mkdir -p "$P/.claude-plugin" "$P/evals/probe/graders"
echo '{"name":"probe","version":"0.0.1"}' > "$P/.claude-plugin/plugin.json"
cat > "$P/evals/probe/prompt.md" <<'EOF'
Run each command with Bash and paste its full output verbatim, including errors. Do not retry or fix anything.
1. env | grep -iE 'proxy|cert|ca_bundle|tmpdir|sandbox|node_' | sort
2. curl -sv https://testnet.garden/ -o /tmp/testnet-garden-claim.json; echo "exit=$?"
3. curl -sv https://testnet.garden/ -o "$TMPDIR/claim.json"; echo "exit=$?"; cat "$TMPDIR/claim.json"
4. curl -sv https://example.com/ -o /dev/null; echo "exit=$?"
5. curl -sv http://localhost:18080/ -o /dev/null; echo "exit=$?"
6. python3 -c 'import urllib.request;print(urllib.request.urlopen("https://testnet.garden/").read())'
7. node -e 'fetch("https://testnet.garden/").then(r=>r.text()).then(console.log,e=>console.log("ERR",e.cause||e))'
EOF
cat > "$P/evals/probe/graders/paste.md" <<'EOF'
---
type: regex
pattern: "exit="
---
EOF

# 3. run it with the env drill would set
cd "$P" && HTTPS_PROXY=http://127.0.0.1:18080 HTTP_PROXY=http://127.0.0.1:18080 NO_PROXY= \
  SSL_CERT_FILE="$CA" CURL_CA_BUNDLE="$CA" REQUESTS_CA_BUNDLE="$CA" NODE_EXTRA_CA_CERTS="$CA" \
  NODE_USE_ENV_PROXY=1 \
  claude plugin eval . --allow-tools Bash "WebFetch(domain:testnet.garden)" --keep-temp --json
```

Read the transcript (the JSON, or the `--keep-temp` directory). What each line settles:

| Step | Settles | Expect if this note is right |
| :- | :- | :- |
| 1 | which proxy/CA vars survive `plugin eval`'s allowlist and the sandbox's overwrite | `HTTPS_PROXY=http://localhost:3128` (Linux) or another local port (macOS), `NO_PROXY=localhost,…`, `TMPDIR=/tmp/claude…`, `SANDBOX_RUNTIME=1`, and your four CA vars unchanged. Any CA var missing means the allowlist dropped it |
| 2 | Q5 | `curl: (23)`; mitmdump's log shows the request anyway |
| 3 | writable temp dir | `exit=0` and the fixture body |
| 4 | ungranted host, Q4 layer 1 | `403` on CONNECT, exit 56; mitmdump's log shows **no** request for `example.com` |
| 5 | route (b) | Linux: connection refused; macOS: `Operation not permitted` |
| 6 | Python through the parent proxy with the CA | fixture body |
| 7 | `node fetch` | fixture body only if `NODE_USE_ENV_PROXY` and `NODE_EXTRA_CA_CERTS` both survived (step 1 tells you); otherwise `ERR` with `ENETUNREACH`/`EAI_AGAIN` (Linux) or `EPERM` (macOS) |

If step 4's CONNECT reaches mitmdump, the run is in a permission mode that allows ungranted hosts, and drill must rely on its own 403 alone; the note's ranking still holds, only layer 1 is gone.

## Open uncertainties

- **`plugin eval`'s env allowlist.** "Proxy and certificate settings" is all the docs say. `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` are certain; `NODE_EXTRA_CA_CERTS` likely; `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `REQUESTS_CA_BUNDLE`, `NODE_USE_ENV_PROXY` unknown. Probe step 1.
- **Permission mode of a `plugin eval` run.** Refuse-vs-allow for ungranted hosts is inferred from "limited to domains you grant". Probe step 4.
- **`WebFetch(domain:…)` → sandbox allowlist in `plugin eval`.** Documented for ordinary sessions; the plugin-evals page implies the same. Probe steps 2 and 3.
- **Exact client error strings** (curl 23/56, Python `ProxyError`) are from memory, not the sources above.
- **Linux only read from docs and source.** Nothing was executed on either platform from this session.
- **Claude Code's bundled runtime version.** The source read is `sandbox-runtime` HEAD on 2026-11-06; Claude Code pins some version of `@anthropic-ai/sandbox-runtime`, and the env-var list could differ slightly. The docs' description matches this HEAD, so any drift is likely small.
