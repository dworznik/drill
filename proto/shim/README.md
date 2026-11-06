# PROTOTYPE: shim generator interface (drill#7)

Throwaway. Lives on `proto/7-shim-interface`, never merged.

**Question:** what does a shim look like, and how does a fixture map a call's argv to stdout, stderr and exit code? Prototyped with a fake `gh` (`auth status`, `repo view`, `api <path>`, plus `run view` for polling).

**Run it:** `bash proto/shim/demo.sh` (needs bash and jq). Captured output: [`demo.out`](demo.out).

## What it shows

- **No generator.** One generic script, [`shim`](shim), serves every faked binary. drill symlinks `bin/gh -> shim` on the prepended `PATH` and the script reads its name from `$0`. The fixture directory isn't baked in at generation time, because per #3 the shim finds the workspace at call time by walking up to `.drill/`. So `drill shim <binary> <fixture-dir>` has nothing left to do.
- **A fixture is one JSON table per binary:** `fixtures/gh.json`, an array of `{argv, stdout | stdout_file, stderr, exit}` ([example](example-case/fixtures/gh.json)).
  - `argv` must match the call exactly and have the same length. Each element is a glob, so `"repos/o/r/contents/*"` covers any path.
  - The first matching entry in file order wins, so an exact entry placed above a glob beats it.
  - Later entries with the identical `argv` pattern form a sequence: the Nth identical call gets the Nth entry and the last one sticks. This covers polling (`gh run view` until `completed`).
  - `stdout` may be a string (printed as is) or any JSON value (printed as JSON). `stdout_file` points at a file under `fixtures/` for large bodies. `exit` defaults to 0.
- **A miss** (no entry matches) prints `drill-shim: no fixture for: gh pr list` to stderr, exits 127, and writes `"fixture": null` to the trace.
- **Fixtures sit at `.drill/fixtures/`** in the workspace, not at its root, so a skill running `ls` doesn't stumble on them.
- **Each call appends one trace line:** `{bin, argv, stdin, env, fixture}`. `env` records which variables are present, never their values. The real line format belongs to #6; these fields are placeholders.

## What it surfaced

- **Order is strict.** `--json name` and `--json name,owner,defaultBranchRef --jq .name` both miss an entry written for `--json name,owner,defaultBranchRef`. The shim doesn't understand `gh` flags and doesn't run `--jq`, so a fixture author writes one entry per call shape the skill makes, with the output already filtered.
- **stdin must only be read from a pipe or file.** The first version read `/dev/stdin` whenever it wasn't a terminal and broke when stdin was a socket. An agent's shell can hand a shim anything, so reading the wrong kind of stdin can hang the call.
- **Glob plus sequence was ambiguous** until "first match wins, sequences only among identical patterns". Before that, an exact entry and a glob that both matched were treated as one sequence.
