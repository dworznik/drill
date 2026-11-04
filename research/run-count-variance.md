# Cross-model runs: run count and reporting of variance

Research for [dworznik/drill#4](https://github.com/dworznik/drill/issues/4), child of map [#1](https://github.com/dworznik/drill/issues/1). Researched 2026-11-04.

## Question

What do existing skill-eval tools (`claude plugin eval`, SkillBench, Anthropic's `skill-creator`, `agent-skills-eval`) report across repeated runs: mean, stddev, per-run outcomes, pass@k / pass^k? What is a sane default `--runs` for a 7B model? Does any tool report "flaky" distinct from "fails", and by what rule?

## Short answer

- Only `claude plugin eval` and `skill-creator` repeat a case at all. Both default to **3 runs** and report a **mean**. `skill-creator` adds stddev/min/max; `claude plugin eval` adds per-run grader results and a "perfect runs" share. SkillBench 0.1.0 and `agent-skills-eval` 0.1.1 run each case **once**.
- **No tool reports pass@k, pass^k, or a confidence interval**, and **none classifies a case as "flaky"** by a mechanical rule. `skill-creator` and agentskills.io only tell a human (or an analyzer agent) to treat "high stddev" as possibly flaky.
- At n=3 a case that passes 3/3 has a 95% Wilson interval of [0.44, 1.00]: 3 runs cannot tell a reliable case from a coin flip. For a local 7B runner, where a run costs wall-clock time and no money, **default to 10 runs**; keep `claude plugin eval` at its own default of 3 because it is billed.
- Recommended rule, borrowed from CI flaky-test tooling (Develocity, Bazel): with `c` passes out of `n` valid runs, **pass** if `c = n`, **fail** if `c = 0`, **flaky** if `0 < c < n`. Runs that ended on an infrastructure error are counted separately, not as fails.

## What each tool reports

### `claude plugin eval` (Claude Code)

Source: official docs, [Test plugins with evals](https://code.claude.com/docs/en/plugin-evals); CLI help of the installed `claude` binary.

- **Run count.** "One run of a non-deterministic agent tells you little, so each case runs three times by default." `--runs <n>` overrides the per-case `runs` field (default 3, range 1 to 50). The count is runs per arm, so with the no-plugin baseline a case costs 2n agent runs, "plus three short judge calls per `llm` or `baseline` grader per run".
- **Per-run score.** "A run's score is the fraction of its graders that passed, weighted if you set weights."
- **Per-case aggregate.** "The case's score is the mean across its runs. A case passes when its score meets the `--threshold`, `1.0` by default." With the default threshold, one failing grader in one run fails the case.
- **Baseline.** `WITH`, `W/OUT`, and `Δ` (with minus without) per case; `aggregates.meanDelta` across the suite. Δ never changes the exit code.
- **JSON (`aggregate-result.json`, `schemaVersion: 1`).** `aggregates.overallScore` (mean case score), `aggregates.casesPassed` / `casesTotal`, `aggregates.meanDelta`, `cases[].aggregates.score`, `cases[].aggregates.delta`, and per-run entries under `cases[].arms.with[]` / `cases[].arms.without[]` with `error`, `aborted`, `skippedPaidGraders`. The docs say the document also "carries ... per-run grader results with explanations and evidence". The exact field name of a run's own score is not listed in the docs (unverified; read it from a real file).
- **Report.** `report.html` shows suite score, ablation Δ, baseline score, cases passing, and "Perfect runs ... the share of with-plugin runs where every grader passed". A single-arm run shows `SCORE` and `PASS%` columns.
- **Errors.** A run that "ended on an API error scores 0 without grading" and forces a non-zero exit; a timed-out run may still be graded.
- **Variance / flaky.** No stddev, CI, pass@k, or pass^k. No flaky status. Variance guidance is about graders: "An `llm` grader asks a model for a verdict, so its answer can differ between runs", so prefer `regex` graders for long output. The docs also say "A single run is noisy, so confirm any change at the default three runs before you trust it."

### Anthropic `skill-creator`

Source: [anthropics/skills @ `ca45fc4`](https://github.com/anthropics/skills/tree/ca45fc496a3436251f191354091d557da31c15b5/skills/skill-creator): `scripts/aggregate_benchmark.py`, `scripts/run_eval.py`, `agents/analyzer.md`, `references/schemas.md`, `SKILL.md`.

- **Output eval (`aggregate_benchmark.py`).** Reads `eval-N/<config>/run-K/grading.json` and writes `benchmark.json` + `benchmark.md`. For each configuration (for example `with_skill` and `without_skill`) it reports `pass_rate`, `time_seconds`, and `tokens`, each as `{mean, stddev, min, max}`, using the **sample** stddev (`n-1`). `delta` is the difference of means between the first two configurations. `runs[]` keeps every run's `pass_rate`, `passed`, `failed`, `total`, and expectations, so per-run outcomes survive.
  - The stats pool **all runs of all evals** in a configuration into one list, so `stddev` mixes between-case and within-case variation. It is not a per-case repeatability measure.
  - `metadata.runs_per_configuration` is **hardcoded to 3**, whatever was actually run.
  - `benchmark.md` renders pass rate as `mean% ± stddev%`.
- **Trigger eval (`run_eval.py`).** `--runs-per-query` default **3**, `--trigger-threshold` default **0.5**. A should-trigger query passes when `trigger_rate >= 0.5`, a should-not-trigger query when `trigger_rate < 0.5`. `SKILL.md` describes this as "running each query 3 times to get a reliable trigger rate".
- **Flaky.** No mechanical rule. The analyzer agent prompt asks "Is it **highly variable**? (flaky expectation or non-deterministic behavior)" and gives the example "Eval 3 shows high variance (50% ± 40%) - run 2 had an unusual failure that may be flaky". `SKILL.md` asks the analyst to surface "high-variance evals (possibly flaky)".
- **Observation (not a documented claim).** The `SKILL.md` workflow writes one `with_skill/outputs/` per eval, while the aggregator expects `run-K/` subfolders, so in the default interactive flow there is often only one run per eval.

The open spec at agentskills.io ([Evaluating skill output quality](https://agentskills.io/skill-creation/evaluating-skills)) uses the same `benchmark.json` shape (`mean`, `stddev`) and says: "Standard deviation (`stddev`) is only meaningful with multiple runs per eval." It also says: "If the same eval passes sometimes and fails others (reflected as high `stddev` in the benchmark), the eval may be flaky (sensitive to model randomness), or the skill's instructions may be ambiguous". It gives no run count and no threshold for "high".

### SkillBench

Source: `skillbench` 0.1.0 sdist from [PyPI](https://pypi.org/project/skillbench/) (the README's clone URL is a placeholder, so the canonical repo is unknown). Read `src/skillbench/cli.py`, `core/runner.py`, `store/history.py`.

- **No repeat option.** `skillbench test` and `skillbench eval` take `--adapter`/`--frameworks`, `--model`, `--tags`, `-o`; nothing sets a run or trial count. Each scenario runs once per adapter.
- **Reports** per-scenario, per-assertion pass/fail; summary counts `total_scenarios`, `total_evals`, `passed`, `failed`. JSON via `-o`, history in SQLite. Exit 0 if all pass, 1 otherwise.
- No mean/stddev across runs, no pass@k, no flaky status. Cross-adapter comparison is side by side, not statistical.

### `agent-skills-eval` (darkrishabh/agent-skills-eval)

Source: [darkrishabh/agent-skills-eval @ `f10ae2b`](https://github.com/darkrishabh/agent-skills-eval/tree/f10ae2b) (npm 0.1.1): `src/cli.ts`, `src/artifacts.ts`, `docs/artifact-contract.md`.

- **No repeat option.** CLI options include `--baseline`, `--target`, `--judge`, `--base-url` (OpenAI-compatible), `--concurrency`, `--eval-id`, `--layout`. Nothing sets a run count. Each eval runs once per mode (`with_skill`, optionally `without_skill`) per iteration.
- **Reports** `grading.json` per run (`assertion_results[]`, `summary.{passed, failed, total, pass_rate}`) and `benchmark.json` with `run_summary.with_skill|without_skill.{pass_rate, time_seconds, tokens}.{mean, stddev}` plus `delta`. Per the artifact contract: "Standard deviation is the population standard deviation", and `benchmark.json` "does not include per-case results, cost, sample counts, or an overall pass/fail flag."
- Because there is one run per eval, its `stddev` is purely **between-case** spread, not run-to-run variance.
- The HTML report colors pass rate `ok` at >= 0.8, `warn` at >= 0.5. No flaky status, no pass@k.

### Summary table

| Tool | Default runs per case | Per-case stat | Suite stat | Per-run outcomes kept | stddev | CI | pass@k / pass^k | Flaky status |
|---|---|---|---|---|---|---|---|---|
| `claude plugin eval` | 3 per arm (`--runs`, 1 to 50) | mean run score vs `--threshold` (1.0) | mean case score, cases passed, mean Δ, perfect-run share | yes (JSON + report) | no | no | no | no |
| `skill-creator` output eval | whatever was run (metadata says 3) | none (pooled) | mean, sample stddev, min, max per config; Δ of means | yes (`runs[]`) | yes, pooled across cases | no | no | analyst judgment only |
| `skill-creator` trigger eval | 3 (`--runs-per-query`) | trigger rate vs 0.5 | passed/total | counts only | no | no | no | no |
| SkillBench 0.1.0 | 1 | pass/fail | counts | n/a | no | no | no | no |
| `agent-skills-eval` 0.1.1 | 1 | assertion pass rate | mean, population stddev per mode; Δ | yes (`grading.json`) | yes, between cases only | no | no | no |

## Metrics from the literature

- **pass@k** (Chen et al., [Evaluating Large Language Models Trained on Code](https://arxiv.org/abs/2107.03374), 2021): probability that at least one of k samples succeeds. Its unbiased estimator from n ≥ k samples with c successes is `1 - C(n-c, k) / C(n, k)` (Section 2.1; quoted from memory of the paper, not re-fetched).
- **pass^k** (Yao et al., [τ-bench](https://arxiv.org/abs/2406.12045), 2024): "the chance that all k i.i.d. task trials are successful, averaged across tasks", estimated as `E_task[C(c, k) / C(n, k)]`. Their main table uses "at least 3 trials per task"; they report "pass^8 <25% in retail" for state-of-the-art agents.
- Anthropic, [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) (2026-01-09): use "pass@k for tools where one success matters, pass^k for agents where consistency is essential". Gives no trial count. Notes that a 0% pass rate across many trials "is most often a signal of a broken task", and warns that trials failing from a shared environment limit are not independent ("infrastructure flakiness rather than agent performance").
- Miller, [Adding Error Bars to Evals](https://arxiv.org/abs/2411.00640) (Anthropic, 2024): report standard errors; for binary scores `SE = sqrt(p(1-p)/n)`; resampling each question K times cuts the conditional variance to σ²/K, with diminishing returns; **do not pool all K×N answers as if independent**, because repeats of one question are not independent draws (use clustered SEs). This is exactly the flaw in `skill-creator`'s pooled stddev.
- Binomial intervals: Brown, Cai & DasGupta, "Interval Estimation for a Binomial Proportion", *Statistical Science* 16(2), 2001, recommend the Wilson (or Jeffreys) interval over the Wald `p ± 1.96·SE` interval for small n (cited from memory, not re-fetched). The Wald interval collapses to zero width at 0/n and n/n, which is the common case here.

### Why stddev is the wrong number for a single case

For a case with binary run outcomes, the stddev of the outcomes is `sqrt(p(1-p))`: a function of the mean, adding no information. A useful uncertainty measure must depend on n. The Wilson 95% interval does (computed for this note):

| n | all pass (n/n) | one fail | half | all fail (0/n) |
|---|---|---|---|---|
| 3 | [0.44, 1.00] | 2/3: [0.21, 0.94] | — | [0.00, 0.56] |
| 5 | [0.57, 1.00] | 4/5: [0.38, 0.96] | — | [0.00, 0.43] |
| 10 | [0.72, 1.00] | 9/10: [0.60, 0.98] | 5/10: [0.24, 0.76] | [0.00, 0.28] |
| 20 | [0.84, 1.00] | 19/20: [0.76, 0.99] | 10/20: [0.30, 0.70] | [0.00, 0.16] |
| 30 | [0.89, 1.00] | 29/30: [0.83, 0.99] | 15/30: [0.33, 0.67] | [0.00, 0.11] |

Chance that n runs reveal a flaky case (at least one pass and one fail), `1 - p^n - (1-p)^n`:

| true pass rate p | n=3 | n=5 | n=8 | n=10 | n=20 |
|---|---|---|---|---|---|
| 0.9 | 0.27 | 0.41 | 0.57 | 0.65 | 0.88 |
| 0.8 | 0.48 | 0.67 | 0.83 | 0.89 | 0.99 |
| 0.5 | 0.75 | 0.94 | 0.99 | 1.00 | 1.00 |

## Recommendations for drill

These are drill's own recommendations, derived from the sources above; no source prescribes them.

1. **Default run count per runner, recorded in the manifest.**
   - Local/OpenAI-compatible runner (7B models): **`--runs 10`**. Runs cost wall-clock time, not money; at 10, an all-pass case is bounded at ≥ 0.72 (95%), and an 80%-reliable case shows up as flaky 89% of the time. Small models are more likely to sit at mid-range pass rates where `p(1-p)` and therefore uncertainty is largest (reasoning, not a cited result). Allow `--runs 3` or 5 for quick iteration, labeled as such.
   - `claude plugin eval` runner: keep its default **3** (billed, and its docs treat 3 as the confirmation level); raise with `--runs` for a release results file.
2. **Report per case:** the ordered vector of per-run outcomes, `c/n`, the pass rate, the Wilson 95% interval, and the status (below). For `claude plugin eval`, a run "passes" when its score is 1.0 (all graders passed), matching its default threshold and its "perfect runs" tile; keep the fractional score too.
3. **Report per suite:** mean of per-case pass rates (pass@1), counts of pass/flaky/fail/error cases, and optionally pass^k at k = n via `C(c,k)/C(n,k)`. Do not report a pooled stddev across all runs; if a suite-level error bar is wanted, use the clustered SE from Miller (cluster = case).
4. **Status rule (flaky vs fails).** With `c` passes out of `n` valid runs: `pass` if `c = n`; `fail` if `c = 0`; `flaky` if `0 < c < n`. This is the CI rule ("marked as FLAKY if it fails and succeeds within the execution of a single Gradle task"; Bazel marks a test FLAKY when a later attempt passes after a failure: [Develocity flaky test detection guide](https://docs.develocity.ai/2026.2/guides/flaky-test-detection-guide/index.md)) applied to n planned runs instead of retries. It needs no tunable threshold, which suits bash + jq.
5. **Errors are not fails.** Runs that end on an infrastructure error (API or rate-limit error, endpoint down, timeout before any output) are counted as `error` and excluded from n; if every run errored, the case status is `error`. `claude plugin eval` scores API-error runs 0, so drill must read `cases[].arms.with[].error` and reclassify.

## Open uncertainties

- The exact JSON field name of a single run's score inside `cases[].arms.with[]` in `aggregate-result.json` is not in the docs; confirm from a real results file before writing the jq.
- SkillBench's canonical repository is unknown (PyPI README has a `YOUR_USERNAME` placeholder); findings are from the 0.1.0 sdist only.
- The pass@k estimator formula and the Brown–Cai–DasGupta recommendation are cited from knowledge of the papers, not re-read for this note.
- Bazel's own docs for `--flaky_test_attempts` were not read; the Bazel FLAKY behavior is as described by the Develocity guide.
- No source gives an empirical run-to-run variance figure for ~7B models on agent tasks; the 10-run default rests on the binomial arithmetic above, not on measured 7B variance. The first drill results files can check it: if most 7B cases land at 0/10 or 10/10, a lower default is fine.
