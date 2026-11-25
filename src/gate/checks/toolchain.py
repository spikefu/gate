"""Runs the ecosystem's own tools: linter, type checker, formatter, security, dependency audit.

An explicit command in gate.toml always wins. Otherwise the first installed
tool for each language in play is used. A missing tool is reported as skipped,
never as a failure; `--strict` turns skips into failures where that matters (CI).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import proc
from ..context import Context
from ..findings import FAIL, WARN, Finding, Skipped

TIMEOUT = 600
FILES = "{files}"

LANG_BY_EXT = {
    ".py": "python", ".ts": "node", ".tsx": "node", ".js": "node", ".jsx": "node",
    ".mjs": "node", ".cjs": "node", ".go": "go", ".rs": "rust", ".rb": "ruby",
}


@dataclass(frozen=True)
class Tool:
    name: str
    argv: tuple[str, ...]
    needs: tuple[str, ...] = ()  # repo files that must exist for the tool to apply

    @property
    def per_file(self) -> bool:
        return FILES in self.argv


TOOLS: dict[str, dict[str, list[Tool]]] = {
    "lint": {
        "python": [Tool("ruff", ("ruff", "check", FILES)), Tool("flake8", ("flake8", FILES))],
        "node": [Tool("eslint", ("eslint", FILES)), Tool("biome", ("biome", "lint", FILES))],
        "go": [Tool("staticcheck", ("staticcheck", "./...")), Tool("go", ("go", "vet", "./..."))],
        "rust": [Tool("cargo", ("cargo", "clippy", "-q"), ("Cargo.toml",))],
        "ruby": [Tool("rubocop", ("rubocop", FILES))],
    },
    "typecheck": {
        "python": [Tool("pyright", ("pyright", FILES)), Tool("mypy", ("mypy", FILES))],
        "node": [Tool("tsc", ("tsc", "--noEmit"), ("tsconfig.json",))],
        "rust": [Tool("cargo", ("cargo", "check", "-q"), ("Cargo.toml",))],
    },
    "format": {
        "python": [Tool("ruff", ("ruff", "format", "--check", FILES)), Tool("black", ("black", "--check", FILES))],
        "node": [Tool("prettier", ("prettier", "--check", FILES))],
        "go": [Tool("gofmt", ("gofmt", "-l", FILES))],
    },
    "security": {
        "*": [Tool("gitleaks", ("gitleaks", "detect", "--no-banner", "--redact"))],
        "python": [Tool("bandit", ("bandit", "-q", "-r", FILES))],
    },
    "deps": {
        "python": [Tool("pip-audit", ("pip-audit",))],
        "node": [Tool("npm", ("npm", "audit", "--audit-level=high"), ("package.json",))],
        "go": [Tool("govulncheck", ("govulncheck", "./..."), ("go.mod",))],
    },
}


def run(ctx: Context, category: str) -> list[Finding]:
    explicit = ctx.config.toolchain.get(category)
    if explicit:
        return _judge(ctx, category, explicit, proc.run(explicit, ctx.root, TIMEOUT), project_wide=True)
    findings, ran = [], False
    for lang in _languages(ctx):
        tool = _first_installed(ctx, category, lang)
        if tool is None:
            continue
        ran = True
        files = [f for f in ctx.files if LANG_BY_EXT.get(Path(f).suffix) == lang]
        result = proc.run(_argv(ctx, tool, files), ctx.root, TIMEOUT)
        findings += _judge(ctx, category, tool.name, result, project_wide=not tool.per_file)
    if not ran:
        raise Skipped(f"no {category} tool installed for {', '.join(_languages(ctx)) or 'these files'}")
    return findings


def available(ctx: Context, category: str) -> bool:
    """Whether auto-detection would find a tool for any language in the repo."""
    return any(_first_installed(ctx, category, lang) for lang in _languages(ctx))


def _judge(ctx: Context, category: str, tool: str, result: tuple[int | None, str],
           project_wide: bool) -> list[Finding]:
    code, output = result
    if code == 0:
        return []
    if code is None:
        return [_finding(category, tool, FAIL, f"{tool} timed out after {TIMEOUT}s", output)]
    if project_wide and ctx.scoped:
        named = _named_files(output, ctx)
        # It failed somewhere else: not this change's fault, so report without blocking.
        if named and named.isdisjoint(ctx.files):
            return [_finding(category, tool, WARN, f"{tool} fails, but not in the files being checked", output)]
        # It named no file at all (a crash, a bad config). That cannot be pinned
        # on other files, and a broken tool checks nothing, so it blocks.
        if not named:
            return [_finding(category, tool, FAIL, f"{tool} failed (exit {code}) without naming a file", output)]
    return [_finding(category, tool, FAIL, f"{tool} failed (exit {code})", output)]


def _finding(category: str, tool: str, severity: str, message: str, output: str) -> Finding:
    return Finding(check=category, rule=tool, key=f"{category}:{tool}", path=".", severity=severity,
                   message=message, detail=proc.tail(output))


_PATH_TOKEN = re.compile(r"[\w@+./\\-]+")


def _named_files(output: str, ctx: Context) -> set[str]:
    """Repo files a tool's output refers to.

    Matched as whole path tokens, never substrings, so `data.py` does not
    count as a mention of `a.py`. A token may be absolute, or relative to a
    subdirectory (a workspace package's own tool prints `src/app.ts`), so it
    matches every repo file whose path ends with it. An ambiguous token then
    names several files, which errs toward blocking.
    """
    by_name: dict[str, list[str]] = {}
    for rel in ctx.repo_files:
        by_name.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
    root = ctx.root.as_posix() + "/"
    named: set[str] = set()
    for token in set(_PATH_TOKEN.findall(output)):
        token = token.replace("\\", "/").removeprefix(root).removeprefix("./").rstrip(".")
        for rel in by_name.get(token.rsplit("/", 1)[-1], ()):
            if rel == token or rel.endswith("/" + token):
                named.add(rel)
    return named


def _languages(ctx: Context) -> list[str]:
    seen = dict.fromkeys(LANG_BY_EXT.get(Path(f).suffix) for f in ctx.files)
    return [lang for lang in seen if lang]


def _first_installed(ctx: Context, category: str, lang: str) -> Tool | None:
    table = TOOLS.get(category, {})
    for tool in [*table.get(lang, []), *table.get("*", [])]:
        if _binary(ctx, tool.name) and all((ctx.root / n).exists() for n in tool.needs):
            return tool
    return None


def _binary(ctx: Context, name: str) -> str | None:
    local = ctx.root / "node_modules" / ".bin" / name
    return str(local) if local.exists() else shutil.which(name)


def _argv(ctx: Context, tool: Tool, files: list[str]) -> list[str]:
    argv = [_binary(ctx, tool.name) or tool.name]
    for token in tool.argv[1:]:
        # A whole-repo run passes "." rather than every path, to stay inside argv limits.
        argv += (files if ctx.scoped else ["."]) if token == FILES else [token]
    return argv
