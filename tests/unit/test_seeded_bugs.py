"""The seeded-bugs check: asks only about the lines a change adds, and says what the tests did not notice."""

import sys

import pytest

from gate import rules
from gate.checks import seeded_bugs
from gate.config import ConfigError, load
from gate.findings import Skipped

CALC = '''def price(amount, member):
    if amount >= 100 and member:
        return amount - 10
    return amount
'''
CHECK = '''import calc
assert calc.price(200, True) == 190
assert calc.price(200, False) == 200
assert calc.price(50, True) == 50
'''
CONFIG = f'[seeded-bugs]\ncommand = "{sys.executable} check.py"\n'


def changed(repo, calc=CALC, config=CONFIG, check=CHECK):
    """A repo whose last commit has the tests, and whose working tree adds calc.py."""
    repo.write("gate.toml", config)
    repo.write("check.py", check)
    repo.write("calc.py", "def price(amount, member):\n    return amount\n")
    repo.commit()
    repo.write("calc.py", calc)
    return repo.changed()


def said(findings) -> list[tuple[str, str, int | None]]:
    return [(f.rule, f.path, f.line) for f in findings]


def test_a_fault_in_a_changed_line_that_the_tests_pass_with_is_reported_as_a_warning(repo):
    (finding,) = seeded_bugs.run(changed(repo))
    assert (finding.rule, finding.path, finding.line, finding.severity) == ("unnoticed", "calc.py", 2, "warn")
    assert finding.message.startswith("changing `>=` to `>` went unnoticed: the tests still pass.")
    assert "Add a test that would fail" in finding.message


def test_tests_that_pin_the_boundary_leave_nothing_to_report(repo):
    assert seeded_bugs.run(changed(repo, check=CHECK + "assert calc.price(100, True) == 90\n")) == []


def test_only_the_lines_the_change_adds_are_asked_about(repo):
    repo.write("gate.toml", CONFIG)
    repo.write("check.py", CHECK)
    repo.write("calc.py", CALC)
    repo.commit()
    repo.write("calc.py", CALC + "\n\ndef label(n):\n    return 'big' if n > 9 else 'small'\n")
    found = seeded_bugs.run(repo.changed())
    assert all(f.line is not None and f.line >= 7 for f in found if f.rule == "unnoticed"), "the old boundary is not this change's"
    assert ("unnoticed", "calc.py", 8) in said(found)


def test_a_change_to_the_tests_alone_plants_nothing(repo):
    repo.write("gate.toml", CONFIG)
    repo.write("calc.py", CALC)
    repo.write("tests/test_calc.py", "import calc\n")
    repo.commit()
    repo.write("tests/test_calc.py", "import calc\nassert calc.price(1, True) == 1 or True\n")
    assert seeded_bugs.run(repo.changed()) == []


def test_it_can_be_made_to_fail_the_gate(repo):
    (finding,) = seeded_bugs.run(changed(repo, config=CONFIG + 'severity = "fail"\n'))
    assert finding.severity == "fail" and finding.blocks()


def test_a_run_cut_short_by_the_budget_says_how_many_were_not_tried(repo):
    found = seeded_bugs.run(changed(repo, config=CONFIG + "max_faults = 1\n"))
    (short,) = [f for f in found if f.rule == "cut-short"]
    assert "tried 1 of" in short.message and "were not tried" in short.message and short.severity == "warn"


def test_a_file_no_test_depends_on_is_reported_once(repo):
    found = seeded_bugs.run(changed(repo, check="assert True\n"))
    assert said(found) == [("undefended", "calc.py", None)]
    assert "breaking this file outright went unnoticed" in found[0].message


def test_it_says_why_when_it_cannot_run(repo):
    with pytest.raises(Skipped, match="no tests are named"):
        seeded_bugs.run(changed(repo, config=""))
    repo.write("gate.toml", CONFIG)
    with pytest.raises(Skipped, match="judges a change"):
        seeded_bugs.run(repo.context())
    with pytest.raises(Skipped, match="do not pass in a clean copy"):
        seeded_bugs.run(changed(repo, check="raise SystemExit(1)\n"))


def test_the_same_fault_keeps_the_same_key_so_it_is_one_finding_from_run_to_run(repo):
    first = [f.key for f in seeded_bugs.run(changed(repo))]
    assert first == [f.key for f in seeded_bugs.run(repo.changed())] == ["seeded-bugs:calc.py:2:changing `>=` to `>`"]


def test_the_written_rules_mention_it_only_when_it_is_set_up_and_in_a_profile(repo):
    repo.write("gate.toml", CONFIG)
    assert "### Seeded bugs" not in rules.render(load(repo.root / "gate.toml")), "named, but no profile runs it"
    repo.write("gate.toml", CONFIG + '[profiles]\nchange = ["seeded-bugs"]\n')
    block = rules.render(load(repo.root / "gate.toml"))
    assert "### Seeded bugs" in block and "a fault they still pass with is reported" in block
    repo.write("gate.toml", CONFIG + 'severity = "fail"\n[profiles]\nchange = ["seeded-bugs"]\n')
    assert "a fault they still pass with fails the gate" in rules.render(load(repo.root / "gate.toml"))
    repo.write("gate.toml", '[profiles]\nchange = ["seeded-bugs"]\n')
    assert "### Seeded bugs" not in rules.render(load(repo.root / "gate.toml")), "in a profile, but no tests named"


def test_settings_that_make_no_sense_are_refused(repo):
    for bad in ('severity = "maybe"', "max_faults = 0", "timeout_seconds = 0", "unknown = 1"):
        repo.write("gate.toml", f"[seeded-bugs]\n{bad}\n")
        with pytest.raises(ConfigError, match="seeded-bugs"):
            load(repo.root / "gate.toml")
    repo.write("gate.toml", '[seeded-bugs]\nlink = ["vendor"]\n')
    assert load(repo.root / "gate.toml").seeded_bugs.link == ("vendor",)
