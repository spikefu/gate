"""Markers that switch a check off are findings, counted per file and kind, and only where they act."""

from gate import baseline
from gate.checks import suppressions


def counts(repo, selected=None):
    return {(f.path, f.rule): f.value for f in suppressions.run(repo.context(selected))}


def test_python_markers_are_counted_by_kind(repo):
    repo.write("a.py", "import os  # noqa: F401\n"
                       "x: int = f()  # type: ignore[assignment]\n"
                       "y = g()  # pyright: ignore\n"
                       "def h():  # pragma: no cover\n    pass\n"
                       "@pytest.mark.skip(reason='later')\ndef test_a(): pass\n"
                       "@pytest.mark.xfail\ndef test_b(): pass\n"
                       "@unittest.skipIf(True, 'x')\ndef test_c(): pass\n")
    assert counts(repo) == {("a.py", "lint"): 1, ("a.py", "type"): 2, ("a.py", "coverage"): 1, ("a.py", "test"): 3}


def test_a_python_marker_quoted_in_a_string_or_docstring_is_not_counted(repo):
    # Tools that write or test such markers must not be accused of using them.
    repo.write("a.py", '"""Explains # noqa and # type: ignore."""\n'
                       'SAMPLE = "x = 1  # noqa\\n@pytest.mark.skip\\n"\n'
                       "noqa = 1\nskip = lambda: 0\n")
    assert counts(repo) == {}


def test_typescript_markers_are_counted_by_kind(repo):
    repo.write("a.ts", "// eslint-disable-next-line no-console\n"
                       "/* eslint-disable */\n"
                       "// @ts-ignore\n"
                       "// @ts-expect-error wrong on purpose\n"
                       "const v = input as any\n"
                       "/* istanbul ignore next */\n")
    repo.write("a.test.ts", "it.skip('later', () => {})\ndescribe.skip('x', () => {})\nxit('y', () => {})\n")
    assert counts(repo) == {("a.ts", "lint"): 2, ("a.ts", "type"): 3, ("a.ts", "coverage"): 1, ("a.test.ts", "test"): 3}


def test_ordinary_code_that_resembles_a_marker_is_not_counted(repo):
    repo.write("a.ts", "const many = xs as Array<number>\nconst anything = 1\nqueue.skip(3)\n"
                       "const s = cast as anyThing\nexport function skip() {}\nitems.forEach((it) => it.skipped)\n")
    repo.write("c.tsx", "// treated the same as any other user\nconst copy = <p>Open to members as any guest would be</p>\n"
                        "test.todo('write this')\n")
    repo.write("d.rb", "x = skip\nnext if skip\n")
    repo.write("b.js", "const x = y as any\n")  # not TypeScript, so `as any` means nothing here
    repo.write("notes.md", "use // @ts-ignore sparingly\n")
    assert counts(repo) == {}


def test_other_languages_have_their_own_markers(repo):
    repo.write("a.go", "x := f() //nolint:errcheck\nfunc TestA(t *testing.T) { t.Skip(\"later\") }\n")
    repo.write("a.rs", "#[allow(dead_code)]\nfn f() {}\n#[ignore]\n#[test]\nfn t() {}\n")
    repo.write("A.java", "@SuppressWarnings(\"unchecked\")\nclass A { @Disabled void t() {} }\n")
    repo.write("a.rb", "x = 1 # rubocop:disable Style/Foo\n  skip 'later'\n")
    assert counts(repo) == {("a.go", "lint"): 1, ("a.go", "test"): 1, ("a.rs", "lint"): 1, ("a.rs", "test"): 1,
                            ("A.java", "lint"): 1, ("A.java", "test"): 1, ("a.rb", "lint"): 1, ("a.rb", "test"): 1}


def test_the_finding_points_at_the_first_marker_and_lists_them(repo):
    repo.write("a.py", "x = 1\ny = f()  # noqa\nz = g()  # noqa: E501\n")
    (finding,) = suppressions.run(repo.context())
    assert (finding.line, finding.value, finding.severity, finding.key) == (2, 2, "fail", "suppressions/lint:a.py")
    assert finding.detail == "2: y = f()  # noqa\n3: z = g()  # noqa: E501"
    assert "fix what the linter reported" in finding.message


def test_a_repo_can_stop_counting_a_kind(repo):
    repo.write("gate.toml", '[suppressions]\nkinds = ["lint", "test"]\n')
    repo.write("a.py", "x = f()  # type: ignore\ny = 1  # noqa\n")
    assert counts(repo) == {("a.py", "lint"): 1}


def test_only_the_selected_files_are_judged(repo):
    repo.write("old.py", "x = 1  # noqa\n")
    repo.write("new.py", "y = 1  # noqa\n")
    assert counts(repo, ["new.py"]) == {("new.py", "lint"): 1}


def test_the_baseline_lets_a_count_fall_but_not_rise_and_says_what_was_allowed(repo):
    repo.write("a.py", "x = 1  # noqa\ny = 2  # noqa\n")
    accepted = baseline.current(suppressions.run(repo.context()))
    assert accepted == {"suppressions/lint:a.py": [2]}

    repo.write("a.py", "x = 1  # noqa\n")
    (fewer,) = baseline.apply(suppressions.run(repo.context()), accepted)
    assert fewer.baselined

    repo.write("a.py", "x = 1  # noqa\ny = 2  # noqa\nz = 3  # noqa\n")
    (more,) = baseline.apply(suppressions.run(repo.context()), accepted)
    assert more.blocks() and more.message.endswith("(the baseline allows 2)")

    repo.write("a.py", "x = 1  # noqa\ny = f()  # type: ignore\n")
    by_rule = {f.rule: f for f in baseline.apply(suppressions.run(repo.context()), accepted)}
    assert by_rule["lint"].baselined and by_rule["type"].blocks(), "an accepted kind does not cover a different kind"
