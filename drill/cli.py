"""The `drill` console script: `drill run <rig>`, `drill compare`, `drill table`."""

import argparse
import sys
from pathlib import Path


class Refusal(Exception):
    """A drill that can't start, with the message the caller sees."""


def check_rig(rig: Path) -> None:
    """Refuse a rig that lacks the parts every drill needs (ADR-0002)."""
    if not rig.is_dir():
        raise Refusal(f"no rig directory at {rig}")
    if not (rig / "rig.json").is_file():
        raise Refusal(f"{rig} has no rig.json")
    if not (rig / "cases").is_dir():
        raise Refusal(f"{rig} has no cases/ directory")


def cmd_run(args) -> int:
    check_rig(args.rig)
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
