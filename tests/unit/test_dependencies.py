"""Declared packages are read from each ecosystem's manifest, and a name nobody approved is a finding."""

import json

import pytest

from gate import dependencies, gaps, rules
from gate.checks import new_dependencies
from gate.config import load
from gate.findings import Skipped


def names(repo, *files):
    return dependencies.current(dependencies.declared(repo.root, files))


def approve(repo, **approved):
    dependencies.save(repo.root, {eco: sorted(n) for eco, n in approved.items()})


def package(**sections):
    return json.dumps(sections)


def found(repo, selected=None):
    return {(f.symbol, f.path, f.line) for f in new_dependencies.run(repo.context(selected))}


def test_reads_every_section_of_a_package_json(repo):
    repo.write("package.json", package(dependencies={"react": "^19"}, devDependencies={"vitest": "^3"},
                                       peerDependencies={"@scope/pkg": "*"}, optionalDependencies={"fsevents": "*"},
                                       scripts={"not-a-package": "x"}))
    assert names(repo, "package.json") == {"npm": ["@scope/pkg", "fsevents", "react", "vitest"]}


def test_reads_python_manifests_and_normalizes_names(repo):
    repo.write("pyproject.toml", '[build-system]\nrequires = ["hatchling"]\n[project]\n'
               'dependencies = ["Requests>=2", "typing_extensions; python_version < \'3.12\'"]\n'
               '[project.optional-dependencies]\ndev = ["pytest>=8"]\n[dependency-groups]\nlint = ["ruff"]\n'
               '[tool.poetry.dependencies]\npython = "^3.13"\nhttpx = "*"\n')
    repo.write("requirements-dev.txt", "# tools\n-r requirements.txt\nblack==24.1  # formatter\nZope.Interface\n\n")
    repo.write("requirements/prod.txt", "gunicorn\n")
    repo.write("notes.txt", "not-a-requirement\n")
    assert names(repo, "pyproject.toml", "requirements-dev.txt", "requirements/prod.txt", "notes.txt") == {
        "pypi": ["black", "gunicorn", "hatchling", "httpx", "pytest", "requests", "ruff", "typing-extensions", "zope-interface"]}


def test_reads_go_rust_and_ruby_manifests(repo):
    repo.write("go.mod", "module example.com/app\n\ngo 1.23\n\nrequire github.com/a/one v1.0.0\n\nrequire (\n"
                         "\tgithub.com/b/two v2.0.0\n\tgithub.com/c/pulled-in v0.1.0 // indirect\n)\n")
    repo.write("Cargo.toml", '[package]\nname = "app"\n[dependencies]\nserde = "1"\n[dev-dependencies]\nrstest = "0.2"\n'
                             '[target."cfg(unix)".dependencies]\nlibc = "0.2"\n')
    repo.write("Gemfile", "source \"https://rubygems.org\"\ngem \"rails\", \"~> 8\"\n  gem 'puma'\n")
    assert names(repo, "go.mod", "Cargo.toml", "Gemfile") == {
        "cargo": ["libc", "rstest", "serde"], "go": ["github.com/a/one", "github.com/b/two"], "rubygems": ["puma", "rails"]}


def test_a_broken_manifest_declares_nothing_rather_than_crashing(repo):
    repo.write("package.json", "{ not json")
    repo.write("pyproject.toml", "[project\n")
    assert names(repo, "package.json", "pyproject.toml") == {}


def test_the_check_says_it_cannot_run_until_someone_has_approved_a_list(repo):
    repo.write("package.json", package(dependencies={"react": "^19"}))
    with pytest.raises(Skipped, match="gate dependencies --accept"):
        new_dependencies.run(repo.context())


def test_a_new_package_fails_and_points_at_its_line(repo):
    repo.write("package.json", json.dumps({"dependencies": {"react": "^19", "left-pad": "^1"}}, indent=2))
    approve(repo, npm=["react"])
    (finding,) = new_dependencies.run(repo.context())
    assert (finding.symbol, finding.path, finding.line, finding.severity) == ("left-pad", "package.json", 4, "fail")
    assert finding.key == "new-dependencies/unapproved:npm:left-pad"
    assert "exists under exactly this name" in finding.message


def test_changing_a_version_or_removing_a_package_is_not_a_finding(repo):
    approve(repo, npm=["react", "gone"])
    repo.write("package.json", package(dependencies={"react": "^20"}))
    assert found(repo) == set()


def test_approval_is_per_ecosystem_and_covers_every_manifest(repo):
    approve(repo, npm=["react"], pypi=["requests"])
    repo.write("apps/a/package.json", package(dependencies={"react": "^19"}))
    repo.write("apps/b/package.json", package(dependencies={"react": "^19", "requests": "*"}))
    repo.write("requirements.txt", "requests\nreact\n")
    assert {(name, path) for name, path, _line in found(repo)} == {("requests", "apps/b/package.json"), ("react", "requirements.txt")}


def test_only_the_selected_manifests_are_judged(repo):
    approve(repo, npm=[])
    repo.write("apps/a/package.json", package(dependencies={"old-unapproved": "*"}))
    repo.write("apps/b/package.json", package(dependencies={"new-unapproved": "*"}))
    assert {name for name, _p, _l in found(repo, ["apps/b/package.json", "apps/b/src/x.ts"])} == {"new-unapproved"}


def test_gaps_reports_a_scheduled_check_with_nothing_approved(repo):
    repo.write("gate.toml", "")
    repo.write("package.json", package(dependencies={"react": "^19", "zod": "^4"}))
    upkeep = [g.message for g in gaps.find(repo.context()) if g.area == "upkeep"]
    assert any("2 package(s) are declared and none has been approved" in m for m in upkeep)
    approve(repo, npm=["react", "zod"])
    assert not any("approved" in g.message for g in gaps.find(repo.context()))


def test_prose_tells_agents_not_to_add_or_approve_packages(repo):
    repo.write("gate.toml", "")
    block = rules.render(load(repo.root / "gate.toml"))
    assert "### Dependencies" in block and "Never run `gate dependencies --accept`" in block
    repo.write("gate.toml", '[profiles]\nfast = ["syntax"]\nfull = ["syntax"]\n')
    assert "### Dependencies" not in rules.render(load(repo.root / "gate.toml"))
