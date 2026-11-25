"""Builds file -> imported-file edges for Python and JS/TS.

Regex-based and best effort: an import in a comment counts. That errs toward
reporting, which is the safe side for a boundary rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .js_resolve import EXTS as JS_EXTS
from .js_resolve import Resolver

_PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(.+)$", re.MULTILINE)
_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]*,[ \t]*[\w.]+)*)", re.MULTILINE)
_JS_IMPORT = re.compile(r"""(?:\bfrom|\brequire\s*\(|\bimport\s*\(|\bimport)\s*['"]([^'"\n]+)['"]""")


@dataclass(frozen=True)
class Edge:
    target: str
    line: int
    spec: str


def edges_for(root: Path, repo_files: tuple[str, ...], sources: list[str]) -> dict[str, list[Edge]]:
    """Edges out of each file in `sources`, resolved against the whole repo."""
    resolver = Resolver(root, repo_files)
    modules = _python_modules(repo_files)
    graph: dict[str, list[Edge]] = {}
    for rel in sources:
        suffix = Path(rel).suffix
        if suffix != ".py" and suffix not in JS_EXTS:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        graph[rel] = _python_edges(text, rel, modules) if suffix == ".py" else _js_edges(text, rel, resolver)
    return graph


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _js_edges(text: str, rel: str, resolver: Resolver) -> list[Edge]:
    edges = []
    for m in _JS_IMPORT.finditer(text):
        target = resolver.resolve(rel, m.group(1))
        if target and target != rel:
            edges.append(Edge(target, _line_of(text, m.start()), m.group(1)))
    return edges


# ----------------------------------------------------------------- python

def _python_modules(repo_files: tuple[str, ...]) -> dict[str, str]:
    """Dotted module name -> file, registered both from the repo root and from a src/ layout."""
    index: dict[str, str] = {}
    for rel in repo_files:
        if not rel.endswith(".py"):
            continue
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):
            index.setdefault(".".join(parts[start:]), rel)
    return index


def _python_edges(text: str, rel: str, modules: dict[str, str]) -> list[Edge]:
    edges: list[Edge] = []
    package = rel[:-3].split("/")[:-1]
    for m in _PY_FROM.finditer(text):
        dots, mod, names = m.groups()
        base = _relative_base(package, dots, mod)
        line = _line_of(text, m.start())
        targets = {modules.get(base)} | {modules.get(f"{base}.{n}") for n in re.findall(r"\w+", names)}
        edges += [Edge(t, line, m.group(0).strip()) for t in sorted(t for t in targets if t and t != rel)]
    for m in _PY_IMPORT.finditer(text):
        line = _line_of(text, m.start())
        for mod in re.findall(r"[\w.]+", m.group(1)):
            target = modules.get(mod)
            if target and target != rel:
                edges.append(Edge(target, line, f"import {mod}"))
    return edges


def _relative_base(package: list[str], dots: str, mod: str) -> str:
    if not dots:
        return mod
    up = len(dots) - 1
    base = package[:len(package) - up] if up else package
    return ".".join([*base, *([mod] if mod else [])])
