"""Planting faults: each lands on code, says what it did, and leaves a file that still parses."""

from gate.lang import faults

PY = b'''def allowed(age, member):
    # age < 18 is refused
    note = "a < b and not c"
    if age >= 18 and not member:
        return True
    return age + 1 == 3
'''

TS = b'''function allowed(age: number, member: boolean): Map<string, true> {
  // age < 18 is refused
  const note = "a < b && !c";
  if (age >= 18 && !member) {
    return age - 1 !== 3 || false;
  }
  return <T,>(x: T) => x;
}
'''


def said(source: bytes, lang: str, lines=None) -> list[tuple[int, str]]:
    return [(f.line, f.says) for f in faults.plant(source, lang, lines)]


def test_python_faults_are_the_small_slips_and_each_says_what_it_did():
    assert said(PY, "python") == [
        (4, "making the condition always false"),
        (4, "making the condition always true"),
        (4, "changing `>=` to `>`"),
        (4, "changing `and` to `or`"),
        (4, "dropping `not`"),
        (5, "changing `True` to `False`"),
        (6, "changing `+` to `-`"),
        (6, "changing `==` to `!=`"),
    ]


def test_nothing_is_planted_in_a_comment_or_a_string():
    assert all(line not in (2, 3) for line, _ in said(PY, "python"))
    assert all(line not in (2, 3) for line, _ in said(TS, "typescript"))


def test_typescript_faults_leave_types_and_generics_alone():
    found = said(TS, "typescript")
    assert (4, "changing `>=` to `>`") in found and (4, "changing `&&` to `||`") in found
    assert (4, "dropping `!`") in found and (5, "changing `-` to `+`") in found
    assert (5, "changing `!==` to `===`") in found and (5, "changing `||` to `&&`") in found
    assert (5, "changing `false` to `true`") in found
    assert all(line not in (1, 7) for line, _ in found), "`<` in a type and `true` as a type are not code that runs"


def test_only_the_lines_asked_for_get_a_fault():
    assert said(PY, "python", {6}) == [(6, "changing `+` to `-`"), (6, "changing `==` to `!=`")]
    assert said(PY, "python", set()) == []


def test_a_planted_fault_changes_exactly_what_it_says_and_the_file_still_parses():
    for fault in faults.plant(PY, "python"):
        changed = faults.planted(PY, fault)
        assert changed != PY and len(changed.splitlines()) == len(PY.splitlines())
        compile(changed, "allowed.py", "exec")
    nudge = next(f for f in faults.plant(PY, "python") if f.says == "changing `>=` to `>`")
    assert faults.planted(PY, nudge).splitlines()[3] == b"    if age > 18 and not member:"
    forced = next(f for f in faults.plant(PY, "python") if f.says == "making the condition always true")
    assert faults.planted(PY, forced).splitlines()[3] == b"    if True:"
    dropped = next(f for f in faults.plant(PY, "python") if f.says == "dropping `not`")
    assert faults.planted(PY, dropped).splitlines()[3] == b"    if age >= 18 and member:"


def test_a_javascript_condition_keeps_its_brackets_and_a_negation_loses_only_its_bang():
    forced = next(f for f in faults.plant(TS, "typescript") if f.says == "making the condition always false")
    assert faults.planted(TS, forced).splitlines()[3] == b"  if (false) {"
    dropped = next(f for f in faults.plant(TS, "typescript") if f.says == "dropping `!`")
    assert faults.planted(TS, dropped).splitlines()[3] == b"  if (age >= 18 && member) {"


def test_a_condition_that_is_already_a_constant_is_not_forced_and_unary_minus_is_not_swapped():
    assert said(b"while True:\n    x = -1\n", "python") == [(1, "changing `True` to `False`")]


def test_a_language_faults_cannot_read_gets_none():
    assert faults.language("main.go") is None and faults.language("notes.md") is None
    assert faults.language("app.py") == "python" and faults.language("page.tsx") == "tsx"
    assert faults.plant(b"package main", "go") == []


def test_a_truth_value_handed_over_as_a_setting_is_left_alone():
    source = b"@dataclass(frozen=True)\nclass A:\n    on: bool = True\n\ndef f(p):\n    p.mkdir(exist_ok=True)\n    return True\n"
    assert said(source, "python") == [(3, "changing `True` to `False`"), (7, "changing `True` to `False`")]
