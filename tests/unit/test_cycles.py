"""Modules that import each other in a loop are findings; imports that do not run at load time are not."""

from gate import baseline
from gate.checks import boundaries
from gate.lang import cycles


def found(repo, selected=None):
    return {f.path: (f.value, f.detail) for f in boundaries.run(repo.context(selected)) if f.rule == "cycle"}


def ts(*imports):
    return "".join(f'import {{ x{i} }} from "{spec}"\n' for i, spec in enumerate(imports)) + "export const v = 1\n"


def test_tangles_are_groups_that_can_all_reach_each_other():
    graph = {"a": ["b"], "b": ["c"], "c": ["a", "d"], "d": ["e"], "e": ["d"], "f": ["a"], "g": []}
    assert cycles.tangles(graph) == [["a", "b", "c"], ["d", "e"]]
    assert cycles.shortest_loop(graph, "a") == ["a", "b", "c", "a"]
    assert cycles.shortest_loop({"a": ["b", "c"], "b": ["c"], "c": ["a"]}, "a") == ["a", "c", "a"]


def test_a_long_chain_does_not_exhaust_recursion():
    graph = {str(i): [str(i + 1)] for i in range(5000)} | {"5000": ["0"]}
    (group,) = cycles.tangles(graph)
    assert len(group) == 5001


def test_two_modules_importing_each_other_are_one_finding_at_the_import(repo):
    repo.write("src/a.ts", "// header\n" + ts("./b"))
    repo.write("src/b.ts", ts("./a"))
    repo.write("src/c.ts", ts("./a"))
    (finding,) = [f for f in boundaries.run(repo.context()) if f.rule == "cycle"]
    assert (finding.path, finding.line, finding.value, finding.key) == ("src/a.ts", 2, 2, "boundaries/cycle:src/a.ts")
    assert finding.detail == "src/a.ts -> src/b.ts -> src/a.ts"
    assert finding.severity == "fail" and "2 modules import each other in a loop" in finding.message


def test_a_larger_tangle_lists_its_shortest_loop_and_every_member(repo):
    repo.write("a.ts", ts("./b"))
    repo.write("b.ts", ts("./c", "./a"))
    repo.write("c.ts", ts("./a"))
    assert found(repo) == {"a.ts": (3, "a.ts -> b.ts -> a.ts\nall 3: a.ts, b.ts, c.ts")}


def test_imports_that_do_not_run_at_load_time_do_not_make_a_cycle(repo):
    repo.write("a.ts", ts("./b"))
    repo.write("b.ts", 'import type { T } from "./a"\nexport type { U } from "./a"\n'
                       'import { type V, type W } from "./a"\nconst later = () => import("./a")\n')
    assert found(repo) == {}
    repo.write("b.ts", 'import { type V, real } from "./a"\n')   # one real name makes it a real import
    assert set(found(repo)) == {"a.ts"}


def test_python_cycles_count_only_top_level_imports(repo):
    repo.write("pkg/__init__.py", "from pkg import a, b\n")           # a package naming its modules is not a cycle
    repo.write("pkg/a.py", "from pkg import b\n")
    repo.write("pkg/b.py", "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from pkg import a\n\n\n"
                           "def late():\n    from pkg import a\n    import pkg.a\n")
    assert found(repo) == {}
    repo.write("pkg/b.py", "from pkg import a\n")
    assert found(repo) == {"pkg/a.py": (2, "pkg/a.py -> pkg/b.py -> pkg/a.py")}


def test_a_changed_file_is_reported_when_it_joins_a_cycle_through_unchanged_files(repo):
    repo.write("a.ts", ts("./b"))
    repo.write("b.ts", ts("./c"))
    repo.write("c.ts", ts("./a"))
    repo.write("x.ts", ts("./y"))
    repo.write("y.ts", ts("./x"))
    assert set(found(repo, ["c.ts"])) == {"a.ts"}, "the cycle c closes, not the unrelated one"
    assert found(repo, ["README.md"]) == {}


def test_cycles_can_be_switched_off_and_do_not_need_other_rules(repo):
    repo.write("a.ts", ts("./b"))
    repo.write("b.ts", ts("./a"))
    assert set(found(repo)) == {"a.ts"}
    repo.write("gate.toml", "[boundaries]\ncycles = false\n")
    assert found(repo) == {}


def test_a_baselined_tangle_may_shrink_but_not_grow(repo):
    repo.write("a.ts", ts("./b"))
    repo.write("b.ts", ts("./a"))
    accepted = baseline.current(boundaries.run(repo.context()))
    assert accepted == {"boundaries/cycle:a.ts": [2]}
    repo.write("b.ts", ts("./a", "./c"))
    repo.write("c.ts", ts("./a"))
    (finding,) = baseline.apply(boundaries.run(repo.context()), accepted)
    assert finding.blocks() and finding.value == 3 and finding.message.endswith("(the baseline allows 2)")
