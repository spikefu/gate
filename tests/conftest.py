"""Shared fixtures: throwaway repos built on disk, so checks run against real files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gate import runner
from gate.config import load


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True)

    def commit(self) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", "snapshot")

    def context(self, selected: list[str] | None = None):
        return runner.build_context(self.root, load(self.root / "gate.toml"), selected)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path)
    r.git("init", "-q")
    return r


def branchy(name: str, branches: int) -> str:
    """A Python function whose cyclomatic complexity is `branches` + 1."""
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(branches))
    return f"def {name}(x):\n{body}    return -1\n"
