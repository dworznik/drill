# drill

Drilling agent skills: fake the tools a skill calls, run it through agent runners and models, check what it did, and keep the results.

## Language

### What gets drilled

**Skill**:
An agent skill package (a `SKILL.md` and its files) under test, owned by an external repo.
_Avoid_: plugin, prompt

**Rig**:
drill's per-skill setup for one skill: its shims, fixtures, held-out cases and results.
_Avoid_: harness, suite, skill directory

**Case**:
One prompt plus the expectations its run is checked against.
_Avoid_: scenario, test, eval

**Held-out case**:
A case kept in the rig, out of the skill author's sight.
_Avoid_: private case, hidden test

**Shipped case**:
A case that lives in the skill's own repo.
_Avoid_: public case, bundled eval

### Faking the world

**Shim**:
A fake stand-in for a tool the skill calls, which records each call to the trace and answers from a fixture.
_Avoid_: mock, stub, fake binary

**Fixture**:
The canned, synthetic answers the shims give for one case.
_Avoid_: recording, cassette, sample data

**Trace**:
The ordered record of every shim call made during one run.
_Avoid_: log, transcript

**Transcript**:
The runner's record of the agent's messages during one run, distinct from the trace.
_Avoid_: trace, conversation log

### Running and results

**Runner**:
The harness that drives an agent through cases, independent of which model it uses.
_Avoid_: adapter, provider, backend

**Model**:
The language model a runner drives.

**Run**:
One execution of one case on one runner and model.
_Avoid_: trial, attempt

**Drill**:
One session over a skill across its cases, runners and models, producing exactly one results file.
_Avoid_: eval, benchmark, job

**Results file**:
The record a drill leaves: per-case outcomes over its runs, plus a manifest.
_Avoid_: report, scorecard

**Manifest**:
The provenance stamped on a results file: skill revision, runner, model, run count, date and shim versions.
_Avoid_: metadata, header
