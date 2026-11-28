"""Quotes in a JSX file's element text are text, and must not hide the functions that follow."""

from gate.lang import jsx_text

PAGE = b'''function One() {
  const note = "it's code";
  return <p title="it's an attribute">the owner's note, "so called"</p>;
}

// it's a comment
function Two() {
  return <p>somebody's `second` {"it's an expression"}</p>;
}
'''


def test_only_the_quotes_in_element_text_are_blanked_and_nothing_moves():
    out = jsx_text.without_quotes(PAGE, "page.tsx")
    assert len(out) == len(PAGE)
    assert out.count(b"\n") == PAGE.count(b"\n")
    lines = out.decode().split("\n")
    assert lines[1] == '  const note = "it\'s code";'
    assert lines[2] == '  return <p title="it\'s an attribute">the owner s note,  so called </p>;'
    assert lines[5] == "// it's a comment"
    assert lines[7] == '  return <p>somebody s  second  {"it\'s an expression"}</p>;'


def test_the_same_holds_for_jsx_in_a_plain_javascript_file():
    out = jsx_text.without_quotes(b"const a = <p>it's</p>;\n", "page.jsx")
    assert out == b"const a = <p>it s</p>;\n"


def test_a_file_with_no_quotes_or_in_no_known_language_is_handed_back_as_it_was():
    plain = b"function f() { return <p>none</p>; }\n"
    assert jsx_text.without_quotes(plain, "page.tsx") is plain
    other = b"it's <p>not</p> a language gate reads as a tree\n"
    assert jsx_text.without_quotes(other, "notes.txt") is other
