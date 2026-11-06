# The `secret` rule reads the transcript as well as the trace

A secret can leak through the agent's own messages or an unshimmed command, which the trace never sees. So one `secret` rule, `{"secret": "<literal>", "label": "…"}`, fails a run if the literal appears in any shim's argv, any request URL (ADR-0014), or anything the agent wrote in the transcript: reply text and every tool-call input. Tool results don't count, since they are what the agent was shown, and thinking is redacted. drill reads each run's transcript at `aggregate-result.json`'s `cases[].arms.<arm>[].tracePath` (kept with `--keep-temp`); a run with no file there is an `error`. drill copies it into raw evidence as `transcript.jsonl` and deletes the kept temp dir. `secret` replaces the prototype's `never-in-argv`. Decided in [Secret-leak check as a first-class assertion](https://github.com/dworznik/drill/issues/8).

## Considered options

- **`plugin eval`'s native `regex` grader with `target: trace, match: not_contains`:** it also scans tool results, so it fails whenever a tool shows the agent the secret, even if the agent never repeats it.
