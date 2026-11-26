"""Syntax, complexity and size checks: each rule fires at its limit and not below it."""

from conftest import branchy

from gate.checks import complexity, size, syntax


def by_rule(findings):
    return {f.rule: f for f in findings}


def test_syntax_fails_python_that_python_itself_rejects(repo):
    repo.write("ok.py", "def f():\n    return 1\n")
    repo.write("bad.py", "def f(:\n    return 1\n")
    findings = syntax.run(repo.context())
    assert [(f.path, f.line, f.severity) for f in findings] == [("bad.py", 1, "fail")]


def test_syntax_ignores_text_that_is_not_code(repo):
    repo.write("requirements.txt", "requests>=2 ; (((\n")
    assert syntax.run(repo.context()) == []


# Valid TypeScript that the tree-sitter grammar rejects.
GRAMMAR_FALSE_POSITIVE = "async function f() {\n  const using = await g();\n  const x = using.flatMap((u) => u)[0];\n  if (x) {}\n}\n"


def test_unconfirmed_parse_error_warns_instead_of_blocking(repo):
    repo.write("a.ts", GRAMMAR_FALSE_POSITIVE)
    (finding,) = syntax.run(repo.context())
    assert finding.severity == "warn" and "unconfirmed" in finding.message


def test_repo_can_opt_a_language_into_blocking(repo):
    repo.write("gate.toml", '[syntax]\nfail_on = ["typescript"]\n')
    repo.write("a.ts", "function f( {\n")
    (finding,) = syntax.run(repo.context())
    assert finding.severity == "fail"


def test_ccn_warns_between_limits_and_fails_above(repo):
    repo.write("a.py", branchy("fine", 9) + branchy("warned", 10) + branchy("failed", 15))
    found = {f.symbol: f for f in complexity.run(repo.context())}
    assert "fine" not in found
    assert (found["warned"].severity, found["warned"].value) == ("warn", 11)
    assert (found["failed"].severity, found["failed"].value, found["failed"].limit) == ("fail", 16, 15)


def test_function_length_and_parameter_limits(repo):
    long_body = "".join(f"    v{i} = {i}\n" for i in range(125))
    repo.write("a.py", f"def long():\n{long_body}    return 0\n\ndef wide(self, a, b, c, d, e, f):\n    return a\n")
    found = by_rule(complexity.run(repo.context()))
    assert found["function-lines"].symbol == "long"
    assert found["function-lines"].value > 120
    # `self` is not counted, so six real parameters is one over the limit of five.
    assert (found["parameters"].symbol, found["parameters"].value) == ("wide", 6)


def test_complexity_limits_come_from_config(repo):
    repo.write("gate.toml", "[complexity]\nccn_fail = 3\nccn_warn = 2\n")
    repo.write("a.py", branchy("f", 4))
    (finding,) = complexity.run(repo.context())
    assert (finding.severity, finding.limit) == ("fail", 3)


def test_complexity_key_has_no_line_number(repo):
    repo.write("a.py", branchy("f", 16))
    before = complexity.run(repo.context())[0]
    repo.write("a.py", "\n\n# moved down\n" + branchy("f", 16))
    after = complexity.run(repo.context())[0]
    assert before.line != after.line
    assert before.key == after.key == "complexity/ccn:a.py::f"


def test_size_warns_then_fails(repo):
    repo.write("small.py", "x = 1\n" * 300)
    repo.write("long.py", "x = 1\n" * 301)
    repo.write("huge.py", "x = 1\n" * 501)
    found = {f.path: f for f in size.run(repo.context())}
    assert set(found) == {"long.py", "huge.py"}
    assert found["long.py"].severity == "warn"
    assert (found["huge.py"].severity, found["huge.py"].value, found["huge.py"].limit) == ("fail", 501, 500)


def test_size_ignores_non_code_and_honours_overrides(repo):
    repo.write("gate.toml", '[[size.override]]\nglob = "**/*_test.py"\nmax_file_lines_fail = 1000\n')
    repo.write("data.json", "1\n" * 5000)
    repo.write("pkg/big_test.py", "x = 1\n" * 800)
    repo.write("pkg/big.py", "x = 1\n" * 800)
    assert [f.path for f in size.run(repo.context())] == ["pkg/big.py"]


