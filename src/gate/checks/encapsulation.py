"""Two boundary rules that need no configuration: private names stay private, and no file knows too many others.

They live apart from the declared rules in `boundaries` because nothing here
is the repo's stated intent. Both rest on conventions wide enough to assume.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..files import is_test
from ..findings import WARN, Finding
from ..lang.import_graph import Edge

_IMPORTED = re.compile(r"\bimport\s+(.+)$")


def private_imports(graph: dict[str, list[Edge]]) -> list[Finding]:
    """Python imports of a `_private` module or name from outside the folder that owns it.

    Why: a leading underscore is how Python says "this may change without notice". Code in the
    same package may share such helpers; code elsewhere depending on one has coupled itself to
    an implementation detail. Tests are exempt, since they may test internals directly.
    """
    findings = {}
    for src, edges in graph.items():
        if not src.endswith(".py") or is_test(src):
            continue
        for edge in edges:
            for owner, name in [*_private_modules(edge), *_private_names(edge)]:
                if owner and not src.startswith(owner + "/"):
                    findings[(src, edge.line, name)] = Finding(
                        check="boundaries", rule="private", key=f"boundaries/private:{src}->{edge.target}::{name}",
                        path=src, line=edge.line, symbol=name,
                        message=f"imports the private `{name}` from {edge.target}, outside {owner}/ which owns it: "
                                "use what that package makes public, or make this public there",
                    )
    return list(findings.values())


def _is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _private_modules(edge: Edge) -> list[tuple[str, str]]:
    """(owning folder, name) when the imported file is, or sits inside, a `_private` module."""
    parts = PurePosixPath(edge.target).with_suffix("").parts
    for depth, part in enumerate(parts):
        if _is_private(part):
            return [("/".join(parts[:depth]), part)]
    return []


def _private_names(edge: Edge) -> list[tuple[str, str]]:
    """(owning folder, name) for each `_private` name a `from module import ...` takes from this file."""
    listed = _IMPORTED.search(edge.spec)
    if listed is None:
        return []
    target = PurePosixPath(edge.target)
    owner = target.parent.as_posix() if target.parent.as_posix() != "." else ""
    # `import x as _y` renames something public; only the name before `as` is what the module calls it.
    names = [item.split(" as ")[0].strip(" ()") for item in listed.group(1).split(",")]
    return [(owner, name) for name in names if _is_private(name)]


def fan_out(graph: dict[str, list[Edge]], limit: int) -> list[Finding]:
    """Files that import more of the repo's own modules than `limit`.

    Why: a file that has to know that many others is usually doing several jobs, and every one of
    those modules becomes harder to change. It warns and never fails: a composition root, where
    the parts of a program are wired together, is long on imports for a good reason.
    """
    findings = []
    for src, edges in sorted(graph.items()):
        count = len({edge.target for edge in edges})
        if limit and count > limit and not is_test(src):
            findings.append(Finding(
                check="boundaries", rule="fan-out", key=f"boundaries/fan-out:{src}", path=src, severity=WARN,
                value=count, limit=limit,
                message=f"imports {count} of this repo's modules (more than {limit}): unless it is where the program "
                        "is wired together, it probably has more than one job",
            ))
    return findings
