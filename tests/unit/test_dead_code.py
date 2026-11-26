"""A definition is dead when its name appears once in the whole repo; anything less certain is left alone."""

from gate import baseline
from gate.checks import dead_code

USES = 'import { used } from "./lib"\nexport const app = used()\n'


def found(repo, selected=None):
    return {(f.path, f.symbol) for f in dead_code.run(repo.context(selected))}


def test_an_export_nothing_refers_to_is_reported_with_its_kind_and_line(repo):
    repo.write("src/lib.ts", "export function used() { return 1 }\n\nexport function abandoned() { return 2 }\n"
                             "export const LEFTOVER = 3\nexport interface Orphan { a: number }\nexport type Stray = string\n")
    repo.write("src/app.ts", USES)
    findings = {f.symbol: f for f in dead_code.run(repo.context())}
    assert set(findings) == {"abandoned", "LEFTOVER", "Orphan", "Stray"}
    assert (findings["abandoned"].line, findings["abandoned"].key) == (3, "dead-code/unused:src/lib.ts::abandoned")
    assert findings["abandoned"].message.startswith("function `abandoned` is never referred to")
    assert findings["LEFTOVER"].message.startswith("constant `LEFTOVER`")


def test_any_other_mention_anywhere_counts_as_a_use(repo):
    repo.write("src/lib.ts", "export function used() {}\nexport function a() {}\nexport function b() {}\n"
                             "export function c() {}\nexport function d() {}\nexport function e() { return e }\n")
    repo.write("src/app.ts", USES + "import * as lib from \"./lib\"\nlib.a()\n")
    repo.write("tests/lib.test.ts", "b()\n")                    # only a test uses it
    repo.write("config/routes.json", '{"handler": "c"}\n')      # named in configuration
    repo.write("docs/api.md", "Call `d` to begin.\n")           # named in a document
    assert found(repo) == set()                                  # and `e` refers to itself


def test_a_file_loaded_by_convention_is_not_judged(repo):
    # Nothing imports a route or page; the framework decides what it must export.
    repo.write("src/app/users/route.ts", "export async function GET() {}\nexport const dynamic = \"force-dynamic\"\n")
    repo.write("src/lib.ts", "export function used() {}\n")
    repo.write("src/app.ts", USES)
    assert found(repo) == set()


def test_a_module_re_exported_wholesale_is_not_judged(repo):
    repo.write("pkg/src/shapes.ts", "export function circle() {}\nexport function square() {}\n")
    repo.write("pkg/src/index.ts", 'export * from "./shapes"\n')
    assert found(repo) == set()


def test_default_exports_and_non_exported_code_are_not_judged(repo):
    repo.write("src/lib.ts", "export function used() {}\nfunction internalOnly() {}\nexport default function Widget() {}\n")
    repo.write("src/app.ts", USES)
    assert found(repo) == set()


def test_python_functions_and_classes_are_judged_in_imported_modules(repo):
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/core.py", "def used():\n    return 1\n\n\ndef abandoned():\n    return 2\n\n\nclass Leftover:\n    pass\n\n\n"
                              "def _private_and_unused():\n    pass\n\n\nasync def stale():\n    pass\n")
    repo.write("pkg/app.py", "from pkg.core import used\n\nused()\n")
    assert found(repo) == {("pkg/core.py", "abandoned"), ("pkg/core.py", "Leftover"),
                           ("pkg/core.py", "_private_and_unused"), ("pkg/core.py", "stale")}


def test_python_definitions_something_else_may_call_are_left_alone(repo):
    repo.write("pkg/__init__.py", "")
    repo.write("pkg/core.py", "def used():\n    pass\n\n\n@app.route(\"/x\")\ndef registered():\n    pass\n\n\n"
                              "@decorator\n\nclass Registered:\n    def method_nobody_calls(self):\n        pass\n\n\n"
                              "def __getattr__(name):\n    pass\n")
    repo.write("pkg/star.py", "def forwarded():\n    pass\n")
    repo.write("pkg/plugin.py", "def loaded_by_name():\n    pass\n")   # nothing imports this module
    repo.write("pkg/app.py", "from pkg.core import used\nfrom pkg.star import *\n\nused()\n")
    assert found(repo) == set()


def test_test_files_and_excluded_files_are_not_judged_and_ignored_names_are_skipped(repo):
    repo.write("gate.toml", '[dead-code]\nexclude = ["src/public/**"]\nignore = ["calledByName"]\n')
    repo.write("src/lib.ts", "export function used() {}\nexport function calledByName() {}\n")
    repo.write("src/public/api.ts", "export function forCustomers() {}\n")
    repo.write("tests/helpers.ts", "export function unusedHelper() {}\n")
    repo.write("src/app.ts", USES + 'import "./public/api"\nimport "../tests/helpers"\n')
    assert found(repo) == set()


def test_only_the_selected_files_are_reported_but_the_whole_repo_is_searched(repo):
    repo.write("src/old.ts", "export function used() {}\nexport function oldDead() {}\n")
    repo.write("src/new.ts", "export function fresh() {}\nexport function newDead() {}\n")
    repo.write("src/app.ts", 'import { used } from "./old"\nimport { fresh } from "./new"\nused(); fresh()\n')
    assert found(repo, ["src/new.ts"]) == {("src/new.ts", "newDead")}


def test_it_can_be_a_warning_and_is_baselined_by_name(repo):
    repo.write("src/lib.ts", "export function used() {}\nexport function abandoned() {}\n")
    repo.write("src/app.ts", USES)
    accepted = baseline.current(dead_code.run(repo.context()))
    assert accepted == {"dead-code/unused:src/lib.ts::abandoned": [None]}
    repo.write("src/lib.ts", "export function used() {}\nexport function abandoned() {}\nexport function another() {}\n")
    after = {f.symbol: f for f in baseline.apply(dead_code.run(repo.context()), accepted)}
    assert after["abandoned"].baselined and after["another"].blocks()

    repo.write("gate.toml", '[dead-code]\nseverity = "warn"\n')
    assert {f.severity for f in dead_code.run(repo.context())} == {"warn"}


def test_recording_a_finding_in_the_baseline_does_not_make_it_look_used(repo):
    repo.write("src/lib.ts", "export function used() {}\nexport function abandoned() {}\n")
    repo.write("src/app.ts", USES)
    baseline.save(repo.root, baseline.current(dead_code.run(repo.context())))
    assert found(repo) == {("src/lib.ts", "abandoned")}, "its name in .gate/baseline.json is not a use"
