"""Reads the coverage reports a repo's own test run leaves behind.

gate does not run tests under coverage; the repo's test command does, and
writes a report. Two formats cover nearly every toolchain: lcov (`lcov.info`,
from JavaScript, Go, Rust and C tooling, and `coverage lcov` in Python) and
Cobertura XML (`coverage xml`, and most JVM and .NET tools).

The hard part is the file names. A report names files the way its tool saw
them: absolute, or relative to the package the tests ran in, which in a
monorepo is not the repo root. Each name is resolved against the repo, then
against the folders above the report, nearest first.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# file -> line number -> times executed. A line absent from the inner map is not executable.
Hits = dict[str, dict[int, int]]


def read(root: Path, reports: list[str], repo_files: tuple[str, ...]) -> Hits:
    """Every measured line from the given reports, keyed by repo-relative file. Counts add up across reports."""
    known = set(repo_files)
    out: Hits = {}
    for report in reports:
        path = root / report
        raw = _cobertura(path) if path.suffix == ".xml" else _lcov(path)
        for name, lines in raw.items():
            rel = _resolve(root, path, name, known)
            if rel is None:
                continue
            merged = out.setdefault(rel, {})
            for number, hits in lines.items():
                merged[number] = merged.get(number, 0) + hits
    return out


def percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else round(100.0 * covered / total, 1)


def _lcov(path: Path) -> Hits:
    out: Hits = {}
    current: dict[int, int] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("SF:"):
            current = out.setdefault(line[3:].strip(), {})
        elif line.startswith("DA:") and current is not None:
            number, _, hits = line[3:].partition(",")
            if number.isdigit():
                current[int(number)] = current.get(int(number), 0) + _count(hits.split(",")[0])
    return out


def _cobertura(path: Path) -> Hits:
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return {}
    sources = [s.text.strip() for s in tree.iter("source") if s.text and s.text.strip()]
    out: Hits = {}
    for cls in tree.iter("class"):
        name = cls.get("filename")
        if name:
            lines = out.setdefault(_under_a_source(name, sources), {})
            for number, hits in _cobertura_lines(cls):
                lines[number] = lines.get(number, 0) + hits
    return out


def _under_a_source(name: str, sources: list[str]) -> str:
    """Cobertura names files relative to one of its <source> folders; find which."""
    return next((str(Path(s) / name) for s in sources if (Path(s) / name).is_file()), name)


def _cobertura_lines(cls: ET.Element) -> list[tuple[int, int]]:
    return [(int(line.get("number", "")), _count(line.get("hits", "0")))
            for line in cls.iter("line") if line.get("number", "").isdigit()]


def _count(text: str) -> int:
    try:
        return int(float(text))
    except ValueError:
        return 0


def _resolve(root: Path, report: Path, name: str, known: set[str]) -> str | None:
    """The repo file a report means by `name`, or None if it is not one of the repo's files."""
    name = name.replace("\\", "/")
    if Path(name).is_absolute():
        try:
            rel = Path(name).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return None
        return rel if rel in known else None
    name = name.removeprefix("./")
    if name in known:
        return name
    for folder in report.resolve().parents:
        try:
            rel = (folder / name).relative_to(root.resolve()).as_posix()
        except ValueError:
            return None  # walked above the repo
        if rel in known:
            return rel
    return None
