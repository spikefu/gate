"""End to end through the real CLI in a subprocess: exit codes, JSON, git scoping, baseline flow."""

import json
import subprocess

from conftest import branchy, check_json, gate

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
    repo.write("bad.py", branchy("g", 35))
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


def test_rules_check_fails_until_written_and_again_after_the_config_changes(repo):
    repo.write("gate.toml", STRUCTURAL)
    assert gate(repo, "rules", "--check")[0] == 1
    assert not (repo.root / "AGENTS.md").exists(), "--check must not write"

    code, out, _ = gate(repo, "rules")
    assert code == 0 and "wrote AGENTS.md" in out
    assert gate(repo, "rules", "--check")[0] == 0
    assert "up to date" in gate(repo, "rules")[1]

    repo.write("gate.toml", STRUCTURAL + "[complexity]\nccn_fail = 12\n")
    code, out, _ = gate(repo, "rules", "--check")
    assert code == 1 and "AGENTS.md is out of date" in out
    gate(repo, "rules")
    assert "at most 12" in (repo.root / "AGENTS.md").read_text()


def test_rules_print_writes_nothing(repo):
    code, out, _ = gate(repo, "rules", "--print")
    assert code == 0 and out.startswith("<!-- gate:rules:start -->")
    assert not (repo.root / "AGENTS.md").exists()


