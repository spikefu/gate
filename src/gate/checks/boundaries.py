"""Architecture rules over the import graph.

Two forms. `forbid` ("a !-> b") is a layering rule. `only` expresses
ports and adapters: a target that just the named layers may import, which a
forbid list cannot say without enumerating every other directory.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from ..context import CODE_EXTS, Context
from ..files import is_test, matches
from ..findings import Finding
from ..lang import cycles
from ..lang.import_graph import Edge, edges_for, externals_for, matches_package
from ..settings import Boundaries, OnlyRule
from . import encapsulation

PACKAGE = "pkg:"


def run(ctx: Context) -> list[Finding]:
    rules = ctx.config.boundaries
    findings: list[Finding] = []
    graph = edges_for(ctx.root, ctx.repo_files, ctx.code_files())
    if rules.forbid or rules.only or rules.modules:
        for src, edges in graph.items():
            for edge in edges:
                reason = _violation(ctx, src, edge.target) or _reaches_inside(rules, src, edge)
                if reason:
                    findings.append(_finding(src, edge, reason))
        findings += _outside(ctx)
    if rules.private:
        findings += encapsulation.private_imports(graph)
    findings += encapsulation.fan_out(graph, rules.max_imports)
    if rules.cycles:
        findings += _cycles(ctx)
    return findings


def _reaches_inside(rules: Boundaries, src: str, edge: Edge) -> str | None:
    """Whether `src` imports a file inside a module it is not part of, other than the module's entry."""
    if is_test(src) or _asked_the_package(edge):
        return None  # a test may need a module's internals to test them
    dst = edge.target
    for rule in rules.modules:
        found = rule.inside(dst)
        if found is None:
            continue
        module, inner = found
        if src.startswith(module + "/") or rule.is_entry(inner):
            return None
        entry = ", ".join(rule.entry) or "its entry point"
        why = f" ({rule.why})" if rule.why else ""
        return f"reaches inside the module {module}: import it through {entry}{why}"
    return None


def _asked_the_package(edge: Edge) -> bool:
    """Python's `from pkg import name` asks the package for `name`, which may be something its
    `__init__` exports or a submodule of the same name. The text cannot say which, so it is read
    as going through the package. `from pkg.name import x` names the submodule and is not."""
    words = edge.spec.split()
    if len(words) < 2 or words[0] != "from":
        return False
    asked = words[1].rsplit(".", 1)[-1]
    target = PurePosixPath(edge.target)
    return asked != (target.parent.name if target.name == "__init__.py" else target.stem)


def _outside(ctx: Context) -> list[Finding]:
    """Imports of outside packages that a rule keeps out of the importing file."""
    rules = ctx.config.boundaries
    named = [right for _left, right in rules.forbid] + [rule.target for rule in rules.only]
    if not any(pattern.startswith(PACKAGE) for pattern in named):
        return []
    findings = []
    for src, imports in externals_for(ctx.root, ctx.repo_files, ctx.code_files()).items():
        for imported in imports:
            reason = _package_violation(rules, src, imported.name)
            if reason:
                findings.append(Finding(
                    check="boundaries", rule="import", key=f"boundaries:{src}->{PACKAGE}{imported.name}", path=src,
                    line=imported.line, message=f"imports the package {imported.name}; {reason}",
                ))
    return findings


def _package_violation(rules: Boundaries, src: str, name: str) -> str | None:
    for left, right in rules.forbid:
        if right.startswith(PACKAGE) and matches(src, left) and matches_package(name, right):
            return f"rule: {left} !-> {right}"
    for rule in rules.only:
        if rule.target.startswith(PACKAGE) and matches_package(name, rule.target) and _governed_and_not_allowed(rule, src):
            why = f" ({rule.why})" if rule.why else ""
            return f"only {', '.join(rule.allowed_from)} may import {rule.target}{why}"
    return None


def _cycles(ctx: Context) -> list[Finding]:
    """One finding per group of modules that import each other, if the run touches any of them."""
    code = [f for f in ctx.repo_files if Path(f).suffix in CODE_EXTS]  # a cycle can pass through unchanged files
    edges = edges_for(ctx.root, ctx.repo_files, code)
    graph = {src: sorted({e.target for e in found if _loads(src, e)}) for src, found in edges.items()}
    judged = set(ctx.files)
    return [
        _cycle_finding(group, cycles.shortest_loop(graph, group[0]), edges[group[0]])
        for group in cycles.tangles(graph) if not judged.isdisjoint(group)
    ]


def _cycle_finding(group: list[str], loop: list[str], first_edges: list[Edge]) -> Finding:
    everyone = f"\nall {len(group)}: {', '.join(group)}" if len(group) > len(loop) - 1 else ""
    return Finding(
        check="boundaries", rule="cycle", key=f"boundaries/cycle:{group[0]}", path=group[0],
        line=next((e.line for e in first_edges if e.target == loop[1]), None), value=len(group), limit=0,
        message=f"{len(group)} modules import each other in a loop: move what they share into a module "
                "neither depends on, or pass it in",
        detail=" -> ".join(loop) + everyone,
    )


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
        if _breaks(rule, src, dst):
            why = f" ({rule.why})" if rule.why else ""
            return f"only {', '.join(rule.allowed_from)} may import {rule.target}{why}"
    return None


def _breaks(rule: OnlyRule, src: str, dst: str) -> bool:
    """Whether `src` importing `dst` crosses into a target that only named layers may import."""
    if not matches(dst, rule.target) or matches(src, rule.target):
        return False  # not an import of the target, or the target importing itself
    return _governed_and_not_allowed(rule, src)


def _governed_and_not_allowed(rule: OnlyRule, src: str) -> bool:
    governed = not rule.applies_to or any(matches(src, scope) for scope in rule.applies_to)
    return governed and not any(matches(src, allowed) for allowed in rule.allowed_from)


def _finding(src: str, edge: Edge, reason: str) -> Finding:
    return Finding(
        check="boundaries", rule="import", key=f"boundaries:{src}->{edge.target}", path=src,
        line=edge.line, message=f"imports {edge.target}; {reason}",
    )
