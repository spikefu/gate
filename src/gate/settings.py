"""The settings each check takes from gate.toml, and how each section is read and validated.

Kept apart from the loader so that adding a check means adding its settings
here, without the file that assembles the whole config growing with it.

Validation is strict on purpose: a misspelled key that is silently ignored
would leave a rule unenforced while everyone believes it is on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fnmatch import fnmatchcase

from .files import matches

SUPPRESSION_KINDS = ("lint", "type", "test", "coverage")
TOOLCHAIN = ("lint", "typecheck", "format", "security", "deps")


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
class ChangeSize:
    # Added lines of non-test code in one change before it is too much to review well.
    max_lines: int = 400
    severity: str = "warn"


@dataclass(frozen=True)
class Coverage:
    # Reports the repo's own test run writes: lcov or Cobertura XML.
    reports: tuple[str, ...] = ()
    # Of the lines a change adds, the share the tests must run.
    min_changed_percent: int = 80
    # Of all measured lines; 0 leaves the whole-repo figure unjudged.
    min_percent: int = 0
    severity: str = "fail"


@dataclass(frozen=True)
class OnlyRule:
    target: str
    allowed_from: tuple[str, ...]
    why: str = ""
    # Importers the rule governs; empty means the whole repo. Lets a rule cover
    # product code while leaving tests and scripts free to reach in.
    applies_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleRule:
    """Folders that are modules: code outside one may import only its entry files."""
    path: str  # a folder, or a pattern such as `src/modules/*` naming one module per match
    # Files outsiders may import, relative to the module. Empty means the language's
    # own entry point: `index.*` or `__init__.py`.
    entry: tuple[str, ...] = ()
    why: str = ""

    def inside(self, rel: str) -> tuple[str, str] | None:
        """If `rel` is a file within one of these modules: (the module's folder, the file's path inside it)."""
        parts = rel.split("/")
        depth = len(self.path.split("/"))
        root = "/".join(parts[:depth])
        if len(parts) <= depth or not matches(root, self.path):
            return None
        return root, "/".join(parts[depth:])

    def is_entry(self, inner: str) -> bool:
        if self.entry:
            return any(fnmatchcase(inner, pattern) for pattern in self.entry)
        return inner == "__init__.py" or ("/" not in inner and inner.rsplit(".", 1)[0] == "index")


@dataclass(frozen=True)
class Boundaries:
    forbid: tuple[tuple[str, str], ...] = ()
    only: tuple[OnlyRule, ...] = ()
    modules: tuple[ModuleRule, ...] = ()
    # Modules importing each other in a loop are findings.
    cycles: bool = True


@dataclass(frozen=True)
class Command:
    name: str
    run: str
    timeout: int = 300


def read_fields(cls, table: dict, section: str):
    try:
        return cls(**table)
    except TypeError as e:
        raise ConfigError(f"[{section}]: {e}") from e


def read_command(name: str, table: dict) -> Command:
    run = table.get("run", table.get("command")) if isinstance(table, dict) else None
    if not run:
        raise ConfigError(f"check '{name}' needs a command")
    return Command(name=name, run=str(run),
                   timeout=int(table.get("timeout_seconds", 300)))


_LIMITS = ("ccn_warn", "ccn_fail", "max_function_lines", "max_parameters")


def read_complexity(table: dict) -> Complexity:
    table = dict(table)
    overrides = []
    for o in table.pop("override", []):
        limits = {k: v for k, v in o.items() if k != "glob"}
        unknown = set(limits) - set(_LIMITS)
        if "glob" not in o or not limits or unknown:
            raise ConfigError("[[complexity.override]] needs 'glob' and at least one of: " + ", ".join(_LIMITS))
        overrides.append((o["glob"], tuple(sorted(limits.items()))))
    return replace(read_fields(Complexity, table, "complexity"), overrides=tuple(overrides))


def read_size(table: dict) -> Size:
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


def _forbid_rule(rule: str) -> tuple[str, str]:
    left, sep, right = (part.strip() for part in rule.partition("!->"))
    if not sep or not left or not right:
        raise ConfigError(f"[boundaries] forbid rule must look like 'a !-> b': {rule!r}")
    if left.startswith("pkg:"):
        raise ConfigError(f"[boundaries] forbid: a package cannot be the importer, only what is imported: {rule!r}")
    return left, right


def read_boundaries(table: dict) -> Boundaries:
    forbid = [_forbid_rule(rule) for rule in table.get("forbid", [])]
    only = []
    for o in table.get("only", []):
        if "target" not in o or not o.get("allowed_from"):
            raise ConfigError("[[boundaries.only]] needs 'target' and 'allowed_from'")
        only.append(OnlyRule(o["target"], tuple(o["allowed_from"]), o.get("why", ""),
                             tuple(o.get("applies_to", ()))))
    unknown = set(table) - {"forbid", "only", "module", "cycles"}
    if unknown or not isinstance(table.get("cycles", True), bool):
        raise ConfigError("[boundaries]: keys are forbid, only, module and cycles (true or false); "
                          f"got {', '.join(sorted(table))}")
    modules = tuple(_module_rule(m) for m in table.get("module", []))
    return Boundaries(tuple(forbid), tuple(only), modules, table.get("cycles", True))


def _module_rule(table: dict) -> ModuleRule:
    path = str(table.get("path", "")).strip("/")
    if not path or "**" in path or set(table) - {"path", "entry", "why"}:
        raise ConfigError("[[boundaries.module]] needs 'path' (a folder, or a pattern like 'src/modules/*' without '**'), "
                          "and takes only 'entry' and 'why' besides")
    return ModuleRule(path, tuple(table.get("entry", ())), table.get("why", ""))


def read_suppressions(table: dict) -> tuple[str, ...]:
    kinds = table.get("kinds", SUPPRESSION_KINDS)
    unknown = [*(set(table) - {"kinds"}), *(k for k in kinds if k not in SUPPRESSION_KINDS)]
    if unknown:
        raise ConfigError(f"[suppressions]: unknown key or kind {', '.join(sorted(unknown))}; "
                          f"kinds are {', '.join(SUPPRESSION_KINDS)}")
    return tuple(kinds)


def read_duplication(table: dict) -> Duplication:
    cfg = read_fields(Duplication, {**table, "exclude": tuple(table.get("exclude", ()))}, "duplication")
    if cfg.min_lines < 2:
        raise ConfigError("[duplication]: min_lines must be at least 2")
    return cfg


def read_dead_code(table: dict) -> DeadCode:
    cfg = read_fields(DeadCode, {**table, "ignore": tuple(table.get("ignore", ())),
                             "exclude": tuple(table.get("exclude", ()))}, "dead-code")
    if cfg.severity not in ("fail", "warn"):
        raise ConfigError('[dead-code]: severity must be "fail" or "warn"')
    return cfg


def read_change_size(table: dict) -> ChangeSize:
    cfg = read_fields(ChangeSize, table, "change-size")
    if cfg.severity not in ("fail", "warn") or cfg.max_lines < 1:
        raise ConfigError('[change-size]: severity must be "fail" or "warn", and max_lines at least 1')
    return cfg


def read_coverage(table: dict) -> Coverage:
    table = dict(table)
    report = table.pop("report", table.pop("reports", ()))
    cfg = read_fields(Coverage, {**table, "reports": (report,) if isinstance(report, str) else tuple(report)}, "coverage")
    in_range = 0 <= cfg.min_changed_percent <= 100 and 0 <= cfg.min_percent <= 100
    if cfg.severity not in ("fail", "warn") or not in_range:
        raise ConfigError('[coverage]: severity must be "fail" or "warn", and percentages between 0 and 100')
    return cfg


def read_severity(table: dict, section: str) -> str:
    severity = table.get("severity", "fail")
    if set(table) - {"severity"} or severity not in ("fail", "warn"):
        raise ConfigError(f'[{section}]: the only key is severity, "fail" or "warn"')
    return severity


def read_toolchain(table: dict) -> dict[str, str]:
    unknown = set(table) - set(TOOLCHAIN)
    if unknown:
        raise ConfigError(f"[toolchain]: unknown key(s) {', '.join(sorted(unknown))}")
    return dict(table)


def read_rules_targets(table: dict) -> tuple[str, ...]:
    unknown = set(table) - {"targets"}
    if unknown:
        raise ConfigError(f"[rules]: unknown key(s) {', '.join(sorted(unknown))}")
    return tuple(table.get("targets", ()))
