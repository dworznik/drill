"""A rig's held-out cases: `plugin eval` case folders plus drill's `fixtures/` and `trace-rules.json` (ADR-0006)."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from drill.lib.trace_rules import BadRules, validate
from drill.rig import Refusal

OWN_SCAFFOLD = re.compile(r"^[^#\n]*\bscaffold_script\s*:", re.MULTILINE)
INLINE_CONTEXT = re.compile(r"^context[^\S\n]*:[^\S\n]*[^#\s]", re.MULTILINE)  # spaces only: a block starts on the next line


@dataclass(frozen=True)
class Case:
    name: str
    path: Path
    rules: dict = field(default_factory=lambda: {"rules": []})

    @property
    def fixtures(self) -> Path:
        return self.path / "fixtures"


def load_case(path: Path) -> Case:
    yaml = path / "case.yaml"
    if yaml.is_file() and OWN_SCAFFOLD.search(yaml.read_text()):
        raise Refusal(f"case {path.name} sets its own scaffold_script; drill owns the scaffold, remove it")
    if yaml.is_file() and INLINE_CONTEXT.search(yaml.read_text()):
        raise Refusal(f"case {path.name}: write case.yaml's context as a block, not inline, so drill can add its scaffold")
    rules = {"rules": []}
    rules_file = path / "trace-rules.json"
    if rules_file.is_file():
        try:
            rules = json.loads(rules_file.read_text())
        except json.JSONDecodeError as e:
            raise Refusal(f"case {path.name}: trace-rules.json is not valid JSON: {e}") from e
        try:
            validate(rules)
        except BadRules as e:
            raise Refusal(f"case {path.name}: trace-rules.json: {e}") from e
    return Case(path.name, path, rules)


def load_cases(cases_dir: Path) -> list[Case]:
    cases = [load_case(p) for p in sorted(cases_dir.iterdir()) if p.is_dir() and not p.name.startswith(".")]
    if not cases:
        raise Refusal(f"{cases_dir} holds no case folders")
    return cases
