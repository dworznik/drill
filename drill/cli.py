"""The `drill` console script: `drill run <rig>`, `drill compare`, `drill table`."""

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from drill.rig import Refusal, load_rig
from drill.skill import BadPin, SkillCache, drill_revision, local_skill, revision


@contextmanager
def skill_copy(rig, skill_dir: Path | None):
    """The throwaway skill copy for this drill and its revision."""
    if skill_dir is not None:
        if not skill_dir.is_dir():
            raise Refusal(f"--skill: no directory at {skill_dir}")
        with local_skill(skill_dir) as (copy, rev):
            yield copy, rev
        return
    pin = rig.pin()
    try:
        with SkillCache().export(pin) as copy:
            yield copy, {"sha": pin.sha, "dirty": False}
    except BadPin as e:
        raise Refusal(str(e)) from e


def cmd_run(args) -> int:
    rig = load_rig(args.rig)
    with skill_copy(rig, args.skill) as (skill, skill_rev):
        revisions = {"skill": skill_rev, "drill": drill_revision(), "rig": revision(rig.path)}
        print(json.dumps({"revisions": revisions}, indent=2))
        print(f"drill: {args.rig}: nothing to run yet")
    return 0


def cmd_compare(args) -> int:
    raise Refusal("compare is not built yet")


def cmd_table(args) -> int:
    raise Refusal("table is not built yet")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="drill", description="Drill an agent skill from its rig.")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    run = sub.add_parser("run", help="drill a skill from a local rig directory")
    run.add_argument("rig", type=Path, help="path to the rig (holds rig.json and cases/)")
    run.add_argument("--skill", type=Path, metavar="DIR",
                     help="drill this local skill working tree instead of the rig's pin")
    run.set_defaults(func=cmd_run)

    compare = sub.add_parser("compare", help="diff two results files (not built yet)")
    compare.add_argument("results", nargs="*", type=Path, metavar="results.json")
    compare.set_defaults(func=cmd_compare)

    table = sub.add_parser("table", help="render the public summary of a results file (not built yet)")
    table.add_argument("results", type=Path, metavar="results.json")
    table.set_defaults(func=cmd_table)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except Refusal as e:
        print(f"drill: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
