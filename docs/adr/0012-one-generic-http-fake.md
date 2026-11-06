# One generic, client-agnostic HTTP fake in `lib/`

A skill that says only "POST to this API" leaves the client to the agent: `curl`, Python, `node`, `wget`. A shimmed `curl` misses every other client, so drill has one HTTP fake in `lib/`, shared by every rig, that answers from per-case fixtures and records each request into the trace whatever client sent it. The `plugin eval` sandbox limits network to granted domains, so a request the fake doesn't intercept fails instead of reaching the real host. drill never touches a live service; live smoke tests are the skill's concern. Decided in [Mock HTTP server: generic or per rig](https://github.com/dworznik/drill/issues/11).

## Considered options

- **A `curl` shim with fixtures:** enough for `testnet-garden-claim` today, wrong for any skill that doesn't name its client.
- **A mock server per rig:** the same piece rebuilt in every rig that speaks HTTP.