def test_ccn_exactly_at_the_fail_limit_does_not_fail(repo):
    repo.write("a.py", branchy("at", 14) + branchy("over", 15))
    found = {f.symbol: f for f in complexity.run(repo.context())}
    assert (found["at"].value, found["at"].severity) == (15, "warn")
    assert (found["over"].value, found["over"].severity) == (16, "fail")


def test_ccn_exactly_at_the_warn_limit_is_silent(repo):
    repo.write("a.py", branchy("at", 9) + branchy("over", 10))
    assert [f.symbol for f in complexity.run(repo.context())] == ["over"]


def test_file_exactly_at_a_size_limit_is_not_over_it(repo):
    repo.write("gate.toml", "[size]\nmax_file_lines_warn = 5\nmax_file_lines_fail = 10\n")
    for name, lines in (("warn_at.py", 5), ("warn_over.py", 6), ("fail_at.py", 10), ("fail_over.py", 11)):
        repo.write(name, "x = 1\n" * lines)
    found = {f.path: f.severity for f in size.run(repo.context())}
    assert found == {"warn_over.py": "warn", "fail_at.py": "warn", "fail_over.py": "fail"}


def long_function(name, lines):
    return f"def {name}():\n" + "".join(f"    x{i} = {i}\n" for i in range(lines - 2)) + "    return 0\n"


def test_function_length_fails_past_80_lines_and_not_at_80(repo):
    repo.write("a.py", long_function("at", 80) + long_function("over", 81))
    assert [(f.symbol, f.rule, f.value) for f in complexity.run(repo.context())] == [("over", "function-lines", 81)]


def test_a_limit_can_differ_for_files_matching_a_glob_and_the_first_match_wins(repo):
    repo.write("gate.toml", '[[complexity.override]]\nglob = "ui/**"\nmax_function_lines = 150\n'
                            '[[complexity.override]]\nglob = "**/*.py"\nmax_function_lines = 10\nccn_fail = 3\n')
    repo.write("ui/view.py", long_function("markup", 100) + branchy("still_checked", 20))
    repo.write("core/logic.py", long_function("logic", 100))
    found = {(f.path, f.symbol, f.rule) for f in complexity.run(repo.context()) if f.severity == "fail"}
    assert found == {("ui/view.py", "still_checked", "ccn"), ("core/logic.py", "logic", "function-lines")}


def test_test_files_get_a_longer_limit_by_name_or_folder_and_other_files_do_not(repo):
    repo.write("gate.toml", "[size]\nmax_file_lines_fail = 10\nmax_file_lines_warn = 5\nmax_test_file_lines_fail = 20\n")
    for name in ("src/a.test.ts", "tests/unit/helpers.py", "pkg/test_x.py", "src/__tests__/b.ts", "src/latest.py", "src/contest/c.py"):
        repo.write(name, "x = 1\n" * 15)
    repo.write("tests/huge.py", "x = 1\n" * 21)
    found = {f.path: f.severity for f in size.run(repo.context())}
    assert found == {"src/latest.py": "fail", "src/contest/c.py": "fail", "tests/huge.py": "fail"}


def test_an_explicit_override_beats_the_test_file_limit(repo):
    repo.write("gate.toml", '[size]\nmax_test_file_lines_fail = 50\n[[size.override]]\nglob = "tests/**"\nmax_file_lines_fail = 10\n')
    repo.write("tests/a.py", "x = 1\n" * 15)
    assert [f.severity for f in size.run(repo.context())] == ["fail"]


def test_raising_the_general_file_limit_past_the_test_default_raises_it_for_tests_too(repo):
    # Otherwise a repo that allows 1500-line files would hold its tests to a stricter 1000.
    repo.write("gate.toml", "[size]\nmax_file_lines_fail = 1500\n")
    repo.write("tests/test_big.py", "x = 1\n" * 1200)
    repo.write("src/big.py", "x = 1\n" * 1200)
    assert [f for f in size.run(repo.context()) if f.severity == "fail"] == []
