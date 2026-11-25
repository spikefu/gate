"""Project commands through the real CLI: exit codes, timeouts, and whose fault a failure is on a scoped run."""

from conftest import check_json


def test_commands_pass_and_fail_by_exit_code(repo):
    repo.write("gate.toml", '[tests.unit]\ncommand = "exit 0"\n[[command]]\nname = "e2e"\nrun = "echo boom; exit 3"\n'
                            '[profiles]\nfast = ["unit", "e2e"]\n')
    code, report = check_json(repo)
    assert code == 1
    (f,) = report["findings"]
    assert (f["check"], f["detail"]) == ("e2e", "boom")
    assert "exit 3" in f["message"]
    assert set(report["ran"]) == {"unit", "e2e"}


def test_command_timeout_fails(repo):
    repo.write("gate.toml", '[tests.unit]\ncommand = "sleep 30"\ntimeout_seconds = 1\n[profiles]\nfast = ["unit"]\n')
    code, report = check_json(repo)
    assert code == 1 and "timed out" in report["findings"][0]["message"]


def test_skipped_check_passes_normally_and_fails_under_strict(repo):
    repo.write("gate.toml", '[profiles]\nfast = ["docs"]\n')
    code, report = check_json(repo)
    assert code == 0 and "docs" in report["skipped"]
    assert check_json(repo, "--strict")[0] == 1


def test_explicit_lint_failure_elsewhere_only_warns_on_scoped_run(repo):
    repo.write("gate.toml", '[toolchain]\nlint = "echo \'old.py:1 bad\'; exit 1"\n[profiles]\nfast = ["lint"]\n')
    repo.write("old.py", "x = 1\n")
    repo.commit()
    repo.write("new.py", "y = 1\n")
    code, report = check_json(repo, "--changed")
    assert code == 0 and report["findings"][0]["severity"] == "warn"
    assert check_json(repo)[0] == 1


def test_scoped_tool_failure_in_a_similarly_named_file_does_not_block(repo):
    repo.write("gate.toml", '[toolchain]\nlint = "echo data.py:1 bad; exit 1"\n[profiles]\nfast = ["lint"]\n')
    repo.write("data.py", "x = 1\n")
    repo.commit()
    repo.write("a.py", "y = 1\n")
    code, report = check_json(repo, "--changed")
    assert code == 0 and report["findings"][0]["severity"] == "warn"


def test_scoped_tool_failure_naming_the_changed_file_blocks_however_the_path_is_written(repo):
    repo.write("pkg/src/a.py", "x = 1\n")
    repo.write("gate.toml", "placeholder = 1\n")
    repo.commit()
    repo.write("pkg/src/a.py", "x = 2\n")
    for printed in ("pkg/src/a.py:1:1", "./pkg/src/a.py(1,1)", f"{repo.root.resolve().as_posix()}/pkg/src/a.py:1", "src/a.py:1"):
        repo.write("gate.toml", f'[toolchain]\nlint = "echo \'{printed} bad\'; exit 1"\n[profiles]\nfast = ["lint"]\n')
        code, report = check_json(repo, "--changed")
        assert code == 1, printed
        assert report["findings"][0]["severity"] == "fail", printed


def test_scoped_tool_that_fails_without_naming_any_file_blocks(repo):
    # A crashed or misconfigured tool checked nothing, so it must not pass as "not your files".
    repo.write("gate.toml", '[toolchain]\nlint = "echo linter config is invalid; exit 2"\n[profiles]\nfast = ["lint"]\n')
    repo.write("old.py", "x = 1\n")
    repo.commit()
    repo.write("new.py", "y = 1\n")
    code, report = check_json(repo, "--changed")
    assert code == 1
    assert "without naming a file" in report["findings"][0]["message"]


def test_whole_repo_tool_failure_is_reported_as_a_plain_failure(repo):
    # Attribution to "other files" only makes sense when some files are out of scope.
    repo.write("gate.toml", '[toolchain]\nlint = "echo linter config is invalid; exit 2"\n[profiles]\nfast = ["lint"]\n')
    repo.write("a.py", "x = 1\n")
    code, report = check_json(repo)
    (finding,) = report["findings"]
    assert code == 1 and finding["severity"] == "fail"
    assert finding["message"] == 'echo linter config is invalid; exit 2 failed (exit 2)'


def test_commands_find_tools_in_the_repos_own_virtualenv_before_path(repo):
    repo.write("gate.toml", '[toolchain]\nlint = "projlint"\n[profiles]\nfast = ["lint"]\n')
    repo.write(".gitignore", ".venv/\n")
    repo.write("a.py", "x = 1\n")
    repo.write(".venv/bin/projlint", "#!/bin/sh\nexit 0\n")
    (repo.root / ".venv/bin/projlint").chmod(0o755)
    assert check_json(repo)[0] == 0
