"""Architecture rules over the import graph.

Two forms. `forbid` ("a !-> b") is a layering rule. `only` expresses
ports and adapters: a target that just the named layers may import, which a
forbid list cannot say without enumerating every other directory.
"""

from __future__ import annotations

from pathlib import Path

from ..context import CODE_EXTS, Context
from ..files import matches
from ..findings import Finding
from ..lang import cycles
from ..lang.import_graph import Edge, edges_for


def run(ctx: Context) -> list[Finding]:
    rules = ctx.config.boundaries
    findings: list[Finding] = []
    if rules.forbid or rules.only:
        graph = edges_for(ctx.root, ctx.repo_files, ctx.code_files())
        for src, edges in graph.items():
            for edge in edges:
                reason = _violation(ctx, src, edge.target)
                if reason:
                    findings.append(_finding(src, edge, reason))
    if rules.cycles:
        findings += _cycles(ctx)
    return findings


def _cycles(ctx: Context) -> list[Finding]:
    """One finding per group of modules that import each other, if the run touches any of them."""
    code = [f for f in ctx.repo_files if Path(f).suffix in CODE_EXTS]  # a cycle can pass through unchanged files
    edges = edges_for(ctx.root, ctx.repo_files, code)
    graph = {src: sorted({e.target for e in found if _loads(src, e)}) for src, found in edges.items()}
    judged = set(ctx.files)
    out = []
    for group in cycles.tangles(graph):
        if judged.isdisjoint(group):
            continue
        loop = cycles.shortest_loop(graph, group[0])
        out.append(Finding(
            check="boundaries", rule="cycle", key=f"boundaries/cycle:{group[0]}", path=group[0],
            line=next((e.line for e in edges[group[0]] if e.target == loop[1]), None), value=len(group), limit=0,
            message=f"{len(group)} modules import each other in a loop: move what they share into a module "
                    "neither depends on, or pass it in",
            detail=" -> ".join(loop) + (f"\nall {len(group)}: {', '.join(group)}" if len(group) > len(loop) - 1 else ""),
        ))
    return out


def _loads(src: str, edge: Edge) -> bool:
    """Whether this import runs while `src` is loading, so that it can take part in a cycle."""
    if edge.lazy:
        return False
    # A module importing its own package is how Python packages are laid out, not a design problem.
    package = edge.target.removesuffix("__init__.py")
    return not (package != edge.target and src.startswith(package))


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
