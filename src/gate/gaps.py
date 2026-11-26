"""Lists the weaknesses in a repo's rules that gate can see for itself.

Why: rules decay unless someone keeps asking where they fall short. This gives
whoever is asking, a person or an agent, a factual place to start: what is not
checked at all, what could be tighter at no cost, and what the repo already
does that no rule protects. It reports and never changes anything; judging
which gaps matter, and finding the ones no tool can see, is the reader's job.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import baseline, dependencies, files, propose, rules
from .checks import complexity, toolchain
from .config import Config
from .context import Context
from .files import matches
from .settings import TOOLCHAIN


@dataclass(frozen=True)
class Gap:
    area: str
    message: str
    suggestion: str = ""


def find(ctx: Context) -> list[Gap]:
    if not (ctx.root / "gate.toml").is_file():
        return [Gap("config", "No gate.toml: only the default limits apply and no commands or boundaries are checked.",
                    "Run `gate init`.")]
    return [*_unchecked(ctx), *_boundaries(ctx), *_headroom(ctx), *_blind_spots(ctx), *_upkeep(ctx), *_ci(ctx)]


# ---------------------------------------------------------- what never runs

def _unchecked(ctx: Context) -> list[Gap]:
    cfg = ctx.config
    scheduled = {check for checks in cfg.profiles.values() for check in checks}
    out = [gap for category in TOOLCHAIN if (gap := _tool_gap(ctx, category, scheduled))]
    out += _test_gaps(ctx, scheduled)
    for name in [*cfg.tests, *cfg.commands]:
        if name not in scheduled:
            out.append(Gap("unchecked", f"`{name}` is defined but no profile runs it.", f"Add \"{name}\" to a profile."))
    return out


def _tool_gap(ctx: Context, category: str, scheduled: set[str]) -> Gap | None:
    if category not in ctx.config.toolchain and not toolchain.available(ctx, category):
        return Gap("unchecked", f"Nothing checks `{category}`: no command is configured and no tool is installed.",
                   f"Add a `{category}` command under `[toolchain]`, or install a tool for it.")
    if category not in scheduled:
        return Gap("unchecked", f"`{category}` can run here but no profile includes it.", f"Add \"{category}\" to a profile.")
    return None


def _test_gaps(ctx: Context, scheduled: set[str]) -> list[Gap]:
    cfg = ctx.config
    if not cfg.tests:
        return [Gap("unchecked", "No test command is configured, so tests are never part of the gate.",
                    "Add `[tests.unit]` (and `[tests.integration]`) with the repo's test commands.")]
    if cfg.coverage.reports and "coverage" in scheduled:
        return []
    return [Gap("unchecked", "Tests run, but nothing checks that they run the code a change adds.",
                "Have the test command write a coverage report, set `[coverage] report`, and add "
                "\"coverage\" to a profile after the tests.")]


# ------------------------------------------- structure nothing protects yet

def _boundaries(ctx: Context) -> list[Gap]:
    configured = ctx.config.boundaries
    proposed = propose.boundary_rules(ctx.root, ctx.repo_files)
    missing = [rule for rule in proposed if not _covered(rule, configured.forbid)]
    if not missing:
        return []
    lead = ("No boundary rules are configured, but the code already keeps to some."
            if not configured.forbid and not configured.only else
            "The code already keeps to import directions that no rule protects.")
    return [Gap("boundaries", f"{lead} One careless import would break each of these silently: {', '.join(f'`{r}`' for r in missing[:10])}.",
                "Add the ones that are intent, not coincidence, to `[boundaries] forbid`.")]


def _covered(rule: str, forbid: tuple[tuple[str, str], ...]) -> bool:
    left, right = (part.strip() for part in rule.split("!->"))
    return any(matches(left, a) and matches(right, b) for a, b in forbid)


# ------------------------------------------- limits looser than they need be

def _headroom(ctx: Context) -> list[Gap]:
    """Limits raised above gate's defaults that nothing in the repo needs any more.

    Why only those: a limit is a judgement about readable code, not a record
    of what the code happens to be today. Lowering one to the current maximum
    just because nothing would fail turns it into an arbitrary number that
    forces the next honest change to be split.
    """
    c, s, standard = ctx.config.complexity, ctx.config.size, Config()
    worst = {"ccn": 0, "lines": 0, "params": 0}
    for _rel, fn in complexity.functions(ctx):
        worst["ccn"] = max(worst["ccn"], fn.cyclomatic_complexity)
        worst["lines"] = max(worst["lines"], fn.nloc)
        worst["params"] = max(worst["params"], complexity.param_count(fn))
    longest = max((_line_count(ctx.root / rel) for rel in ctx.code_files() if not files.is_test(rel)), default=0)
    candidates = (
        ("[complexity] ccn_fail", worst["ccn"], c.ccn_fail, standard.complexity.ccn_fail, "the most complex function"),
        ("[complexity] max_function_lines", worst["lines"], c.max_function_lines,
         standard.complexity.max_function_lines, "the longest function"),
        ("[complexity] max_parameters", worst["params"], c.max_parameters, standard.complexity.max_parameters,
         "the widest signature"),
        ("[size] max_file_lines_fail", longest, s.fail, standard.size.fail, "the longest file outside tests"),
    )
    return [
        Gap("headroom", f"`{key}` was raised to {limit} (the default is {default}), but {what} is now at {value}.",
            "Nothing needs the looser limit any more. Consider returning it to the default.")
        for key, value, limit, default, what in candidates if limit > default and value <= default
    ]


def _line_count(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


# ------------------------------------------------- what gate is told to skip

def _blind_spots(ctx: Context) -> list[Gap]:
    cfg = ctx.config
    out = []
    if cfg.exclude:
        hidden = len(files.all_files(ctx.root)) - len(ctx.repo_files)
        if hidden:
            out.append(Gap("blind spots", f"{hidden} file(s) match `[files] exclude` and are never checked.",
                           "Confirm each pattern still only covers generated or vendored code."))
    for glob, limits in cfg.complexity.overrides:
        looser = [f"{name} {value}" for name, value in limits if value > getattr(cfg.complexity, name)]
        if looser:
            out.append(Gap("blind spots", f"Functions in files matching `{glob}` are allowed {', '.join(looser)}.",
                           "Confirm the exception is still needed."))
    for o in cfg.size.overrides:
        if o.fail is not None and o.fail > cfg.size.fail:
            out.append(Gap("blind spots", f"Files matching `{o.glob}` may reach {o.fail} lines instead of {cfg.size.fail}.",
                           "Confirm the exception is still needed."))
    return out


# ----------------------------------------------------------------- upkeep

def _upkeep(ctx: Context) -> list[Gap]:
    from . import runner  # local import: runner depends on the checks this module also uses

    out = []
    recorded = baseline.load(ctx.root)
    now = baseline.current(runner.structural_findings(ctx))
    if baseline.pruned(recorded, now) != recorded:
        out.append(Gap("upkeep", "The baseline records violations that have since been fixed or improved.",
                       "Run `gate baseline --prune`."))
    if recorded:
        out.append(Gap("upkeep", f"{baseline.size(recorded)} violation(s) are tolerated by the baseline.",
                       "Run `gate todo` for the next file to clean up."))
    scheduled = {check for checks in ctx.config.profiles.values() for check in checks}
    declared = dependencies.current(dependencies.declared(ctx.root, ctx.repo_files))
    if "new-dependencies" in scheduled and declared and dependencies.load(ctx.root) is None:
        out.append(Gap("upkeep", f"{dependencies.count(declared)} package(s) are declared and none has been approved, "
                                 "so a new one is not noticed.",
                       "Review them, then run `gate dependencies --accept`."))
    if rules.stale(ctx.root, ctx.config):
        out.append(Gap("upkeep", "The rules written out for agents are missing or lag gate.toml.", "Run `gate rules`."))
    return out


def _ci(ctx: Context) -> list[Gap]:
    pipelines = [f for f in ctx.repo_files if _is_pipeline(f)]
    if not pipelines:
        # A hook only runs where it is installed, and can be skipped. CI is the copy nobody can skip.
        return [Gap("ci", "No CI configuration was found, so nothing runs the gate on a change that skipped the local hook.",
                    "Add a CI job running `gate check --profile full --strict`.")]
    for rel in pipelines:
        try:
            if "gate check" in (ctx.root / rel).read_text(encoding="utf-8", errors="replace"):
                return []
        except OSError:
            continue
    return [Gap("ci", "This repo has CI configuration, but none of it runs `gate check`.",
                "Add a step running `gate check --profile full --strict`.")]


CI_FILES = (".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", "Jenkinsfile",
            ".buildkite/pipeline.yml", ".travis.yml")


def _is_pipeline(rel: str) -> bool:
    return rel in CI_FILES or (rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")))


# ----------------------------------------------------------------- output

def to_text(gaps: list[Gap]) -> str:
    if not gaps:
        return "gate gaps: none that gate can see. That is not the same as none: see what it cannot see."
    lines = [f"gate gaps: {len(gaps)} that gate can see for itself"]
    for area in dict.fromkeys(g.area for g in gaps):
        lines.append(area)
        for g in (g for g in gaps if g.area == area):
            lines.append(f"  - {g.message}")
            if g.suggestion:
                lines.append(f"    {g.suggestion}")
    lines.append("gate cannot see: tests that assert nothing, duplicated or dead code, unclear names, missing error handling.")
    return "\n".join(lines)


def to_json(gaps: list[Gap]) -> str:
    return json.dumps({"gaps": [asdict(g) for g in gaps]}, indent=2, sort_keys=True)

