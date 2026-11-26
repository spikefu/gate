"""Finds definitions that nothing in the repo refers to.

Why: an agent that changes approach halfway leaves the first attempt behind,
and code nobody calls still has to be read, kept compiling and kept passing
review. It also hides real intent: a reader cannot tell abandoned from unused.

The test is deliberately crude and strict: a top-level definition is dead
when its name appears exactly once in the whole repo, which is the definition
itself. Any other mention anywhere, in code, a test, a config file or a
document, counts as a use. That misses dead code whose name is shared with
something live, and in exchange almost never accuses code that is in use.

Three more guards keep it honest:

- Only modules that other code imports are judged. A file loaded by
  convention (a route, a page, a migration, a plugin) has no importer, and
  what it must export is decided by the framework, not by this repo.
- A module re-exported wholesale (`export * from`, `from x import *`) is
  skipped: its names are forwarded somewhere this check cannot follow.
- A decorated Python definition is skipped: the decorator may register it.

Default exports and files nobody imports at all are not judged, so an unused
file is not found. That needs to know the entry points, which is a job for a
tool that understands the framework.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..context import Context
from ..files import is_test, matches
from ..findings import Finding
from ..lang.import_graph import Edge, edges_for
from ..lang.js_resolve import EXTS as JS_EXTS
from ..lang.js_resolve import Resolver
from ..settings import DeadCode

MAX_BYTES = 1_000_000

_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_JS_EXPORT = re.compile(
    r"^export\s+(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(function\*?|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
_JS_STAR = re.compile(r"""\bexport\s+\*\s+(?:as\s+[\w$]+\s+)?from\s*['"]([^'"\n]+)['"]""")
_PY_DEF = re.compile(r"^(?:async[ \t]+)?(def|class)[ \t]+(\w+)", re.MULTILINE)
_PY_STAR = re.compile(r"^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+\*", re.MULTILINE)
_KIND = {"def": "function", "const": "constant", "let": "variable", "var": "variable"}


def run(ctx: Context) -> list[Finding]:
    cfg = ctx.config.dead_code
    candidates = [rel for rel in ctx.files if _judgeable(rel, cfg.exclude)]
    if not candidates:
        return []
    texts = _texts(ctx)
    uses = Counter(word for text in texts.values() for word in _WORD.findall(text))
    judged = sorted(set(candidates) & _judged_modules(ctx, texts))
    return [finding for rel in judged for finding in _in_file(rel, texts[rel], uses, cfg)]


def _in_file(rel: str, text: str, uses: Counter[str], cfg: DeadCode) -> list[Finding]:
    here = Counter(_WORD.findall(text))
    findings = []
    for kind, name, line in definitions(rel, text):
        if name in cfg.ignore:
            continue
        if uses[name] == 1:
            findings.append(_finding(rel, kind, name, line, cfg.severity))
        elif cfg.exports and _needlessly_exported(rel, kind, uses[name], here[name]):
            findings.append(_export_finding(rel, kind, name, line, cfg.severity))
    return findings


def _needlessly_exported(rel: str, kind: str, everywhere: int, here: int) -> bool:
    """An export nothing outside its file mentions.

    Types are left alone: a compiler that writes declaration files can require a type to be
    exported because an exported function mentions it, even though nobody imports it by name.
    """
    return not rel.endswith(".py") and kind not in ("interface", "type") and everywhere == here


def _judged_modules(ctx: Context, texts: dict[str, str]) -> set[str]:
    """Modules whose definitions this repo alone decides: imported by something, and not forwarded wholesale."""
    graph = edges_for(ctx.root, ctx.repo_files, [rel for rel in texts if _is_module(rel)])
    imported = {edge.target for edges in graph.values() for edge in edges}
    return (imported & set(texts)) - _forwarded(ctx, texts, graph)


def _is_module(rel: str) -> bool:
    return rel.endswith(".py") or Path(rel).suffix in JS_EXTS


def _judgeable(rel: str, exclude: tuple[str, ...]) -> bool:
    if not _is_module(rel) or rel.endswith(".d.ts") or is_test(rel):
        return False
    return not any(matches(rel, pattern) for pattern in exclude)


def _texts(ctx: Context) -> dict[str, str]:
    """Every readable text file in the repo: a name mentioned in any of them is in use."""
    out = {}
    for rel in ctx.repo_files:
        # gate.toml names definitions in order to talk about them (the ignore list), which is
        # not using them. The baseline, which names them too, is never among the repo's files.
        if rel == "gate.toml":
            continue
        path = ctx.root / rel
        try:
            if path.stat().st_size <= MAX_BYTES:
                out[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # binary or unreadable: it cannot mention a name
            continue
    return out


def _forwarded(ctx: Context, texts: dict[str, str], graph: dict[str, list[Edge]]) -> set[str]:
    """Modules re-exported wholesale, whose names leave by a route this check cannot follow."""
    resolver = Resolver(ctx.root, ctx.repo_files)
    out: set[str] = set()
    for rel in graph:
        out |= _python_stars(texts[rel], graph[rel]) if rel.endswith(".py") else _js_stars(rel, texts[rel], resolver)
    return out


def _python_stars(text: str, edges: list[Edge]) -> set[str]:
    starred = {match.group(1) for match in _PY_STAR.finditer(text)}
    return {edge.target for edge in edges if any(f"from {s} import" in edge.spec for s in starred)}


def _js_stars(rel: str, text: str, resolver: Resolver) -> set[str]:
    targets = (resolver.resolve(rel, match.group(1)) for match in _JS_STAR.finditer(text))
    return {target for target in targets if target}


def definitions(rel: str, text: str) -> list[tuple[str, str, int]]:
    """(kind, name, line) of each top-level definition this check will judge."""
    if rel.endswith(".py"):
        return [(kind, name, line) for kind, name, line in _matches(_PY_DEF, text)
                if not name.startswith("__") and not _decorated(text, line)]
    return _matches(_JS_EXPORT, text)


def _matches(pattern: re.Pattern[str], text: str) -> list[tuple[str, str, int]]:
    return [(m.group(1), m.group(2), text.count("\n", 0, m.start()) + 1) for m in pattern.finditer(text)]


def _decorated(text: str, line: int) -> bool:
    above = text.splitlines()[:line - 1]
    while above and not above[-1].strip():
        above.pop()
    return bool(above) and above[-1].lstrip().startswith("@")


def _export_finding(rel: str, kind: str, name: str, line: int, severity: str) -> Finding:
    kind = _KIND.get(kind, kind.rstrip("*"))
    return Finding(
        check="dead-code", rule="needless-export", key=f"dead-code/needless-export:{rel}::{name}", path=rel, line=line,
        symbol=name, severity=severity,
        message=f"{kind} `{name}` is exported but only this file uses it: drop the `export`, so the module "
                "exposes only what others rely on",
    )


def _finding(rel: str, kind: str, name: str, line: int, severity: str) -> Finding:
    kind = _KIND.get(kind, kind.rstrip("*"))
    return Finding(
        check="dead-code", rule="unused", key=f"dead-code/unused:{rel}::{name}", path=rel, line=line, symbol=name,
        severity=severity,
        message=f"{kind} `{name}` is never referred to anywhere in the repo: delete it, or use it",
    )
