"""Exact copies of a block are findings; layout and comments do not hide one, and small or similar code is left alone."""

from gate import baseline
from gate.checks import duplication


def block(prefix="value", lines=12):
    """A function body of `lines` distinct, token-rich statements."""
    return "".join(f"    {prefix}_{i} = compute(source[{i}], offset + {i}, scale * {i})\n" for i in range(lines))


def fn(name, body):
    return f"def {name}(source, offset, scale):\n{body}    return total\n"


def found(repo, selected=None):
    return {f.path: (f.value, f.detail) for f in duplication.run(repo.context(selected))}


def test_a_block_copied_into_another_file_is_reported_in_both(repo):
    repo.write("src/a.py", "import os\n\n" + fn("first", block()))
    repo.write("src/b.py", fn("unrelated", block("other")) + "\n" + fn("second", block()))
    assert found(repo) == {
        "src/a.py": (13, "4-16 is also at src/b.py:17-29"),
        "src/b.py": (13, "17-29 is also at src/a.py:4-16"),
    }


def test_reformatting_or_commenting_a_copy_does_not_hide_it(repo):
    repo.write("a.ts", "export function a() {\n" + block() + "}\n")
    spaced = "".join(f"\n  // step\n        {line.strip()}\n" for line in block().splitlines())
    repo.write("b.ts", "import x from \"y\"\nexport function b() {\n" + spaced + "}\n")
    assert set(found(repo)) == {"a.ts", "b.ts"}


def test_a_copy_within_one_file_is_reported(repo):
    repo.write("a.py", fn("first", block()) + "\n" + fn("second", block()))
    ((value, detail),) = found(repo).values()
    assert value == 26 and "2-14 is also at a.py:17-29" in detail


def test_code_that_is_similar_but_not_identical_is_left_alone(repo):
    repo.write("a.py", fn("first", block("left")))
    repo.write("b.py", fn("second", block("right")))
    assert found(repo) == {}


def test_a_block_below_the_line_or_token_threshold_is_left_alone(repo):
    repo.write("a.py", fn("first", block(lines=7)))
    repo.write("b.py", fn("second", block(lines=7)))
    short_lines = "".join(f"    x{i} = {i}\n" for i in range(12))  # twelve lines but few tokens
    repo.write("c.py", fn("third", short_lines))
    repo.write("d.py", fn("fourth", short_lines))
    assert found(repo) == {}
    repo.write("gate.toml", "[duplication]\nmin_lines = 5\nmin_tokens = 20\n")
    assert set(found(repo)) == {"a.py", "b.py", "c.py", "d.py"}


def test_repeated_imports_and_closing_brackets_are_not_a_copy(repo):
    imports = "".join(f"import {{ thing{i}, other{i}, third{i} }} from \"../lib/module{i}\"\n" for i in range(15))
    closing = "    }\n  }\n})\n" * 6
    repo.write("a.ts", imports + "export const a = 1\n" + closing)
    repo.write("b.ts", imports + "export const b = 2\n" + closing)
    assert found(repo) == {}


def test_test_files_are_exempt_unless_the_repo_says_otherwise(repo):
    repo.write("tests/test_a.py", fn("test_a", block()))
    repo.write("tests/test_b.py", fn("test_b", block()))
    repo.write("src/a.py", fn("copied_from_a_test", block()))
    assert found(repo) == {}
    repo.write("gate.toml", "[duplication]\ninclude_tests = true\n")
    assert set(found(repo)) == {"tests/test_a.py", "tests/test_b.py", "src/a.py"}


def test_excluded_files_are_neither_judged_nor_compared_against(repo):
    repo.write("gate.toml", '[duplication]\nexclude = ["gen/**"]\n')
    repo.write("gen/client.py", fn("generated", block()))
    repo.write("src/a.py", fn("mine", block()))
    assert found(repo) == {}


def test_a_changed_file_is_compared_with_the_whole_repo_but_only_it_is_reported(repo):
    repo.write("src/old.py", fn("existing", block()))
    repo.write("src/new.py", fn("rewritten", block()))
    assert set(found(repo, ["src/new.py"])) == {"src/new.py"}
    assert found(repo, ["README.md"]) == {}


def test_the_longest_match_is_taken_and_each_block_reported_once(repo):
    repo.write("a.py", fn("whole", block(lines=20)))
    repo.write("b.py", fn("partial", block(lines=11)))
    repo.write("c.py", fn("whole_again", block(lines=20)))
    value, detail = found(repo)["a.py"]
    assert (value, detail) == (21, "2-22 is also at c.py:2-22")


def test_the_baseline_lets_copied_lines_fall_but_not_rise(repo):
    repo.write("a.py", fn("first", block()))
    repo.write("b.py", fn("second", block()))
    accepted = baseline.current(duplication.run(repo.context()))
    assert accepted == {"duplication/copied-lines:a.py": [13], "duplication/copied-lines:b.py": [13]}
    assert all(f.baselined for f in baseline.apply(duplication.run(repo.context()), accepted))

    repo.write("a.py", fn("first", block()) + "\n" + fn("extra", block("more", 15)))
    repo.write("c.py", fn("third", block("more", 15)))
    after = {f.path: f for f in baseline.apply(duplication.run(repo.context()), accepted)}
    assert after["b.py"].baselined and after["a.py"].blocks() and after["c.py"].blocks()
    assert after["a.py"].message.endswith("(the baseline allows 13)")
