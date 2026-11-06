# drill is the tool; rigs and results live in a private rigs repo

`drill` holds only the tool: `lib/` (shim, HTTP fake, trace checks), the runner file and the drilling procedure. It holds no skill source, no pin to one, no rig and no results. Each skill's rig (pin, cases, fixtures, trace rules) and its `results/` live in one directory of a private `drill-rigs` repo. Private keeps held-out cases out of the skill author's sight and lets the rig for the private `testnet-garden` skill live there; results stay next to the cases and fixtures that produced them. Decided in [How drill gets a skill: submodule or clone at run time](https://github.com/dworznik/drill/issues/15).

## Considered options

- **Skills as git submodules under `vendor/`:** a public repo can't hold a private submodule, and it makes `drill` a client of every skill repo's history.
- **Clone `owner/repo@sha` at run time with the pin in `drill`:** puts the per-skill dependency in the tool, which is what this decision removes.

## Consequences

Drilling a third-party skill is not a feature: any skill is one more rig. Publishing results out of the private repo is a later, separate step.
