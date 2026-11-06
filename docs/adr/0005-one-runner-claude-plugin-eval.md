# v1 has one runner, `claude plugin eval`

Claude Code is the main use case, so v1 drives it through `claude plugin eval` only. The runner is its own file, `runners/plugin-eval`, whose inputs are written down (case, throwaway skill copy, shim `bin/`, model, settings, run count, output dir); there is no general adapter interface until a second runner exists. A drill takes one or more `--model <id>[:key=val,…]` and writes one results file. Decided in [Which runners in v1, and what does `drill run` hand each one](https://github.com/dworznik/drill/issues/5).

## Considered options

- **SkillBench's `claude-code` CLI adapter:** can run shims, but runs unsandboxed with `--dangerously-skip-permissions`, once per scenario and with no baseline arm; drill does its own repetition and trace checks, so it adds nothing over `plugin eval`, which at least gives the with/without-skill Δ.
- **evolve:** no documented local-model support, and it writes fixtures without the exec bit.
- **aider:** declines every suggested shell command when headless.
- **`skill-creator`:** an agent-driven workflow, not a headless runner; its `evals.json` is a case format.

## Consequences

Other coding CLIs (codex, gemini, opencode, pi) and small or local models are post-v1, each one more runner file.
