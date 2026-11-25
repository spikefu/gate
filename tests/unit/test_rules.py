"""`gate rules`: the prose says what the config enforces, and only gate's section is ever touched."""

from gate import rules
from gate.config import load


def config(repo, text=""):
    repo.write("gate.toml", text)
    return load(repo.root / "gate.toml")


def test_prose_states_the_configured_limits_not_the_defaults(repo):
    block = rules.render(config(repo, "[complexity]\nccn_fail = 8\nccn_warn = 6\nmax_parameters = 3\n"
                                      "[size]\nmax_file_lines_fail = 200\nmax_file_lines_warn = 150\n"
                                      '[[size.override]]\nglob = "**/*_test.py"\nmax_file_lines_fail = 900\n'))
    assert "cyclomatic complexity at most 8 (warns above 6), at most 120 lines, at most 3 parameters" in block
    assert "Files: at most 200 lines (warns above 150)." in block
    assert "Files matching `**/*_test.py`: at most 900 lines." in block


def test_prose_states_both_kinds_of_boundary_and_omits_the_heading_when_there_are_none(repo):
    block = rules.render(config(repo, '[boundaries]\nforbid = ["src/domain !-> src/infra"]\n'
                                      '[[boundaries.only]]\ntarget = "src/db"\nallowed_from = ["src/modules"]\n'
                                      'applies_to = ["src"]\nwhy = "Ports and adapters."\n'))
    assert "- `src/domain` must not import `src/infra`." in block
    assert "- Only `src/modules` may import `src/db` (applies to code under `src`). Ports and adapters." in block
    assert "### Boundaries" not in rules.render(config(repo))


def test_prose_names_profiles_and_the_commands_behind_checks(repo):
    block = rules.render(config(repo, '[toolchain]\nlint = "npm run lint"\n[tests.unit]\ncommand = "npm test"\n'
                                      '[profiles]\nfast = ["syntax", "lint"]\nfull = ["syntax", "lint", "unit"]\n'))
    assert "- `gate check --profile fast` (default): syntax, lint." in block
    assert "- `gate check --profile full`: syntax, lint, unit." in block
    assert "- `lint` runs `npm run lint`." in block and "- `unit` runs `npm test`." in block


def test_apply_replaces_only_gates_section_and_keeps_the_authors_text():
    old = "# Project\n\nMine.\n\n" + rules.START + "\nold rules\n" + rules.END + "\n\n## After\n\nAlso mine.\n"
    new = rules.apply(old, rules.START + "\nnew rules\n" + rules.END + "\n")
    assert new == "# Project\n\nMine.\n\n" + rules.START + "\nnew rules\n" + rules.END + "\n\n## After\n\nAlso mine.\n"


def test_apply_appends_to_a_file_without_a_section_and_fills_an_empty_one():
    block = rules.START + "\nr\n" + rules.END + "\n"
    assert rules.apply("# Project\n\nMine.\n", block) == "# Project\n\nMine.\n\n" + block
    assert rules.apply("", block) == block


def test_writes_whichever_instruction_files_exist_and_agents_md_when_none_do(repo):
    cfg = config(repo)
    assert [p.name for p in rules.write(repo.root, cfg)] == ["AGENTS.md"]

    (repo.root / "AGENTS.md").unlink()
    repo.write("CLAUDE.md", "# Notes\n")
    assert [p.name for p in rules.write(repo.root, cfg)] == ["CLAUDE.md"]
    assert not (repo.root / "AGENTS.md").exists()
    assert (repo.root / "CLAUDE.md").read_text().startswith("# Notes\n\n" + rules.START)


def test_configured_targets_win_and_writing_twice_changes_nothing(repo):
    cfg = config(repo, '[rules]\ntargets = ["docs/AI.md"]\n')
    repo.write("docs/AI.md", "intro\n")
    assert [p.name for p in rules.write(repo.root, cfg)] == ["AI.md"]
    first = (repo.root / "docs/AI.md").read_text()
    assert rules.write(repo.root, cfg) == []
    assert (repo.root / "docs/AI.md").read_text() == first


def test_stale_when_the_config_changes_after_the_prose_was_written(repo):
    rules.write(repo.root, config(repo))
    assert rules.stale(repo.root, config(repo)) == []
    assert [p.name for p in rules.stale(repo.root, config(repo, "[complexity]\nccn_fail = 9\n"))] == ["AGENTS.md"]


def test_prose_invites_the_agent_to_challenge_the_rules_and_keeps_the_decision_with_the_human(repo):
    block = rules.render(config(repo))
    assert "### Improving these rules" in block
    assert "you are expected to challenge them" in block
    assert "Run `gate gaps`" in block
    assert "even when nobody asked" in block
    assert "Propose; do not apply." in block
