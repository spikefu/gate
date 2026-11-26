"""One change may only add so much non-test code before a person should look at it."""

from gate.checks import change_size

CONFIG = "[change-size]\nmax_lines = 10\n"


def code(lines, tag="x"):
    return "".join(f"{tag}{i} = {i}\n" for i in range(lines))


def start(repo, config=CONFIG, files=None):
    repo.write("gate.toml", config)
    for name, text in (files or {}).items():
        repo.write(name, text)
    repo.commit()


def found(repo):
    return [(f.value, f.limit, f.severity) for f in change_size.run(repo.changed())]


def test_a_change_within_the_budget_is_silent_and_one_past_it_warns(repo):
    start(repo)
    repo.write("src/a.py", code(10))
    assert found(repo) == []
    repo.write("src/a.py", code(11))
    assert found(repo) == [(11, 10, "warn")]


def test_modified_and_new_lines_count_across_files_and_the_largest_are_listed(repo):
    start(repo, files={"src/a.py": code(20), "src/b.py": code(5)})
    repo.write("src/a.py", code(20) + code(6, "added"))        # 6 new lines
    repo.write("src/b.py", code(5, "changed"))                 # 5 lines rewritten
    repo.write("src/c.py", code(9))                            # new file, 9 lines
    (finding,) = change_size.run(repo.changed())
    assert (finding.value, finding.path) == (20, ".")
    assert finding.detail == "src/c.py: +9\nsrc/a.py: +6\nsrc/b.py: +5", "largest first"
    assert "adds 20 lines of non-test code, past the 10" in finding.message


def test_deleting_code_never_counts(repo):
    start(repo, files={"src/a.py": code(200), "src/b.py": code(50)})
    repo.write("src/a.py", code(5))
    (repo.root / "src/b.py").unlink()
    assert found(repo) == []


def test_tests_documents_and_lockfiles_do_not_count(repo):
    start(repo)
    repo.write("tests/test_a.py", code(500))
    repo.write("src/a.test.ts", code(500))
    repo.write("README.md", "word\n" * 500)
    repo.write("package-lock.json", "{}\n" * 500)
    repo.write("src/a.py", code(4))
    assert found(repo) == []


def test_a_whole_repo_run_has_nothing_to_compare(repo):
    start(repo)
    repo.write("src/a.py", code(500))
    assert change_size.run(repo.context()) == []


def test_committing_resets_the_count(repo):
    start(repo)
    repo.write("src/a.py", code(50))
    assert found(repo) == [(50, 10, "warn")]
    repo.commit()
    assert found(repo) == []


def test_a_repo_can_make_it_block(repo):
    start(repo, CONFIG + 'severity = "fail"\n')
    repo.write("src/a.py", code(50))
    assert found(repo) == [(50, 10, "fail")]
