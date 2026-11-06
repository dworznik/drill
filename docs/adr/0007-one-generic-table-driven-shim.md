# One generic table-driven shim, no generator

A single `lib/shim` serves every faked binary: drill symlinks `bin/<binary>` to it for each binary listed in `rig.json` `"shims"`, and the shim reads its name from `$0`. Each case answers from `fixtures/<bin>.json`, an array of `{argv, stdout | stdout_file, stderr, exit}`. `argv` is the same length as the call and each element is a glob (`*` also matches `/`); the first match wins; later entries with an identical `argv` pattern answer in sequence, the last one sticking. Matching is strict and tool-agnostic: flag order counts, and the shim never parses flags or applies `--jq`. A miss prints `drill-shim: no fixture for: <bin> <args>`, exits 127 and fails the run. Decided in [Shim generator interface](https://github.com/dworznik/drill/issues/7), prototyped on [`proto/7-shim-interface`](https://github.com/dworznik/drill/tree/proto/7-shim-interface/proto/shim).

## Consequences

The rig has no `shims/` directory and no `drill shim` command exists. The shim reads stdin only when it is a pipe or regular file, since an inherited socket can hang it.
