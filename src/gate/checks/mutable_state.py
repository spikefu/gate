"""Counts module-level variables that code reassigns: state shared by everything that imports the module.

Why: a function that reads or writes such a variable gives different results
depending on what ran before it, so it cannot be understood, tested or reused
on its own, and two callers interfere without either knowing. It is the most
common hidden side effect, and the cheapest one to add.

What counts is narrow and syntactic. In JS and TS, a `let` or `var` declared
at the top level of a module (a `const` cannot be reassigned). In Python, a
`global` statement, which is the only way a function rebinds a module's
variable. Mutating a top-level object in place is not seen.

One finding per file carries the count, so the baseline makes it a ratchet.
Test files are exempt: shared setup variables there are a test-runner idiom.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..context import Context
from ..files import is_test
from ..findings import FAIL, Finding
from ..lang.js_resolve import EXTS as JS_EXTS

MAX_BYTES = 1_000_000

_JS_TOP_LEVEL = re.compile(r"^(?:export\s+)?(?:let|var)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
# The whole line must be the statement, so prose such as "global state is bad" in a docstring is not one.
_PY_GLOBAL = re.compile(r"^[ \t]+global[ \t]+(\w+(?:[ \t]*,[ \t]*\w+)*)[ \t]*(?:#.*)?$", re.MULTILINE)


def run(ctx: Context) -> list[Finding]:
    findings = []
    for rel in ctx.files:
        found = variables(ctx.root / rel, rel)
        if found:
            findings.append(_finding(rel, found))
    return findings


def variables(path: Path, rel: str) -> list[tuple[int, str]]:
    """(line, name) of each module-level variable the file's code can reassign."""
    python = rel.endswith(".py")
    if not _judged(rel):
        return []
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _rebound_globals(text) if python else [(_line(text, m.start()), m.group(1)) for m in _JS_TOP_LEVEL.finditer(text)]


def _judged(rel: str) -> bool:
    known = rel.endswith(".py") or Path(rel).suffix in JS_EXTS
    return known and not rel.endswith(".d.ts") and not is_test(rel)


def _rebound_globals(text: str) -> list[tuple[int, str]]:
    seen: dict[str, int] = {}
    for m in _PY_GLOBAL.finditer(text):
        for name in re.findall(r"\w+", m.group(1)):
            seen.setdefault(name, _line(text, m.start()))  # one variable, however many functions rebind it
    return sorted((line, name) for name, line in seen.items())


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _finding(rel: str, found: list[tuple[int, str]]) -> Finding:
    names = ", ".join(name for _line_number, name in found[:8]) + (", ..." if len(found) > 8 else "")
    return Finding(
        check="mutable-state", rule="module-variables", key=f"mutable-state/module-variables:{rel}", path=rel,
        line=found[0][0], value=len(found), limit=0, severity=FAIL,
        message=f"{len(found)} module-level variable(s) that code reassigns ({names}): pass the value in, or keep it "
                "in an object whose owner is clear",
    )
