"""Finds blocks of code that appear more than once, identical apart from layout and comments.

Why: writing a second copy is cheaper than finding the first, and an agent
that has not read the whole repo will do it often. Each copy then has to be
found and fixed separately, and usually is not.

A file being checked is compared with every code file in the repo, so new code
that copies old code is caught on a `--changed` run. One finding per file
carries the number of duplicated lines, which makes the baseline a ratchet:
existing copies are accepted once and the figure may only fall.

Only exact copies are found. Lines are compared after dropping whitespace,
comments, imports and lines that are only brackets, so reformatting a copy
does not hide it, but renaming a variable does. That is deliberate: matching
"similar" code would flag code that merely has the same shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..context import CODE_EXTS, Context
from ..files import is_test, matches
from ..findings import FAIL, Finding

MAX_BYTES = 1_000_000
MAX_CANDIDATES = 50  # places one block may be compared with; bounds the work on boilerplate-heavy repos
MAX_LISTED = 10

_TRIVIAL = re.compile(r"^[\s{}()\[\];,]*$")
_COMMENT = re.compile(r"^\s*(//|#(?!\[|include|define|if|pragma)|/\*|\*|--\s|<!--)")
_IMPORT = re.compile(r"^\s*(import\b|from\s+\S+\s+import\b|export\s+(\*|\{[^}]*\})\s+from\b|use\s|using\s|#include\b|package\s"
                     r"|(const|let|var)\s+[\w{}\s,:]+=\s*require\()")
_TOKEN = re.compile(r"\w+|[^\w\s]")


@dataclass(frozen=True)
class Line:
    number: int   # in the source file
    text: str     # normalized
    tokens: int


@dataclass(frozen=True)
class Clone:
    start: int        # source lines in the file being judged
    end: int
    other: str
    other_start: int
    other_end: int


def run(ctx: Context) -> list[Finding]:
    cfg = ctx.config.duplication
    eligible = [rel for rel in ctx.repo_files if _eligible(ctx, rel)]
    judged = sorted(set(ctx.files) & set(eligible))
    if not judged:
        return []
    lines = {rel: _lines(ctx.root / rel) for rel in eligible}
    index = _index(lines, cfg.min_lines)
    findings = []
    for rel in judged:
        clones = _clones(rel, lines, index, cfg.min_lines, cfg.min_tokens)
        if clones:
            findings.append(_finding(rel, clones))
    return findings


def _eligible(ctx: Context, rel: str) -> bool:
    cfg = ctx.config.duplication
    if Path(rel).suffix not in CODE_EXTS or any(matches(rel, pattern) for pattern in cfg.exclude):
        return False
    return cfg.include_tests or not is_test(rel)


def _lines(path: Path) -> list[Line]:
    """The lines that carry meaning, normalized so layout and comments do not matter."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for number, text in enumerate(raw, 1):
        if _TRIVIAL.match(text) or _COMMENT.match(text) or _IMPORT.match(text):
            continue
        normal = " ".join(text.split())
        out.append(Line(number, normal, len(_TOKEN.findall(normal))))
    return out


def _window(lines: list[Line], at: int, size: int) -> tuple[str, ...]:
    return tuple(line.text for line in lines[at:at + size])


def _index(lines: dict[str, list[Line]], size: int) -> dict[tuple[str, ...], list[tuple[str, int]]]:
    """Every run of `size` meaningful lines in the repo, and where it occurs."""
    index: dict[tuple[str, ...], list[tuple[str, int]]] = {}
    for rel in sorted(lines):
        for at in range(len(lines[rel]) - size + 1):
            index.setdefault(_window(lines[rel], at, size), []).append((rel, at))
    return index


def _clones(rel: str, lines: dict[str, list[Line]], index: dict, size: int, min_tokens: int) -> list[Clone]:
    mine, out, at = lines[rel], [], 0
    while at <= len(mine) - size:
        best = _longest(rel, at, lines, index.get(_window(mine, at, size), ())[:MAX_CANDIDATES], size)
        if best is None or sum(line.tokens for line in mine[at:at + best[2]]) < min_tokens:
            at += 1
            continue
        other, other_at, length = best
        theirs = lines[other]
        out.append(Clone(mine[at].number, mine[at + length - 1].number, other,
                         theirs[other_at].number, theirs[other_at + length - 1].number))
        at += length
    return out


def _longest(rel: str, at: int, lines: dict[str, list[Line]], candidates, size: int) -> tuple[str, int, int] | None:
    """The other place this block continues longest: (file, position, length)."""
    mine, best = lines[rel], None
    for other, other_at in candidates:
        if other == rel and abs(other_at - at) < size:
            continue  # a block is not a copy of itself
        theirs, length = lines[other], size
        while (at + length < len(mine) and other_at + length < len(theirs)
               and mine[at + length].text == theirs[other_at + length].text
               and not (other == rel and at + length >= other_at > at)):
            length += 1
        if best is None or length > best[2]:
            best = (other, other_at, length)
    return best


def _finding(rel: str, clones: list[Clone]) -> Finding:
    covered = {number for clone in clones for number in range(clone.start, clone.end + 1)}
    listed = [f"{c.start}-{c.end} is also at {c.other}:{c.other_start}-{c.other_end}" for c in clones[:MAX_LISTED]]
    if len(clones) > MAX_LISTED:
        listed.append(f"... and {len(clones) - MAX_LISTED} more block(s)")
    return Finding(
        check="duplication", rule="copied-lines", key=f"duplication/copied-lines:{rel}", path=rel, line=clones[0].start,
        value=len(covered), limit=0, severity=FAIL,
        message=f"{len(covered)} line(s) in {len(clones)} block(s) are copies of code elsewhere: "
                "use the existing code, or move it somewhere both can share",
        detail="\n".join(listed),
    )
