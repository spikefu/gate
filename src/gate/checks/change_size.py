"""Warns when one change adds more code than a person can review properly.

Why: an agent can write faster than anyone can read, and the person who has
to approve the result is the scarce resource. Review studies find that the
share of defects a reviewer catches falls off beyond a few hundred lines. Past
that point the change gets skimmed and approved, which is no review at all.

It counts lines added or modified since the base ref in code files that are
not tests, so deleting code, writing tests and regenerating a lockfile never
count against the budget. It warns by default: the work may be fine, and
the remedy (stop, have it looked at, commit, carry on) is the person's call,
not something an agent can do by editing files. Like every comparison with a
base ref, it only has something to say on a `--changed` run.
"""

from __future__ import annotations

from pathlib import Path

from .. import files
from ..context import CODE_EXTS, Context
from ..findings import Finding

MAX_LISTED = 5


def run(ctx: Context) -> list[Finding]:
    cfg = ctx.config.change_size
    if ctx.base is None:
        return []
    counted = files.added_lines(ctx.root, ctx.base)
    added = {rel: counted.get(rel, 0) for rel in ctx.files if Path(rel).suffix in CODE_EXTS and not files.is_test(rel)}
    total = sum(added.values())
    if total <= cfg.max_lines:
        return []
    largest = sorted(added.items(), key=lambda item: (-item[1], item[0]))[:MAX_LISTED]
    return [Finding(
        check="change-size", rule="added-lines", key="change-size/added-lines", path=".", severity=cfg.severity,
        value=total, limit=cfg.max_lines,
        message=f"this change adds {total} lines of non-test code, past the {cfg.max_lines} a person can review well: "
                "stop at the next natural point and have it reviewed before adding more",
        detail="\n".join(f"{rel}: +{lines}" for rel, lines in largest if lines),
    )]
