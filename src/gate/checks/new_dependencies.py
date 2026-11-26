"""Fails on a declared package that nobody has approved. The reasoning is in `gate.dependencies`."""

from __future__ import annotations

from .. import dependencies
from ..context import Context
from ..findings import FAIL, Finding, Skipped


def run(ctx: Context) -> list[Finding]:
    approved = dependencies.load(ctx.root)
    if approved is None:
        # Failing every package in a repo that has never been asked would be noise, and passing
        # silently would be a lie, so the check reports that it has nothing to compare with.
        raise Skipped("no dependencies are approved yet; a person runs `gate dependencies --accept` to start")
    return [_finding(dep) for dep in dependencies.unapproved(dependencies.declared(ctx.root, ctx.files), approved)]


def _finding(dep: dependencies.Dependency) -> Finding:
    return Finding(
        check="new-dependencies", rule="unapproved", key=f"new-dependencies/unapproved:{dep.ecosystem}:{dep.name}",
        path=dep.path, line=dep.line, symbol=dep.name, severity=FAIL,
        message=f"`{dep.name}` ({dep.ecosystem}) is a new dependency nobody has approved: say why it is needed and "
                "that the package exists under exactly this name; a person then runs `gate dependencies --accept`",
    )
