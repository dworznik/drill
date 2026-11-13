"""A rig on disk: `rig.json` and `cases/`, plus `results/` that drill creates (ADR-0002)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from drill.skill import BadPin, Pin


# A bare DNS name: rig.json's hosts are what the sandbox grants, so no scheme, port or path.
HOST = re.compile(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")


class Refusal(Exception):
    """A drill that can't start, with the message the caller sees."""


@dataclass(frozen=True)
class Rig:
    path: Path
    config: dict

    @property
    def shims(self) -> list[str]:
        return list(self.config.get("shims", []))

    @property
    def hosts(self) -> list[str]:
        """The hosts the HTTP fake answers for, each granted to the run's sandbox (ADR-0013)."""
        return [h.lower() for h in self.config.get("hosts", [])]

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
    hosts = config.get("hosts", [])
    if not isinstance(hosts, list) or not all(isinstance(h, str) and HOST.fullmatch(h) for h in hosts):
        raise Refusal(f"{path}/rig.json: hosts must be a list of host names, like \"api.example.com\" "
                      "(no scheme, port or path)")
    return Rig(path, config)

