"""Fails a change whose new lines the tests never run.

Why: a suite that passes says nothing about code it does not execute, and
new code is where that matters. A whole-repo percentage hides it, since a
thousand untested new lines barely move a large total, and it punishes old
code nobody is touching. So the question asked is narrower: of the lines this
change added, how many did the tests run?

Only lines the report knows are executable count, so comments and blank lines
are free. A changed file missing from the report altogether is reported
separately as a warning: it is either untested or simply not measured, and
the report cannot say which.

The report is the repo's, written by its own test command. If it is older
than the code being judged, its line numbers describe a different file, so
the check says it could not run instead of guessing.
"""

from __future__ import annotations

from pathlib import Path

from .. import coverage, files
from ..context import CODE_EXTS, Context
from ..findings import WARN, Finding, Skipped

MAX_LISTED = 8


def run(ctx: Context) -> list[Finding]:
    cfg = ctx.config.coverage
    if not cfg.reports:
        raise Skipped("no coverage report is configured; set `[coverage] report` to the file your tests write")
    present = [r for r in cfg.reports if (ctx.root / r).is_file()]
    if not present:
        raise Skipped(f"coverage report not found ({', '.join(cfg.reports)}); run the tests with coverage first")
    hits = coverage.read(ctx.root, present, ctx.repo_files)
    findings = _total(ctx, hits)
    if ctx.base is not None:
        findings += _changed(ctx, hits, present)
    return findings


def _total(ctx: Context, hits: coverage.Hits) -> list[Finding]:
    cfg = ctx.config.coverage
    lines = [count for per_file in hits.values() for count in per_file.values()]
    overall = coverage.percent(sum(1 for count in lines if count), len(lines))
    if not cfg.min_percent or overall >= cfg.min_percent:
        return []
    return [Finding(check="coverage", rule="total", key="coverage/total", path=".", severity=cfg.severity,
                    value=overall, limit=cfg.min_percent,
                    message=f"{overall:g}% of measured lines are covered, below the {cfg.min_percent}% required")]


def _changed(ctx: Context, hits: coverage.Hits, reports: list[str]) -> list[Finding]:
    cfg = ctx.config.coverage
    changed = _changed_code(ctx)
    measured = {rel: lines & set(hits[rel]) for rel, lines in changed.items() if rel in hits}
    _refuse_stale(ctx, reports, [rel for rel, lines in measured.items() if lines])
    missed = {rel: _not_run(lines, hits[rel]) for rel, lines in measured.items()}
    total = sum(len(lines) for lines in measured.values())
    covered = total - sum(len(lines) for lines in missed.values())
    findings = []
    if total and coverage.percent(covered, total) < cfg.min_changed_percent:
        findings.append(_uncovered(cfg, covered, total, missed))
    return findings + _unmeasured(changed, hits)


def _not_run(lines: set[int], hits: dict[int, int]) -> list[int]:
    return sorted(number for number in lines if not hits[number])


def _changed_code(ctx: Context) -> dict[str, set[int]]:
    """Lines this change added, in the code files being judged that are not tests."""
    assert ctx.base is not None
    judged = {rel for rel in ctx.files if Path(rel).suffix in CODE_EXTS and not files.is_test(rel)}
    return {rel: lines for rel, lines in files.changed_lines(ctx.root, ctx.base).items() if rel in judged}


def _unmeasured(changed: dict[str, set[int]], hits: coverage.Hits) -> list[Finding]:
    absent = sorted(rel for rel, lines in changed.items() if rel not in hits and lines)
    if not absent:
        return []
    return [Finding(
        check="coverage", rule="unmeasured", key="coverage/unmeasured", path=absent[0], severity=WARN,
        message=f"{len(absent)} changed file(s) are not in the coverage report: untested, or not measured",
        detail="\n".join(absent[:MAX_LISTED]),
    )]


def _refuse_stale(ctx: Context, reports: list[str], judged: list[str]) -> None:
    written = min((ctx.root / r).stat().st_mtime for r in reports)
    newer = [rel for rel in judged if (ctx.root / rel).stat().st_mtime > written]
    if newer:
        raise Skipped(f"the coverage report is older than {newer[0]}"
                      + (f" and {len(newer) - 1} more" if len(newer) > 1 else "")
                      + "; run the tests with coverage again")


def _uncovered(cfg, covered: int, total: int, missed: dict[str, list[int]]) -> Finding:
    worst = sorted(((rel, lines) for rel, lines in missed.items() if lines), key=lambda item: (-len(item[1]), item[0]))
    got = coverage.percent(covered, total)
    return Finding(
        check="coverage", rule="changed-lines", key="coverage/changed-lines", path=worst[0][0], line=worst[0][1][0],
        severity=cfg.severity, value=got, limit=cfg.min_changed_percent,
        message=f"the tests run {covered} of the {total} lines this change adds ({got:g}%, {cfg.min_changed_percent}% "
                "required): test the new code, do not exclude it",
        detail="\n".join(f"{rel}: not run: {_ranges(lines)}" for rel, lines in worst[:MAX_LISTED]),
    )


def _ranges(numbers: list[int]) -> str:
    """Line numbers as compact ranges: 3, 4, 5, 9 becomes 3-5, 9."""
    spans: list[list[int]] = []
    for number in numbers:
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)
