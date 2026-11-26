"""Module-level variables that code reassigns are counted per file, and only where the syntax is certain."""

from gate import baseline
from gate.checks import mutable_state


def found(repo, selected=None):
    return {f.path: (f.value, f.line) for f in mutable_state.run(repo.context(selected))}


def test_top_level_let_and_var_count_and_const_and_locals_do_not(repo):
    repo.write("src/a.ts", "const fixed = 1\nlet current = null\nexport let shared = 0\nvar legacy\n"
                           "function f() {\n  let local = 1\n  var other = 2\n}\nconst letter = \"let x\"\n")
    (finding,) = mutable_state.run(repo.context())
    assert (finding.path, finding.value, finding.line, finding.severity) == ("src/a.ts", 3, 2, "fail")
    assert "(current, shared, legacy)" in finding.message
    assert finding.key == "mutable-state/module-variables:src/a.ts"


def test_python_counts_each_variable_a_global_statement_rebinds_once(repo):
    repo.write("a.py", "count = 0\ncache = {}\n\n\ndef bump():\n    global count\n    count += 1\n\n\n"
                       "def reset():\n    global count, cache  # both\n    count = 0\n\n\n"
                       "def doc():\n    \"\"\"\n    global state is a bad idea\n    \"\"\"\n    globals()\n")
    assert found(repo) == {"a.py": (2, 6)}


def test_tests_declaration_files_and_other_languages_are_not_judged(repo):
    repo.write("src/a.test.ts", "let server\n")
    repo.write("tests/conftest.py", "def f():\n    global x\n")
    repo.write("types/env.d.ts", "declare let process: any\nvar x\n")
    repo.write("notes.md", "let it be\n")
    repo.write("main.go", "var counter int\n")
    assert found(repo) == {}


def test_only_selected_files_are_judged_and_the_baseline_makes_it_a_ratchet(repo):
    repo.write("old.ts", "let a = 1\n")
    repo.write("new.ts", "let b = 1\n")
    assert set(found(repo, ["new.ts"])) == {"new.ts"}
    accepted = baseline.current(mutable_state.run(repo.context()))
    assert accepted == {"mutable-state/module-variables:new.ts": [1], "mutable-state/module-variables:old.ts": [1]}
    repo.write("new.ts", "let b = 1\nlet c = 2\n")
    after = {f.path: f for f in baseline.apply(mutable_state.run(repo.context()), accepted)}
    assert after["old.ts"].baselined and after["new.ts"].blocks()
