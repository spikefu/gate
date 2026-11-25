"""Architecture rules over the import graph.

Two forms. `forbid` ("a !-> b") is a layering rule. `only` expresses
ports and adapters: a target that just the named layers may import, which a
forbid list cannot say without enumerating every other directory.
"""

from __future__ import annotations

from ..context import Context
from ..files import matches
from ..findings import Finding
from ..lang.import_graph import Edge, edges_for


def run(ctx: Context) -> list[Finding]:
    rules = ctx.config.boundaries
    if not rules.forbid and not rules.only:
        return []
    findings = []
    graph = edges_for(ctx.root, ctx.repo_files, ctx.code_files())
    for src, edges in graph.items():
        for edge in edges:
            reason = _violation(ctx, src, edge.target)
            if reason:
                findings.append(_finding(src, edge, reason))
    return findings


def _violation(ctx: Context, src: str, dst: str) -> str | None:
    rules = ctx.config.boundaries
    for left, right in rules.forbid:
        if matches(src, left) and matches(dst, right):
            return f"rule: {left} !-> {right}"
    for rule in rules.only:
        if not matches(dst, rule.target) or matches(src, rule.target):
            continue
        if rule.applies_to and not any(matches(src, scope) for scope in rule.applies_to):
            continue
        if not any(matches(src, allowed) for allowed in rule.allowed_from):
            why = f" ({rule.why})" if rule.why else ""
            return f"only {', '.join(rule.allowed_from)} may import {rule.target}{why}"
    return None


def _finding(src: str, edge: Edge, reason: str) -> Finding:
    return Finding(
        check="boundaries", rule="import", key=f"boundaries:{src}->{edge.target}", path=src,
        line=edge.line, message=f"imports {edge.target}; {reason}",
    )
