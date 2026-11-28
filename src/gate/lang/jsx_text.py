"""Takes the quotes out of a JSX file's element text, for a reader that takes them for strings.

The text between tags is not code: `<p>the owner's note</p>` has no string in
it. A reader that scans for quotes without knowing where the tags are starts a
string at that apostrophe and ends it at the next one, however far away, and
every brace between the two is lost to it. Functions vanish and the one before
them appears to run on to where the string ended.

The file is read as a syntax tree, so only element text is touched. Each quote
becomes a space: the same length, so every line and column stays where it was.
"""

from __future__ import annotations

from . import faults

_QUOTES = frozenset(b"'\"`")


def without_quotes(source: bytes, path: str) -> bytes:
    """`source` with the quotes in its element text blanked; unchanged when it cannot be read as a tree."""
    lang = faults.language(path)
    if lang is None or not any(quote in source for quote in _QUOTES):
        return source
    out = bytearray(source)
    stack = [faults.tslp.get_parser(lang).parse(source).root_node]
    while stack:
        node = stack.pop()
        if node.type == "jsx_text":
            for at in range(node.start_byte, node.end_byte):
                if out[at] in _QUOTES:
                    out[at] = 0x20
        stack.extend(node.children)
    return bytes(out)
