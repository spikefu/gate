"""`gate init` only records what it can observe; `gate todo` ranks the baseline worst first."""

import json

from gate import init, propose, todo
from gate.config import load
from gate.checks import boundaries
from gate.findings import Finding


def baselined(path, rule, value, limit, symbol=None):
    return Finding(check="complexity", rule=rule, key=f"{rule}:{path}::{symbol}", path=path, symbol=symbol,
                   value=value, limit=limit, message=f"{rule} {value} > {limit}: fix", baselined=True)


def test_todo_ranks_files_by_total_excess_then_path():
    found = [
        baselined("mild.py", "ccn", 16, 15, "a"),
        baselined("bad.py", "ccn", 45, 15, "a"),
        baselined("bad.py", "ccn", 20, 15, "b"),
        baselined("many.py", "ccn", 30, 15, "a"),
        baselined("many.py", "function-lines", 240, 120, "a"),
    ]
    queue = todo.queue(found)
    assert [t.path for t in queue] == ["bad.py", "many.py", "mild.py"]
    assert [f.symbol for f in queue[0].findings] == ["a", "b"], "worst finding first within a file"
    assert queue[0].score == round((45 / 15 - 1) + (20 / 15 - 1), 2)


def test_todo_text_reports_hidden_files_and_stale_entries():
    queue = todo.queue([baselined("a.py", "ccn", 30, 15, "f"), baselined("b.py", "ccn", 20, 15, "g")])
    text = todo.to_text(queue, queue[:1], stale=3)
    assert "2 baselined violation(s) in 2 file(s)" in text
    assert "b.py" not in text and "1 more file(s)" in text
    assert "3 recorded violation(s) are already fixed" in text


def test_init_uses_the_scripts_a_node_repo_already_defines(repo):
    repo.write("package.json", json.dumps({"scripts": {
        "lint": "eslint", "typecheck": "tsc", "test": "vitest run", "test:e2e": "playwright test", "dev": "x"}}))
    repo.write("pnpm-lock.yaml", "")
    repo.write("src/a.ts", "export const a = 1\n")
    plan = init.plan(repo.context())
    assert plan.toolchain == {"lint": "pnpm run lint", "typecheck": "pnpm run typecheck"}
    assert plan.tests == {"unit": "pnpm run test"}
    assert plan.commands == {"e2e": "pnpm run test:e2e"}
    assert plan.fast[-1] == "lint"
    assert plan.full[-2:] == ["typecheck", "unit"]
    assert "e2e" not in plan.full, "e2e needs a running app, so it is offered but not scheduled"


def test_init_detects_pytest_and_invents_nothing_else(repo):
    repo.write("pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    repo.write("pkg/a.py", "x = 1\n")
    plan = init.plan(repo.context())
    assert plan.tests == {"unit": "python -m pytest -q"}
    assert plan.toolchain == {} and plan.commands == {}


def test_init_on_an_empty_repo_still_gives_structural_checks(repo):
    plan = init.plan(repo.context())
    assert plan.fast == ["syntax", "complexity", "size", "boundaries"]
    assert plan.tests == {} and plan.boundaries == []


def layered(repo):
    repo.write("src/db/index.ts", "export const db = 1\n")
    repo.write("src/modules/billing.ts", 'import { db } from "../db"\n')
    repo.write("src/app/page.ts", 'import { b } from "../modules/billing"\n')
    repo.write("src/lib/util.ts", "export const u = 1\n")
    repo.write("tests/db.test.ts", 'import { db } from "../src/db"\n')


def test_proposes_one_way_rules_one_level_down_and_ignores_tests(repo):
    layered(repo)
    ctx = repo.context()
    rules = propose.boundary_rules(ctx.root, ctx.repo_files)
    assert rules == ["src/db !-> src/modules", "src/modules !-> src/app"]


def test_workspace_proposals_keep_shared_code_importable(repo):
    for name in ("client", "server"):
        repo.write(f"apps/{name}/package.json", json.dumps({"name": f"@o/{name}"}))
        repo.write(f"apps/{name}/src/index.ts", 'import { s } from "@o/shared"\n')
    repo.write("apps/desktop/package.json", json.dumps({"name": "@o/desktop"}))
    repo.write("apps/desktop/src/main.ts", "export const d = 1\n")
    repo.write("packages/shared/package.json", json.dumps({"name": "@o/shared"}))
    repo.write("packages/shared/src/index.ts", "export const s = 1\n")
    ctx = repo.context()
    rules = propose.boundary_rules(ctx.root, ctx.repo_files)
    assert "packages/shared !-> apps/client" in rules
    assert "apps/client !-> apps/server" in rules and "apps/server !-> apps/client" in rules
    assert "apps/desktop !-> packages/shared" not in rules, "desktop may start using shared tomorrow"


def test_rendered_config_loads_and_its_rules_hold_today(repo):
    layered(repo)
    repo.write("package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    text = init.render(init.plan(repo.context()))
    repo.write("gate.toml", text)
    config = load(repo.root / "gate.toml")
    assert config.tests["unit"].run == "npm run test --silent"
    assert len(config.boundaries.forbid) == 2
    assert boundaries.run(repo.context()) == []
