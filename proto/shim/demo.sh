#!/usr/bin/env bash
# PROTOTYPE (drill#7). Run: bash proto/shim/demo.sh
#
# Question: what does a shim look like, and how does a fixture map
# argv -> stdout/stderr/exit? This plays drill's part (build bin/, seed the
# workspace) and the agent's part (call gh), printing the answer and the
# trace line after every call.
#
# Shell rather than the usual HTML demo: the thing being prototyped is the
# shell script itself, and it lifts into lib/ as-is.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)

# --- drill's part -----------------------------------------------------------
run=$(mktemp -d "${TMPDIR:-/tmp}/drill-proto.XXXXXX")
mkdir -p "$run/bin" "$run/workspace/.drill"
# "Generating" a shim is one symlink per binary named by the rig.
for b in gh; do ln -s "$here/shim" "$run/bin/$b"; done
# The scaffold step seeds fixtures under .drill/ (not the workspace root,
# so the agent doesn't trip over them in `ls`).
cp -R "$here/example-case/fixtures" "$run/workspace/.drill/fixtures"
export PATH="$run/bin:$PATH" GH_TOKEN=fake
mkdir -p "$run/workspace/sub/dir"

# --- the agent's part -------------------------------------------------------
call() {
    printf '\n\033[1m$ %s\033[0m\n' "$*"
    (cd "$run/workspace/${CWD:-}" && eval "$@")
    echo "[exit $?]"
    printf 'trace: %s\n' "$(tail -n1 "$run/workspace/.drill/trace.jsonl")"
}

echo "== 1. happy path: exact argv, JSON stdout, stderr-only answer"
call gh auth status
call gh repo view --json name,owner,defaultBranchRef

echo; echo "== 2. big answer from a file, an exact entry beating a glob, and the glob returning an error"
call gh api repos/octo-drill/widgets/actions/workflows
call gh api repos/octo-drill/widgets/contents/README.md
call gh api repos/octo-drill/widgets/contents/.github/workflows/ci.yml

echo; echo "== 3. polling: the same argv twice gets two different answers, then sticks"
call gh run view 4242 --json status
call gh run view 4242 --json status
call gh run view 4242 --json status

echo; echo "== 4. misses: flags in another order, an extra --jq, an unknown command"
call gh repo view --json name,owner,defaultBranchRef --jq .name
call gh repo view --json name
call gh pr list

echo; echo "== 5. stdin is recorded, called from a subdirectory"
CWD=sub/dir call "echo '{\"title\":\"x\"}' | gh api repos/octo-drill/widgets/issues --input -"

echo; echo "== full trace ($run/workspace/.drill/trace.jsonl)"
jq -c . "$run/workspace/.drill/trace.jsonl"
rm -rf "$run"
