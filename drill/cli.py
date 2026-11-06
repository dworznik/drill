"""The `drill` console script: `drill run <rig>`, `drill compare`, `drill table`."""

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from drill import compare
from drill.cases import load_cases
from drill.drill import drill
from drill.lib.http_fake import NoOpenSSL, require_openssl
from drill.results import all_runs
from drill.rig import Refusal, load_rig
from drill.runners.plugin_eval import Model
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
    if args.skill is None:
        rig.pin()  # refuse a bad pin before anything else
    cases = load_cases(rig.path / "cases")
    if rig.hosts:
        try:
            require_openssl()
        except NoOpenSSL as e:
            raise Refusal(str(e)) from e
    if not args.model:
        raise Refusal("run needs at least one --model <id>")
    try:
        models = [Model.parse(m) for m in args.model]
    except ValueError as e:
        raise Refusal(str(e)) from e
    rig_rev = revision(rig.path)  # before drill writes results/
    with skill_copy(rig, args.skill) as (skill, skill_rev):
        revisions = {"skill": skill_rev, "drill": drill_revision(), "rig": rig_rev}
        path = drill(rig, cases, skill, revisions, models, runs=args.runs)
    results = json.loads(path.read_text())
    for r in all_runs(results):
        print(f"{r['status']:<5}  {r['case']}  {r['model']}  {r['arm']} #{r['run']}  score {r['score']:.2f}"
              + (f"  {r['error'][:120]}" if r["error"] else ""))
        for v in r["trace"]:
            if not v["ok"]:
                print(f"         FAIL {v['rule']}  ({v['why']})")
    for row in results["results"]:
        rate = "—" if row["passRate"] is None else f"{row['c']}/{row['n']}"
        print(f"{row['status']:<6} {row['case']}  {row['model']}  {rate}")
    print(f"drill: wrote {path}")
    return 0


def cmd_compare(args) -> int:
    files, models = args.results, args.model
    if models or len(files) == 1:
        if len(files) != 1 or len(models) != 2:
            raise Refusal("to compare two models, give one results file and two --model")
        print(compare.compare_models(compare.load(files[0]), models[0], models[1], files[0].name), end="")
        return 0
    if not files:
        files = compare.newest_two(args.rig)
    elif len(files) != 2:
        raise Refusal("compare takes two results files, or none for the rig's two newest")
    a, b = (compare.load(f) for f in files)
    print(compare.compare_files(a, b, files[0].name, files[1].name), end="")
    return 0


def cmd_table(args) -> int:
    raise Refusal("table is not built yet")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="drill", description="Drill an agent skill from its rig.")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    run = sub.add_parser("run", help="drill a skill from a local rig directory")
    run.add_argument("rig", type=Path, help="path to the rig (holds rig.json and cases/)")
    run.add_argument("--model", action="append", default=[], metavar="ID[:K=V,...]",
                     help="model to drill on; repeat for several")
    run.add_argument("--runs", type=int, help="runs per case (default: plugin eval's, 3)")
    run.add_argument("--skill", type=Path, metavar="DIR",
                     help="drill this local skill working tree instead of the rig's pin")
    run.set_defaults(func=cmd_run)

    cmp = sub.add_parser("compare", help="diff two results files, or two models in one, as markdown")
    cmp.add_argument("results", nargs="*", type=Path, metavar="results.json",
                     help="before and after; none picks the rig's two newest")
    cmp.add_argument("--rig", type=Path, default=Path("."), help="the rig whose results/ to read (default: .)")
    cmp.add_argument("--model", action="append", default=[], metavar="ID",
                     help="with one results file, give two: compare the first model to the second")
    cmp.set_defaults(func=cmd_compare)

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
