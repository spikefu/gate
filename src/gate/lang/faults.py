"""Plants one small fault at a time in a source file: the kind of slip a test should notice.

Why these and no others: each is a change a careless edit really makes, each
keeps the file parsing, and each is small enough to say in a few words. A
comparison nudged across its boundary, `and` taken for `or`, a truth value
flipped, a negation dropped, a plus for a minus, a condition that no longer
decides anything.

The file is read as a syntax tree, so a fault lands only on code. An operator
inside a string, a comment, a type or a tag is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

tslp: Any
try:
    import tree_sitter_language_pack as _pack
    tslp = _pack
except ImportError:  # the check that uses this reports itself as skipped
    tslp = None

# The languages whose trees this knows how to read.
LANGS = {"python", "javascript", "typescript", "tsx"}

_NUDGE = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==", "===": "!==", "!==": "===",
          "and": "or", "or": "and", "&&": "||", "||": "&&", "+": "-", "-": "+"}
# The kinds of node an operator must sit directly inside to be an operator at all. `<` in a type, or
# `-` before a number, sits inside something else.
_BINARY = {"comparison_operator", "boolean_operator", "binary_operator", "binary_expression"}
_TRUTH = {"python": ("True", "False"), "javascript": ("true", "false"), "typescript": ("true", "false"),
          "tsx": ("true", "false")}
_NEGATION = {"not_operator": "not", "unary_expression": "!"}
_DECIDES = {"if_statement", "elif_clause", "while_statement"}


@dataclass(frozen=True)
class Fault:
    line: int          # counted from 1
    start: int         # byte offsets of what is replaced
    end: int
    put: str           # what goes there
    says: str          # the fault in words, as in "changing `<` to `<=`"


def language(path: str) -> str | None:
    """The language of a file, when it is one faults can be planted in."""
    if tslp is None:
        return None
    try:
        lang = tslp.detect_language_from_path(path)
    except Exception:
        return None
    return lang if lang in LANGS else None


def plant(source: bytes, lang: str, lines: set[int] | None = None) -> list[Fault]:
    """Every fault that could be planted in `source`, in the order they appear. `lines` keeps only those on these lines."""
    if tslp is None or lang not in LANGS:
        return []
    tree = tslp.get_parser(lang).parse(source)
    found: list[Fault] = []
    _walk(tree.root_node, source, lang, found)
    kept = [f for f in found if lines is None or f.line in lines]
    return sorted(set(kept), key=lambda f: (f.start, f.end, f.put))


def planted(source: bytes, fault: Fault) -> bytes:
    """`source` with one fault in it."""
    return source[:fault.start] + fault.put.encode() + source[fault.end:]


def _walk(node, source: bytes, lang: str, out: list[Fault]) -> None:
    out.extend(_here(node, source, lang))
    for child in node.children:
        _walk(child, source, lang, out)


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _fault(node, put: str, says: str) -> Fault:
    return Fault(line=node.start_point[0] + 1, start=node.start_byte, end=node.end_byte, put=put, says=says)


def _here(node, source: bytes, lang: str) -> list[Fault]:
    """The faults that can be planted at this one node."""
    parent = node.parent.type if node.parent is not None else ""
    if node.type in _NUDGE and node.child_count == 0 and parent in _BINARY:
        return [_fault(node, _NUDGE[node.type], f"changing `{node.type}` to `{_NUDGE[node.type]}`")]
    if node.type in ("true", "false"):
        return _flipped(node, lang, parent)
    if node.type in _NEGATION:
        return _dropped(node, source)
    if node.type in _DECIDES:
        return _forced(node, source, lang)
    return []


def _flipped(node, lang: str, parent: str) -> list[Fault]:
    """A truth value turned into its opposite, where it is a value the code decides and not a type or a setting."""
    if parent == "literal_type" or _a_setting(node):
        return []
    yes, no = _TRUTH[lang]
    was, now = (yes, no) if node.type == "true" else (no, yes)
    return [_fault(node, now, f"changing `{was}` to `{now}`")]


def _a_setting(node) -> bool:
    """Is this truth value a setting handed to something else, not a decision this code makes?

    `frozen=True` on a decorator, `exist_ok=True` in a call. Flipping one mostly changes nothing a test could
    see, and a check that cries wolf is a check people stop reading.
    """
    if node.parent is not None and node.parent.type == "keyword_argument":
        return True
    above = node.parent
    while above is not None:
        if above.type == "decorator":
            return True
        above = above.parent
    return False


def _dropped(node, source: bytes) -> list[Fault]:
    """A negation with the negating taken away."""
    word = _NEGATION[node.type]
    operator = node.children[0] if node.children else None
    inner = node.child_by_field_name("argument")
    if operator is None or inner is None or operator.type != word:
        return []
    return [_fault(node, _text(inner, source), f"dropping `{word}`")]


def _forced(node, source: bytes, lang: str) -> list[Fault]:
    """A condition that no longer decides: always true, and always false."""
    condition = node.child_by_field_name("condition")
    if condition is None:
        return []
    yes, no = _TRUTH[lang]
    # JavaScript keeps its condition in brackets, which must stay.
    wrap = "({})" if condition.type == "parenthesized_expression" else "{}"
    was = _text(condition, source)
    if was.strip("() ") in (yes, no):
        return []  # already a constant: flipping it is the truth-value fault, planted elsewhere
    return [Fault(line=condition.start_point[0] + 1, start=condition.start_byte, end=condition.end_byte,
                  put=wrap.format(value), says=f"making the condition always {word}")
            for value, word in ((yes, "true"), (no, "false"))]
