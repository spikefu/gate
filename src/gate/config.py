"""Loads and validates gate.toml.

Validation is strict on purpose: a misspelled key that is silently ignored
would leave a rule unenforced while everyone believes it is on.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .files import matches

STRUCTURAL = ("syntax", "complexity", "size", "boundaries", "suppressions", "test-integrity", "duplication", "dead-code",
              "new-dependencies")
SUPPRESSION_KINDS = ("lint", "type", "test", "coverage")
TOOLCHAIN = ("lint", "typecheck", "format", "security", "deps")
# Designed but not built yet. Accepted in config so a file written against the
# design keeps loading; a profile that names one reports it as skipped.
PLANNED = ("test-quality", "coverage", "docs", "scope", "imports")

_TOP_LEVEL = {"version", "files", "profiles", "syntax", "complexity", "size", "boundaries",
              "suppressions", "test-integrity", "duplication", "dead-code", "toolchain", "tests", "command", "rules", *PLANNED}


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
    max_function_lines: int = 80
    max_parameters: int = 5
    # Per-glob exceptions, first match wins: (glob, {limit name: value}).
    overrides: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = ()

    def for_file(self, rel: str) -> Complexity:
        """The limits that apply to one file."""
        for glob, limits in self.overrides:
            if matches(rel, glob):
                return replace(self, overrides=(), **dict(limits))
        return self


@dataclass(frozen=True)
class SizeOverride:
    glob: str
    warn: int | None = None
    fail: int | None = None


@dataclass(frozen=True)
class Size:
    warn: int = 300
    fail: int = 500
    # Test files run long for a good reason: many small cases, each spelled out.
    test_fail: int = 1000
    overrides: tuple[SizeOverride, ...] = ()


@dataclass(frozen=True)
class Duplication:
    # A block counts as a copy when it has at least this many meaningful lines and tokens.
    min_lines: int = 10
    min_tokens: int = 100
    # Tests repeat their setup on purpose, so that each one reads on its own.
    include_tests: bool = False
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeadCode:
    severity: str = "fail"
    # Names used in ways the repo's text does not show (built from strings, called by a framework).
    ignore: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


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
    # Which kinds of suppression marker count as findings.
    suppressions: tuple[str, ...] = SUPPRESSION_KINDS
    test_integrity_severity: str = "fail"
    duplication: Duplication = Duplication()
    dead_code: DeadCode = DeadCode()
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
        complexity=_complexity(raw.get("complexity", {})),
        size=_size(raw.get("size", {})),
        boundaries=_boundaries(raw.get("boundaries", {})),
        suppressions=_suppressions(raw.get("suppressions", {})),
        test_integrity_severity=_severity(raw.get("test-integrity", {}), "test-integrity"),
        duplication=_duplication(raw.get("duplication", {})),
        dead_code=_dead_code(raw.get("dead-code", {})),
        toolchain=_toolchain(raw.get("toolchain", {})),
        tests=tests,
        commands=commands,
        rules_targets=_rules_targets(raw.get("rules", {})),
    )
    return _with_profiles(cfg, raw.get("profiles", {}))


def _fields(cls, table: dict, section: str):
    try:
        return cls(**table)
    except TypeError as e:
        raise ConfigError(f"[{section}]: {e}") from e


def _command(name: str, table: dict) -> Command:
    run = table.get("run", table.get("command")) if isinstance(table, dict) else None
    if not run:
        raise ConfigError(f"check '{name}' needs a command")
    return Command(name=name, run=str(run),
                   timeout=int(table.get("timeout_seconds", 300)))


_LIMITS = ("ccn_warn", "ccn_fail", "max_function_lines", "max_parameters")


def _complexity(table: dict) -> Complexity:
    table = dict(table)
    overrides = []
    for o in table.pop("override", []):
        limits = {k: v for k, v in o.items() if k != "glob"}
        unknown = set(limits) - set(_LIMITS)
        if "glob" not in o or not limits or unknown:
            raise ConfigError("[[complexity.override]] needs 'glob' and at least one of: " + ", ".join(_LIMITS))
        overrides.append((o["glob"], tuple(sorted(limits.items()))))
    return replace(_fields(Complexity, table, "complexity"), overrides=tuple(overrides))


def _size(table: dict) -> Size:
    overrides = tuple(
        SizeOverride(o["glob"], o.get("max_file_lines_warn"), o.get("max_file_lines_fail"))
        for o in table.get("override", [])
    )
    unknown = set(table) - {"max_file_lines_warn", "max_file_lines_fail", "max_test_file_lines_fail", "override"}
    if unknown:
        raise ConfigError(f"[size]: unknown key(s) {', '.join(sorted(unknown))}")
    fail = table.get("max_file_lines_fail", 500)
    # A repo that raises the general limit above the test default means it for tests too.
    test_fail = table.get("max_test_file_lines_fail", max(1000, fail))
    return Size(table.get("max_file_lines_warn", 300), fail, test_fail, overrides)


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


def _suppressions(table: dict) -> tuple[str, ...]:
    kinds = table.get("kinds", SUPPRESSION_KINDS)
    unknown = [*(set(table) - {"kinds"}), *(k for k in kinds if k not in SUPPRESSION_KINDS)]
    if unknown:
        raise ConfigError(f"[suppressions]: unknown key or kind {', '.join(sorted(unknown))}; "
                          f"kinds are {', '.join(SUPPRESSION_KINDS)}")
    return tuple(kinds)


def _duplication(table: dict) -> Duplication:
    cfg = _fields(Duplication, {**table, "exclude": tuple(table.get("exclude", ()))}, "duplication")
    if cfg.min_lines < 2:
        raise ConfigError("[duplication]: min_lines must be at least 2")
    return cfg


def _dead_code(table: dict) -> DeadCode:
    cfg = _fields(DeadCode, {**table, "ignore": tuple(table.get("ignore", ())),
                             "exclude": tuple(table.get("exclude", ()))}, "dead-code")
    if cfg.severity not in ("fail", "warn"):
        raise ConfigError('[dead-code]: severity must be "fail" or "warn"')
    return cfg


def _severity(table: dict, section: str) -> str:
    severity = table.get("severity", "fail")
    if set(table) - {"severity"} or severity not in ("fail", "warn"):
        raise ConfigError(f'[{section}]: the only key is severity, "fail" or "warn"')
    return severity


def _toolchain(table: dict) -> dict[str, str]:
    unknown = set(table) - set(TOOLCHAIN)
    if unknown:
        raise ConfigError(f"[toolchain]: unknown key(s) {', '.join(sorted(unknown))}")
    return dict(table)


def _rules_targets(table: dict) -> tuple[str, ...]:
    unknown = set(table) - {"targets"}
    if unknown:
        raise ConfigError(f"[rules]: unknown key(s) {', '.join(sorted(unknown))}")
    return tuple(table.get("targets", ()))


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