def test_baseline_check_is_a_ratchet(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.py", branchy("f", 30))
    repo.write("b.py", branchy("g", 30))
    assert gate(repo, "baseline", "--check")[0] == 0, "nothing recorded is nothing stale"
    gate(repo, "baseline", "--accept")
    assert gate(repo, "baseline", "--check")[0] == 0

    repo.write("a.py", branchy("f", 20))  # improved, still over the limit
    code, out, _ = gate(repo, "baseline", "--check")
    assert code == 1 and "0 fixed and 1 improved" in out
    before = (repo.root / ".gate/baseline.json").read_text()

    repo.write("b.py", branchy("g", 2))  # fixed
    code, out, _ = gate(repo, "baseline", "--check")
    assert code == 1 and "1 fixed and 1 improved" in out
    assert (repo.root / ".gate/baseline.json").read_text() == before, "--check must not write"

    gate(repo, "baseline", "--prune")
    assert gate(repo, "baseline", "--check")[0] == 0


def test_baseline_check_ignores_new_violations_which_are_gate_checks_job(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.py", branchy("f", 30))
    gate(repo, "baseline", "--accept")
    repo.write("new.py", branchy("h", 30))
    repo.write("a.py", branchy("f", 40))
    assert gate(repo, "baseline", "--check")[0] == 0
    assert gate(repo, "check")[0] == 1


def test_gaps_reports_and_never_fails_or_writes(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.py", branchy("f", 3))
    repo.commit()
    code, out, _ = gate(repo, "gaps")
    assert code == 0 and "No test command is configured" in out
    assert json.loads(gate(repo, "gaps", "--json")[1])["gaps"]
    status = subprocess.run(["git", "-C", str(repo.root), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""


def anonymous(*branch_counts):
    """A TypeScript file of anonymous functions, which all share the name `(anonymous)`."""
    def body(n):
        return "(x: number) => { " + " ".join(f"if (x === {i}) return {i};" for i in range(n)) + " return -1 },\n"
    return "export const handlers = [" + "".join(body(n) for n in branch_counts) + "]\n"


def test_new_anonymous_functions_cannot_hide_behind_a_baselined_one(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.ts", anonymous(20))
    gate(repo, "baseline", "--accept")
    assert gate(repo, "check")[0] == 0

    repo.write("a.ts", anonymous(20, 19, 18))
    code, report = check_json(repo)
    assert code == 1
    assert sorted((f["value"], f["baselined"]) for f in report["findings"]) == [(19, False), (20, False), (21, True)]

    # Accepting all three records all three, and fixing one is then noticed.
    gate(repo, "baseline", "--accept")
    assert json.loads((repo.root / ".gate/baseline.json").read_text())["entries"] == {"complexity/ccn:a.ts::(anonymous)": [21, 20, 19]}
    repo.write("a.ts", anonymous(20, 18))
    code, out, _ = gate(repo, "baseline", "--check")
    assert code == 1 and "1 fixed" in out


def test_old_format_baseline_stops_covering_extra_same_named_functions_and_says_why(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("a.ts", anonymous(28, 24))
    repo.write(".gate/baseline.json", json.dumps({"version": 1, "entries": {"complexity/ccn:a.ts::(anonymous)": 29}}))
    code, report = check_json(repo)
    assert code == 1
    assert sorted((f["value"], f["baselined"]) for f in report["findings"]) == [(25, False), (29, True)]
    out = gate(repo, "baseline")[1]
    assert "1 new or worsened" in out and "old format" in out and "--accept" in out

    gate(repo, "baseline", "--accept")
    assert gate(repo, "check")[0] == 0
    assert "old format" not in gate(repo, "baseline")[1]


def test_adding_a_suppression_fails_until_a_person_accepts_it(repo):
    repo.write("gate.toml", '[profiles]\nfast = ["suppressions"]\n')
    repo.write("a.ts", "// @ts-ignore\nexport const a: number = f()\n")
    assert check_json(repo)[0] == 1
    gate(repo, "baseline", "--accept")
    assert check_json(repo)[0] == 0
    repo.commit()

    repo.write("a.ts", "// @ts-ignore\nexport const a: number = f()\nexport const b = g() as any\n")
    code, report = check_json(repo, "--changed")
    (finding,) = report["findings"]
    assert code == 1 and (finding["rule"], finding["value"]) == ("type", 2)
    assert "the baseline allows 1" in finding["message"]

    repo.write("a.ts", "export const a: number = f()\n")
    assert check_json(repo, "--changed")[0] == 0
    assert gate(repo, "baseline", "--check")[0] == 1, "the fix has to be recorded so it cannot be undone"


def test_a_change_that_deletes_a_test_fails_until_it_is_committed(repo):
    repo.write("gate.toml", '[profiles]\nfast = ["test-integrity"]\n')
    repo.write("a.test.ts", "it('adds', () => { expect(add(1, 1)).toBe(2) })\nit('subtracts', () => { expect(sub(1, 1)).toBe(0) })\n")
    repo.commit()
    assert check_json(repo, "--changed")[0] == 0

    repo.write("a.test.ts", "it('adds', () => { expect(add(1, 1)).toBe(2) })\n")
    code, report = check_json(repo, "--changed")
    (finding,) = report["findings"]
    assert code == 1 and finding["check"] == "test-integrity" and "removes 1 test(s)" in finding["message"]
    assert check_json(repo)[0] == 0, "a whole-repo run has no before to compare with"

    repo.commit()  # committing is the person's acceptance
    assert check_json(repo, "--changed")[0] == 0
    assert check_json(repo, "--changed", "--base", "HEAD~1")[0] == 1, "against the older ref the loss is still visible"


def test_metrics_prints_facts_as_text_or_json_and_changes_nothing(repo):
    repo.write("gate.toml", STRUCTURAL)
    repo.write("src/a.py", "from src import b\n")
    repo.write("src/b.py", "x = 1\n")
    repo.write("src/__init__.py", "")
    repo.commit()
    code, out, _ = gate(repo, "metrics", "--json")
    report = json.loads(out)
    assert code == 0 and {f["path"] for f in report["files"]} == {"src/__init__.py", "src/a.py", "src/b.py"}
    assert "most depended on" in gate(repo, "metrics")[1]
    repo.write("src/c.py", "y = 2\n")
    assert [f["path"] for f in json.loads(gate(repo, "metrics", "--json", "--changed")[1])["files"]] == ["src/c.py"]
    (repo.root / "src/c.py").unlink()
    status = subprocess.run(["git", "-C", str(repo.root), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout == ""
