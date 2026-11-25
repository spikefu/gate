"""Boundary rules, through relative imports, tsconfig aliases and workspace packages."""

import json

from gate.checks import boundaries
from gate.lang.js_resolve import strip_jsonc


def violations(repo, selected=None):
    return {(f.path, f.message.split(";")[0]) for f in boundaries.run(repo.context(selected))}


def test_forbid_rule_on_relative_imports(repo):
    repo.write("gate.toml", '[boundaries]\nforbid = ["src/domain !-> src/infra"]\n')
    repo.write("src/infra/db.ts", "export const db = 1\n")
    repo.write("src/domain/order.ts", 'import { db } from "../infra/db"\n')
    repo.write("src/app/main.ts", 'import { db } from "../infra/db"\n')
    assert violations(repo) == {("src/domain/order.ts", "imports src/infra/db.ts")}


def test_only_rule_sees_tsconfig_alias_despite_comment_like_pattern(repo):
    # Ports and adapters: database access only inside src/modules.
    repo.write("gate.toml", '[[boundaries.only]]\ntarget = "src/db"\nallowed_from = ["src/modules"]\nwhy = "ports"\n')
    repo.write("tsconfig.json", '{\n  // aliases\n  "compilerOptions": { "paths": { "@/*": ["./src/*"] }, },\n}\n')
    repo.write("src/db/index.ts", 'export const db = 1\nexport * from "./schema"\n')
    repo.write("src/db/schema.ts", "export const t = 1\n")
    repo.write("src/modules/billing.ts", 'import { db } from "@/db"\n')
    repo.write("src/actions/pay.ts", 'import { t } from "@/db/schema"\n')
    repo.write("src/app/page.tsx", 'const m = await import("@/db")\n')
    found = boundaries.run(repo.context())
    assert {f.path for f in found} == {"src/actions/pay.ts", "src/app/page.tsx"}
    assert all("ports" in f.message for f in found)


def test_only_rule_can_be_limited_to_some_importers(repo):
    repo.write("gate.toml", '[[boundaries.only]]\ntarget = "src/db"\nallowed_from = ["src/modules"]\napplies_to = ["src"]\n')
    repo.write("src/db/index.ts", "export const db = 1\n")
    repo.write("src/app/page.ts", 'import { db } from "../db"\n')
    repo.write("tests/db.test.ts", 'import { db } from "../src/db"\n')
    assert {p for p, _ in violations(repo)} == {"src/app/page.ts"}


def test_workspace_package_import_crosses_boundary(repo):
    repo.write("gate.toml", '[boundaries]\nforbid = ["packages/shared !-> apps"]\n')
    repo.write("package.json", json.dumps({"name": "root", "workspaces": ["apps/*", "packages/*"]}))
    repo.write("apps/server/package.json", json.dumps({"name": "@acme/server"}))
    repo.write("apps/server/src/index.ts", "export const s = 1\n")
    repo.write("packages/shared/package.json", json.dumps({"name": "@acme/shared"}))
    repo.write("packages/shared/src/bad.ts", 'import { s } from "@acme/server"\n')
    repo.write("packages/shared/src/ok.ts", 'import x from "react"\n')
    assert violations(repo) == {("packages/shared/src/bad.ts", "imports apps/server/src/index.ts")}


def test_ts_source_importing_js_extension_resolves_to_ts_file(repo):
    repo.write("gate.toml", '[boundaries]\nforbid = ["a !-> b"]\n')
    repo.write("b/x.ts", "export const x = 1\n")
    repo.write("a/y.ts", 'import { x } from "../b/x.js"\n')
    assert violations(repo) == {("a/y.ts", "imports b/x.ts")}


def test_python_absolute_and_relative_imports_in_src_layout(repo):
    repo.write("gate.toml", '[boundaries]\nforbid = ["src/app/domain !-> src/app/infra"]\n')
    repo.write("src/app/infra/db.py", "x = 1\n")
    repo.write("src/app/domain/a.py", "from app.infra import db\n")
    repo.write("src/app/domain/b.py", "from ..infra.db import x\n")
    repo.write("src/app/domain/c.py", "import json\n")
    assert {p for p, _ in violations(repo)} == {"src/app/domain/a.py", "src/app/domain/b.py"}


def test_scoped_run_only_reports_selected_sources(repo):
    repo.write("gate.toml", '[boundaries]\nforbid = ["a !-> b"]\n')
    repo.write("b/x.ts", "export const x = 1\n")
    repo.write("a/old.ts", 'import { x } from "../b/x"\n')
    repo.write("a/new.ts", 'import { x } from "../b/x"\n')
    assert {p for p, _ in violations(repo, ["a/new.ts"])} == {"a/new.ts"}


def test_strip_jsonc_keeps_comment_openers_inside_strings():
    text = '{ "a": "@/*", /* gone */ "b": "http://x", // gone\n "c": [1, 2,], }'
    assert json.loads(strip_jsonc(text)) == {"a": "@/*", "b": "http://x", "c": [1, 2]}
