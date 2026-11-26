"""Turns a profile into a report: resolve each check name, run it, apply the baseline."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from . import baseline, files
from .checks import (
    boundaries,
    command,
    complexity,
    dead_code,
    duplication,
    new_dependencies,
    size,
    suppressions,
    syntax,
    test_integrity,
    toolchain,
)
from .config import PLANNED, TOOLCHAIN, Config
from .context import Context
from .findings import Finding, Report, Skipped

_STRUCTURAL: dict[str, Callable[[Context], list[Finding]]] = {
    "syntax": syntax.run,
    "complexity": complexity.run,
    "size": size.run,
    "boundaries": boundaries.run,
    "suppressions": suppressions.run,
    "test-integrity": test_integrity.run,
    "duplication": duplication.run,
    "dead-code": dead_code.run,
    "new-dependencies": new_dependencies.run,
}


def build_context(root: Path, config: Config, selected: list[str] | None, base: str | None = None) -> Context:
    """`selected` None means the whole repo. `base` is set when the selection is a diff against that ref."""
    repo = tuple(files.all_files(root, config.exclude))
    chosen = repo if selected is None else tuple(selected)
    return Context(root=root, config=config, files=chosen, repo_files=repo, scoped=selected is not None, base=base)


def run(ctx: Context, profile: str | None, mode: str, strict: bool = False) -> Report:
    name, checks = ctx.config.checks_for(profile)
    report = Report(profile=name, mode=mode, files=len(ctx.files), strict=strict)
    for check in checks:
        started = time.monotonic()
        try:
            report.findings += _resolve(ctx, check)()
            report.ran[check] = round(time.monotonic() - started, 2)
        except Skipped as e:
            report.skipped[check] = str(e)
    report.findings = baseline.apply(report.findings, baseline.load(ctx.root))
    return report


def structural_findings(ctx: Context) -> list[Finding]:
    """Every baselinable finding in the repo, regardless of profile."""
    return [f for name in baseline.BASELINABLE for f in _STRUCTURAL[name](ctx)]


def _resolve(ctx: Context, check: str) -> Callable[[], list[Finding]]:
    cfg = ctx.config
    if check in _STRUCTURAL:
        return lambda: _STRUCTURAL[check](ctx)
    if check in cfg.tests:
        return lambda: command.run(ctx, cfg.tests[check])
    if check in cfg.commands:
        return lambda: command.run(ctx, cfg.commands[check])
    if check in TOOLCHAIN:
        return lambda: toolchain.run(ctx, check)
    if check in PLANNED:
        return lambda: _not_built(check)
    raise Skipped(f"'{check}' is not configured")


def _not_built(check: str) -> list[Finding]:
    raise Skipped(f"'{check}' is designed but not implemented yet")
