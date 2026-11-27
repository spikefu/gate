"""Warns about a change whose tests would not notice a fault in it.

Why: a suite that passes, and even one that runs every new line, says only
that nothing it looked at was wrong. Whether it looked closely enough is a
different question, and the direct way to ask it is to break the new code a
little and see whether a test fails. So for the lines a change adds, one
small fault at a time is planted and the tests are run. A fault they pass with
is a line whose behaviour no test pins down.

Only the lines of the change are asked about, so the cost follows the size of
the change and not of the repo, and old code is nobody's problem until it is
touched. Everything happens in a copy, so the working tree is never altered.

A fault that goes unnoticed is not always a missing test. Sometimes the code
behaves the same either way. That takes a person to tell, which is why this
warns unless a repo decides it should fail.
"""

from __future__ import annotations

from pathlib import Path

from .. import files, seeded
from ..context import Context
from ..findings import WARN, Finding, Skipped
from ..lang import faults

MAX_BYTES = 400_000


def run(ctx: Context) -> list[Finding]:
    cfg = ctx.config.seeded_bugs
    if not cfg.command:
        raise Skipped("no tests are named for it; set `[seeded-bugs] command` to tests quick enough to run many times")
    if ctx.base is None:
        raise Skipped("it judges a change, not a whole repo; run it with --changed")
    if faults.tslp is None:
        raise Skipped("tree-sitter-language-pack is not installed")
    planted = _planted(ctx)
    try:
        outcome = seeded.run(ctx.root, ctx.repo_files, planted, cfg)
    except seeded.CannotRun as e:
        raise Skipped(str(e)) from e
    return [*(_unnoticed(rel, fault, cfg.severity) for rel, fault in outcome.unnoticed),
            *(_undefended(rel, cfg.severity) for rel in outcome.undefended),
            *_cut_short(outcome)]


def _planted(ctx: Context) -> dict[str, list[faults.Fault]]:
    """The faults that can be planted in the lines this change added, by file. Test files are what does the judging."""
    assert ctx.base is not None
    out: dict[str, list[faults.Fault]] = {}
    for rel, lines in sorted(files.changed_lines(ctx.root, ctx.base).items()):
        lang = faults.language(rel)
        if lang is None or rel not in ctx.files or files.is_test(rel):
            continue
        source = _read(ctx.root / rel)
        found = faults.plant(source, lang, lines) if source else []
        if found:
            out[rel] = found
    return out


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes() if path.stat().st_size <= MAX_BYTES else b""
    except OSError:
        return b""


def _unnoticed(rel: str, fault: faults.Fault, severity: str) -> Finding:
    return Finding(
        check="seeded-bugs", rule="unnoticed", key=f"seeded-bugs:{rel}:{fault.line}:{fault.says}", path=rel,
        line=fault.line, severity=severity,
        message=f"{fault.says} went unnoticed: the tests still pass. Add a test that would fail, or say why this "
                "one cannot matter",
    )


def _undefended(rel: str, severity: str) -> Finding:
    return Finding(
        check="seeded-bugs", rule="undefended", key=f"seeded-bugs:{rel}:undefended", path=rel, severity=severity,
        message="breaking this file outright went unnoticed: either no test depends on it, or the tests are not "
                "running this copy of the code (an installed copy, say)",
    )


def _cut_short(outcome: seeded.Outcome) -> list[Finding]:
    if not outcome.cut_short:
        return []
    return [Finding(
        check="seeded-bugs", rule="cut-short", key="seeded-bugs/cut-short", path=".", severity=WARN,
        message=f"tried {outcome.tried} of {outcome.total} faults before the budget ran out; "
                f"{outcome.cut_short} were not tried. Raise `[seeded-bugs] max_faults` or `max_seconds`, or make "
                "the change smaller",
    )]
