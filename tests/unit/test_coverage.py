"""Of the lines a change adds, the tests must run most; reports are read as the tools write them."""

import os
import time

import pytest

from gate import coverage
from gate.checks import coverage as check
from gate.findings import Skipped

CONFIG = '[coverage]\nreport = "coverage/lcov.info"\n'


def lcov(**files):
    """An lcov report: file -> {line: hits}."""
    out = []
    for name, lines in files.items():
        out += [f"SF:{name}", *(f"DA:{n},{hits}" for n, hits in lines.items()), "end_of_record"]
    return "\n".join(out) + "\n"


def write_report(repo, text, path="coverage/lcov.info"):
    repo.write(path, text)
    later = time.time() + 5   # a report written after the code it describes
    os.utime(repo.root / path, (later, later))


def start(repo, config=CONFIG):
    repo.write("gate.toml", config)
    repo.write(".gitignore", "coverage/\n")
    repo.write("src/a.py", "def old():\n    return 1\n")
    repo.commit()


def found(repo):
    return {f.rule: f for f in check.run(repo.changed())}


def test_reads_lcov_and_cobertura_and_adds_them_up(repo):
    repo.write("src/a.py", "x\n")
    repo.write("coverage/lcov.info", lcov(**{"src/a.py": {1: 0, 2: 3}}))
    repo.write("coverage/cov.xml", f"""<coverage><sources><source>{repo.root}</source></sources><packages><package>
        <classes><class filename="src/a.py"><lines><line number="1" hits="2"/><line number="4" hits="0"/></lines></class>
        <class filename="elsewhere/b.py"><lines><line number="1" hits="1"/></lines></class></classes></package></packages></coverage>""")
    hits = coverage.read(repo.root, ["coverage/lcov.info", "coverage/cov.xml"], ("src/a.py",))
    assert hits == {"src/a.py": {1: 2, 2: 3, 4: 0}}, "files outside the repo are dropped"


def test_resolves_names_relative_to_the_package_the_tests_ran_in_or_absolute(repo):
    repo.write("apps/server/src/app.ts", "x\n")
    repo.write("apps/server/coverage/lcov.info", lcov(**{"src/app.ts": {1: 1}}))
    repo.write("abs.info", lcov(**{str(repo.root / "apps/server/src/app.ts"): {2: 1}, "/somewhere/else.ts": {1: 1}}))
    known = ("apps/server/src/app.ts",)
    assert coverage.read(repo.root, ["apps/server/coverage/lcov.info"], known) == {"apps/server/src/app.ts": {1: 1}}
    assert coverage.read(repo.root, ["abs.info"], known) == {"apps/server/src/app.ts": {2: 1}}


def test_it_cannot_run_without_a_configured_report_that_exists(repo):
    repo.write("gate.toml", "")
    with pytest.raises(Skipped, match="no coverage report is configured"):
        check.run(repo.context())
    repo.write("gate.toml", CONFIG)
    with pytest.raises(Skipped, match="not found"):
        check.run(repo.context())


def test_new_lines_the_tests_do_not_run_fail_with_the_lines_named(repo):
    start(repo)
    repo.write("src/a.py", "def old():\n    return 1\n\n\ndef new(x):\n    if x:\n        return 2\n    return 3\n")
    write_report(repo, lcov(**{"src/a.py": {1: 1, 2: 1, 5: 1, 6: 1, 7: 0, 8: 0}}))
    finding = found(repo)["changed-lines"]
    assert (finding.value, finding.limit, finding.severity, finding.path, finding.line) == (50.0, 80, "fail", "src/a.py", 7)
    assert "the tests run 2 of the 4 lines this change adds (50%" in finding.message
    assert finding.detail == "src/a.py: not run: 7-8"


def test_enough_coverage_of_the_new_lines_passes_whatever_the_old_code_looks_like(repo):
    start(repo)
    repo.write("src/a.py", "def old():\n    return 1\n\n\ndef new(x):\n    return 2\n")
    write_report(repo, lcov(**{"src/a.py": {1: 0, 2: 0, 5: 1, 6: 1}}))   # old code untested, new code tested
    assert found(repo) == {}


def test_only_executable_added_lines_in_non_test_code_count(repo):
    start(repo)
    repo.write("src/a.py", "def old():\n    return 1\n\n# a comment\n\ndef new():\n    return 2\n")
    repo.write("tests/test_a.py", "def test_new():\n    assert True\n")
    write_report(repo, lcov(**{"src/a.py": {1: 1, 2: 1, 6: 1, 7: 1}, "tests/test_a.py": {1: 0, 2: 0}}))
    assert found(repo) == {}


