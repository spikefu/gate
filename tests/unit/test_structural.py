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
