"""Decides which files a run looks at.

Git is the source of truth when available because it already knows what is
ignored; the directory walk is only a fallback for folders that are not repos.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

SKIP_DIRS = {
    ".git", ".gate", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", "target", ".pytest_cache", ".mypy_cache", "coverage", ".build",
}


class NotARepo(Exception):
    pass


def matches(path: str, pattern: str) -> bool:
    """Path rule used everywhere: a glob if it has wildcards, else a directory prefix."""
    if any(ch in pattern for ch in "*?["):
        return PurePosixPath(path).full_match(pattern)
    prefix = pattern.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


# Conventions across the common test runners. Deliberately narrow: a file is
# only treated as a test when its name or folder says so.
TEST_GLOBS = (
    "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/*_test.go", "**/*_spec.rb",
    "**/__tests__/**", "**/tests/**", "**/test/**", "**/e2e/**",
)


def is_test(path: str) -> bool:
    return any(matches(path, glob) for glob in TEST_GLOBS)


def all_files(root: Path, exclude: tuple[str, ...] = ()) -> list[str]:
    listed = _git(root, "ls-files", "-co", "--exclude-standard", "-z")
    paths = listed if listed is not None else _walk(root)
    return _keep(root, paths, exclude)


def changed_files(root: Path, base: str, exclude: tuple[str, ...] = ()) -> list[str]:
    diff = _git(root, "diff", "--name-only", "-z", base)
    if diff is None:
        raise NotARepo(f"--changed needs a git repository with ref '{base}': {root}")
    untracked = _git(root, "ls-files", "-o", "--exclude-standard", "-z") or []
    return _keep(root, [*diff, *untracked], exclude)


def deleted_files(root: Path, base: str, exclude: tuple[str, ...] = ()) -> list[str]:
    """Files that exist at `base` and are gone from the working tree."""
    gone = _git(root, "diff", "--name-only", "--diff-filter=D", "--no-renames", "-z", base) or []
    return sorted(rel for rel in gone if not any(matches(rel, pat) for pat in exclude))


def at_ref(root: Path, ref: str, rel: str) -> str | None:
    """A file's text as it was at `ref`, or None if it did not exist there."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "show", f"{ref}:./{rel}"], capture_output=True)
    except OSError:
        return None
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def explicit_files(root: Path, paths: list[str], exclude: tuple[str, ...] = ()) -> list[str]:
    """Explicit paths may be files or directories, relative to the caller's cwd."""
    out: list[str] = []
    everything = None
    for raw in paths:
        p = Path(raw).resolve()
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            everything = everything if everything is not None else all_files(root, exclude)
            out += [f for f in everything if rel in (".", "") or matches(f, rel)]
        else:
            out.append(rel)
    return _keep(root, out, exclude)


def _keep(root: Path, paths: list[str], exclude: tuple[str, ...]) -> list[str]:
    kept = set()
    for rel in paths:
        if any(part in SKIP_DIRS for part in rel.split("/")):
            continue
        if any(matches(rel, pat) for pat in exclude):
            continue
        if (root / rel).is_file():
            kept.add(rel)
    return sorted(kept)


def _git(root: Path, *args: str) -> list[str] | None:
    """Run a -z git listing from `root`, with paths relative to it. None if git can't answer."""
    try:
        proc = subprocess.run(["git", "-C", str(root), *args, "--", "."], capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    prefix = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-prefix"],
                            capture_output=True, text=True).stdout.strip()
    names = [n for n in proc.stdout.split("\0") if n]
    # `git diff` reports repo-root-relative paths even with -C into a subdirectory.
    if args[0] == "diff" and prefix:
        names = [n[len(prefix):] for n in names if n.startswith(prefix)]
    return names


def _walk(root: Path) -> list[str]:
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            out.append((Path(dirpath) / name).relative_to(root).as_posix())
    return out
