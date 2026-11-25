"""Loads and validates gate.toml.

Validation is strict on purpose: a misspelled key that is silently ignored
would leave a rule unenforced while everyone believes it is on.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STRUCTURAL = ("syntax", "complexity", "size", "boundaries")
TOOLCHAIN = ("lint", "typecheck", "format", "security", "deps")
# Designed but not built yet. Accepted in config so a file written against the
# design keeps loading; a profile that names one reports it as skipped.
PLANNED = ("test-quality", "coverage", "docs", "scope", "imports", "rules")

_TOP_LEVEL = {"version", "files", "profiles", "syntax", "complexity", "size", "boundaries",
              "toolchain", "tests", "command", *PLANNED}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Syntax:
    # Languages whose tree-sitter parse errors block without a second opinion.
    fail_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Complexity:
    ccn_warn: int = 10
    ccn_fail: int = 15
    max_function_lines: int = 120
    max_parameters: int = 5


@dataclass(frozen=True)
class SizeOverride:
    glob: str
    warn: int | None = None
    fail: int | None = None


@dataclass(frozen=True)
class Size:
    warn: int = 300
    fail: int = 500
    overrides: tuple[SizeOverride, ...] = ()


@dataclass(frozen=True)
class OnlyRule:
    target: str
    allowed_from: tuple[str, ...]
    why: str = ""
    # Importers the rule governs; empty means the whole repo. Lets a rule cover
    # product code while leaving tests and scripts free to reach in.
    applies_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class Boundaries:
    forbid: tuple[tuple[str, str], ...] = ()
    only: tuple[OnlyRule, ...] = ()


@dataclass(frozen=True)
class Command:
    name: str
    run: str
    timeout: int = 300


@dataclass(frozen=True)
class Config:
    exclude: tuple[str, ...] = ()
    profiles: dict[str, list[str]] = field(default_factory=dict)
    default_profile: str = "fast"
    syntax: Syntax = Syntax()
    complexity: Complexity = Complexity()
    size: Size = Size()
    boundaries: Boundaries = Boundaries()
    toolchain: dict[str, str] = field(default_factory=dict)
    tests: dict[str, Command] = field(default_factory=dict)
    commands: dict[str, Command] = field(default_factory=dict)

    def checks_for(self, profile: str | None) -> tuple[str, list[str]]:
        name = profile or self.default_profile
        if name not in self.profiles:
            raise ConfigError(f"unknown profile '{name}' (have: {', '.join(sorted(self.profiles))})")
        return name, self.profiles[name]

    def known_checks(self) -> set[str]:
        return {*STRUCTURAL, *TOOLCHAIN, *PLANNED, *self.tests, *self.commands}


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
    tests = {n: _command(n, t) for n, t in raw.get("tests", {}).items()}
    commands = {c.get("name", "?"): _command(c.get("name", "?"), c) for c in raw.get("command", [])}
    cfg = Config(
        exclude=tuple(raw.get("files", {}).get("exclude", ())),
        syntax=Syntax(tuple(_fields(Syntax, raw.get("syntax", {}), "syntax").fail_on)),
        complexity=_fields(Complexity, raw.get("complexity", {}), "complexity"),
        size=_size(raw.get("size", {})),
        boundaries=_boundaries(raw.get("boundaries", {})),
        toolchain=_toolchain(raw.get("toolchain", {})),
        tests=tests,
        commands=commands,
    )
    return _with_profiles(cfg, raw.get("profiles", {}))


def _fields(cls, table: dict, section: str):
    try:
        return cls(**table)
    except TypeError as e:
        raise ConfigError(f"[{section}]: {e}") from e


def _command(name: str, table: dict) -> Command:
    if not isinstance(table, dict) or not table.get("run", table.get("command")):
        raise ConfigError(f"check '{name}' needs a command")
    return Command(name=name, run=table.get("run", table.get("command")),
                   timeout=int(table.get("timeout_seconds", 300)))


def _size(table: dict) -> Size:
    overrides = tuple(
        SizeOverride(o["glob"], o.get("max_file_lines_warn"), o.get("max_file_lines_fail"))
        for o in table.get("override", [])
    )
    unknown = set(table) - {"max_file_lines_warn", "max_file_lines_fail", "override"}
    if unknown:
        raise ConfigError(f"[size]: unknown key(s) {', '.join(sorted(unknown))}")
    return Size(table.get("max_file_lines_warn", 300), table.get("max_file_lines_fail", 500), overrides)


def _boundaries(table: dict) -> Boundaries:
    forbid = []
    for rule in table.get("forbid", []):
        left, sep, right = rule.partition("!->")
        if not sep or not left.strip() or not right.strip():
            raise ConfigError(f"[boundaries] forbid rule must look like 'a !-> b': {rule!r}")
        forbid.append((left.strip(), right.strip()))
    only = []
    for o in table.get("only", []):
        if "target" not in o or not o.get("allowed_from"):
            raise ConfigError("[[boundaries.only]] needs 'target' and 'allowed_from'")
        only.append(OnlyRule(o["target"], tuple(o["allowed_from"]), o.get("why", ""),
                             tuple(o.get("applies_to", ()))))
    return Boundaries(tuple(forbid), tuple(only))


def _toolchain(table: dict) -> dict[str, str]:
    unknown = set(table) - set(TOOLCHAIN)
    if unknown:
        raise ConfigError(f"[toolchain]: unknown key(s) {', '.join(sorted(unknown))}")
    return dict(table)


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
