"""Command line entry point. Exit codes are the contract: 0 clean, 1 findings, 2 misuse."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import baseline, dependencies, files, gaps, init, rules, runner, todo
from .config import ConfigError, load

OK, FAILED, USAGE = 0, 1, 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = Path(args.root).resolve()
        config = load(Path(args.config) if args.config else root / "gate.toml")
        return args.handler(args, root, config)
    except (ConfigError, files.NotARepo, ValueError) as e:
        print(f"gate: {e}", file=sys.stderr)
        return USAGE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gate", description="Check a repo against its declared rules.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="run the checks; exit 1 on findings")
    check.add_argument("paths", nargs="*", help="files or directories (default: whole repo)")
    check.add_argument("--changed", action="store_true", help="only files in the git diff, plus untracked")
    check.add_argument("--base", default="HEAD", help="git ref --changed compares against")
    check.add_argument("--profile", help="named set of checks from gate.toml")
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.add_argument("--strict", action="store_true", help="a check that could not run is a failure")
    check.set_defaults(handler=_check)

    base = sub.add_parser("baseline", help="show, shrink, or accept recorded violations")
    group = base.add_mutually_exclusive_group()
    group.add_argument("--prune", action="store_true", help="drop fixed entries and tighten improved ones")
    group.add_argument("--accept", action="store_true",
                       help="record every current violation; a human decision, not for automation")
    group.add_argument("--check", action="store_true",
                       help="exit 1 if --prune would change the baseline; makes it a ratchet in CI")
    base.set_defaults(handler=_baseline)

    queue = sub.add_parser("todo", help="list baselined violations as a work queue, worst file first")
    queue.add_argument("--next", type=int, metavar="N", help="show only the first N files")
    queue.add_argument("--json", action="store_true", help="machine-readable output")
    queue.set_defaults(handler=_todo)

    start = sub.add_parser("init", help="write a starter gate.toml from what the repo already does")
    start.add_argument("--print", action="store_true", help="print the config instead of writing it")
    start.set_defaults(handler=_init)

    prose = sub.add_parser("rules", help="write the rules as prose into AGENTS.md / CLAUDE.md")
    mode = prose.add_mutually_exclusive_group()
    mode.add_argument("--print", action="store_true", help="print the section instead of writing it")
    mode.add_argument("--check", action="store_true", help="exit 1 if any instruction file is out of date")
    prose.set_defaults(handler=_rules)

    weak = sub.add_parser("gaps", help="list weaknesses in this repo's rules that gate can see for itself")
    weak.add_argument("--json", action="store_true", help="machine-readable output")
    weak.set_defaults(handler=_gaps)

    deps = sub.add_parser("dependencies", help="show packages nobody has approved, or approve the current set")
    deps.add_argument("--accept", action="store_true",
                      help="approve every package declared today; a human decision, not for automation")
    deps.set_defaults(handler=_dependencies)

    for p in (check, base, queue, start, prose, weak, deps):
        p.add_argument("--root", default=".", help="repo root (default: current directory)")
        p.add_argument("--config", help="path to gate.toml (default: <root>/gate.toml)")
    return parser


def _check(args, root: Path, config) -> int:
    exclude = config.exclude
    if args.changed:
        selected, mode, base = files.changed_files(root, args.base, exclude), "changed", args.base
    elif args.paths:
        selected, mode, base = files.explicit_files(root, args.paths, exclude), "paths", None
    else:
        selected, mode, base = None, "all", None
    ctx = runner.build_context(root, config, selected, base)
    report = runner.run(ctx, args.profile, mode, strict=args.strict)
    print(report.to_json() if args.json else report.to_text())
    return OK if report.ok else FAILED


def _baseline(args, root: Path, config) -> int:
    ctx = runner.build_context(root, config, None)
    found = runner.structural_findings(ctx)
    now = baseline.current(found)
    recorded = baseline.load(root)
    if args.accept:
        baseline.save(root, now)
        print(f"baseline: accepted {baseline.size(now)} violation(s) into {baseline.FILE}")
        return OK
    kept = baseline.pruned(recorded, now)
    fixed = baseline.size(recorded) - baseline.size(kept)
    tightened = baseline.tightened(recorded, kept)
    if args.prune:
        if kept != recorded:
            baseline.save(root, kept)
        print(f"baseline: removed {fixed} fixed, tightened {tightened}, {baseline.size(kept)} remain")
        return OK
    if args.check:
        if kept == recorded:
            print(f"baseline: up to date, {baseline.size(recorded)} recorded")
            return OK
        print(f"baseline: stale, {fixed} fixed and {tightened} improved but still recorded. Run `gate baseline --prune`.")
        return FAILED
    new = sum(1 for f in baseline.apply(found, recorded) if f.blocks())
    print(f"baseline: {baseline.size(recorded)} recorded, {fixed} now fixed, {tightened} improved, {new} new or worsened")
    if new and baseline.is_legacy(root):
        print("note: this baseline is in the old format, which kept one value per function name. Functions that "
              "shared a name with a recorded one used to be covered and no longer are. Review `gate check`, then "
              "run `gate baseline --accept` once to record them.")
    return OK


def _rules(args, root: Path, config) -> int:
    if args.print:
        print(rules.render(config), end="")
        return OK
    if args.check:
        behind = rules.stale(root, config)
        for path in behind:
            print(f"rules: {path.relative_to(root)} is out of date. Run `gate rules`.")
        if not behind:
            print("rules: up to date")
        return FAILED if behind else OK
    changed = rules.write(root, config)
    for path in changed:
        print(f"rules: wrote {path.relative_to(root)}")
    if not changed:
        print("rules: up to date")
    return OK


def _dependencies(args, root: Path, config) -> int:
    ctx = runner.build_context(root, config, None)
    declared = dependencies.declared(root, ctx.repo_files)
    now, approved = dependencies.current(declared), dependencies.load(root)
    if args.accept:
        dependencies.save(root, now)
        print(f"dependencies: approved {dependencies.count(now)} package(s) into {dependencies.FILE}")
        return OK
    if approved is None:
        print(f"dependencies: {dependencies.count(now)} declared, none approved yet. "
              "Review them, then run `gate dependencies --accept`.")
        return OK
    new = dependencies.unapproved(declared, approved)
    gone = dependencies.count(approved) - sum(1 for eco, names in approved.items() for n in names if n in now.get(eco, ()))
    print(f"dependencies: {dependencies.count(approved)} approved, {len({(d.ecosystem, d.name) for d in new})} new, "
          f"{gone} approved but no longer declared")
    for dep in new:
        print(f"  new  {dep.name} ({dep.ecosystem})  {dep.path}" + (f":{dep.line}" if dep.line else ""))
    return OK


def _gaps(args, root: Path, config) -> int:
    found = gaps.find(runner.build_context(root, config, None))
    print(gaps.to_json(found) if args.json else gaps.to_text(found))
    return OK


def _todo(args, root: Path, config) -> int:
    ctx = runner.build_context(root, config, None)
    recorded = baseline.load(root)
    found = baseline.apply(runner.structural_findings(ctx), recorded)
    todos = todo.queue([f for f in found if f.baselined])
    stale = baseline.size(recorded) - baseline.size(baseline.pruned(recorded, baseline.current(found)))
    shown = todos if args.next is None else todos[:args.next]
    render = todo.to_json if args.json else todo.to_text
    print(render(todos, shown, stale))
    return OK


def _init(args, root: Path, config) -> int:
    target = root / "gate.toml"
    if target.exists() and not args.print:
        raise ValueError(f"{target} already exists; edit it, or delete it to start over")
    ctx = runner.build_context(root, config, None)
    text = init.render(init.plan(ctx))
    if args.print:
        print(text)
        return OK
    target.write_text(text, encoding="utf-8")
    failing = [f for f in runner.structural_findings(runner.build_context(root, load(target), None)) if f.blocks()]
    print(f"wrote {target}")
    print(f"{len(failing)} structural violation(s) today in {len({f.path for f in failing})} file(s).")
    print("Next: review gate.toml, run `gate check`, then fix what it reports or `gate baseline --accept`.")
    return OK


if __name__ == "__main__":
    sys.exit(main())
