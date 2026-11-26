"""Proposes boundary rules from the imports a repo already has.

The idea is to freeze the architecture as it stands: every proposed rule holds
today, so adopting them adds no violations, and the human deletes the ones
that are coincidence rather than intent.
"""

from __future__ import annotations

from pathlib import Path

from .context import CODE_EXTS
from .files import matches
from .lang.import_graph import edges_for
from .lang.js_resolve import Resolver

# Directories that reach into product code by design and are not layers of it.
NON_PRODUCT = {"tests", "test", "__tests__", "e2e", "scripts", "docs", "examples", "fixtures", "migrations"}
MAX_RULES = 20


def boundary_rules(root: Path, repo_files: tuple[str, ...]) -> list[str]:
    code = [f for f in repo_files
            if Path(f).suffix in CODE_EXTS and not NON_PRODUCT.intersection(f.split("/"))]
    graph = edges_for(root, repo_files, code)
    packages = Resolver(root, repo_files).packages
    top = units(code, packages)
    # Separately packaged parts that do not import each other are separate on
    # purpose, so that is proposed as a rule. Sibling folders are not: see below.
    packaged = set(top) <= set(packages.values())
    rules = _rules(top, _unit_edges(graph, top), include_unrelated=packaged) if len(top) >= 2 else []
    # The layering that matters often sits one level down (src/db, src/modules).
    # There, only one-way dependencies are proposed: sibling folders that happen
    # not to import each other are too weak a signal to turn into rules.
    for part in top or [""]:
        inner = branch(code, part)
        if len(inner) >= 2:
            rules += _rules(inner, _unit_edges(graph, inner), include_unrelated=False)
    return list(dict.fromkeys(rules))[:MAX_RULES]


def units(code: list[str], packages: dict[str, str]) -> list[str]:
    """The top-level parts of the product: workspace packages, else the first directory level that branches."""
    workspace = sorted(d for d in packages.values() if any(matches(f, d) for f in code))
    return workspace if len(workspace) >= 2 else branch(code, "")


def branch(code: list[str], node: str) -> list[str]:
    """Subdirectories of `node` that hold code, looking through lone wrapper directories."""
    while True:
        prefix = f"{node}/" if node else ""
        inside = [f[len(prefix):] for f in code if f.startswith(prefix)]
        children = sorted({f.split("/")[0] for f in inside if "/" in f})
        loose = sum(1 for f in inside if "/" not in f)
        # A lone wrapper directory (src/, src/<package>/) is not a layer; look inside it.
        if len(children) == 1 and loose <= 1:
            node = prefix + children[0]
            continue
        return [prefix + c for c in children] if len(children) >= 2 else []


def _unit_edges(graph: dict, parts: list[str]) -> set[tuple[str, str]]:
    def unit_of(path: str) -> str | None:
        return next((u for u in parts if matches(path, u)), None)

    edges = set()
    for src, found in graph.items():
        a = unit_of(src)
        for edge in found:
            b = unit_of(edge.target)
            if a and b and a != b:
                edges.add((a, b))
    return edges


def _rules(parts: list[str], edges: set[tuple[str, str]], include_unrelated: bool) -> list[str]:
    importers = {a for a, _ in edges}
    # A part others depend on that depends on nothing: forbidding imports of it
    # would outlaw the obvious next use of a shared library.
    foundations = {b for _, b in edges} - importers
    pairs = sorted((b, a) for a, b in edges if (b, a) not in edges)
    if include_unrelated:
        pairs += _unrelated(parts, edges, foundations)
    return [f"{a} !-> {b}" for a, b in pairs]


def _unrelated(parts: list[str], edges: set[tuple[str, str]], foundations: set[str]) -> list[tuple[str, str]]:
    """Pairs of parts with no import either way today, which a rule can keep apart."""
    return sorted(
        (a, b) for a in parts for b in parts
        if a != b and (a, b) not in edges and (b, a) not in edges and b not in foundations
    )