def test_a_changed_file_missing_from_the_report_is_a_warning_not_a_failure(repo):
    start(repo)
    repo.write("src/b.py", "def b():\n    return 1\n")
    repo.write("docs.md", "words\n")
    write_report(repo, lcov(**{"src/a.py": {1: 1, 2: 1}}))
    (finding,) = check.run(repo.changed())
    assert (finding.rule, finding.severity, finding.detail) == ("unmeasured", "warn", "src/b.py")


def test_a_report_older_than_the_code_is_refused(repo):
    start(repo)
    repo.write("coverage/lcov.info", lcov(**{"src/a.py": {1: 1, 2: 1, 5: 0}}))
    old = time.time() - 60
    os.utime(repo.root / "coverage/lcov.info", (old, old))
    repo.write("src/a.py", "def old():\n    return 1\n\n\ndef new():\n    return 2\n")
    with pytest.raises(Skipped, match="older than src/a.py"):
        check.run(repo.changed())


def test_the_whole_repo_figure_is_judged_only_when_asked(repo):
    start(repo)
    write_report(repo, lcov(**{"src/a.py": {1: 1, 2: 0, 3: 0, 4: 0}}))
    assert check.run(repo.context()) == []
    repo.write("gate.toml", CONFIG + "min_percent = 60\n")
    (finding,) = check.run(repo.context())
    assert (finding.rule, finding.value, finding.limit) == ("total", 25.0, 60)


def test_thresholds_and_severity_are_configurable(repo):
    start(repo, CONFIG + 'min_changed_percent = 40\nseverity = "warn"\n')
    repo.write("src/a.py", "def old():\n    return 1\n\n\ndef new(x):\n    return 2\n")
    write_report(repo, lcov(**{"src/a.py": {5: 1, 6: 0}}))
    assert found(repo) == {}, "50% meets a 40% bar"
    repo.write("gate.toml", CONFIG + 'min_changed_percent = 90\nseverity = "warn"\n')
    assert found(repo)["changed-lines"].severity == "warn"


def test_a_line_any_report_saw_run_counts_as_run(repo):
    repo.write("src/a.py", "x\n")
    repo.write("unit.info", lcov(**{"src/a.py": {1: 4, 2: 0}}))
    repo.write("e2e.info", lcov(**{"src/a.py": {1: 0, 2: 0}}))
    assert coverage.read(repo.root, ["unit.info", "e2e.info"], ("src/a.py",)) == {"src/a.py": {1: 4, 2: 0}}


def test_a_file_the_repo_does_not_track_is_dropped_even_when_it_is_inside_the_repo(repo):
    repo.write("build/out.py", "x\n")
    repo.write("abs.info", lcov(**{str(repo.root / "build/out.py"): {1: 1}}))
    assert coverage.read(repo.root, ["abs.info"], ("src/a.py",)) == {}


def test_cobertura_names_are_relative_to_its_source_folders_and_a_broken_file_reads_as_empty(repo):
    repo.write("apps/server/src/app.py", "x\n")
    repo.write("cov.xml", f"""<coverage><sources><source>{repo.root / 'apps/server'}</source></sources>
        <class filename="src/app.py"><lines><line number="3" hits="1"/></lines></class></coverage>""")
    repo.write("broken.xml", "<coverage><class")
    known = ("apps/server/src/app.py",)
    assert coverage.read(repo.root, ["cov.xml", "broken.xml"], known) == {"apps/server/src/app.py": {3: 1}}


def test_exactly_meeting_the_bar_passes(repo):
    start(repo)
    repo.write("src/a.py", "def old():\n    return 1\n" + "".join(f"v{i} = {i}\n" for i in range(5)))
    write_report(repo, lcov(**{"src/a.py": {3: 1, 4: 1, 5: 1, 6: 1, 7: 0}}))   # 4 of 5 = 80%
    assert found(repo) == {}


def test_only_the_lines_the_change_added_count_not_the_one_after_them(repo):
    repo.write("gate.toml", CONFIG)
    repo.write(".gitignore", "coverage/\n")
    repo.write("src/a.py", "a = 1\nuntested_before = 2\n")
    repo.commit()
    repo.write("src/a.py", "a = 1\nadded = 3\nuntested_before = 2\n")
    write_report(repo, lcov(**{"src/a.py": {1: 1, 2: 1, 3: 0}}))
    assert found(repo) == {}, "line 3 was already there and already untested"


def test_the_file_with_most_unrun_lines_is_named_first(repo):
    start(repo)
    repo.write("src/b.py", "x = 1\n")
    repo.write("src/z.py", "x = 1\ny = 2\nz = 3\n")
    write_report(repo, lcov(**{"src/a.py": {1: 1}, "src/b.py": {1: 0}, "src/z.py": {1: 0, 2: 0, 3: 0}}))
    finding = found(repo)["changed-lines"]
    assert finding.path == "src/z.py" and finding.detail == "src/z.py: not run: 1-3\nsrc/b.py: not run: 1"
