"""Builds file -> imported-file edges for Python and JS/TS.

Regex-based and best effort: an import in a comment counts. That errs toward
reporting, which is the safe side for a boundary rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from .js_resolve import EXTS as JS_EXTS
from .js_resolve import Resolver

_PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(.+)$", re.MULTILINE)
_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]*,[ \t]*[\w.]+)*)", re.MULTILINE)
_JS_IMPORT = re.compile(r"""(\bfrom|\brequire\s*\(|\bimport\s*\(|\bimport)\s*['"]([^'"\n]+)['"]""")
_JS_TYPE_ONLY = re.compile(r"^(?:import|export)\s+type\b")
_JS_NAMED = re.compile(r"^(?:import|export)\s*\{([^}]*)\}\s*$")


@dataclass(frozen=True)
class Edge:
    target: str
    line: int
    spec: str
    # True when the import does not run as the importing module loads: a type-only import, a
    # dynamic import(), or a Python import inside a function or an `if TYPE_CHECKING:` block.
    # Such an edge is a dependency but cannot be part of a load-time cycle.
    lazy: bool = False


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


@dataclass(frozen=True)
class External:
    """An import of something that is not this repo's code: a package or a standard-library module."""
    name: str  # as written, minus Node's `node:` prefix: `express`, `fs/promises`, `os.path`
    line: int


def externals_for(root: Path, repo_files: tuple[str, ...], sources: list[str]) -> dict[str, list[External]]:
    """What each file in `sources` imports from outside the repo."""
    resolver = Resolver(root, repo_files)
    own = _python_top_levels(repo_files)
    out: dict[str, list[External]] = {}
    for rel in sources:
        suffix = Path(rel).suffix
        if suffix != ".py" and suffix not in JS_EXTS:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out[rel] = _python_externals(text, own) if suffix == ".py" else _js_externals(text, rel, resolver)
    return out


def _js_externals(text: str, rel: str, resolver: Resolver) -> list[External]:
    found = ((resolver.external(rel, m.group(2)), _line_of(text, m.start())) for m in _JS_IMPORT.finditer(text))
    return [External(name, line) for name, line in found if name]


def _python_externals(text: str, own: set[str]) -> list[External]:
    named = [(m.group(2), m.start()) for m in _PY_FROM.finditer(text) if not m.group(1)]
    for m in _PY_IMPORT.finditer(text):
        named += [(mod, m.start()) for mod in re.findall(r"[\w.]+", m.group(1))]
    return [External(mod, _line_of(text, at)) for mod, at in named if mod and mod.split(".")[0] not in own]


def _python_top_levels(repo_files: tuple[str, ...]) -> set[str]:
    """Names importable as the repo's own top-level modules: those sitting in a folder that is not a package.

    A `logging.py` inside a package is `pkg.logging`, so `import logging` there still means the
    standard library. Only a module or package at a source root can shadow an outside name.
    """
    packages = {rel.rsplit("/", 1)[0] if "/" in rel else "" for rel in repo_files if rel.endswith("/__init__.py")}
    own = set()
    for rel in repo_files:
        if not rel.endswith(".py"):
            continue
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and "/".join(parts[:-1]) not in packages:
            own.add(parts[-1])
    return own


def matches_package(name: str, pattern: str) -> bool:
    """Whether an imported name falls under a `pkg:` pattern: the package itself or anything inside it."""
    pattern = pattern.removeprefix("pkg:").removeprefix("node:")
    if any(ch in pattern for ch in "*?["):
        return fnmatchcase(name, pattern)
    return name == pattern or name.startswith((pattern + "/", pattern + "."))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _js_edges(text: str, rel: str, resolver: Resolver) -> list[Edge]:
    edges = []
    for m in _JS_IMPORT.finditer(text):
        target = resolver.resolve(rel, m.group(2))
        if target and target != rel:
            lazy = m.group(1).startswith("import") and m.group(1).endswith("(") or _type_only(text, m.start())
            edges.append(Edge(target, _line_of(text, m.start()), m.group(2), lazy))
    return edges


def _type_only(text: str, from_at: int) -> bool:
    """Whether the `from` at this offset closes an import or export of types alone."""
    start = max(text.rfind("import", 0, from_at), text.rfind("export", 0, from_at))
    if start == -1:
        return False
    head = text[start:from_at].strip()
    if _JS_TYPE_ONLY.match(head):
        return True
    named = _JS_NAMED.match(head)
    names = [n.strip() for n in named.group(1).split(",") if n.strip()] if named else []
    return bool(names) and all(n.startswith("type ") for n in names)


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
        lazy = m.group(0)[:1] in (" ", "\t")
        edges += [Edge(t, line, m.group(0).strip(), lazy) for t in sorted(t for t in targets if t and t != rel)]
    for m in _PY_IMPORT.finditer(text):
        line = _line_of(text, m.start())
        for mod in re.findall(r"[\w.]+", m.group(1)):
            target = modules.get(mod)
            if target and target != rel:
                edges.append(Edge(target, line, f"import {mod}", m.group(0)[:1] in (" ", "\t")))
    return edges


def _relative_base(package: list[str], dots: str, mod: str) -> str:
    if not dots:
        return mod
    up = len(dots) - 1
    base = package[:len(package) - up] if up else package
    return ".".join([*base, *([mod] if mod else [])])
