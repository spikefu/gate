"""Facts about each file and folder, for whoever is judging the design.

Why: whether a module has one job, or an interface exposes the right things,
is a judgement no check can make without being wrong too often to trust. But
the judgement goes better with numbers in hand than from reading alone: which
files everything depends on, which depend on everything, which folders are
reached into past their entry point. This reports those and decides nothing.

Test files are left out, both as subjects and as importers, so the picture is
of the product code's own structure.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .checks import complexity, dead_code, mutable_state
from .context import CODE_EXTS, Context
from .files import is_test
from .lang.import_graph import edges_for, externals_for


def collect(ctx: Context) -> dict:
    """Everything `gate metrics` reports, for the files in play."""
    # Dependents of a changed file are mostly files that did not change, so the graph is the whole repo's.
    code = [rel for rel in ctx.repo_files if Path(rel).suffix in CODE_EXTS and not is_test(rel)]
    imports, imported_by, deferred = _coupling(ctx, code)
    subjects = sorted(rel for rel in ctx.code_files() if not is_test(rel))
    packages = externals_for(ctx.root, ctx.repo_files, subjects)
    functions: dict[str, list] = {}
    for rel, fn in complexity.functions(replace(ctx, files=tuple(subjects))):
        functions.setdefault(rel, []).append(fn)
    files = [
        _file(ctx, rel, functions.get(rel, []), sorted({e.name for e in packages.get(rel, [])}),
              (len(imports.get(rel, [])), len(imported_by.get(rel, ()))))
        for rel in subjects
    ]
    return {
        "files": files,
        "folders": _folders(code, imports, {PurePosixPath(rel).parent.as_posix() for rel in subjects}),
        **_edges(subjects, imports, deferred),
    }


def _edges(subjects: list[str], imports: dict[str, list[str]], deferred: set[tuple[str, str]]) -> dict[str, list[list[str]]]:
    """Each import from a reported file to another product file, as [importer, imported]: for tools that draw the structure.

    Listed apart are the ones that are only ever a type, or made after the file has loaded: they cannot make a loop when the code runs.
    """
    edges = [[rel, target] for rel in subjects for target in imports.get(rel, [])]
    return {"edges": edges, "deferred_edges": [edge for edge in edges if (edge[0], edge[1]) in deferred]}


def _coupling(ctx: Context, code: list[str]) -> tuple[dict[str, list[str]], dict[str, set[str]], set[tuple[str, str]]]:
    """For product code only: what each file imports, what imports each file, and which of those imports are all deferred."""
    known = set(code)
    graph = edges_for(ctx.root, ctx.repo_files, code)
    imports = {src: sorted({e.target for e in edges if e.target in known}) for src, edges in graph.items()}
    deferred = _deferred(graph, imports)
    imported_by: dict[str, set[str]] = {}
    for src, targets in imports.items():
        for target in targets:
            imported_by.setdefault(target, set()).add(src)
    return imports, imported_by, deferred


def _deferred(graph: dict, imports: dict[str, list[str]]) -> set[tuple[str, str]]:
    """The imports between two files that are never made while the importing file loads."""
    eager = {(src, edge.target) for src, edges in graph.items() for edge in edges if not edge.lazy}
    return {(src, target) for src, targets in imports.items() for target in targets} - eager


def _file(ctx: Context, rel: str, functions: list, packages: list[str], coupling: tuple[int, int]) -> dict:
    try:
        text = (ctx.root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    is_module = rel.endswith(".py") or Path(rel).suffix in dead_code.JS_EXTS
    return {
        "path": rel,
        "lines": len(text.splitlines()),
        "functions": len(functions),
        "max_complexity": max((fn.cyclomatic_complexity for fn in functions), default=0),
        "longest_function": max((fn.nloc for fn in functions), default=0),
        "imports": coupling[0],
        "imported_by": coupling[1],
        # False where gate cannot read this language's imports: the zeros above then mean unknown, not none.
        "imports_read": is_module,
        "packages": packages,
        # What the file offers: exported declarations in JS/TS, top-level functions and classes in Python.
        "definitions": len(dead_code.definitions(rel, text)) if is_module else 0,
        "shared_state": len(mutable_state.variables(ctx.root / rel, rel)),
    }


def _is_entry(rel: str) -> bool:
    name = PurePosixPath(rel).name
    return name == "__init__.py" or name.rsplit(".", 1)[0] == "index"


def _folders(code: list[str], imports: dict[str, list[str]], wanted: set[str]) -> list[dict]:
    """Per folder: how much it depends on, how much depends on it, and whether outsiders use its entry or its insides."""
    folder_of = {rel: PurePosixPath(rel).parent.as_posix() for rel in code}
    out_to, in_from, reached = _between_folders(imports, folder_of)
    rows = []
    for folder in sorted(wanted):
        members = [rel for rel in code if folder_of[rel] == folder]
        depends, dependents = len(out_to.get(folder, ())), len(in_from.get(folder, ()))
        rows.append({
            "path": folder,
            "files": len(members),
            "depends_on_folders": depends,
            "depended_on_by_folders": dependents,
            # 0 is depended on and depends on nothing (stable); 1 depends on others and nothing needs it.
            "instability": round(depends / (depends + dependents), 2) if depends + dependents else None,
            "has_entry": any(_is_entry(rel) for rel in members),
            "imports_past_entry": reached[folder],
        })
    return rows


def _between_folders(imports: dict[str, list[str]], folder_of: dict[str, str]):
    """Imports that cross a folder line: who depends on whom, and how many skip the entry point."""
    out_to: dict[str, set[str]] = {}
    in_from: dict[str, set[str]] = {}
    reached: Counter[str] = Counter()
    for src, targets in imports.items():
        for target in targets:
            a, b = folder_of[src], folder_of[target]
            if a == b:
                continue
            out_to.setdefault(a, set()).add(b)
            in_from.setdefault(b, set()).add(a)
            if not _is_entry(target):
                reached[b] += 1
    return out_to, in_from, reached


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def _ranked(title: str, files: list[dict], key: str, top: int) -> list[str]:
    ranked = [f for f in sorted(files, key=lambda f: (-f[key], f["path"])) if f[key]][:top]
    return [title, *(f"  {f[key]:>5}  {f['path']}" for f in ranked)] if ranked else []


def to_text(report: dict, top: int) -> str:
    files, folders = report["files"], report["folders"]
    lines = [f"gate metrics: {len(files)} file(s), {len(folders)} folder(s); test files left out"]
    for title, key in (("most depended on", "imported_by"), ("depends on most", "imports"),
                       ("most complex function", "max_complexity"), ("longest", "lines"), ("shared state", "shared_state")):
        lines += _ranked(title, files, key, top)
    reached = [f for f in sorted(folders, key=lambda f: (-f["imports_past_entry"], f["path"])) if f["imports_past_entry"]][:top]
    if reached:
        lines += ["folders imported past their entry point (imports, has entry)",
                  *(f"  {f['imports_past_entry']:>5}  {f['path']}  {'entry' if f['has_entry'] else 'no entry'}" for f in reached)]
    lines.append("these are facts, not findings: what they mean for the design is for a reader to judge")
    return "\n".join(lines)
