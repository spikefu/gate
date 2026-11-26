"""A folder declared a module may be imported only through its entry point by code outside it."""

from gate import rules
from gate.checks import boundaries
from gate.config import load

RULE = '[boundaries]\ncycles = false\n[[boundaries.module]]\npath = "src/modules/*"\n'


def found(repo, selected=None):
    return {(f.path, f.message.split(";")[0]) for f in boundaries.run(repo.context(selected)) if f.rule == "import"}


def billing(repo):
    repo.write("src/modules/billing/index.ts", 'export { charge } from "./charge"\n')
    repo.write("src/modules/billing/charge.ts", 'import { rate } from "./internal/rates"\nexport const charge = rate\n')
    repo.write("src/modules/billing/internal/rates.ts", "export const rate = 1\n")


def test_code_outside_a_module_may_use_its_entry_but_not_what_is_behind_it(repo):
    repo.write("gate.toml", RULE)
    billing(repo)
    repo.write("src/app/ok.ts", 'import { charge } from "../modules/billing"\nimport { c } from "../modules/billing/index"\n')
    repo.write("src/app/bad.ts", 'import { charge } from "../modules/billing/charge"\n'
                                 'import type { R } from "../modules/billing/internal/rates"\n')
    repo.write("src/modules/orders/index.ts", 'import { rate } from "../billing/internal/rates"\n')
    assert found(repo) == {("src/app/bad.ts", "imports src/modules/billing/charge.ts"),
                           ("src/app/bad.ts", "imports src/modules/billing/internal/rates.ts"),
                           ("src/modules/orders/index.ts", "imports src/modules/billing/internal/rates.ts")}
    (finding,) = [f for f in boundaries.run(repo.context()) if f.path == "src/modules/orders/index.ts"]
    assert finding.message.endswith("reaches inside the module src/modules/billing: import it through its entry point")
    assert finding.key == "boundaries:src/modules/orders/index.ts->src/modules/billing/internal/rates.ts"


def test_a_modules_own_files_and_tests_may_reach_its_internals(repo):
    repo.write("gate.toml", RULE)
    billing(repo)   # charge.ts imports ./internal/rates from inside the module
    repo.write("tests/billing/rates.test.ts", 'import { rate } from "../../src/modules/billing/internal/rates"\n')
    repo.write("src/modules/billing/charge.test.ts", 'import { charge } from "./charge"\n')
    assert found(repo) == set()


def test_named_entry_files_replace_the_default(repo):
    repo.write("gate.toml", RULE + 'entry = ["api.ts", "types/*.ts"]\nwhy = "the rest may change freely"\n')
    repo.write("src/modules/billing/api.ts", "export const a = 1\n")
    repo.write("src/modules/billing/index.ts", "export const i = 1\n")
    repo.write("src/modules/billing/types/money.ts", "export type M = number\n")
    repo.write("src/app/a.ts", 'import { a } from "../modules/billing/api"\nimport type { M } from "../modules/billing/types/money"\n'
                               'import { i } from "../modules/billing"\n')
    (finding,) = [f for f in boundaries.run(repo.context()) if f.rule == "import"]
    assert finding.message == ("imports src/modules/billing/index.ts; reaches inside the module src/modules/billing: "
                               "import it through api.ts, types/*.ts (the rest may change freely)")


