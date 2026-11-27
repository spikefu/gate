"""Trying planted faults: in a copy, against the repo's own tests, without ever touching the working tree."""

import sys

import pytest

from gate import seeded
from gate.lang import faults

CALC = '''def price(amount, member):
    if amount >= 100 and member:
        return amount - 10
    return amount
'''
# Checks the discount, and never asks about exactly 100: the boundary is untested.
CHECK = '''import calc
assert calc.price(200, True) == 190
assert calc.price(200, False) == 200
assert calc.price(50, True) == 50
'''


def limits(**over) -> seeded.Settings:
    base = dict(command=f"{sys.executable} check.py", timeout_seconds=30, max_faults=50, max_seconds=120, link=("node_modules",))
    return seeded.Settings(**{**base, **over})


def setup(repo, check=CHECK):
    repo.write("calc.py", CALC)
    repo.write("check.py", check)
    repo.commit()
    return {"calc.py": faults.plant(CALC.encode(), "python")}


def tracked(repo) -> tuple[str, ...]:
    return ("calc.py", "check.py")


def test_a_fault_the_tests_fail_on_is_noticed_and_one_they_pass_on_is_not(repo):
    planted = setup(repo)
    outcome = seeded.run(repo.root, tracked(repo), planted, limits())
    assert outcome.tried == outcome.total == len(planted["calc.py"]) and outcome.cut_short == 0
    assert [(rel, f.line, f.says) for rel, f in outcome.unnoticed] == [("calc.py", 2, "changing `>=` to `>`")]
    assert outcome.undefended == []


def test_the_working_tree_is_never_touched(repo):
    planted = setup(repo)
    before = {rel: ((repo.root / rel).read_bytes(), (repo.root / rel).stat().st_mtime_ns) for rel in tracked(repo)}
    seeded.run(repo.root, tracked(repo), planted, limits())
    assert {rel: ((repo.root / rel).read_bytes(), (repo.root / rel).stat().st_mtime_ns) for rel in tracked(repo)} == before
    assert sorted(p.name for p in repo.root.iterdir() if p.name != ".git") == ["calc.py", "check.py"]


def test_tests_that_fail_in_a_clean_copy_cannot_judge_anything(repo):
    planted = setup(repo, check="import calc\nassert calc.price(1, True) == 999\n")
    with pytest.raises(seeded.CannotRun, match="do not pass in a clean copy"):
        seeded.run(repo.root, tracked(repo), planted, limits())


def test_a_file_that_can_be_broken_outright_unnoticed_is_reported_once_and_its_faults_are_not_tried(repo):
    planted = setup(repo, check="assert 1 + 1 == 2\n")
    outcome = seeded.run(repo.root, tracked(repo), planted, limits())
    assert outcome.undefended == ["calc.py"] and outcome.unnoticed == []
    assert outcome.tried == 0 and outcome.total == 0 and outcome.cut_short == 0


def test_the_budget_stops_the_run_and_says_how_many_were_left(repo):
    planted = setup(repo)
    outcome = seeded.run(repo.root, tracked(repo), planted, limits(max_faults=2))
    assert outcome.tried == 2 and outcome.cut_short == len(planted["calc.py"]) - 2
    assert seeded.run(repo.root, tracked(repo), planted, limits(max_seconds=0)).tried == 0


def test_a_run_that_takes_too_long_counts_as_noticed(repo):
    repo.write("calc.py", "def spin(n):\n    while n > 0:\n        n = n - 1\n    return n\n")
    repo.write("check.py", "import calc\nassert calc.spin(3) == 0\n")
    repo.commit()
    source = (repo.root / "calc.py").read_bytes()
    forever = [f for f in faults.plant(source, "python") if f.says == "making the condition always true"]
    outcome = seeded.run(repo.root, tracked(repo), {"calc.py": forever}, limits(timeout_seconds=3))
    assert outcome.tried == 1 and outcome.unnoticed == []


def test_installed_packages_are_linked_in_not_copied_and_are_left_alone_afterwards(repo):
    planted = setup(repo, check="import os, calc\nassert os.path.islink('node_modules')\n"
                                "assert open('node_modules/pkg/index.js').read() == 'x'\nassert calc.price(200, True) == 190\n")
    repo.write("node_modules/pkg/index.js", "x")
    seeded.run(repo.root, tracked(repo), planted, limits())
    assert (repo.root / "node_modules/pkg/index.js").read_text() == "x"


def test_no_faults_means_nothing_is_run(repo):
    setup(repo, check="raise SystemExit(1)\n")
    assert seeded.run(repo.root, tracked(repo), {"calc.py": []}, limits()).total == 0


def test_the_tests_run_the_copy_even_when_another_copy_of_the_code_is_installed(repo, tmp_path, monkeypatch):
    """An editable install, or anything else on the path, must not stand in for the code being judged."""
    elsewhere = tmp_path / "installed"
    (elsewhere / "calc.py").parent.mkdir(parents=True)
    (elsewhere / "calc.py").write_text(CALC)
    monkeypatch.setenv("PYTHONPATH", str(elsewhere))
    repo.write("src/calc.py", CALC)
    repo.write("check.py", CHECK)
    repo.commit()
    planted = {"src/calc.py": faults.plant(CALC.encode(), "python")}
    command = f"{sys.executable} -c \"exec(open('check.py').read())\""  # no script folder on the path: only PYTHONPATH finds calc
    outcome = seeded.run(repo.root, ("src/calc.py", "check.py"), planted, limits(command=command))
    assert outcome.undefended == [], "the copy's own code is what the tests imported"
    assert [f.says for _, f in outcome.unnoticed] == ["changing `>=` to `>`"]
