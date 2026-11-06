# drill is Python stdlib behind one `drill` CLI, with no justfile

All of drill's logic, shims included, is Python with the standard library only. The bash + jq prototypes had already grown dense (58 lines of jq for trace checks, a glob regex built inside jq), and the results work (Wilson intervals, nested results, diffs) would have been harder still. A minimal `pyproject.toml` (zero dependencies, `requires-python >= 3.12`, interpreter pinned by `uv`) installs one console script, `drill`, with `uv tool install -e .`; `uv run drill …` works uninstalled. Commands are `drill run <rig>`, `drill compare [a b]` and `drill table <results.json>` on stdlib `argparse`; every call has a verb, so a rig named like a command can't collide. drill writes each shim with the absolute path of its own interpreter, so the agent's `PATH` can't swap it; about 30 ms per faked call is accepted. Tests are stdlib `unittest`, seeded from the prototypes' traces and fixtures. Decided in [Language for drill's logic: bash + jq or Python](https://github.com/dworznik/drill/issues/19).

## Considered options

- **Keep bash + jq:** see above.
- **`just`, `invoke`, `poethepoet`, `nox` as task runner:** a dependency for nothing once the CLI exists; the justfile is deleted and its checks move into `drill run`.

## Consequences

The only non-Python dependency is the `openssl` CLI, for the HTTP fake's CA (ADR-0013).
