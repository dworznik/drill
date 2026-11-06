# The HTTP fake is a CONNECT proxy with its own CA, reached through `HTTPS_PROXY`

Under `plugin eval`'s sandbox the run's `localhost` doesn't reach the host and DNS tricks are refused, but the sandbox proxy forwards allowed hosts to the upstream proxy named by the launch environment's `HTTPS_PROXY`/`HTTP_PROXY`. So the fake is an HTTP CONNECT proxy with a drill CA. `drill run` points `HTTPS_PROXY` and `HTTP_PROXY` at it, appends the CA to the user's bundle and exports it in `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `REQUESTS_CA_BUNDLE` and `NODE_EXTRA_CA_CERTS` (the sandbox makes that file readable), and grants each rig's hosts with `--allow-tools "WebFetch(domain:…)"`. Ungranted hosts get the sandbox's 403; unknown granted hosts are tunnelled upstream and the allowlist is the guard. Probed on Linux and macOS: HTTPS reaches the fake for `curl`, Python and `node`. Researched in [How an agent's HTTP reaches drill's fake under the sandbox](https://github.com/dworznik/drill/issues/20) ([findings](https://github.com/dworznik/drill/blob/research/http-fake-routing/docs/research/http-fake-routing.md)).

## Consequences

- `node fetch` ignores proxy vars and `NODE_USE_ENV_PROXY` doesn't pass the env allowlist, so drill needs a `node` shim that sets `NODE_USE_ENV_PROXY=1`.
- `/tmp` is denied inside a run on macOS; only the workspace and `$TMPDIR` are writable, so drill writes nothing a run reads under `/tmp`, and the scaffold uses absolute paths since it runs with the run's `HOME`.
- The CA is minted with the `openssl` CLI, with `keyUsage` on the CA and `authorityKeyIdentifier` on leaves, or Python 3.13 rejects it.
