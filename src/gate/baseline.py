"""Records today's violations so only new or worsened ones fail.

Why it exists: without it the tool cannot be adopted on a repo that already
breaks its own rules. Why it stores values: a baselined function that grows
from CCN 20 to 30 is a new problem and must fail.

Only structural checks are baselinable. A failing test suite or linter is
never something to grandfather.
"""

from __future__ import annotations

import json
from pathlib import Path

from .findings import FAIL, Finding

BASELINABLE = ("complexity", "size", "boundaries")
FILE = ".gate/baseline.json"


def load(root: Path) -> dict[str, float | None]:
    path = root / FILE
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("entries", {})


def save(root: Path, entries: dict[str, float | None]) -> None:
    path = root / FILE
    path.parent.mkdir(exist_ok=True)
    body = {"version": 1, "entries": dict(sorted(entries.items()))}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def apply(findings: list[Finding], entries: dict[str, float | None]) -> list[Finding]:
    return [f.as_baselined() if _covered(f, entries) else f for f in findings]


def _covered(f: Finding, entries: dict[str, float | None]) -> bool:
    if f.severity != FAIL or f.check not in BASELINABLE or f.key not in entries:
        return False
    recorded = entries[f.key]
    return recorded is None or f.value is None or f.value <= recorded


def current(findings: list[Finding]) -> dict[str, float | None]:
    """What the baseline would be if every present failure were accepted."""
    out: dict[str, float | None] = {}
    for f in findings:
        if f.severity != FAIL or f.check not in BASELINABLE:
            continue
        # Same-named functions share a key; keep the worst so neither regresses silently.
        prior = out.get(f.key)
        out[f.key] = f.value if prior is None else max(prior, f.value or prior)
    return out


def pruned(entries: dict[str, float | None], now: dict[str, float | None]) -> dict[str, float | None]:
    """Shrink only: drop fixed entries and ratchet improved values down. Never adds."""
    out: dict[str, float | None] = {}
    for key, recorded in entries.items():
        if key not in now:
            continue
        value = now[key]
        out[key] = recorded if recorded is None or value is None else min(recorded, value)
    return out
