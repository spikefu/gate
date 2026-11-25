"""What every check is handed: where the repo is, its rules, and which files are in play."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config

# Files the structural checks treat as code. Data, docs and lockfiles are not
# subject to size or complexity limits.
CODE_EXTS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs",
    ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php",
    ".scala", ".lua", ".sh",
}


@dataclass(frozen=True)
class Context:
    root: Path
    config: Config
    files: tuple[str, ...]       # the files this run judges
    repo_files: tuple[str, ...]  # every file in the repo, for cross-file analysis
    scoped: bool                 # True when `files` is a subset (changed or explicit paths)

    def code_files(self) -> list[str]:
        return [f for f in self.files if Path(f).suffix in CODE_EXTS]
