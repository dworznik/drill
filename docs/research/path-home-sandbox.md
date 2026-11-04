# How `PATH` and `HOME` reach the sandbox

Research for [#3](https://github.com/dworznik/drill/issues/3) (map [#1](https://github.com/dworznik/drill/issues/1)). Researched 2026-11-04.

**Question.** For each candidate runner, does the agent's child process inherit a `PATH` prepended on the host? Are directories outside `$HOME` readable from Claude Code's OS sandbox? What environment does SkillBench's temp workspace inherit? The answer decides where shims go: on the host `PATH`, scaffolded into the workspace, or both.

## Answer in one line

Every runner that actually executes shell commands passes the host `PATH` through. Two of the five candidates (SkillBench's API adapters and `agent-skills-eval`) never execute shell commands, so shims can't fire there at all. Put the shim *binaries* on a host-prepended `PATH` in a directory **outside `$HOME`**, and have the shims write their trace **into the run's workspace** (or somewhere else the runner lets them write). Seed per-case fixtures into the workspace through each runner's own setup mechanism.

## Summary table

| Runner | Child inherits host `PATH`? | `HOME` the agent sees | Sandbox read of dirs outside `$HOME` | Where the shim can write | Shim placement |
| :- | :- | :- | :- | :- | :- |
| `claude plugin eval` | **Yes**, through an env allowlist that includes `PATH` | Temp home made for the run | Yes. Your real home and Claude config are **unreadable** | Run workspace only | Host `PATH`, dir outside `$HOME`. Fixtures through `scaffold_script` (`--scaffold`) or `add_dirs` |
| SkillBench, CLI adapters (`claude-code`, `codex`, `aider`, `opencode`, `cursor`) | **Yes**, full `os.environ` | Real `HOME` | No sandbox of SkillBench's own. Claude Code's sandbox applies only if the user's settings turn it on | Anywhere the user can write | Host `PATH`. Fixtures through `setup.files` |
| SkillBench, API adapters (`claude`, `openai`, `azure`) | N/A: Bash is **simulated** and never executed | N/A | N/A | N/A | **Shims can't work** |
| `agent-skills-eval` 0.1.1 (built-in provider) | N/A: one chat completion, no tool execution | N/A | N/A | N/A | **Shims can't work**, unless drill writes a custom `Provider` (the loop row below) |
| `evolve` (Claude harness) | **Yes**, `os.Environ()` plus extras | Real `HOME`; `CLAUDE_CONFIG_DIR` points into the workspace | Yes. evolve's sandbox allows all reads | Workspace, and anywhere outside the protected roots | Host `PATH`. Fixtures can't carry the exec bit |
| Plain `claude -p` loop (ours) | **Yes**, we build the env | Whatever we set | Off unless enabled. When on, all reads are allowed except denied paths | Workspace, `$TMPDIR`, added dirs (when the sandbox is on) | Host `PATH` plus `CLAUDE_ENV_FILE` or a temp `HOME` |

## Cross-cutting gotcha: Claude Code re-derives `PATH` from your shell rc

This affects every runner that keeps the real `HOME`: SkillBench `claude-code`, `evolve`, and a plain loop that doesn't override `HOME`.

At session start, Claude Code "sources `~/.zshrc`, `~/.bashrc`, or `~/.profile` depending on your shell, captures the resulting aliases, functions, and shell options, and applies them to every Bash command" ([tools reference, Bash tool behavior](https://code.claude.com/docs/en/tools-reference#bash-tool-behavior)).

The docs don't say this, but the snapshot also pins `PATH`. The local shell snapshot `~/.claude/shell-snapshots/snapshot-zsh-*.sh` (Claude Code 2.1.318, macOS) ends with a literal `export PATH='…'`. That value is the `PATH` after the rc file ran, and every Bash command then sees it. Here `~/.zshrc` prepends `~/.local/bin`, pnpm, bun, MEGAcmd and others, and rvm and pyenv shims end up first. If those directories hold a real binary with the same name as a shim, they land **ahead of** a dir that was prepended before launch, and the real binary wins.

- **Status:** inferred from the snapshot file and the docs. I couldn't run a live `claude -p` probe from this sandboxed agent session (the harness refused to launch `claude`). A five-second test settles it: put a fake `gh` in `/tmp/x`, run `PATH=/tmp/x:$PATH claude -p … --allowedTools Bash`, and check `command -v gh`.
- **Mitigations:**
  1. `CLAUDE_ENV_FILE` points to "a shell script whose contents Claude Code runs before each Bash command in the same shell process" ([env vars](https://code.claude.com/docs/en/env-vars)). A file containing `export PATH="/opt/drill-shims:$PATH"` re-asserts shim priority on every command.
  2. Alternatively, run the agent with a throwaway `HOME` so no rc file prepends anything. That's what `claude plugin eval` already does.
- **Plugin `bin/` is not an alternative.** A plugin's `bin/` is on the Bash tool's `PATH`, but "plugin `bin/` directories come after the user's own `PATH` entries, so a plugin can't shadow `git`, `ls`, or another system command" ([plugin components, `bin/`](https://code.claude.com/docs/en/plugins/components)). Shims for tools that are installed on the host (`gh`, `curl`, `git`) would lose.

## Claude Code's OS sandbox: what it reads and inherits

From [Sandboxing](https://code.claude.com/docs/en/sandboxing):

- **Backends.** "macOS: uses Seatbelt"; "Linux: uses bubblewrap"; WSL2 uses bubblewrap; native Windows runs commands unsandboxed.
- **Scope.** "The sandbox covers shell commands only. Claude's file tools, MCP servers, and hooks run outside it." Bash, PowerShell and Monitor commands "and the processes they start" are confined. A shim that Bash invokes is therefore inside the sandbox.
- **Reads.** "Default read behavior: read access to the entire computer, except certain denied directories." Directories outside `$HOME` such as `/tmp`, `/opt` and `/var/folders/...` are readable by default. `permissions.blockReadsOutsideWorkingDirectories` and `sandbox.filesystem.denyRead` narrow this.
- **Writes.** "The working directory, a per-user temp directory, and directories you've added."
- **Env.** "Environment variables: Inherited from Claude Code." `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` strips credentials only, and the docs don't mention `PATH` being touched.

`claude plugin eval` tightens this ([plugin evals, Grant tools](https://code.claude.com/docs/en/plugin-evals#grant-tools)): "When you grant `Bash` in any form, every command runs under Claude Code's OS-level sandbox. Writes are confined to the run's workspace, **your home directory and Claude Code configuration are unreadable**, and network access is limited…" The sandbox is mandatory there: with no backend, "Claude Code refuses each run rather than running it unconfined". On Linux this needs `bubblewrap` and `socat`.

**Consequence for drill.** The repo lives at `~/Projects/drill`, under `$HOME`. Under `plugin eval`, shims kept in the repo and put on `PATH` would be unreadable. The dir would still be on `PATH`, but Seatbelt or bubblewrap would block reading the script, so the call would fail with permission denied or fall through to the next match. *(Inferred: the docs say home is unreadable but don't describe exec behavior.)* `drill run` should therefore copy (install) the shims to a directory outside `$HOME`, such as `${TMPDIR}/drill-<run>/bin` or `/tmp/drill-…`, and prepend that.

## Per-runner detail

### `claude plugin eval` (Claude Code 2.1.318)

Source: [Test plugins with evals](https://code.claude.com/docs/en/plugin-evals).

- **Process model.** "Each run gets a temporary home directory, working directory, and Claude Code configuration, and the agent under test runs there as a `claude -p` child process with only your plugin loaded."
- **Env.** "The run inherits only an allowlist from your shell: basics such as `PATH` and locale, proxy and certificate settings, the variables that select and authenticate your model provider, most `ANTHROPIC_*` and `CLAUDE_CODE_*` configuration, and `EVAL_*`." The `env` frontmatter accepts only `EVAL_[A-Z0-9_]*` keys.
  - A host-prepended `PATH` **does** reach the agent.
  - Because `HOME` is a fresh temp dir, your `~/.zshrc` isn't sourced, so the rc gotcha above doesn't apply. *(Inference: the temp home has no rc file.)*
  - It's **unknown** whether `CLAUDE_ENV_FILE` is on the allowlist; it isn't a `CLAUDE_CODE_*` name.
  - Drill-specific settings, such as a trace path or fixture root, must travel as `EVAL_DRILL_*` variables.
- **Workspace.** "Each run starts in an empty workspace." Fixtures get in two ways:
  - `context.scaffold_script` is a Bash script in the case dir that "runs as you, outside the agent's sandbox, and only when you pass `--scaffold`". Its env is "your shell's `PATH`, `HOME` set to the run's temporary home directory, `TMPDIR`, and a few constants such as `TERM=dumb`… neither do the case's `EVAL_*` variables." It has a 120-second limit, and it should handle "files and git state only".
  - `context.add_dirs` lists "directories inside the case directory that Claude may read during the run, granted read-only".
- **Case dir hidden.** "A run can't read the eval directory." The only way a shim can read a fixture that sits next to the case is if the dir is listed in `add_dirs`, or if a scaffold copied it into the workspace.
- **Trace location.** Writes are confined to the workspace, so the shim must append its JSONL trace inside the workspace. Graders can then read it with `{ source: file, path: … }`, and `--keep-temp` keeps it.
  - `EVAL_*` vars reach the child, but the workspace's absolute path isn't known before the run. The shim should therefore locate the workspace itself, for example by walking up from `$PWD` to a marker such as `.drill/` that the scaffold wrote.
  - Whether writes to `$TMPDIR` are allowed under plugin eval is **unverified**: the general sandbox allows them, but the plugin-eval page says writes are confined to the workspace.
- **Verification gap.** The guard in this agent session refused `claude plugin eval --help`, so the flags were taken from the docs page, not from local `--help` output. `claude plugin --help` (run locally) confirms the subcommand exists in 2.1.318 and mentions "scaffold scripts … only when you opt in".

### SkillBench 0.1.0 (PyPI `skillbench`)

Source: the sdist `skillbench-0.1.0.tar.gz` from PyPI, read in full. PyPI lists no project URL, so I couldn't find an upstream GitHub repo. The README's clone URL is a `YOUR_USERNAME` placeholder.

- **Workspace.** `core/runner.py` creates it with `workspace = Path(tempfile.mkdtemp(prefix="skillbench_"))`, which puts it under `$TMPDIR` (on macOS, `/var/folders/…`, outside `$HOME`). It then runs `_setup_workspace` and deletes the workspace with `shutil.rmtree` afterward.
- **Setup.**
  - `setup.files[]` entries take `{path, content | fixture}`. `content` is written with `write_text` (mode 0644). `fixture` is copied with `shutil.copy2`, which **preserves the exec bit**, from a path resolved against SkillBench's own current directory.
  - `setup.env` is **parsed but never applied**: no code reads it outside serialization in `core/scenario.py`.
- **`claude-code` adapter** (`adapters/claude_code.py`):
  - Runs `claude -p <prompt> --output-format stream-json --max-turns N --dangerously-skip-permissions` through `asyncio.create_subprocess_exec(..., cwd=str(workspace))` with **no `env=`**. The child therefore inherits SkillBench's entire environment, including `PATH` and the real `HOME`.
  - Because the real `HOME` is used, the user's `~/.claude/settings.json` (including any `sandbox.enabled`) and `~/.zshrc` apply. The rc `PATH` gotcha applies here.
  - The skill is copied into `<workspace>/.claude/skills/<name>/`, including `scripts/`, `references/` and `assets/`.
- **Other CLI adapters.** `aider` and `opencode` build `env = os.environ.copy()` plus provider keys. `codex` and `cursor` pass no `env`, so they inherit everything. All of them inherit `PATH`.
- **API adapters** (`claude`, `openai`, `azure`, and the custom `OPENAI_BASE_URL` endpoint):
  - The Bash tool is fake. `openai_api.py` returns `"[simulated] Would execute: …\n(Tool execution is simulated in eval mode)"`, and `claude_api.py` has the same branch.
  - **No command ever runs, so shims are never invoked and no trace is produced.** This matters for map ticket #5 (the runner for small/local models): SkillBench's `openai` adapter can't exercise shims.

### `agent-skills-eval` 0.1.1 ([darkrishabh/agent-skills-eval](https://github.com/darkrishabh/agent-skills-eval) @ `f10ae2b`)

- **Built-in provider.** The README says "The built-in OpenAI-compatible provider makes a single chat-completion request per target run (plus retries). Function tool definitions let you grade the returned tool-call requests; the provider does not connect to MCP servers, **execute tools**, or run a multi-turn agent loop. To evaluate a skill that needs real MCP execution today, implement a custom `Provider` that owns the tool loop." `PATH` is therefore irrelevant: nothing gets spawned.
- **Experimental runtime.** `src/agent-runtime.ts` ("Not wired into the stable Provider API") does the following:
  - copies a declared fixture dir into `mkdtempSync(path.join(tmpdir(), 'agent-skills-native-'))/target`
  - copies the skill "preserving … executable permissions"
  - rejects fixtures that contain `.git`, `.agents`, `.claude`, `.opencode` or `evals`
  - says outright: "This is not an OS security sandbox."
  - leaves env to each adapter. No native adapters ship yet; they're pending upstream PRs #25 and #35.
- **Verdict.** Usable for shims only if drill writes the agent loop itself, which collapses into the "plain loop" row.

### `evolve` ([bitwise-media-group/evolve](https://github.com/bitwise-media-group/evolve) @ `2c00f9f`)

Identification is my best guess: a Go CLI that "runs behavioral eval suites in throwaway workspaces" with a Claude Code harness. The map doesn't link a repo, so please confirm.

- **Env.** `internal/runner/runner.go` sets `cmd.Env = append(os.Environ(), spec.Env...)`, so host `PATH` is **inherited**. The Claude harness adds `CLAUDE_CONFIG_DIR=<ws>/.evolve/claude-home` and `DISABLE_AUTOUPDATER=1`. `HOME` stays real, so `~/.zshrc` is sourced and the rc gotcha applies.
- **Workspace.** `internal/workspace/workspace.go` calls `os.MkdirTemp(parent, prefix)` (default `$TMPDIR`), symlinks the skill in, and makes the workspace a git repo.
  - Fixtures are written with `os.WriteFile(path, data, 0o644)`, so **the exec bit is dropped** and fixtures can't deliver executable shims.
- **Sandbox.** It's on by default (`cmd/evolve/sandbox.go`).
  - On macOS, the Seatbelt profile is `(allow default)` plus `(deny file-write* (subpath <root>))` per protected root, with the workspace re-allowed.
  - On Linux, it's `bwrap --dev-bind / / … --ro-bind <root> <root> --bind <ws> <ws>`.
  - **Reads are unrestricted.** Writes are denied under the protected roots, which "default to the parent of the repository under test".
  - When evolve's sandbox is on, it disables Claude Code's own sandbox (`--settings {"sandbox":{"enabled":false}}`) because "Seatbelt … cannot nest".
- **Consequence.** The shim trace must go into the workspace or somewhere outside the protected roots. If the skill repo is a submodule inside `drill/`, the drill tree is probably protected and read-only.

### Plain `claude -p`-style loop (ours)

- We spawn the child, so `PATH`, `HOME` and `CLAUDE_CONFIG_DIR` are whatever we pass. Claude Code then hands its env to sandboxed Bash ("Inherited from Claude Code").
- The sandbox is off unless settings enable it. When it's on, reads outside `$HOME` are allowed by default, and writes are limited to cwd, `$TMPDIR` and added dirs.
- To keep shim priority, either set a temp `HOME` (no rc file) or set `CLAUDE_ENV_FILE` to a file that re-prepends the shim dir. The second option is cheaper because it keeps auth from the real config.
  - **Unverified:** whether macOS Keychain auth survives a changed `HOME`. evolve works around a similar problem by bridging credentials.

## Recommendation

**Use both, split by role:**

1. **Shim binaries go on the host `PATH`, in a directory outside `$HOME`.** At run start, `drill run` copies `lib/shims/*` into something like `$TMPDIR/drill-<run-id>/bin`, prepends that to `PATH`, and also writes a `CLAUDE_ENV_FILE` that re-prepends it. This works for `plugin eval`, SkillBench CLI adapters, evolve and our own loop. It's the only option for evolve, which strips the exec bit from fixtures, and the only one that doesn't depend on a runner-specific scaffold feature.
2. **Per-case fixtures and the trace file live in the workspace.** Seed them with the runner's setup mechanism:
   - `plugin eval`: `scaffold_script` plus `--scaffold`
   - SkillBench: `setup.files`
   - evolve: `files`
   - our loop: a direct copy

   The shim finds its fixture root and trace path by walking up from `$PWD` to a marker dir (`.drill/`). This works because `plugin eval` writes are confined to the workspace, `EVAL_*` vars can't carry an absolute workspace path, and evolve denies writes to protected roots.
3. **Drop SkillBench API adapters and `agent-skills-eval`'s built-in provider as shim runners.** They never execute commands. For the small/local-model runner (#5), that leaves a loop of our own, or a CLI harness pointed at an OpenAI-compatible endpoint (for example SkillBench's `aider` or `opencode` adapter), which does inherit `PATH`.

## Open uncertainties

- **Untested live:** whether `~/.zshrc` reordering lets a real binary shadow a host-prepended shim under `claude -p`. The snapshot evidence says yes, but I couldn't launch `claude` from this session. Run the five-second probe above before relying on it.
- **Untested live:** what happens when a shim on `PATH` lives under `$HOME` inside `plugin eval`'s sandbox: an exec failure versus fallthrough to the next `PATH` match.
- **`plugin eval` details:** whether writes to `$TMPDIR` are allowed (the page says workspace only), and whether `CLAUDE_ENV_FILE` passes its env allowlist.
- **`add_dirs` path:** the absolute path the agent sees for an `add_dirs` directory isn't documented. It could be the original case path or a copy.
- **Linux:** bubblewrap behavior is taken from the docs only. Nothing was run on Linux.
- **Runner identity:** the "SkillBench" and "evolve" picks are my best matches. SkillBench is the PyPI `skillbench` 0.1.0 with no upstream repo found; it is distinct from benchflow-ai/skillsbench. evolve is bitwise-media-group/evolve. Confirm these are the tools #1 means.
