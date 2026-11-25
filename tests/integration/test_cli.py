"""End to end through the real CLI in a subprocess: exit codes, JSON, git scoping, baseline flow."""

import json
import subprocess
import sys

from conftest import branchy


def gate(repo, *args):
    proc = subprocess.run([sys.executable, "-m", "gate.cli", *args], cwd=repo.root, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def check_json(repo, *args):
    code, out, _ = gate(repo, "check", "--json", *args)
    return code, json.loads(out)


STRUCTURAL = '[profiles]\nfast = ["syntax", "complexity", "size", "boundaries"]\n'


def test_clean_repo_passes_and_dirty_repo_fails(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.py", branchy("f", 3))
    assert gate(repo, "check")[0] == 0
    repo.write("b.py", branchy("g", 20))
    code, report = check_json(repo)
    assert code == 1 and report["ok"] is False
    (f,) = report["findings"]
    assert (f["check"], f["path"], f["symbol"], f["value"], f["limit"]) == ("complexity", "b.py", "g", 21, 15)


def test_output_is_deterministic(repo):
    repo.write("gate.toml", STRUCTURAL)
    for name in ("z.py", "a.py", "m.py"):
        repo.write(name, branchy("f", 20))
    first, second = check_json(repo)[1], check_json(repo)[1]
    assert first["findings"] == second["findings"]
    assert [f["path"] for f in first["findings"]] == ["a.py", "m.py", "z.py"]


def test_check_does_not_modify_the_repo(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.py", branchy("f", 20))
    repo.commit()
    gate(repo, "check")
    status = subprocess.run(["git", "-C", str(repo.root), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""


def test_changed_only_judges_the_diff_and_untracked_files(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("old.py", branchy("legacy", 20))
    repo.write("touched.py", "x = 1\n")
    repo.commit()
    assert check_json(repo, "--changed")[1]["files"] == 0
    repo.write("touched.py", branchy("edited", 20))
    repo.write("new.py", branchy("added", 20))
    code, report = check_json(repo, "--changed")
    assert code == 1
    assert sorted(f["path"] for f in report["findings"]) == ["new.py", "touched.py"]
    assert check_json(repo)[1]["summary"]["fail"] == 3


def test_gitignored_files_are_not_checked(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write(".gitignore", "generated/\n")
    repo.write("generated/out.py", branchy("f", 20))
    assert gate(repo, "check")[0] == 0


def test_baseline_flow_accept_then_regress_then_fix_then_prune(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.py", branchy("legacy", 20))
    assert gate(repo, "check")[0] == 1

    # Plain `baseline` reports and writes nothing; only --accept records.
    assert "1 new or worsened" in gate(repo, "baseline")[1]
    assert not (repo.root / ".gate/baseline.json").exists()
    gate(repo, "baseline", "--accept")
    code, report = check_json(repo)
    assert code == 0 and report["summary"] == {"fail": 0, "warn": 0, "baselined": 1, "skipped": 0}

    repo.write("a.py", branchy("legacy", 25))
    assert gate(repo, "check")[0] == 1, "a baselined function that gets worse must fail"

    # --prune must not absorb the regression.
    gate(repo, "baseline", "--prune")
    assert gate(repo, "check")[0] == 1

    repo.write("a.py", branchy("legacy", 3))
    assert "removed 1 fixed" in gate(repo, "baseline", "--prune")[1]
    assert json.loads((repo.root / ".gate/baseline.json").read_text())["entries"] == {}


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


def test_usage_errors_exit_2(repo, tmp_path):
    repo.write("gate.toml", "[nonsense]\n")
    code, _, err = gate(repo, "check")
    assert code == 2 and "nonsense" in err


def test_init_writes_once_reports_violations_and_never_baselines(repo):
    repo.write("a.py", branchy("legacy", 20))
    code, out, _ = gate(repo, "init")
    assert code == 0 and "1 structural violation(s) today in 1 file(s)" in out
    assert (repo.root / "gate.toml").is_file()
    assert not (repo.root / ".gate").exists(), "accepting a baseline is a separate, deliberate step"
    assert gate(repo, "check")[0] == 1

    code, _, err = gate(repo, "init")
    assert code == 2 and "already exists" in err


def test_init_print_writes_nothing(repo):
    code, out, _ = gate(repo, "init", "--print")
    assert code == 0 and "[profiles]" in out
    assert not (repo.root / "gate.toml").exists()


def test_todo_is_the_baseline_worst_first_and_shrinks_as_work_is_done(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("mild.py", branchy("f", 16))
    repo.write("bad.py", branchy("g", 40))
    repo.write("unbaselined.py", "x = 1\n")
    gate(repo, "baseline", "--accept")
    repo.write("unbaselined.py", branchy("h", 30))

    report = json.loads(gate(repo, "todo", "--json")[1])
    assert (report["total_files"], report["total_findings"], report["stale"]) == (2, 2, 0)
    assert [f["path"] for f in report["files"]] == ["bad.py", "mild.py"], "new failures are not cleanup work"
    assert [f["path"] for f in json.loads(gate(repo, "todo", "--json", "--next", "1")[1])["files"]] == ["bad.py"]

    repo.write("bad.py", branchy("g", 2))
    report = json.loads(gate(repo, "todo", "--json")[1])
    assert ([f["path"] for f in report["files"]], report["stale"]) == (["mild.py"], 1)
    assert "run `gate baseline --prune`" in gate(repo, "todo")[1]
