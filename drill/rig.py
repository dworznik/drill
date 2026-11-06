"""A rig on disk: `rig.json` and `cases/`, plus `results/` that drill creates (ADR-0002)."""

import json
from dataclasses import dataclass
from pathlib import Path

from drill.skill import BadPin, Pin


class Refusal(Exception):
    """A drill that can't start, with the message the caller sees."""


@dataclass(frozen=True)
class Rig:
    path: Path
    config: dict

    @property
    def shims(self) -> list[str]:
        return list(self.config.get("shims", []))

    def pin(self) -> Pin:
        try:
            return Pin.from_rig(self.config)
        except BadPin as e:
            raise Refusal(f"{self.path}/rig.json: {e}") from e


def load_rig(path: Path) -> Rig:
    if not path.is_dir():
        raise Refusal(f"no rig directory at {path}")
    if not (path / "rig.json").is_file():
        raise Refusal(f"{path} has no rig.json")
    if not (path / "cases").is_dir():
        raise Refusal(f"{path} has no cases/ directory")
    try:
        config = json.loads((path / "rig.json").read_text())
    except json.JSONDecodeError as e:
        raise Refusal(f"{path}/rig.json is not valid JSON: {e}") from e
    if not isinstance(config, dict):
        raise Refusal(f"{path}/rig.json must be a JSON object")
    shims = config.get("shims", [])
    if not isinstance(shims, list) or not all(isinstance(s, str) and s and "/" not in s for s in shims):
        raise Refusal(f"{path}/rig.json: shims must be a list of binary names")
    return Rig(path, config)
