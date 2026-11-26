"""`gate gaps` reports what the rules do not cover, without changing anything."""

import json

from conftest import branchy

from gate import gaps, rules
from gate.config import load

COVERED = ('[toolchain]\nlint = "true"\ntypecheck = "true"\nformat = "true"\nsecurity = "true"\ndeps = "true"\n'
           '[tests.unit]\ncommand = "true"\n'
           '[profiles]\nfast = ["syntax", "complexity", "size", "boundaries", "lint", "typecheck", "format", "security", "deps", "unit"]\n')


def found(repo, text=COVERED):
    repo.write("gate.toml", text)
    return gaps.find(repo.context())


def areas(found_gaps):
    return {g.area for g in found_gaps}


def messages(found_gaps, area):
    return [g.message for g in found_gaps if g.area == area]


def test_a_repo_without_config_is_told_to_start(repo):
    (gap,) = gaps.find(repo.context())
    assert gap.area == "config" and "gate init" in gap.suggestion


def test_reports_checks_that_can_never_run_or_are_in_no_profile(repo):
    repo.write("a.py", "x = 1\n")
    text = ('[toolchain]\nlint = "true"\n[tests.unit]\ncommand = "true"\n[[command]]\nname = "e2e"\nrun = "true"\n'
            '[profiles]\nfast = ["syntax"]\nfull = ["syntax"]\n')
    unchecked = messages(found(repo, text), "unchecked")
    assert "`lint` can run here but no profile includes it." in unchecked
    assert "`unit` is defined but no profile runs it." in unchecked
    assert "`e2e` is defined but no profile runs it." in unchecked


def test_reports_missing_tests(repo):
    assert any("No test command" in m for m in messages(found(repo, '[profiles]\nfast = ["syntax"]\n'), "unchecked"))


def test_reports_import_directions_the_code_keeps_but_no_rule_protects(repo):
    repo.write("src/db/index.ts", "export const db = 1\n")
    repo.write("src/modules/billing.ts", 'import { db } from "../db"\n')
    (message,) = messages(found(repo), "boundaries")
    assert "No boundary rules are configured" in message and "`src/db !-> src/modules`" in message

    assert "boundaries" not in areas(found(repo, COVERED + '[boundaries]\nforbid = ["src/db !-> src/modules"]\n'))
    # A broader rule covers the narrower proposal.
    assert "boundaries" not in areas(found(repo, COVERED + '[boundaries]\nforbid = ["src/db !-> src"]\n'))


def test_reports_a_limit_raised_above_the_default_that_nothing_needs_any_more(repo):
    repo.write("a.py", branchy("f", 7))
    raised = COVERED + "[complexity]\nccn_fail = 25\n[size]\nmax_file_lines_fail = 900\n"
    headroom = {g.message.split("`")[1]: g for g in found(repo, raised) if g.area == "headroom"}
    assert set(headroom) == {"[complexity] ccn_fail", "[size] max_file_lines_fail"}
    assert "raised to 25 (the default is 15)" in headroom["[complexity] ccn_fail"].message
    assert "returning it to the default" in headroom["[complexity] ccn_fail"].suggestion


def test_never_suggests_tightening_a_default_just_because_nothing_would_fail(repo):
    # A limit is a judgement about readable code, not a record of today's maximum.
    repo.write("a.py", branchy("f", 2))
    assert "headroom" not in areas(found(repo))
    assert "headroom" not in areas(found(repo, COVERED + "[complexity]\nccn_fail = 12\n"))


def test_a_raised_limit_that_code_still_needs_is_not_reported(repo):
    repo.write("a.py", branchy("needs_it", 19))
    assert "headroom" not in areas(found(repo, COVERED + "[complexity]\nccn_fail = 25\n"))


def test_reports_functions_given_looser_limits_as_a_blind_spot(repo):
    text = COVERED + '[[complexity.override]]\nglob = "**/*.tsx"\nmax_function_lines = 150\nccn_fail = 12\n'
    (message,) = [m for m in messages(found(repo, text), "blind spots") if "tsx" in m]
    assert message == "Functions in files matching `**/*.tsx` are allowed max_function_lines 150."


def test_reports_what_gate_is_told_to_skip(repo):
    repo.write("vendor/lib.py", "x = 1\n")
    repo.write("a.py", "x = 1\n")
    text = COVERED + '[files]\nexclude = ["vendor/**"]\n[[size.override]]\nglob = "**/*_test.py"\nmax_file_lines_fail = 2000\n'
    blind = messages(found(repo, text), "blind spots")
    assert "1 file(s) match `[files] exclude` and are never checked." in blind
    assert "Files matching `**/*_test.py` may reach 2000 lines instead of 500." in blind


def test_reports_overdue_upkeep_and_goes_quiet_once_done(repo):
    repo.write("a.py", "x = 1\n")
    assert messages(found(repo), "upkeep") == ["The rules written out for agents are missing or lag gate.toml."]
    rules.write(repo.root, load(repo.root / "gate.toml"))
    assert "upkeep" not in areas(found(repo))


def test_reports_ci_that_exists_but_does_not_run_gate(repo):
    repo.write(".github/workflows/ci.yml", "steps:\n  - run: npm test\n")
    assert messages(found(repo), "ci") == ["This repo has CI configuration, but none of it runs `gate check`."]
    repo.write(".github/workflows/gate.yml", "steps:\n  - run: gate check --profile full --strict\n")
    assert "ci" not in areas(found(repo))


def test_output_says_what_gate_cannot_see_and_json_round_trips(repo):
    repo.write("a.py", "x = 1\n")
    result = found(repo)
    assert "gate cannot see:" in gaps.to_text(result)
    assert "not the same as none" in gaps.to_text([])
    assert [g["area"] for g in json.loads(gaps.to_json(result))["gaps"]] == [g.area for g in result]


def test_reports_a_repo_with_no_ci_at_all(repo):
    (message,) = messages(found(repo), "ci")
    assert "No CI configuration was found" in message
    repo.write(".gitlab-ci.yml", "test:\n  script: gate check --profile full --strict\n")
    assert "ci" not in areas(found(repo))


def test_reports_tests_that_run_without_changed_line_coverage(repo):
    assert any("nothing checks that they run the code a change adds" in m for m in messages(found(repo), "unchecked"))
    covered = COVERED.replace('"unit"]', '"unit", "coverage"]') + '[coverage]\nreport = "lcov.info"\n'
    assert not any("code a change adds" in m for m in messages(found(repo, covered), "unchecked"))
