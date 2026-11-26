"""`gate metrics` reports facts about files and folders and judges nothing."""

from gate import metrics


def collect(repo, selected=None):
    report = metrics.collect(repo.context(selected))
    return {f["path"]: f for f in report["files"]}, {f["path"]: f for f in report["folders"]}


def layout(repo):
    repo.write("src/billing/index.ts", 'export { charge } from "./charge"\n')
    repo.write("src/billing/charge.ts", 'import Stripe from "stripe"\nimport { rate } from "./rates"\nlet client = null\n'
                                        "export function charge(x: number) {\n  if (x > 1) { return rate }\n  return 0\n}\n"
                                        "export const LIMIT = 5\n")
    repo.write("src/billing/rates.ts", "export const rate = 1\n")
    repo.write("src/app/main.ts", 'import { charge } from "../billing"\nimport { rate } from "../billing/rates"\n'
                                  'import fs from "node:fs"\nexport const run = () => charge(rate)\n')
    repo.write("src/app/main.test.ts", 'import { rate } from "../billing/rates"\nimport { run } from "./main"\n')


def test_each_file_reports_its_size_shape_and_coupling(repo):
    layout(repo)
    files, _ = collect(repo)
    charge = files["src/billing/charge.ts"]
    assert (charge["lines"], charge["functions"], charge["max_complexity"]) == (8, 1, 2)
    assert (charge["imports"], charge["imported_by"]) == (1, 1), "imports rates; imported by index"
    assert charge["packages"] == ["stripe"] and charge["definitions"] == 2 and charge["shared_state"] == 1
    assert files["src/app/main.ts"]["packages"] == ["fs"] and files["src/app/main.ts"]["imports"] == 2
    assert files["src/billing/rates.ts"]["imported_by"] == 2, "charge and main; the test file does not count"
    assert "src/app/main.test.ts" not in files


def test_each_folder_reports_what_depends_on_it_and_whether_its_entry_is_used(repo):
    layout(repo)
    _, folders = collect(repo)
    billing, app = folders["src/billing"], folders["src/app"]
    assert (billing["files"], billing["has_entry"], billing["imports_past_entry"]) == (3, True, 1)
    assert (billing["depends_on_folders"], billing["depended_on_by_folders"], billing["instability"]) == (0, 1, 0.0)
    assert (app["depends_on_folders"], app["depended_on_by_folders"], app["instability"]) == (1, 0, 1.0)
    assert app["has_entry"] is False and app["imports_past_entry"] == 0


def test_a_selection_reports_those_files_with_dependents_from_the_whole_repo(repo):
    layout(repo)
    files, folders = collect(repo, ["src/billing/rates.ts"])
    assert set(files) == {"src/billing/rates.ts"} and files["src/billing/rates.ts"]["imported_by"] == 2
    assert set(folders) == {"src/billing"}


def test_every_import_between_product_files_is_listed_as_an_edge(repo):
    layout(repo)
    assert metrics.collect(repo.context())["edges"] == [
        ["src/app/main.ts", "src/billing/index.ts"], ["src/app/main.ts", "src/billing/rates.ts"],
        ["src/billing/charge.ts", "src/billing/rates.ts"], ["src/billing/index.ts", "src/billing/charge.ts"],
    ]
    assert metrics.collect(repo.context(["src/billing/rates.ts"]))["edges"] == [], "edges start at the files reported"


def test_a_file_whose_imports_gate_cannot_read_says_so(repo):
    repo.write("src/a.ts", "export const a = 1\n")
    repo.write("cmd/main.go", 'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println(1) }\n')
    files, _ = collect(repo)
    assert files["src/a.ts"]["imports_read"] is True and files["cmd/main.go"]["imports_read"] is False


def test_python_files_report_definitions_and_globals(repo):
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/core.py", "import os\nfrom pkg import helpers\n\ncount = 0\n\n\ndef a():\n    global count\n    count += 1\n\n\n"
                              "class B:\n    pass\n")
    repo.write("pkg/helpers.py", "x = 1\n")
    files, folders = collect(repo)
    core = files["pkg/core.py"]
    assert (core["definitions"], core["shared_state"], core["packages"], core["imports"]) == (2, 1, ["os"], 2)
    assert folders["pkg"]["instability"] is None, "nothing crosses the folder, so there is no figure"


def test_the_text_report_ranks_and_says_it_is_not_a_verdict(repo):
    layout(repo)
    text = metrics.to_text(metrics.collect(repo.context()), top=1)
    assert "gate metrics: 4 file(s), 2 folder(s); test files left out" in text
    assert "most depended on\n      2  src/billing/rates.ts" in text
    assert "shared state\n      1  src/billing/charge.ts" in text
    assert "folders imported past their entry point (imports, has entry)\n      1  src/billing  entry" in text
    assert text.endswith("these are facts, not findings: what they mean for the design is for a reader to judge")


def test_a_python_package_has_its_init_as_entry_and_non_file_import_targets_are_ignored(repo):
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/core.py", "x = 1\n")
    repo.write("packages/shared/package.json", '{"name": "@acme/shared", "main": "dist/built.js"}')
    repo.write("packages/shared/lib/code.ts", "export const s = 1\n")
    repo.write("apps/web/a.ts", 'import { s } from "@acme/shared"\nimport { t } from "./a.test"\n')
    repo.write("apps/web/a.test.ts", "export const t = 1\n")
    files, folders = collect(repo)
    assert folders["pkg"]["has_entry"] is True
    assert files["apps/web/a.ts"]["imports"] == 0, "a bare package folder and a test file are not product files"


def test_an_import_that_is_only_a_type_or_made_later_is_listed_as_deferred(repo):
    repo.write("src/a.ts", 'import type { B } from "./b"\nimport { c } from "./c"\nimport type { C } from "./c"\nexport const a = c\n')
    repo.write("src/b.ts", 'export type B = number\nexport const load = () => import("./c")\n')
    repo.write("src/c.ts", "export const c = 1\nexport type C = number\n")
    report = metrics.collect(repo.context())
    assert report["edges"] == [["src/a.ts", "src/b.ts"], ["src/a.ts", "src/c.ts"], ["src/b.ts", "src/c.ts"]]
    assert report["deferred_edges"] == [["src/a.ts", "src/b.ts"], ["src/b.ts", "src/c.ts"]], "a imports c for real as well as for a type"


def test_each_function_is_listed_with_its_lines_including_one_inside_another(repo):
    repo.write("src/make.ts", "export function make() {\n  const add = (a: number) => {\n    return a + 1\n  }\n"
                              "  return {\n    run(x: number) {\n      if (x > 1) { return add(x) }\n      return 0\n    },\n  }\n}\n")
    files, _ = collect(repo)
    listed = files["src/make.ts"]["function_list"]
    assert [(f["name"], f["line"], f["end_line"]) for f in listed] == [("make", 1, 11), ("add", 2, 4), ("run", 6, 9)]
    assert [f["complexity"] for f in listed] == [1, 1, 2] and files["src/make.ts"]["functions"] == 3
