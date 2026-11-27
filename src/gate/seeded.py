"""Runs a repo's tests against one planted fault after another, in a copy of the repo.

Why a copy: gate is read-only. Planting a fault means changing a file, and
doing that in the working tree would put a broken file in front of whoever is
editing it, and leave it there if the run were interrupted. So the repo's
files are copied to a temporary folder, the installed packages are linked in
rather than copied, and everything happens there.

Two things are checked before any fault is believed:

- The tests pass in the clean copy. If they do not, a fault "noticed" would
  mean nothing.
- Each file, broken outright, makes the tests fail. If it does not, either no
  test depends on that file or the tests are running some other copy of the
  code, such as an installed one. Either way a fault surviving there says
  nothing about that one fault, so the file is reported once and left.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import proc
from .lang.faults import Fault, planted
from .settings import ConfigError

# Enough to stop any language parsing or compiling the file.
_BROKEN = b"\n((((\n"
_WALK_DEPTH = 4


@dataclass(frozen=True)
class Settings:
    # The tests to run against each planted fault. Quick ones: it is run once for every fault.
    command: str = ""
    # Longer than this and the run is stopped, which counts as the fault being noticed.
    timeout_seconds: int = 120
    # The most faults one run plants, and the longest it may take in all.
    max_faults: int = 30
    max_seconds: int = 600
    # Folders holding what was installed, linked into the copy the faults are planted in.
    link: tuple[str, ...] = ("node_modules", ".venv", "venv")
    severity: str = "warn"


def read_settings(table: dict) -> Settings:
    link = table.get("link")
    try:
        cfg = Settings(**{**table, **({"link": tuple(link)} if link is not None else {})})
    except TypeError as e:
        raise ConfigError(f"[seeded-bugs]: {e}") from e
    positive = min(cfg.timeout_seconds, cfg.max_faults, cfg.max_seconds) >= 1
    if cfg.severity not in ("fail", "warn") or not positive or not isinstance(cfg.command, str):
        raise ConfigError('[seeded-bugs]: severity must be "fail" or "warn", command a string, and the limits at least 1')
    return cfg


@dataclass
class Outcome:
    tried: int = 0
    total: int = 0
    unnoticed: list[tuple[str, Fault]] = field(default_factory=list)
    # Files that could be broken outright without the tests failing.
    undefended: list[str] = field(default_factory=list)

    @property
    def cut_short(self) -> int:
        """Faults that were not tried because the budget ran out."""
        return self.total - self.tried


class CannotRun(Exception):
    """The tests could not be used to judge anything; the message says why."""


@contextmanager
def copy_of(root: Path, files: tuple[str, ...], link: tuple[str, ...]) -> Iterator[Path]:
    """A throwaway copy of the repo's own files, with its installed packages linked in."""
    work = Path(tempfile.mkdtemp(prefix="gate-seeded-"))
    try:
        for rel in files:
            source = root / rel
            if source.is_file():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, work / rel)
        for folder in _linkable(root, set(link)):
            if not (work / folder).exists():
                (work / folder).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(root / folder, work / folder, target_is_directory=True)
        yield work
    finally:
        shutil.rmtree(work, ignore_errors=True)  # removes the links, never what they point at


def _linkable(root: Path, names: set[str], rel: Path = Path("."), depth: int = 0) -> list[Path]:
    """Folders with one of these names, near the top of the repo: where a language keeps what was installed."""
    found: list[Path] = []
    try:
        entries = sorted(e for e in (root / rel).iterdir() if e.is_dir() and e.name != ".git")
    except OSError:
        return found
    for entry in entries:
        inside = rel / entry.name
        if entry.name in names:
            found.append(inside)
        elif depth < _WALK_DEPTH and not entry.is_symlink():
            found += _linkable(root, names, inside, depth + 1)
    return found


def run(root: Path, files: tuple[str, ...], faults: dict[str, list[Fault]], limits: Settings) -> Outcome:
    """Try the faults until they or the budget run out."""
    outcome = Outcome(total=sum(len(found) for found in faults.values()))
    if not outcome.total:
        return outcome
    started = time.monotonic()
    with copy_of(root, files, limits.link) as work:
        env = _env(work)
        _must_pass_clean(work, limits, env)
        queues = {rel: list(found) for rel, found in faults.items() if _defended(work, rel, limits, env, outcome)}
        outcome.total = sum(len(queue) for queue in queues.values())  # a file nothing defends has no faults worth trying
        for rel, fault in _in_turn(queues):
            if outcome.tried >= limits.max_faults or time.monotonic() - started > limits.max_seconds:
                break
            outcome.tried += 1
            if not _noticed(work, rel, planted((work / rel).read_bytes(), fault), limits, env):
                outcome.unnoticed.append((rel, fault))
    return outcome


def _must_pass_clean(work: Path, limits: Settings, env: dict[str, str]) -> None:
    """The tests have to pass before anything is planted, or a fault being "noticed" would mean nothing."""
    code, out = proc.run(limits.command, work, limits.timeout_seconds, env)
    if code != 0:
        raise CannotRun("the tests do not pass in a clean copy of the repo, so they cannot judge a fault"
                        + (" (they timed out)" if code is None else "") + f":\n{proc.tail(out, 600)}")


def _in_turn(queues: dict[str, list[Fault]]) -> Iterator[tuple[str, Fault]]:
    """One fault from each file, then the next from each, so a budget that runs out has starved no file."""
    queues = {rel: list(found) for rel, found in queues.items()}
    while any(queues.values()):
        for rel in [r for r, queue in queues.items() if queue]:
            yield rel, queues[rel].pop(0)


def _env(work: Path) -> dict[str, str]:
    """What the tests are run with: the copy's own code first, and nothing written beside the sources."""
    first = [str(d) for d in (work / "src", work) if d.is_dir()]
    return {"PYTHONPATH": os.pathsep.join([*first, os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
            "PYTHONDONTWRITEBYTECODE": "1"}


def _noticed(work: Path, rel: str, changed: bytes, limits: Settings, env: dict[str, str]) -> bool:
    """Do the tests fail with this file changed? The file is put back whatever happens."""
    path = work / rel
    original = path.read_bytes()
    try:
        path.write_bytes(changed)
        code, _ = proc.run(limits.command, work, limits.timeout_seconds, env)
    finally:
        path.write_bytes(original)
    return code != 0


def _defended(work: Path, rel: str, limits: Settings, env: dict[str, str], outcome: Outcome) -> bool:
    """True when breaking the file outright makes the tests fail, so a fault in it can be judged."""
    if _noticed(work, rel, (work / rel).read_bytes() + _BROKEN, limits, env):
        return True
    outcome.undefended.append(rel)
    return False
