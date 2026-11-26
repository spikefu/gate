"""Boundary rules can keep outside packages out of chosen code, for both forbid and only-rules."""

from gate.checks import boundaries
from gate.lang.import_graph import externals_for, matches_package


def found(repo, selected=None):
    return {(f.path, f.line, f.message.split(";")[0]) for f in boundaries.run(repo.context(selected)) if f.rule == "import"}


def names(repo, rel):
    ctx = repo.context()
    return [e.name for e in externals_for(ctx.root, ctx.repo_files, [rel])[rel]]


def test_a_pattern_covers_the_package_and_everything_inside_it_but_not_lookalikes():
    assert matches_package("express", "pkg:express") and matches_package("fs/promises", "pkg:fs")
    assert matches_package("fs", "pkg:node:fs") and matches_package("os.path", "pkg:os")
    assert matches_package("@aws-sdk/client-s3", "pkg:@aws-sdk/*") and matches_package("lodash/fp", "pkg:lodash")
    assert not matches_package("express-session", "pkg:express") and not matches_package("osmium", "pkg:os")
    assert not matches_package("fsevents", "pkg:fs")


def test_javascript_imports_from_outside_are_told_apart_from_the_repos_own(repo):
    repo.write("tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"], "~lib/*": ["./lib/*"]}}}')
    repo.write("packages/shared/package.json", '{"name": "@acme/shared"}')
    repo.write("packages/shared/index.ts", "export const s = 1\n")
    repo.write("src/local.ts", "export const l = 1\n")
    repo.write("src/a.ts", 'import express from "express"\nimport { readFile } from "node:fs/promises"\n'
                           'import type { S3 } from "@aws-sdk/client-s3"\nconst z = require("zod")\n'
                           'const lazy = () => import("lodash/fp")\n'
                           'import { l } from "./local"\nimport { m } from "@/missing"\nimport { s } from "@acme/shared"\n'
                           'import x from "~lib/x"\nimport "./styles.css"\n')
    assert names(repo, "src/a.ts") == ["express", "fs/promises", "@aws-sdk/client-s3", "zod", "lodash/fp"]


def test_python_imports_from_outside_are_told_apart_from_the_repos_own(repo):
    repo.write("app/__init__.py", "")
    repo.write("app/core.py", "x = 1\n")
    repo.write("app/logging.py", "y = 1\n")          # app.logging: does not shadow the standard library
    repo.write("tools.py", "z = 1\n")                # a top-level module of the repo
    repo.write("app/main.py", "import os, subprocess\nimport os.path\nfrom requests.adapters import HTTPAdapter\n"
                              "import logging\nimport tools\nfrom app import core\nfrom . import core\nfrom .core import x\n"
                              "def f():\n    import socket\n")
    assert names(repo, "app/main.py") == ["requests.adapters", "os", "subprocess", "os.path", "logging", "socket"]


def test_a_forbid_rule_keeps_a_package_out_of_a_folder(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\nforbid = ["src/domain !-> pkg:fs", "src/domain !-> pkg:express"]\n')
    repo.write("src/domain/order.ts", '// pure\nimport { readFileSync } from "node:fs"\nimport type { Request } from "express"\n'
                                      'import { z } from "zod"\n')
    repo.write("src/http/server.ts", 'import express from "express"\nimport fs from "fs"\n')
    assert found(repo) == {("src/domain/order.ts", 2, "imports the package fs"),
                           ("src/domain/order.ts", 3, "imports the package express")}
    (finding,) = [f for f in boundaries.run(repo.context()) if f.line == 2]
    assert finding.key == "boundaries:src/domain/order.ts->pkg:fs"
    assert finding.message == "imports the package fs; rule: src/domain !-> pkg:fs"


def test_an_only_rule_confines_a_package_to_its_adapter(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\n[[boundaries.only]]\ntarget = "pkg:pg"\nallowed_from = ["src/db"]\n'
                            'applies_to = ["src"]\nwhy = "one place talks to Postgres"\n')
    repo.write("src/db/pool.ts", 'import { Pool } from "pg"\n')
    repo.write("src/billing/invoice.ts", 'import { Client } from "pg"\nimport { z } from "zod"\n')
    repo.write("scripts/migrate.ts", 'import { Client } from "pg"\n')     # outside applies_to
    ((path, line, _),) = found(repo)
    assert (path, line) == ("src/billing/invoice.ts", 1)
    (finding,) = [f for f in boundaries.run(repo.context()) if f.rule == "import"]
    assert finding.message == "imports the package pg; only src/db may import pkg:pg (one place talks to Postgres)"


def test_python_packages_and_wildcards_work_the_same_way(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\nforbid = ["core !-> pkg:subprocess", "core !-> pkg:google.*"]\n')
    repo.write("core/__init__.py", "")
    repo.write("core/calc.py", "import math\nimport subprocess\nfrom google.cloud import storage\n")
    repo.write("shell/run.py", "import subprocess\n")
    assert {(p, m) for p, _l, m in found(repo)} == {("core/calc.py", "imports the package subprocess"),
                                                    ("core/calc.py", "imports the package google.cloud")}


def test_path_rules_and_package_rules_do_not_interfere_and_only_selected_files_are_judged(repo):
    repo.write("gate.toml", '[boundaries]\ncycles = false\nforbid = ["src/a !-> src/b", "src/a !-> pkg:axios"]\n')
    repo.write("src/b/x.ts", "export const x = 1\n")
    repo.write("src/a/one.ts", 'import { x } from "../b/x"\nimport axios from "axios"\n')
    repo.write("src/a/two.ts", 'import axios from "axios"\n')
    assert {m for _p, _l, m in found(repo)} == {"imports src/b/x.ts", "imports the package axios"}
    assert {p for p, _l, _m in found(repo, ["src/a/two.ts"])} == {"src/a/two.ts"}
