#!/bin/bash
# Runs INSIDE the plugin-eval sandbox. Every line is prefixed so the transcript is easy to grep.
p() { echo "PROBE|$*"; }
p "whoami=$(id -un) pwd=$PWD home=$HOME tmpdir=$TMPDIR"
for v in HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY SSL_CERT_FILE CURL_CA_BUNDLE REQUESTS_CA_BUNDLE NODE_EXTRA_CA_CERTS NODE_USE_ENV_PROXY GIT_SSL_CAINFO DRILL_MARKER EVAL_MARKER SANDBOX_RUNTIME PATH; do
  p "env $v=${!v-<unset>}"
done
p "ca-readable=$( [ -r /tmp/drill-probe/ca/bundle.pem ] && echo yes || echo no )"
p "netdev=$(tail -n +3 /proc/net/dev | awk -F: '{print $1}' | tr -d ' ' | tr '\n' ',')"

p "--- curl faked https"
curl -sS --max-time 15 -w ' HTTP=%{http_code}\n' https://testnet.garden/api/v1/claims -H 'content-type: application/json' --data '{"secret":"S3CR3T-PROBE","recipient":"0x1"}' 2>&1 | sed 's/^/PROBE|curl-faked: /'; p "curl-faked-exit=${PIPESTATUS[0]}"

p "--- curl faked plain http"
curl -sS --max-time 15 -w ' HTTP=%{http_code}\n' http://testnet.garden/plain 2>&1 | sed 's/^/PROBE|curl-plain: /'; p "curl-plain-exit=${PIPESTATUS[0]}"

p "--- python urllib faked https"
python3 - <<'EOF' 2>&1 | sed 's/^/PROBE|py: /'
import urllib.request, json
try:
    r = urllib.request.urlopen(urllib.request.Request("https://testnet.garden/api/v1/py", data=b'{"secret":"S3CR3T-PY"}', headers={"content-type":"application/json"}), timeout=15)
    print("OK", r.status, r.read()[:120])
except Exception as e:
    print("FAIL", type(e).__name__, str(e)[:200])
EOF

p "--- node fetch faked https (env as inherited)"
node -e 'fetch("https://testnet.garden/api/v1/node",{method:"POST",body:"{\"secret\":\"S3CR3T-NODE\"}"}).then(r=>r.text().then(t=>console.log("OK",r.status,t.slice(0,120)))).catch(e=>console.log("FAIL",e.code||"",e.cause&&e.cause.code||"",String(e.message).slice(0,160)))' 2>&1 | sed 's/^/PROBE|node: /'

p "--- node fetch with NODE_USE_ENV_PROXY=1 forced"
NODE_USE_ENV_PROXY=1 node -e 'fetch("https://testnet.garden/api/v1/node2").then(r=>r.text().then(t=>console.log("OK",r.status,t.slice(0,120)))).catch(e=>console.log("FAIL",e.code||"",e.cause&&e.cause.code||"",String(e.message).slice(0,160)))' 2>&1 | sed 's/^/PROBE|node-forced: /'

p "--- curl ungranted host (example.com)"
curl -sS --max-time 15 -o /dev/null -w 'HTTP=%{http_code}\n' https://example.com/ 2>&1 | sed 's/^/PROBE|curl-ungranted: /'; p "curl-ungranted-exit=${PIPESTATUS[0]}"
curl -sS --max-time 15 -i http://example.com/ 2>&1 | head -5 | sed 's/^/PROBE|curl-ungranted-plain: /'

p "--- curl direct ip, no proxy"
curl -sS --max-time 8 --noproxy '*' -o /dev/null -w 'HTTP=%{http_code}\n' http://1.1.1.1/ 2>&1 | sed 's/^/PROBE|curl-noproxy: /'; p "curl-noproxy-exit=${PIPESTATUS[0]}"

p "--- writes"
for d in /tmp "$TMPDIR" "$PWD" /opt; do
  if ( : > "$d/drill-probe-write.txt" ) 2>/dev/null; then p "write $d=ok"; rm -f "$d/drill-probe-write.txt"; else p "write $d=FAIL"; fi
done
curl -sS --max-time 15 -o /tmp/testnet-garden-claim.json -w 'HTTP=%{http_code}\n' https://testnet.garden/api/v1/o-tmp 2>&1 | sed 's/^/PROBE|curl-o-tmp: /'; p "curl-o-tmp-exit=${PIPESTATUS[0]}"
p "done"
