"""Resolves JS/TS import specifiers to files inside the repo.

Why this exists: a graph that only follows relative paths cannot enforce a
rule like "only src/modules may import @/db", because it never sees `@/db`.
This adds the two non-relative forms that point back into the repo: tsconfig
`paths` aliases and workspace package names.

Resolution is against the known file list, never the disk, so results do not
depend on build output or installed packages.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
# TS ESM source imports "./x.js" while the file on disk is "./x.ts".
_JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


class Resolver:
    def __init__(self, root: Path, repo_files: tuple[str, ...]):
        self.root = root
        self.files = set(repo_files)
        self.packages = self._workspace_packages()
        self._alias_cache: dict[str, list[tuple[str, list[str]]]] = {}

    def resolve(self, importer: str, spec: str) -> str | None:
        """Repo-relative file (or package directory) the spec points at, or None if external."""
        if spec.startswith("."):
            return self._file(posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)))
        for candidate in self._alias_targets(importer, spec):
            hit = self._file(candidate)
            if hit:
                return hit
        return self._package(spec)

    def _file(self, base: str) -> str | None:
        stem, ext = posixpath.splitext(base)
        swapped = [stem + e for e in _JS_TO_TS.get(ext, ())]
        candidates = [base, *swapped, *(base + e for e in EXTS),
                      *(posixpath.join(base, "index" + e) for e in EXTS)]
        return next((c for c in candidates if c in self.files), None)

    # ------------------------------------------------------------- packages

    def _workspace_packages(self) -> dict[str, str]:
        """Package name -> directory, for every package.json in the repo."""
        out = {}
        for rel in self.files:
            if posixpath.basename(rel) != "package.json":
                continue
            name = _read_json(self.root / rel).get("name")
            if name and posixpath.dirname(rel):
                out[name] = posixpath.dirname(rel)
        return out

    def _package(self, spec: str) -> str | None:
        parts = spec.split("/")
        name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
        directory = self.packages.get(name)
        if directory is None:
            return None
        sub = spec[len(name):].lstrip("/")
        if sub:
            return self._file(posixpath.join(directory, sub)) or self._file(posixpath.join(directory, "src", sub))
        # A bare package import still crosses the boundary even when the entry
        # file can't be pinned down, so fall back to the directory itself.
        return (self._file(posixpath.join(directory, "src", "index"))
                or self._file(posixpath.join(directory, "index")) or directory)

    # -------------------------------------------------------------- aliases

    def _alias_targets(self, importer: str, spec: str) -> list[str]:
        out = []
        for pattern, targets in self._aliases_for(posixpath.dirname(importer)):
            prefix, star, suffix = pattern.partition("*")
            if star and spec.startswith(prefix) and spec.endswith(suffix):
                middle = spec[len(prefix):len(spec) - len(suffix) or None]
                out += [t.replace("*", middle) for t in targets]
            elif not star and spec == pattern:
                out += targets
        return out

    def _aliases_for(self, directory: str) -> list[tuple[str, list[str]]]:
        """Aliases from the nearest tsconfig/jsconfig at or above `directory`."""
        if directory in self._alias_cache:
            return self._alias_cache[directory]
        found: list[tuple[str, list[str]]] = []
        for name in ("tsconfig.json", "jsconfig.json"):
            rel = posixpath.join(directory, name) if directory else name
            if rel in self.files:
                found = self._read_aliases(rel, depth=0)
                break
        else:
            if directory:
                found = self._aliases_for(posixpath.dirname(directory))
        self._alias_cache[directory] = found
        return found

    def _read_aliases(self, config_rel: str, depth: int) -> list[tuple[str, list[str]]]:
        data = _read_json(self.root / config_rel)
        here = posixpath.dirname(config_rel)
        options = data.get("compilerOptions", {})
        paths = options.get("paths")
        if paths is None:
            parent = data.get("extends")
            if isinstance(parent, str) and parent.startswith(".") and depth < 5:
                parent_rel = posixpath.normpath(posixpath.join(here, parent))
                parent_rel += "" if parent_rel.endswith(".json") else ".json"
                return self._read_aliases(parent_rel, depth + 1)
            return []
        base = posixpath.normpath(posixpath.join(here, options.get("baseUrl", ".")))
        return [(pattern, [posixpath.normpath(posixpath.join(base, t)) for t in targets])
                for pattern, targets in paths.items()]


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def strip_jsonc(text: str) -> str:
    """Remove comments and trailing commas from JSONC, leaving strings alone.

    String-aware because an alias like "@/*" contains a comment opener.
    """
    out, i, n, in_string = [], 0, len(text), False
    while i < n:
        ch, nxt = text[i], text[i + 1:i + 2]
        if in_string:
            out.append(ch)
            if ch == "\\":
                out.append(nxt)
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "/" and nxt == "/":
            i = text.find("\n", i)
            i = n if i == -1 else i - 1
        elif ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 1
        elif ch == "," and text[i + 1:].lstrip()[:1] in ("}", "]"):
            pass
        else:
            out.append(ch)
        i += 1
    return "".join(out)
