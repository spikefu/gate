"""The baseline as a work queue: which file to clean up next.

Ranked by how far over its limits a file is in total, so the first item is
where a refactor buys the most. One file is the unit of work because one file
is a reviewable diff.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .findings import Finding


@dataclass(frozen=True)
class FileTodo:
    path: str
    score: float
    findings: tuple[Finding, ...]


def queue(baselined: list[Finding]) -> list[FileTodo]:
    by_path: dict[str, list[Finding]] = {}
    for f in baselined:
        by_path.setdefault(f.path, []).append(f)
    todos = [
        FileTodo(path, round(sum(_excess(f) for f in fs), 2), tuple(sorted(fs, key=lambda f: (-_excess(f), f.key))))
        for path, fs in by_path.items()
    ]
    return sorted(todos, key=lambda t: (-t.score, t.path))


def _excess(f: Finding) -> float:
    """How far past the limit, as a ratio, so CCN and line counts compare."""
    if not f.value or not f.limit:
        return 1.0
    return f.value / f.limit - 1


def to_text(todos: list[FileTodo], shown: list[FileTodo], stale: int) -> str:
    total = sum(len(t.findings) for t in todos)
    lines = [f"gate todo: {total} baselined violation(s) in {len(todos)} file(s)"]
    for t in shown:
        lines.append(t.path)
        for f in t.findings:
            where = f"  {f.symbol}:{f.line}" if f.symbol else ""
            lines.append(f"  {f.message.split(':')[0]}{where}")
    if len(shown) < len(todos):
        lines.append(f"... {len(todos) - len(shown)} more file(s)")
    if stale:
        lines.append(f"{stale} recorded violation(s) are already fixed: run `gate baseline --prune`")
    return "\n".join(lines)


def to_json(todos: list[FileTodo], shown: list[FileTodo], stale: int) -> str:
    payload = {
        "total_findings": sum(len(t.findings) for t in todos),
        "total_files": len(todos),
        "stale": stale,
        "files": [{"path": t.path, "score": t.score, "findings": [asdict(f) for f in t.findings]} for t in shown],
    }
    return json.dumps(payload, indent=2, sort_keys=True)
