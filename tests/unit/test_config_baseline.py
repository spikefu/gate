"""Config validation and baseline arithmetic."""

import pytest

from gate import baseline
from gate.config import ConfigError, load
from gate.findings import Finding


def cfg(tmp_path, text):
    path = tmp_path / "gate.toml"
    path.write_text(text)
    return load(path)


def finding(key="complexity/ccn:a.py::f", value=20, check="complexity", severity="fail"):
    return Finding(check=check, rule="ccn", key=key, path="a.py", message="m", value=value, severity=severity)


def test_missing_config_gives_the_documented_defaults(tmp_path):
    config = load(tmp_path / "gate.toml")
    assert (config.complexity.ccn_warn, config.complexity.ccn_fail) == (10, 15)
    assert (config.complexity.max_function_lines, config.complexity.max_parameters) == (80, 5)
    assert (config.size.warn, config.size.fail, config.size.test_fail) == (300, 500, 1000)
    assert config.checks_for(None) == ("fast", ["syntax", "complexity", "size", "boundaries", "suppressions", "test-integrity", "duplication", "dead-code", "new-dependencies", "change-size", "lint"])


@pytest.mark.parametrize("text, fragment", [
    ("[complexty]\nccn_fail = 3\n", "unknown section"),
    ("[complexity]\nccn_fial = 3\n", "ccn_fial"),
    ('[boundaries]\nforbid = ["a -> b"]\n', "a !-> b"),
    ('[profiles]\nfast = ["sintax"]\n', "sintax"),
    ("[toolchain]\nlinter = 'x'\n", "linter"),
    ("[tests.unit]\ntimeout_seconds = 5\n", "needs a command"),
    ('[[complexity.override]]\nglob = "a"\nmax_lines = 5\n', "complexity.override"),
    ('[[complexity.override]]\nmax_function_lines = 5\n', "complexity.override"),
    ('[suppressions]\nkinds = ["lints"]\n', "lints"),
    ('[test-integrity]\nseverity = "off"\n', "test-integrity"),
    ("[duplication]\nmin_line = 5\n", "min_line"),
    ("[duplication]\nmin_lines = 1\n", "at least 2"),
    ('[dead-code]\nseverity = "off"\n', "dead-code"),
    ('[dead-code]\nignored = ["x"]\n', "ignored"),
    ("[change-size]\nmax_lines = 0\n", "change-size"),
    ("[change-size]\nlimit = 5\n", "limit"),
    ("[boundaries]\ncycle = false\n", "cycles"),
    ('[boundaries]\ncycles = "no"\n', "cycles"),
])
def test_typos_are_errors_not_silently_ignored(tmp_path, text, fragment):
    with pytest.raises(ConfigError, match=fragment):
        cfg(tmp_path, text)


def test_tests_and_commands_become_profile_checks(tmp_path):
    config = cfg(tmp_path, '[tests.unit]\ncommand = "pytest"\n[[command]]\nname = "e2e"\nrun = "x"\n'
                           '[profiles]\nci = ["unit", "e2e", "docs"]\ndefault = "ci"\n')
    assert config.checks_for(None) == ("ci", ["unit", "e2e", "docs"])
    assert config.profiles["full"][-1] == "unit"


def test_unknown_profile_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="unknown profile"):
        load(tmp_path / "gate.toml").checks_for("nope")


def test_baseline_covers_equal_or_better_but_not_worse():
    entries = {"complexity/ccn:a.py::f": [20]}
    same, better, worse = (baseline.apply([finding(value=v)], entries)[0] for v in (20, 18, 21))
    assert same.baselined and better.baselined
    assert not worse.baselined and worse.blocks()


def test_baseline_never_covers_tests_lint_or_warnings():
    entries = {"unit:exit-code": [None], "k": [5]}
    test_failure = finding(key="unit:exit-code", check="unit", value=None)
    warning = finding(key="k", value=5, severity="warn")
    assert [f.baselined for f in baseline.apply([test_failure, warning], entries)] == [False, False]


def test_each_recorded_value_covers_one_finding_so_new_functions_cannot_hide_behind_a_shared_name():
    # Anonymous functions, and same-named methods, share a key.
    entries = {"complexity/ccn:a.py::f": [21]}
    original, added, another = baseline.apply([finding(value=21), finding(value=20), finding(value=19)], entries)
    assert original.baselined
    assert added.blocks() and another.blocks()


def test_shared_key_findings_are_paired_worst_with_worst_whatever_order_they_arrive_in():
    entries = {"complexity/ccn:a.py::f": [30, 17]}
    flags = [f.baselined for f in baseline.apply([finding(value=16), finding(value=30), finding(value=18)], entries)]
    # 30 pairs with 30, 18 is over the 17 it pairs with, and 16 has nothing left to pair with.
    assert flags == [False, True, False]


def test_apply_keeps_findings_in_their_original_order():
    found = [finding(value=16), finding(key="other", value=40), finding(value=30)]
    assert [f.value for f in baseline.apply(found, {"complexity/ccn:a.py::f": [30, 16]})] == [16, 40, 30]


def test_current_records_every_finding_under_a_shared_key_worst_first():
    assert baseline.current([finding(value=17), finding(value=30)]) == {"complexity/ccn:a.py::f": [30, 17]}


def test_violations_without_a_measure_are_counted_too():
    def edge():
        return finding(key="boundaries:a->b", check="boundaries", value=None)
    assert baseline.current([edge(), edge()]) == {"boundaries:a->b": [None, None]}
    flags = [f.baselined for f in baseline.apply([edge(), edge(), edge()], {"boundaries:a->b": [None, None]})]
    assert flags == [True, True, False]


def test_prune_only_shrinks():
    recorded = {"fixed": [20], "improved": [20], "worse": [20], "shared": [30, 25, 20]}
    now = {"improved": [16], "worse": [25], "brand-new": [40], "shared": [28, 26]}
    assert baseline.pruned(recorded, now) == {"improved": [16], "worse": [20], "shared": [28, 25]}
    assert baseline.tightened(recorded, baseline.pruned(recorded, now)) == 2
    assert (baseline.size(recorded), baseline.size(baseline.pruned(recorded, now))) == (6, 4)


def test_save_is_sorted_and_round_trips(tmp_path):
    baseline.save(tmp_path, {"b": [2, 9], "a": [1]})
    text = (tmp_path / baseline.FILE).read_text()
    assert text.index('"a"') < text.index('"b"')
    assert baseline.load(tmp_path) == {"a": [1], "b": [9, 2]}


def test_a_version_1_baseline_with_one_value_per_key_still_loads(tmp_path):
    (tmp_path / ".gate").mkdir()
    (tmp_path / baseline.FILE).write_text('{"version": 1, "entries": {"k": 20, "edge": null}}')
    assert baseline.load(tmp_path) == {"k": [20], "edge": [None]}
