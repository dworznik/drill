#!/usr/bin/env bash
# PROTOTYPE (drill#21): run each scripted agent against the HTTP fake and the rln shim,
# then check its trace with the claim rig's rules. Needs python3, openssl, curl (node optional).
# This drives the agents directly, not through `plugin eval`: the route itself was proven in #20.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd) rig=$here/testnet-garden-claim
work=$(mktemp -d) port=18080
"$here/mkca" "$work/certs" testnet.garden
python3 "$here/fake.py" --port $port --certs "$work/certs" & fake=$!
trap 'kill $fake' EXIT
mkdir -p "$work/bin" && ln -s "$here/shim" "$work/bin/rln"
sleep 0.5
for a in "${@:-good-curl good-python good-node secret-in-url unfaked-path no-content-type}"; do for a in $a; do
  ws=$work/runs/$a; mkdir -p "$ws/.drill" "$ws/tmp"
  cp -r "$rig/fixtures" "$ws/.drill/"; echo '["RLN_AGENT"]' > "$ws/.drill/env-keep.json"
  # what the scaffold would do at the start of each run: tell the fake where this run lives
  curl -sS --noproxy '*' -X POST "http://127.0.0.1:$port/_drill/run" --data "{\"workspace\":\"$ws\"}"
  echo "===== $a"
  (cd "$ws" && env -u NO_PROXY -u no_proxy PATH="$work/bin:$PATH" TMPDIR="$ws/tmp" \
      HTTPS_PROXY=http://127.0.0.1:$port HTTP_PROXY=http://127.0.0.1:$port https_proxy=http://127.0.0.1:$port http_proxy=http://127.0.0.1:$port \
      SSL_CERT_FILE="$work/certs/ca.pem" CURL_CA_BUNDLE="$work/certs/ca.pem" REQUESTS_CA_BUNDLE="$work/certs/ca.pem" NODE_EXTRA_CA_CERTS="$work/certs/ca.pem" \
      SECRET=7Qm2XkP9fLr4TzWc8NbVh3 "$rig/agents/$a" 2>&1 | sed 's/^/  agent| /')
  echo "--- trace"; sed 's/^/  /' "$ws/.drill/trace.jsonl"
  echo "--- trace-assert"; python3 "$here/trace_assert.py" "$rig/trace-rules.json" "$ws/.drill/trace.jsonl" | sed 's/^/  /'
done; done
