"""What the agent wrote during one run, read from its transcript (ADR-0011).

The transcript is `plugin eval`'s stream-json record of the run. The agent wrote the text
and tool-call inputs of its `assistant` messages. `user` lines are tool results and text the
harness injected, which the agent was shown, not what it wrote; thinking is redacted.
"""

import json
from pathlib import Path


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def agent_writing(path: Path) -> list[tuple[str, str]]:
    """(where, text) for each piece the agent wrote: `assistant text` or `<Tool> input`.

    Lines that aren't JSON are skipped, so a cut-off transcript is still read up to the cut.
    """
    written = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            written.append(("assistant text", content))
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                written.append(("assistant text", block.get("text") or ""))
            elif block.get("type") == "tool_use":
                written += [(f"{block.get('name', 'tool')} input", s) for s in _strings(block.get("input"))]
    return written
