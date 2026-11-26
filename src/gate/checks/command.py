"""Runs a project's own command and judges it by exit code.

Covers test suites (`[tests.<name>]`) and anything else with a script
(`[[command]]`): Playwright, accessibility scans, migration drift checks.
"""

from __future__ import annotations

from .. import proc
from ..context import Context
from ..findings import Finding
from ..settings import Command


def run(ctx: Context, command: Command) -> list[Finding]:
    code, output = proc.run(command.run, ctx.root, command.timeout)
    if code == 0:
        return []
    message = (f"timed out after {command.timeout}s" if code is None else f"failed (exit {code})")
    return [Finding(check=command.name, rule="exit-code", key=f"{command.name}:exit-code", path=".",
                    message=f"`{command.run}` {message}", detail=proc.tail(output, 3000))]
