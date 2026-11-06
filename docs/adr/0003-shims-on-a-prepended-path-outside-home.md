# Shims go on a prepended `PATH` outside `$HOME`; fixtures and trace go in the workspace

`claude plugin eval` inherits the host `PATH` but makes the real `$HOME` unreadable and confines writes to the run's workspace. So `drill run` puts the shim directory under `$TMPDIR/drill-<run>/bin`, prepends it to `PATH`, and sets `CLAUDE_ENV_FILE` to re-prepend it after the shell's rc files. Per-case fixtures and a `.drill/` marker are seeded into the workspace by drill's scaffold; shims and the HTTP fake find them by walking up from the working directory to `.drill/`, and the trace is appended there. Researched in [How `PATH` and `HOME` reach the sandbox](https://github.com/dworznik/drill/issues/3) ([findings](https://github.com/dworznik/drill/blob/research/path-home-sandbox/docs/research/path-home-sandbox.md)).

## Consequences

Runners that simulate or skip tool execution (SkillBench's API adapters, `agent-skills-eval`'s provider) can never fire a shim, so they are not candidate runners.
