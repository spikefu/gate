"""Counts the markers that switch a check off: lint and type ignores, skipped tests, coverage exclusions.

Why: the quickest way to make a failing check pass is to silence it, and an
agent under pressure to finish will. Every marker is a finding, so adding one
fails. Those already in a repo go into the baseline like any other existing
violation, which records a count per file and kind: the count may fall but
never rise without a person accepting it.

Markers are only counted where they act. In Python that is a real comment or a
decorator, found by tokenizing, so a marker quoted in a string is not counted.
Other languages are matched by line, anchored to the comment or call syntax.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

from ..config import SUPPRESSION_KINDS as KINDS
from ..context import Context
from ..findings import FAIL, Finding

MAX_BYTES = 1_000_000
MAX_LISTED = 10

_ADVICE = {
    "lint": "fix what the linter reported instead of silencing it",
    "type": "give it a real type instead of switching the checker off",
    "test": "fix or delete the test instead of skipping it",
    "coverage": "test the code instead of excluding it",
}

# Python comments, matched against comment tokens only.
_PY_COMMENT = {
    "lint": re.compile(r"#\s*(noqa\b|pylint:\s*disable|ruff:\s*noqa|flake8:\s*noqa|nosec\b)"),
    "type": re.compile(r"#\s*(type:\s*ignore|pyright:\s*ignore|mypy:\s*ignore)"),
    "coverage": re.compile(r"#\s*pragma:\s*no\s*cover"),
}
_PY_SKIP = re.compile(r"^\s*@\s*(pytest\.mark\.(skip|skipif|xfail)|unittest\.skip\w*|skip\w*)\b")

_C_STYLE = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs", ".java", ".kt", ".swift",
            ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".scala")
_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_TS = (".ts", ".tsx", ".mts", ".cts")

# (file suffixes, kind, pattern). A line counts once per kind.
_LINE_RULES: tuple[tuple[tuple[str, ...], str, re.Pattern[str]], ...] = (
    (_JS, "lint", re.compile(r"(//|/\*)\s*(eslint-disable|biome-ignore|oxlint-disable|prettier-ignore)")),
    (_JS, "type", re.compile(r"(//|/\*)\s*@ts-(ignore|expect-error|nocheck)")),
    (_TS, "type", re.compile(r"^(?!\s*(//|\*|/\*)).*\bas\s+any\b(?!\s+\w)")),
    (_JS, "test", re.compile(r"\b(describe|it|test|suite|context)\.skip\s*\(|^\s*(xit|xdescribe|xtest)\s*\(")),
    (_JS, "coverage", re.compile(r"(//|/\*)\s*(istanbul|c8|v8)\s+ignore")),
    ((".go",), "lint", re.compile(r"//\s*nolint\b")),
    ((".go",), "test", re.compile(r"\bt\.Skip(f|Now)?\(")),
    ((".rs",), "lint", re.compile(r"#!?\[\s*allow\(")),
    ((".rs",), "test", re.compile(r"#\[\s*ignore\b")),
    ((".java", ".kt", ".scala"), "lint", re.compile(r"@(SuppressWarnings|Suppress)\b")),
    ((".java", ".kt", ".scala"), "test", re.compile(r"@(Disabled|Ignore)\b")),
    ((".swift",), "lint", re.compile(r"//\s*swiftlint:disable")),
    ((".cs",), "lint", re.compile(r"#pragma\s+warning\s+disable")),
    ((".php",), "lint", re.compile(r"(//|/\*|#)\s*(phpcs:(ignore|disable)|@phpstan-ignore)")),
    ((".rb",), "lint", re.compile(r"#\s*rubocop:disable")),
    ((".rb",), "test", re.compile(r"^\s*(xit|xdescribe|xcontext)\b|^\s*skip\b(\s|\(|$)")),
    ((".c", ".h", ".cc", ".cpp", ".hpp"), "lint", re.compile(r"//\s*NOLINT")),
)
SUFFIXES = {".py", ".rb", *_C_STYLE}


def run(ctx: Context) -> list[Finding]:
    kinds = ctx.config.suppressions
    findings = []
    for rel in ctx.files:
        if Path(rel).suffix not in SUFFIXES:
            continue
        found = _markers(ctx.root / rel)
        for kind in KINDS:
            if kind in kinds and found.get(kind):
                findings.append(_finding(rel, kind, found[kind]))
    return findings


def _markers(path: Path) -> dict[str, list[tuple[int, str]]]:
    """Per kind, the (line number, source line) of every marker in the file."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return {}
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    hits = _python(text) if path.suffix == ".py" else _by_line(text, path.suffix)
    lines = text.splitlines()
    out: dict[str, list[tuple[int, str]]] = {}
    for kind, number in sorted(set(hits), key=lambda hit: (hit[1], hit[0])):
        out.setdefault(kind, []).append((number, lines[number - 1].strip()))
    return out


def _python(text: str) -> list[tuple[str, int]]:
    hits = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                hits += [(kind, token.start[0]) for kind, pattern in _PY_COMMENT.items() if pattern.search(token.string)]
            elif token.type == tokenize.OP and token.string == "@" and _PY_SKIP.match(token.line):
                hits.append(("test", token.start[0]))
    except (tokenize.TokenError, SyntaxError):  # unparseable Python is the syntax check's finding
        return []
    return hits


def _by_line(text: str, suffix: str) -> list[tuple[str, int]]:
    rules = [(kind, pattern) for suffixes, kind, pattern in _LINE_RULES if suffix in suffixes]
    return [(kind, number) for number, line in enumerate(text.splitlines(), 1)
            for kind, pattern in rules if pattern.search(line)]


def _finding(rel: str, kind: str, markers: list[tuple[int, str]]) -> Finding:
    count = len(markers)
    listed = [f"{number}: {line[:160]}" for number, line in markers[:MAX_LISTED]]
    if count > MAX_LISTED:
        listed.append(f"... and {count - MAX_LISTED} more")
    return Finding(
        check="suppressions", rule=kind, key=f"suppressions/{kind}:{rel}", path=rel, line=markers[0][0],
        value=count, limit=0, severity=FAIL,
        message=f"{count} {kind} suppression(s): {_ADVICE[kind]}", detail="\n".join(listed),
    )
