"""Loads and validates gate.toml.

Assembles the whole config from the sections `settings` knows how to read,
and resolves profiles. Unknown sections are errors, for the reason given there.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import seeded, settings, stamp
from .settings import (
    SUPPRESSION_KINDS,
    TOOLCHAIN,
    Boundaries,
    ChangeSize,
    Command,
    Complexity,
    ConfigError,
    Coverage,
    DeadCode,
    Duplication,
    Size,
    Syntax,
)

STRUCTURAL = ("syntax", "complexity", "size", "boundaries", "suppressions", "test-integrity", "duplication", "dead-code",
              "new-dependencies", "change-size", "mutable-state")
# Designed but not built yet. Accepted in config so a file written against the
# design keeps loading; a profile that names one reports it as skipped.
PLANNED = ("test-quality", "docs", "scope", "imports")
# Built, but only meaningful once the repo says where its coverage report or its stamps are, or which tests are
# quick enough to run against every planted fault, so not in a default profile.
OPTIONAL = ("coverage", "stamps", "seeded-bugs")

_TOP_LEVEL = {"version", "files", "profiles", "syntax", "complexity", "size", "boundaries",
              "suppressions", "test-integrity", "duplication", "dead-code", "change-size", "mutable-state", "toolchain", "tests", "command", "rules", "stamp", *OPTIONAL, *PLANNED}


@dataclass(frozen=True)
class Config:
    exclude: tuple[str, ...] = ()
    profiles: dict[str, list[str]] = field(default_factory=dict)
    default_profile: str = "fast"
    syntax: Syntax = Syntax()
    complexity: Complexity = Complexity()
    size: Size = Size()
    boundaries: Boundaries = Boundaries()
    # Which kinds of suppression marker count as findings.
    suppressions: tuple[str, ...] = SUPPRESSION_KINDS
    test_integrity_severity: str = "fail"
    duplication: Duplication = Duplication()
    dead_code: DeadCode = DeadCode()
    change_size: ChangeSize = ChangeSize()
    coverage: Coverage = Coverage()
    seeded_bugs: seeded.Settings = seeded.Settings()
    # Files recording what something the repo keeps was made from.
    stamps: tuple[stamp.Rule, ...] = ()
    toolchain: dict[str, str] = field(default_factory=dict)
    tests: dict[str, Command] = field(default_factory=dict)
    commands: dict[str, Command] = field(default_factory=dict)
    # Instruction files `gate rules` maintains; empty means "whichever exist".
    rules_targets: tuple[str, ...] = ()

    def checks_for(self, profile: str | None) -> tuple[str, list[str]]:
        name = profile or self.default_profile
        if name not in self.profiles:
            raise ConfigError(f"unknown profile '{name}' (have: {', '.join(sorted(self.profiles))})")
        return name, self.profiles[name]

    def known_checks(self) -> set[str]:
        return {*STRUCTURAL, *OPTIONAL, *TOOLCHAIN, *PLANNED, *self.tests, *self.commands}


def load(path: Path | None) -> Config:
    """Parse gate.toml. A missing file means defaults: gate works with no config."""
    raw: dict = {}
    if path is not None and path.is_file():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    unknown = set(raw) - _TOP_LEVEL
    if unknown:
        raise ConfigError(f"unknown section(s) in gate.toml: {', '.join(sorted(unknown))}")
    tests = {n: settings.read_command(n, t) for n, t in raw.get("tests", {}).items()}
    commands = {c.get("name", "?"): settings.read_command(c.get("name", "?"), c) for c in raw.get("command", [])}
    cfg = Config(
        exclude=tuple(raw.get("files", {}).get("exclude", ())),
        syntax=Syntax(tuple(settings.read_fields(Syntax, raw.get("syntax", {}), "syntax").fail_on)),
        complexity=settings.read_complexity(raw.get("complexity", {})),
        size=settings.read_size(raw.get("size", {})),
        boundaries=settings.read_boundaries(raw.get("boundaries", {})),
        suppressions=settings.read_suppressions(raw.get("suppressions", {})),
        test_integrity_severity=settings.read_severity(raw.get("test-integrity", {}), "test-integrity"),
        duplication=settings.read_duplication(raw.get("duplication", {})),
        dead_code=settings.read_dead_code(raw.get("dead-code", {})),
        change_size=settings.read_change_size(raw.get("change-size", {})),
        coverage=settings.read_coverage(raw.get("coverage", {})),
        seeded_bugs=seeded.read_settings(raw.get("seeded-bugs", {})),
        stamps=stamp.read_rules(raw.get("stamp", [])),
        toolchain=settings.read_toolchain(raw.get("toolchain", {})),
        tests=tests,
        commands=commands,
        rules_targets=settings.read_rules_targets(raw.get("rules", {})),
    )
    return _with_profiles(cfg, raw.get("profiles", {}))


def _with_profiles(cfg: Config, table: dict) -> Config:
    """Fill in default profiles, then reject any profile naming an unknown check."""
    table = dict(table)
    default = table.pop("default", "fast")
    fast = [*STRUCTURAL, "lint"]
    profiles = {"fast": fast, "full": [*fast, "typecheck", *cfg.tests], **table}
    known = cfg.known_checks()
    for name, checks in profiles.items():
        bad = [c for c in checks if c not in known]
        if bad:
            raise ConfigError(f"profile '{name}' names unknown check(s): {', '.join(bad)}")
    return Config(**{**cfg.__dict__, "profiles": profiles, "default_profile": default})
