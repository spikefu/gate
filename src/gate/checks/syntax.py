"""Reports files that do not parse, using tree-sitter.

Tree-sitter grammars can lag the language they parse: the TypeScript grammar
has rejected valid code that used `using` as a variable name.
A gate that blocks on a false positive teaches people to ignore it, so a parse
error only fails when a native parser agrees (Python, TOML) or the repo has
opted the language in with `[syntax] fail_on`. Otherwise it is a warning.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ..context import Context
from ..findings import FAIL, WARN, Finding, Skipped

try:
    import tree_sitter_language_pack as tslp
except ImportError:  # the check degrades to skipped rather than breaking the run
    tslp = None

# Path detection is eager (requirements.txt is "vimdoc"), so only real
# programming languages are judged.
LANGS = {
    "python", "javascript", "typescript", "tsx", "jsx", "go", "rust", "java", "c", "cpp",
    "csharp", "ruby", "php", "swift", "kotlin", "scala", "lua", "bash", "toml",
}
MAX_BYTES = 1_000_000
MAX_ERRORS = 20


def run(ctx: Context) -> list[Finding]:
    if tslp is None:
        raise Skipped("tree-sitter-language-pack is not installed")
    findings = []
    for rel in ctx.files:
        lang, errors = _errors(ctx.root / rel)
        if not errors:
            continue
        confirmed = _native_verdict(lang, ctx.root / rel)
        if confirmed is False:
            continue
        blocking = confirmed or lang in ctx.config.syntax.fail_on
        findings.append(_finding(rel, lang, errors, blocking))
    return findings


def _finding(rel: str, lang: str, errors: list[tuple[int, str]], blocking: bool) -> Finding:
    line, first = errors[0]
    first = first.splitlines()[0][:120]
    more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
    note = "" if blocking else f"; unconfirmed, the {lang} grammar may lag the language"
    return Finding(
        check="syntax", rule="parse", key=f"syntax:{rel}", path=rel, line=line,
        severity=FAIL if blocking else WARN, message=f"parse error: {first}{more}{note}",
        detail="\n".join(f"line {n}: {m.splitlines()[0][:120]}" for n, m in errors),
    )


def _errors(path: Path) -> tuple[str, list[tuple[int, str]]]:
    try:
        lang = tslp.detect_language_from_path(str(path))
        if lang not in LANGS or path.stat().st_size > MAX_BYTES:
            return "", []
        source = path.read_text(encoding="utf-8")
        result = tslp.process(source, tslp.ProcessConfig(language=lang, diagnostics=True))
    except Exception:  # an unreadable or undetectable file is not a syntax error
        return "", []
    errors = [(d.span.start_line + 1, d.message) for d in result.diagnostics
              if "error" in str(d.severity).lower()]
    return lang, errors[:MAX_ERRORS]


def _native_verdict(lang: str, path: Path) -> bool | None:
    """True: a real parser also rejects the file. False: it accepts it. None: no second opinion."""
    try:
        source = path.read_text(encoding="utf-8")
        if lang == "python":
            compile(source, str(path), "exec", dont_inherit=True)
        elif lang == "toml":
            tomllib.loads(source)
        else:
            return None
    except (SyntaxError, ValueError):
        return True
    return False
