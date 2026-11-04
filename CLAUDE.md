## Commits

Conventional Commits: `<type>(<scope>): <summary>`, imperative, lower-case, no trailing period, at most 72 characters. The body says why.

- **Types:** `feat fix docs test refactor chore ci build perf`.
- **Scope** is optional; use it when a change is confined to one area: `lib`, `rigs`, `docs`, `adr`.

## Branches and pull requests

- `main` is the only long-lived branch. Never commit to `main`; branch first, including for docs-only changes.
- **Names:** `<type>/<issue>-<slug>`, for example `feat/2-repo-skeleton`. `type` is a commit type, `issue` is the GitHub ticket number, and `slug` is short kebab-case.
    - With no ticket, drop the number (`chore/agent-skills-config`).
    - `research/*` and `proto/*` are the wayfinder's throwaway branches. They are pushed but never merged.
- **Merging:** PRs are squash-merged, so the PR title becomes the commit on `main`. It follows the commit format.
- **Open a PR at the end of every implementation** (`gh pr create --base main`). The body says:
    - what changed;
    - any decision worth a second opinion;
    - explicitly, anything an acceptance criterion asked for that you couldn't verify.
- **A PR that builds on another is stacked natively:** open it on the parent's branch, then create the stack with `POST /repos/dworznik/drill/stacks`, listing the PR numbers bottom first, with header `X-GitHub-Api-Version: 2026-03-10`. Say so in the PR body. A base branch pointing at another PR without a stack is not a stack.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `dworznik/drill` (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