def test_a_single_folder_can_be_a_module_and_other_folders_are_untouched(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\n[[boundaries.module]]\npath = "src/db"\n')
    repo.write("src/db/index.ts", "export const db = 1\n")
    repo.write("src/db/schema.ts", "export const t = 1\n")
    repo.write("src/dbx/deep/thing.ts", "export const x = 1\n")
    repo.write("src/lib/deep/util.ts", "export const u = 1\n")
    repo.write("src/app.ts", 'import { t } from "./db/schema"\nimport { x } from "./dbx/deep/thing"\nimport { u } from "./lib/deep/util"\n')
    assert found(repo) == {("src/app.ts", "imports src/db/schema.ts")}


def test_python_packages_are_entered_through_their_init(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\n[[boundaries.module]]\npath = "app/*"\n')
    repo.write("app/__init__.py", "")
    repo.write("app/billing/__init__.py", "from .charge import charge\n")
    repo.write("app/billing/charge.py", "charge = 1\n")
    repo.write("app/orders/__init__.py", "")
    repo.write("app/orders/ok.py", "from app.billing import charge\n")
    repo.write("app/orders/bad.py", "from app.billing.charge import charge\nimport app.billing.charge\n")
    repo.write("app/orders/relative.py", "from ..billing.charge import charge\nfrom ..billing import charge as c\n")
    # `from app.billing import charge` may be the name __init__ exports, so it is read as using the entry.
    assert found(repo) == {("app/orders/bad.py", "imports app/billing/charge.py"),
                           ("app/orders/relative.py", "imports app/billing/charge.py")}
    assert len([f for f in boundaries.run(repo.context()) if f.rule == "import"]) == 3


def test_a_workspace_package_imported_by_name_is_entered_properly(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\n[[boundaries.module]]\npath = "packages/*"\nentry = ["src/index.ts"]\n')
    repo.write("packages/shared/package.json", '{"name": "@acme/shared", "main": "src/index.ts"}')
    repo.write("packages/shared/src/index.ts", "export const s = 1\n")
    repo.write("packages/shared/src/secret.ts", "export const k = 1\n")
    repo.write("apps/web/ok.ts", 'import { s } from "@acme/shared"\n')
    repo.write("apps/web/bad.ts", 'import { k } from "@acme/shared/src/secret"\n')
    assert {p for p, _m in found(repo)} == {"apps/web/bad.ts"}


def test_only_selected_files_are_judged_and_the_prose_states_the_rule(repo):
    repo.write("gate.toml", RULE)
    billing(repo)
    repo.write("src/app/old.ts", 'import { charge } from "../modules/billing/charge"\n')
    repo.write("src/app/new.ts", 'import { charge } from "../modules/billing/charge"\n')
    assert {p for p, _m in found(repo, ["src/app/new.ts"])} == {"src/app/new.ts"}
    block = rules.render(load(repo.root / "gate.toml"))
    assert ("- `src/modules/*` is a module: code outside it may import only its entry point (`index.*` or `__init__.py`), "
            "never the files behind it.") in block


def test_a_sibling_whose_name_starts_the_same_is_still_outside(repo):
    repo.write("gate.toml", RULE)
    billing(repo)
    repo.write("src/modules/billing-extras/index.ts", 'import { charge } from "../billing/charge"\n')
    assert found(repo) == {("src/modules/billing-extras/index.ts", "imports src/modules/billing/charge.ts")}


def test_only_the_modules_top_level_index_is_its_entry(repo):
    repo.write("gate.toml", RULE)
    billing(repo)
    repo.write("src/modules/billing/internal/index.ts", "export const deep = 1\n")
    repo.write("src/modules/billing/reindex.ts", "export const r = 1\n")
    repo.write("src/app/a.ts", 'import { deep } from "../modules/billing/internal"\nimport { r } from "../modules/billing/reindex"\n')
    assert found(repo) == {("src/app/a.ts", "imports src/modules/billing/internal/index.ts"),
                           ("src/app/a.ts", "imports src/modules/billing/reindex.ts")}


def test_naming_a_python_subpackage_is_reaching_inside(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\n[[boundaries.module]]\npath = "app/*"\n')
    for package in ("app", "app/billing", "app/billing/internal", "app/orders"):
        repo.write(f"{package}/__init__.py", "")
    repo.write("app/orders/bad.py", "from app.billing.internal import rates\n")
    assert found(repo) == {("app/orders/bad.py", "imports app/billing/internal/__init__.py")}


def test_a_workspace_package_whose_entry_file_cannot_be_pinned_down_is_not_a_finding(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\n[[boundaries.module]]\npath = "packages/*"\n')
    repo.write("packages/shared/package.json", '{"name": "@acme/shared", "main": "dist/built.js"}')
    repo.write("packages/shared/lib/code.ts", "export const s = 1\n")
    repo.write("apps/web/ok.ts", 'import { s } from "@acme/shared"\n')
    assert found(repo) == set()
