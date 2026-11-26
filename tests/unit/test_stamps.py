"""A file made from others carries a stamp, and the gate says when those others have changed."""

import hashlib
import json

import pytest

from gate import rules, stamp
from gate.checks import stamps
from gate.config import ConfigError, load
from gate.findings import Skipped

GATE = '[[stamp]]\nfile = "specs/S.stamp"\n'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def start(repo, config=GATE, **stamp_fields):
    """A repo whose S.out was made from S.tla and src/a.py, with a stamp saying so."""
    repo.write("gate.toml", config)
    repo.write("specs/S.tla", "spec\n")
    repo.write("src/a.py", "a = 1\n")
    body = {"version": 1, "made": "specs/S.out", "inputs": {"specs/S.tla": sha("spec\n"), "src/a.py": sha("a = 1\n")}, **stamp_fields}
    repo.write("specs/S.stamp", json.dumps(body))


def found(repo):
    return [(f.rule, f.severity, f.message) for f in stamps.run(repo.context())]


def test_a_stamp_whose_inputs_are_as_they_were_is_silent(repo):
    start(repo)
    assert found(repo) == []


def test_a_changed_input_fails_and_says_which_and_how_to_make_it_again(repo):
    start(repo, remake="make check-specs")
    repo.write("src/a.py", "a = 2\n")
    assert found(repo) == [("stale", "fail", "specs/S.out was made from files that are not as they were (src/a.py changed): "
                                             "make it again with `make check-specs`")]


def test_an_input_that_is_gone_and_a_covered_file_that_is_new_are_stale_too(repo):
    start(repo, covers=["src/**"])
    repo.write("src/b.py", "b = 1\n")
    repo.write("docs/readme.md", "not covered\n")
    (repo.root / "specs" / "S.tla").unlink()
    ((rule, _, message),) = found(repo)
    assert rule == "stale" and "(specs/S.tla is gone, src/b.py is new): make it again" in message


def test_many_changes_are_listed_up_to_a_point(repo):
    start(repo, covers=["src/**"])
    for n in range(7):
        repo.write(f"src/new{n}.py", "x = 1\n")
    assert "src/new3.py is new, and 3 more)" in found(repo)[0][2]


def test_what_was_made_reporting_a_problem_is_a_finding_of_its_own(repo):
    start(repo, ok=False)
    assert found(repo) == [("problem", "fail", "specs/S.out reports a problem: fix what it found, then make it again")]
    repo.write("src/a.py", "a = 2\n")
    assert [rule for rule, _, _ in found(repo)] == ["stale", "problem"]


def test_a_repo_can_make_it_only_warn(repo):
    start(repo, config=GATE + 'severity = "warn"\n')
    repo.write("src/a.py", "a = 2\n")
    assert found(repo)[0][1] == "warn"


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"version": 2, "made": "x", "inputs": {}}',
                                     '{"version": 1, "inputs": {}}', '{"version": 1, "made": "x", "inputs": []}'])
def test_a_stamp_that_is_missing_or_not_a_stamp_is_a_finding(repo, content):
    start(repo)
    if content is None:
        (repo.root / "specs" / "S.stamp").unlink()
    else:
        repo.write("specs/S.stamp", content)
    ((rule, severity, message),) = found(repo)
    assert (rule, severity) == ("unreadable", "fail") and message.startswith("specs/S.stamp is not a stamp that can be read")


def test_with_no_stamp_declared_the_check_says_it_did_not_run(repo):
    repo.write("gate.toml", "")
    with pytest.raises(Skipped, match="no stamp is declared"):
        stamps.run(repo.context())


def test_the_stamp_reader_keeps_only_what_the_format_defines(tmp_path):
    path = tmp_path / "x.stamp"
    path.write_text(json.dumps({"version": 1, "made": "x", "inputs": {"a": "1"}, "covers": "oops", "ok": "yes", "extra": 1}))
    assert stamp.read(path) == stamp.Stamp(made="x", inputs={"a": "1"}, covers=(), ok=True, remake="")
    (tmp_path / "f").write_bytes(b"abc")
    assert stamp.digest(tmp_path / "f") == hashlib.sha256(b"abc").hexdigest()


@pytest.mark.parametrize("config", ['stamp = "a.stamp"\n', '[[stamp]]\nfile = "a.stamp"\nseverity = "loud"\n',
                                    '[[stamp]]\nfile = 3\n', '[[stamp]]\nfile = "a.stamp"\nmood = "x"\n', '[[stamp]]\n'])
def test_a_stamp_declared_wrongly_is_refused(repo, config):
    repo.write("gate.toml", config)
    with pytest.raises(ConfigError, match="stamp"):
        load(repo.root / "gate.toml")


def test_agents_are_told_about_made_files_only_when_a_profile_checks_them(repo):
    start(repo, config=GATE + '[profiles]\nfast = ["syntax", "stamps"]\n')
    text = rules.render(load(repo.root / "gate.toml"))
    assert "### Made files" in text and "`specs/S.stamp`" in text and "Never edit a stamp by hand" in text
    repo.write("gate.toml", GATE)
    assert "### Made files" not in rules.render(load(repo.root / "gate.toml"))


def test_what_was_made_and_its_stamp_are_not_new_files_in_a_folder_the_stamp_covers(repo):
    start(repo, covers=["specs", "src/**"])
    repo.write("specs/S.out", "the result\n")
    assert found(repo) == [], "the stamp and the result both sit in specs/, which it covers"
    repo.write("specs/S.cfg", "new\n")
    assert "specs/S.cfg is new" in found(repo)[0][2]
