"""File line limits, with per-glob overrides for files that are legitimately long."""

from __future__ import annotations

from ..context import Context
from ..files import matches
from ..findings import FAIL, WARN, Finding


def run(ctx: Context) -> list[Finding]:
    findings = []
    for rel in ctx.code_files():
        warn, fail = _limits(ctx, rel)
        lines = _count(ctx.root / rel)
        if lines > fail:
            findings.append(_finding(rel, lines, fail, FAIL, "split it up"))
        elif lines > warn:
            findings.append(_finding(rel, lines, warn, WARN, "getting long"))
    return findings


def _limits(ctx: Context, rel: str) -> tuple[int, int]:
    size = ctx.config.size
    for o in size.overrides:
        if matches(rel, o.glob):
            fail = o.fail if o.fail is not None else size.fail
            # An override that only raises the fail limit should not leave a
            # warn limit above it.
            warn = o.warn if o.warn is not None else max(size.warn, fail if o.fail else 0)
            return warn, fail
    return size.warn, size.fail


def _count(path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _finding(rel, lines, limit, severity, advice) -> Finding:
    return Finding(
        check="size", rule="file-lines", key=f"size/file-lines:{rel}", path=rel,
        value=lines, limit=limit, severity=severity, message=f"{lines} lines > {limit}: {advice}",
    )
