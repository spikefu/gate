"""Records today's violations so only new or worsened ones fail.

Why it exists: without it the tool cannot be adopted on a repo that already
breaks its own rules. Why it stores values: a baselined function that grows
from CCN 20 to 30 is a new problem and must fail.

Why it stores a list per key: a key is a file and a function name, and names
repeat. Every anonymous function shares one, as do same-named methods. One
recorded value per key would let any number of new functions hide behind the
first, so each recorded value covers exactly one finding.

Only structural checks are baselinable, suppression counts among them. A failing test suite or linter is
never something to grandfather.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .findings import FAIL, Finding

BASELINABLE = ("complexity", "size", "boundaries", "suppressions", "duplication")
FILE = ".gate/baseline.json"

Value = float | None
# Per key, worst first. None is a violation with no measure (a forbidden import).
Entries = dict[str, list[Value]]


def load(root: Path) -> Entries:
    path = root / FILE
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8")).get("entries", {})
    # Version 1 recorded a single value per key.
    return {key: _worst_first(v if isinstance(v, list) else [v]) for key, v in raw.items()}


def is_legacy(root: Path) -> bool:
    """A version 1 file: one value per key, so it never said how many findings a key covered."""
    path = root / FILE
    return path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("version", 1) < 2


def save(root: Path, entries: Entries) -> None:
    path = root / FILE
    path.parent.mkdir(exist_ok=True)
    body = {"version": 2, "entries": {k: _worst_first(v) for k, v in sorted(entries.items())}}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def size(entries: Entries) -> int:
    return sum(len(values) for values in entries.values())


def apply(findings: list[Finding], entries: Entries) -> list[Finding]:
    """Mark the findings the baseline covers, pairing each recorded value with one finding, worst with worst."""
    out = list(findings)
    for key, group in _eligible_by_key(findings).items():
        group.sort(key=lambda pair: _rank(pair[1].value))
        for (index, finding), recorded in zip(group, entries.get(key, []), strict=False):
            if _within(finding.value, recorded):
                out[index] = finding.as_baselined()
            else:
                # Say what was accepted, so the reader can tell how much is new.
                out[index] = replace(finding, message=f"{finding.message} (the baseline allows {recorded:g})")
    return out


def current(findings: list[Finding]) -> Entries:
    """What the baseline would be if every present failure were accepted."""
    return {key: _worst_first([f.value for _i, f in group]) for key, group in _eligible_by_key(findings).items()}


def pruned(entries: Entries, now: Entries) -> Entries:
    """Shrink only: drop entries for fixed violations and ratchet improved values down. Never adds."""
    out: Entries = {}
    for key, recorded in entries.items():
        kept = [_tighter(r, n) for r, n in zip(recorded, now.get(key, []), strict=False)]
        if kept:
            out[key] = kept
    return out


def tightened(before: Entries, after: Entries) -> int:
    """How many recorded values `after` holds at a lower figure than `before`."""
    return sum(1 for key, values in after.items() for old, new in zip(before[key], values, strict=False) if old != new)


def _eligible_by_key(findings: list[Finding]) -> dict[str, list[tuple[int, Finding]]]:
    groups: dict[str, list[tuple[int, Finding]]] = {}
    for index, f in enumerate(findings):
        if f.severity == FAIL and f.check in BASELINABLE:
            groups.setdefault(f.key, []).append((index, f))
    return groups


def _rank(value: Value) -> float:
    return float("-inf") if value is None else -value


def _worst_first(values: list[Value]) -> list[Value]:
    return sorted(values, key=_rank)


def _within(value: Value, recorded: Value) -> bool:
    return recorded is None or value is None or value <= recorded


def _tighter(recorded: Value, now: Value) -> Value:
    return recorded if recorded is None or now is None else min(recorded, now)
