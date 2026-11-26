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
from ..lang.import_graph import edges_for
from ..lang.js_resolve import EXTS as JS_EXTS
from ..lang.js_resolve import Resolver

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
    judged = [rel for rel in ctx.files if _judgeable(rel) and not any(matches(rel, p) for p in cfg.exclude)]
    if not judged:
        return []
    texts = _texts(ctx)
    imported, forwarded = _imported_and_forwarded(ctx, texts)
    uses = Counter(word for text in texts.values() for word in _WORD.findall(text))
    findings = []
    for rel in sorted(judged):
        if rel not in imported or rel in forwarded or rel not in texts:
            continue
        for kind, name, line in _definitions(rel, texts[rel]):
            if uses[name] == 1 and name not in cfg.ignore:
                findings.append(_finding(rel, kind, name, line, cfg.severity))
    return findings


def _judgeable(rel: str) -> bool:
    suffix = Path(rel).suffix
    return (suffix == ".py" or suffix in JS_EXTS) and not rel.endswith(".d.ts") and not is_test(rel)


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


def _imported_and_forwarded(ctx: Context, texts: dict[str, str]) -> tuple[set[str], set[str]]:
    """Modules some other file imports, and modules re-exported wholesale."""
    sources = [rel for rel in texts if Path(rel).suffix == ".py" or Path(rel).suffix in JS_EXTS]
    graph = edges_for(ctx.root, ctx.repo_files, sources)
    imported = {edge.target for edges in graph.values() for edge in edges}
    forwarded: set[str] = set()
    resolver = Resolver(ctx.root, ctx.repo_files)
    for rel in sources:
        if rel.endswith(".py"):
            starred = {match.group(1) for match in _PY_STAR.finditer(texts[rel])}
            forwarded |= {edge.target for edge in graph.get(rel, ()) if any(f"from {s} import" in edge.spec for s in starred)}
        else:
            targets = (resolver.resolve(rel, match.group(1)) for match in _JS_STAR.finditer(texts[rel]))
            forwarded |= {target for target in targets if target}
    return imported, forwarded


def _definitions(rel: str, text: str) -> list[tuple[str, str, int]]:
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


def _finding(rel: str, kind: str, name: str, line: int, severity: str) -> Finding:
    kind = _KIND.get(kind, kind.rstrip("*"))
    return Finding(
        check="dead-code", rule="unused", key=f"dead-code/unused:{rel}::{name}", path=rel, line=line, symbol=name,
        severity=severity,
        message=f"{kind} `{name}` is never referred to anywhere in the repo: delete it, or use it",
    )
