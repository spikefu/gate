"""Reads a stamp: a small file saying what something was made from.

Some files in a repo are made from others by a tool that takes a while to
run: an index of the code, the result of a model checker, a generated
client. Such a file goes stale without anyone noticing. A stamp, written
when the file is made and kept in the repo, records a hash of each file it
was made from, so anyone can later tell whether those files have changed.

    {"version": 1,
     "made": "specs/Session.out",                 what was made
     "inputs": {"specs/Session.tla": "<sha256>"}, each file it was made from
     "covers": ["src/session/**"],                (optional) globs that must add no file since
     "ok": true,                                  (optional) whether what was made reports all is well
     "remake": "make check-specs"}                (optional) how to make it again

The format belongs to no one tool. Whatever makes the file writes the stamp;
gate only reads it. A stamp is a claim, and can be written by hand as easily
as a test can be deleted: where it matters, have CI make the file again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .files import matches
from .settings import ConfigError, read_fields

VERSION = 1


@dataclass(frozen=True)
class Rule:
    """One `[[stamp]]` entry of gate.toml: a stamp the repo keeps, and how much a stale one matters."""
    file: str
    severity: str = "fail"


def read_rules(entries: object) -> tuple[Rule, ...]:
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ConfigError("each stamp is declared as a [[stamp]] table")
    rules = tuple(read_fields(Rule, entry, "stamp") for entry in entries)
    if any(not isinstance(rule.file, str) or rule.severity not in ("fail", "warn") for rule in rules):
        raise ConfigError('[[stamp]]: file is the path of the stamp, and severity is "fail" or "warn"')
    return rules


class StampError(Exception):
    """The file is not a stamp this version reads."""


@dataclass(frozen=True)
class Stamp:
    made: str
    inputs: dict[str, str]
    covers: tuple[str, ...] = ()
    ok: bool = True
    remake: str = ""


@dataclass
class Drift:
    """How the repo differs from what a stamp recorded."""
    changed: list[str] = field(default_factory=list)  # recorded, and different now
    gone: list[str] = field(default_factory=list)     # recorded, and no longer there
    added: list[str] = field(default_factory=list)    # covered, and not there when it was made

    def any(self) -> bool:
        return bool(self.changed or self.gone or self.added)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> Stamp:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StampError(f"cannot be read ({error})") from error
    inputs = data.get("inputs") if isinstance(data, dict) else None
    if not isinstance(inputs, dict) or data.get("version") != VERSION or not isinstance(data.get("made"), str):
        raise StampError(f'needs "version": {VERSION}, "made" and "inputs"')
    covers = data.get("covers", [])
    return Stamp(made=data["made"], inputs={str(k): str(v) for k, v in inputs.items()},
                 covers=tuple(str(c) for c in covers) if isinstance(covers, list) else (),
                 ok=data.get("ok", True) is not False, remake=str(data.get("remake", "")))


def drift(root: Path, stamp: Stamp, repo_files: tuple[str, ...]) -> Drift:
    out = Drift()
    for rel, recorded in sorted(stamp.inputs.items()):
        path = root / rel
        if not path.is_file():
            out.gone.append(rel)
        elif digest(path) != recorded:
            out.changed.append(rel)
    out.added = sorted(rel for rel in repo_files if rel not in stamp.inputs and any(matches(rel, glob) for glob in stamp.covers))
    return out
