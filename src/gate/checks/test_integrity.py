"""Fails a change that leaves the repo with fewer tests or fewer assertions than it started with.

Why: when a test will not pass, the cheapest fix is to delete it or strip the
assertion that fails, and the suite stays green. Nothing else notices: the
linter is happy and the remaining tests pass.

It compares the test files a change touches, as they were at the base ref and
as they are now, and counts across all of them together. So moving tests
between files, renaming a file or splitting one costs nothing; only a net loss
is a finding. It cannot tell a test deleted to hide a failure from one deleted
with the feature it covered, so it says what was lost and leaves that call to
a person: committing the change is the acceptance.

It only has something to compare on a `--changed` run. Counting is by line
against each language's test and assertion syntax, which is approximate; a
count that is slightly off is off on both sides of the comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import files
from ..context import Context
from ..findings import Finding

_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")

# suffixes -> (a line that declares a test, a line that asserts something)
_SYNTAX: tuple[tuple[tuple[str, ...], re.Pattern[str], re.Pattern[str]], ...] = (
    ((".py",),
     re.compile(r"^\s*(async\s+)?def\s+test\w*\s*\("),
     re.compile(r"^\s*assert\b|\bself\.assert\w+\(|\bpytest\.(raises|warns|fail)\b|\.assert_\w+\(")),
    (_JS,
     re.compile(r"^\s*(it|test)(\.\w+)*\s*[(`]"),
     re.compile(r"\bexpect(\.\w+)?\s*\(|\bassert(\.\w+)?\s*\(|\.should\b")),
    ((".go",),
     re.compile(r"^func\s+(Test|Fuzz|Example)\w*\s*\("),
     re.compile(r"\bt\.(Error|Errorf|Fatal|Fatalf|Fail|FailNow)\(|\b(assert|require)\.\w+\(")),
    ((".rs",),
     re.compile(r"#\[\s*(\w+::)*test\b"),
     re.compile(r"\b(debug_)?assert(_eq|_ne)?!\s*\(|#\[\s*should_panic")),
    ((".java", ".kt", ".scala"),
     re.compile(r"@(Test|ParameterizedTest|RepeatedTest)\b"),
     re.compile(r"\bassert\w*\s*\(|\bverify\s*\(|\bshould\w*\b")),
    ((".rb",),
     re.compile(r"^\s*(it|specify|test)\s*[\"'(]|^\s*def\s+test_\w+"),
     re.compile(r"\bexpect\b|\bassert\w*\b|\.should\b|\bmust_\w+")),
    ((".cs",),
     re.compile(r"\[\s*(Fact|Theory|Test|TestMethod|TestCase)\b"),
     re.compile(r"\bAssert\.\w+|\.Should\(\)")),
    ((".swift",),
     re.compile(r"^\s*func\s+test\w*\s*\(|@Test\b"),
     re.compile(r"\bXCTAssert\w*\(|#expect\(|#require\(")),
    ((".php",),
     re.compile(r"function\s+test\w*\s*\(|^\s*(it|test)\s*\("),
     re.compile(r"->assert\w+\(|\bexpect\s*\(")),
)
_BY_SUFFIX = {suffix: (test, check) for suffixes, test, check in _SYNTAX for suffix in suffixes}


@dataclass(frozen=True)
class Count:
    tests: int = 0
    assertions: int = 0


def run(ctx: Context) -> list[Finding]:
    if ctx.base is None:
        return []  # a whole-repo run has no "before" to compare with
    severity = ctx.config.test_integrity_severity
    changes = _changes(ctx)
    lost_tests = _lost(changes, "tests")
    if lost_tests:
        return [_finding("fewer-tests", "test(s)", lost_tests, changes, severity)]
    lost_assertions = _lost(changes, "assertions")
    if lost_assertions:
        return [_finding("fewer-assertions", "assertion(s)", lost_assertions, changes, severity)]
    return []


def count(text: str, suffix: str) -> Count:
    """How many tests and assertions a file's text declares."""
    syntax = _BY_SUFFIX.get(suffix)
    if syntax is None:
        return Count()
    test, check = syntax
    lines = text.splitlines()
    return Count(sum(1 for line in lines if test.search(line)), sum(1 for line in lines if check.search(line)))


def _changes(ctx: Context) -> dict[str, tuple[Count, Count]]:
    """Every test file the change touches, added or removed: its counts before and after."""
    assert ctx.base is not None
    touched = {*ctx.files, *files.deleted_files(ctx.root, ctx.base, ctx.config.exclude)}
    out = {}
    for rel in sorted(touched):
        suffix = Path(rel).suffix
        if suffix not in _BY_SUFFIX or not files.is_test(rel):
            continue
        before = count(files.at_ref(ctx.root, ctx.base, rel) or "", suffix)
        after = count(_read(ctx.root / rel), suffix)
        if before != after:
            out[rel] = (before, after)
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""  # deleted in this change


def _lost(changes: dict[str, tuple[Count, Count]], measure: str) -> int:
    return max(0, sum(getattr(before, measure) - getattr(after, measure) for before, after in changes.values()))


def _finding(rule: str, noun: str, lost: int, changes: dict[str, tuple[Count, Count]], severity: str) -> Finding:
    measure = "tests" if rule == "fewer-tests" else "assertions"
    deltas = sorted(((getattr(after, measure) - getattr(before, measure), rel) for rel, (before, after) in changes.items()))
    lines = [f"{rel}: {_was_now(before, after, measure)}"
             for _delta, rel in deltas for before, after in [changes[rel]] if _delta]
    return Finding(
        check="test-integrity", rule=rule, key=f"test-integrity/{rule}", path=deltas[0][1], severity=severity,
        value=lost, limit=0,
        message=f"this change removes {lost} {noun} overall: restore them, or say in your report why they should go",
        detail="\n".join(lines),
    )


def _was_now(before: Count, after: Count, measure: str) -> str:
    was, now = getattr(before, measure), getattr(after, measure)
    return f"{was} -> {now} {measure}" + (" (file deleted)" if now == 0 and after == Count() and was else "")
