"""Fails when something the repo keeps was made from files that have since changed.

Why: an agent changes the code and moves on; the index, the checked
specification or the generated client that was made from that code still
describes the old code, and nothing says so. Each such file has a stamp
(see `gate.stamp`) recording what it was made from. This check compares.

It knows nothing about what made the file or what the file is for. The repo
lists its stamps in gate.toml, and whatever makes each file writes its stamp.
"""

from __future__ import annotations

from .. import stamp
from ..context import Context
from ..findings import Finding, Skipped

MAX_LISTED = 4


def run(ctx: Context) -> list[Finding]:
    if not ctx.config.stamps:
        raise Skipped("no stamp is declared; add [[stamp]] entries naming the stamps this repo keeps")
    return [finding for rule in ctx.config.stamps for finding in _judge(ctx, rule)]


def _judge(ctx: Context, rule: stamp.Rule) -> list[Finding]:
    try:
        recorded = stamp.read(ctx.root / rule.file)
    except stamp.StampError as error:
        return [_finding(rule, "unreadable", f"{rule.file} is not a stamp that can be read: {error}. Make what it is for again")]
    again = f": make it again with `{recorded.remake}`" if recorded.remake else ": make it again"
    out = []
    moved = stamp.drift(ctx.root, recorded, ctx.repo_files)
    if moved.any():
        out.append(_finding(rule, "stale", f"{recorded.made} was made from files that are not as they were ({_listed(moved)}){again}"))
    if not recorded.ok:
        out.append(_finding(rule, "problem", f"{recorded.made} reports a problem: fix what it found, then make it again"))
    return out


def _listed(moved: stamp.Drift) -> str:
    named = [*(f"{rel} changed" for rel in moved.changed), *(f"{rel} is gone" for rel in moved.gone),
             *(f"{rel} is new" for rel in moved.added)]
    more = f", and {len(named) - MAX_LISTED} more" if len(named) > MAX_LISTED else ""
    return ", ".join(named[:MAX_LISTED]) + more


def _finding(rule: stamp.Rule, kind: str, message: str) -> Finding:
    return Finding(check="stamps", rule=kind, key=f"stamps/{kind}:{rule.file}", path=rule.file, severity=rule.severity, message=message)
