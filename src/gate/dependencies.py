"""Reads the packages a repo declares, and keeps the list a person has approved.

Why: a dependency is code from outside the repo that runs with the repo's
permissions, and adding one takes a single line. An agent will add one to
save writing twenty lines, and sometimes names a package that does not exist,
which someone else can then register. So a new name is a person's decision.

Only names are tracked, per ecosystem, and only direct dependencies: the
manifests people edit, not lockfiles. A version change is not a new
dependency. Approving a package once approves it for every manifest in the
repo, since the question being answered is whether the package is trusted.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

FILE = ".gate/dependencies.json"

Approved = dict[str, list[str]]  # ecosystem -> sorted names


@dataclass(frozen=True)
class Dependency:
    ecosystem: str
    name: str
    path: str
    line: int | None


def ecosystem_of(rel: str) -> str | None:
    """Which package ecosystem a file declares dependencies for, if any."""
    path = PurePosixPath(rel)
    name = path.name
    if name in _BY_NAME:
        return _BY_NAME[name]
    if name.endswith(".txt") and (name.startswith("requirements") or path.parent.name == "requirements"):
        return "pypi"
    return None


def declared(root: Path, files: tuple[str, ...] | list[str]) -> list[Dependency]:
    """Every direct dependency declared by the manifests among `files`."""
    out = []
    for rel in sorted(files):
        ecosystem = ecosystem_of(rel)
        if ecosystem is None:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parser = _requirements if rel.endswith(".txt") else _PARSERS[PurePosixPath(rel).name]
        lines = text.splitlines()
        out += [Dependency(ecosystem, name, rel, _line_of(lines, name)) for name in sorted(set(parser(text)))]
    return out


def current(deps: list[Dependency]) -> Approved:
    """What the approved list would be if everything declared today were accepted."""
    out: dict[str, set[str]] = {}
    for dep in deps:
        out.setdefault(dep.ecosystem, set()).add(dep.name)
    return {ecosystem: sorted(names) for ecosystem, names in sorted(out.items())}


def load(root: Path) -> Approved | None:
    """The approved list, or None when nobody has approved anything yet."""
    path = root / FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("approved", {})


def save(root: Path, approved: Approved) -> None:
    path = root / FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"version": 1, "approved": approved}, indent=2) + "\n", encoding="utf-8")


def unapproved(deps: list[Dependency], approved: Approved) -> list[Dependency]:
    return [dep for dep in deps if dep.name not in approved.get(dep.ecosystem, ())]


def count(approved: Approved) -> int:
    return sum(len(names) for names in approved.values())


# ----------------------------------------------------------------- parsers

def _package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    names: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        table = data.get(section) if isinstance(data, dict) else None
        names += list(table) if isinstance(table, dict) else []
    return names


_PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _python_name(requirement: str) -> str | None:
    match = _PEP508_NAME.match(requirement)
    # Python treats case and -, _ and . as the same in a package name.
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else None


def _pyproject(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    project = data.get("project", {})
    requirements = [*project.get("dependencies", []), *data.get("build-system", {}).get("requires", [])]
    for group in [*project.get("optional-dependencies", {}).values(), *data.get("dependency-groups", {}).values()]:
        requirements += [r for r in group if isinstance(r, str)]
    names = [_python_name(r) for r in requirements if isinstance(r, str)]
    poetry = data.get("tool", {}).get("poetry", {})
    tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {}),
              *(g.get("dependencies", {}) for g in poetry.get("group", {}).values())]
    names += [_python_name(key) for table in tables for key in table if key.lower() != "python"]
    return [name for name in names if name]


def _requirements(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        # Comments and options such as -r or -e do not start like a package name, so yield none.
        name = _python_name(line.split(" #")[0])
        if name:
            names.append(name)
    return names


def _go_mod(text: str) -> list[str]:
    names, in_block = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_block = True
        elif in_block and stripped == ")":
            in_block = False
        elif "// indirect" in stripped:
            continue  # pulled in by something else, not chosen here
        elif in_block and stripped and not stripped.startswith("//"):
            names.append(stripped.split()[0])
        elif stripped.startswith("require ") and len(stripped.split()) >= 3:
            names.append(stripped.split()[1])
    return names


def _cargo(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    sections = ("dependencies", "dev-dependencies", "build-dependencies")
    tables = [data, data.get("workspace", {}), *data.get("target", {}).values()]
    return [name for table in tables if isinstance(table, dict) for section in sections for name in table.get(section, {})]


_GEM = re.compile(r"""^\s*gem\s+['"]([^'"]+)['"]""", re.MULTILINE)


def _gemfile(text: str) -> list[str]:
    return _GEM.findall(text)


_PARSERS = {"package.json": _package_json, "pyproject.toml": _pyproject, "go.mod": _go_mod,
            "Cargo.toml": _cargo, "Gemfile": _gemfile}
_BY_NAME = {"package.json": "npm", "pyproject.toml": "pypi", "go.mod": "go", "Cargo.toml": "cargo", "Gemfile": "rubygems"}


def _line_of(lines: list[str], name: str) -> int | None:
    """Where the manifest mentions the package, for a finding to point at."""
    spelled = re.sub(r"-", "[-_.]", re.escape(name).replace(r"\-", "-"))
    pattern = re.compile(rf"(?<![\w.-]){spelled}(?![\w.-])", re.IGNORECASE)
    return next((number for number, line in enumerate(lines, 1) if pattern.search(line)), None)
