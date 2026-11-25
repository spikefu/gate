"""Per-function limits: cyclomatic complexity, length, parameter count.

Measured with lizard. Length is its NLOC, so comments and
blank lines never count against a function.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import lizard

from ..context import Context
from ..findings import FAIL, WARN, Finding

EXTS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
    ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".scala", ".lua",
}


def run(ctx: Context) -> list[Finding]:
    return [finding for rel, fn in functions(ctx) for finding in _judge(ctx, rel, fn)]


def functions(ctx: Context) -> Iterator[tuple[str, Any]]:
    """Every function lizard can measure in the files in play, with its file."""
    for rel in ctx.files:
        if Path(rel).suffix not in EXTS:
            continue
        try:
            analysis = lizard.analyze_file(str(ctx.root / rel))
        except Exception:  # lizard cannot parse everything; syntax is another check's job
            continue
        for fn in analysis.function_list:
            yield rel, fn


def _judge(ctx: Context, rel: str, fn) -> list[Finding]:
    cfg = ctx.config.complexity
    ccn, lines, params = fn.cyclomatic_complexity, fn.nloc, param_count(fn)
    out = []
    if ccn > cfg.ccn_fail:
        out.append(_finding(rel, fn, ("ccn", ccn, cfg.ccn_fail), FAIL, "refactor"))
    elif ccn > cfg.ccn_warn:
        out.append(_finding(rel, fn, ("ccn", ccn, cfg.ccn_warn), WARN, ""))
    if lines > cfg.max_function_lines:
        out.append(_finding(rel, fn, ("function-lines", lines, cfg.max_function_lines), FAIL, "split it up"))
    if params > cfg.max_parameters:
        out.append(_finding(rel, fn, ("parameters", params, cfg.max_parameters), FAIL, "group into an object"))
    return out


_LABELS = {"ccn": "CCN {v} > {l}", "function-lines": "{v} lines > {l}", "parameters": "{v} parameters > {l}"}


def _finding(rel: str, fn, measure: tuple[str, int, int], severity: str, advice: str) -> Finding:
    rule, value, limit = measure
    message = _LABELS[rule].format(v=value, l=limit) + (f": {advice}" if advice else "")
    return Finding(
        check="complexity", rule=rule, key=f"complexity/{rule}:{rel}::{fn.name}", path=rel,
        line=fn.start_line, symbol=fn.name, value=value, limit=limit, severity=severity, message=message,
    )


def param_count(fn) -> int:
    """Exclude the method receiver, which is not a real argument."""
    return sum(1 for p in fn.parameters if p not in ("self", "cls"))
