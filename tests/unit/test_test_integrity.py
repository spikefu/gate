"""A change may move tests around freely, but may not leave the repo with fewer tests or assertions."""

from gate.checks import test_integrity
from gate.checks.test_integrity import Count, count

TWO = "def test_a():\n    assert f() == 1\n\ndef test_b():\n    assert g() == 2\n    assert h()\n"


def found(repo):
    return [(f.rule, f.value, f.path) for f in test_integrity.run(repo.changed())]


def start(repo, **tests):
    for name, text in tests.items():
        repo.write(f"tests/{name}.py", text)
    repo.write("src/app.py", "x = 1\n")
    repo.commit()


def test_counts_tests_and_assertions_per_language():
    assert count(TWO, ".py") == Count(2, 3)
    assert count("class T(TestCase):\n    def test_x(self):\n        self.assertEqual(1, 1)\n        with pytest.raises(E):\n"
                 "            f()\n        m.assert_called_once()\n", ".py") == Count(1, 3)
    assert count("describe('x', () => {\n  it('a', () => { expect(1).toBe(1) })\n  test.each(rows)('b', (r) => {\n"
                 "    expect(r).toBeTruthy()\n    assert.equal(r, 1)\n  })\n})\n", ".ts") == Count(2, 3)
    assert count("func TestA(t *testing.T) {\n\tif x { t.Fatalf(\"no\") }\n\trequire.Equal(t, 1, 1)\n}\n", ".go") == Count(1, 2)
    assert count("#[test]\nfn a() { assert_eq!(1, 1); }\n#[tokio::test]\nasync fn b() { assert!(true); }\n", ".rs") == Count(2, 2)
    assert count("anything", ".md") == Count()


def test_a_whole_repo_run_has_nothing_to_compare(repo):
    start(repo, test_a=TWO)
    repo.write("tests/test_a.py", "")
    assert test_integrity.run(repo.context()) == []


def test_deleting_a_test_fails_and_says_where(repo):
    start(repo, test_a=TWO)
    repo.write("tests/test_a.py", "def test_a():\n    assert f() == 1\n")
    (finding,) = test_integrity.run(repo.changed())
    assert (finding.rule, finding.value, finding.path, finding.severity) == ("fewer-tests", 1, "tests/test_a.py", "fail")
    assert finding.detail == "tests/test_a.py: 2 -> 1 tests"


def test_deleting_a_test_file_fails(repo):
    start(repo, test_a=TWO, test_b=TWO)
    (repo.root / "tests/test_b.py").unlink()
    (finding,) = test_integrity.run(repo.changed())
    assert (finding.rule, finding.value) == ("fewer-tests", 2)
    assert finding.detail == "tests/test_b.py: 2 -> 0 tests (file deleted)"


def test_removing_an_assertion_from_a_surviving_test_fails(repo):
    start(repo, test_a=TWO)
    repo.write("tests/test_a.py", TWO.replace("    assert h()\n", "    h()\n"))
    assert found(repo) == [("fewer-assertions", 1, "tests/test_a.py")]


def test_moving_tests_between_files_or_renaming_a_file_is_not_a_loss(repo):
    start(repo, test_a=TWO)
    repo.write("tests/test_a.py", "def test_a():\n    assert f() == 1\n")
    repo.write("tests/test_new.py", "def test_b():\n    assert g() == 2\n    assert h()\n")
    assert found(repo) == []
    (repo.root / "tests/test_a.py").rename(repo.root / "tests/test_renamed.py")
    assert found(repo) == []


def test_adding_or_rewriting_tests_without_a_net_loss_passes(repo):
    start(repo, test_a=TWO)
    repo.write("tests/test_a.py", TWO + "\ndef test_c():\n    assert k()\n")
    assert found(repo) == []
    repo.write("tests/test_a.py", "def test_one():\n    assert a\n    assert b\n\ndef test_two():\n    assert c\n")
    assert found(repo) == []


def test_only_test_files_count(repo):
    # A runtime assert removed from source is not a test being weakened.
    repo.write("src/app.py", "def test_like_name():\n    assert ready\n")
    repo.commit()
    repo.write("src/app.py", "x = 1\n")
    assert found(repo) == []


def test_excluded_files_are_ignored(repo):
    repo.write("gate.toml", '[files]\nexclude = ["tests/generated/**"]\n')
    start(repo)
    repo.write("tests/generated/test_g.py", TWO)
    repo.commit()
    (repo.root / "tests/generated/test_g.py").unlink()
    assert found(repo) == []


def test_a_repo_can_make_it_a_warning(repo):
    repo.write("gate.toml", '[test-integrity]\nseverity = "warn"\n')
    start(repo, test_a=TWO)
    repo.write("tests/test_a.py", "")
    (finding,) = test_integrity.run(repo.changed())
    assert finding.severity == "warn"


def test_a_staged_rename_cannot_hide_a_test_dropped_along_the_way(repo):
    start(repo, test_a=TWO + "\ndef test_c():\n    assert k()\n")
    repo.git("mv", "tests/test_a.py", "tests/test_renamed.py")
    repo.write("tests/test_renamed.py", TWO)
    repo.git("add", "-A")
    assert [(rule, value) for rule, value, _path in found(repo)] == [("fewer-tests", 1)]
