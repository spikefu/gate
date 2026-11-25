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
    assert (config.complexity.max_function_lines, config.complexity.max_parameters) == (120, 5)
    assert config.checks_for(None) == ("fast", ["syntax", "complexity", "size", "boundaries", "lint"])


@pytest.mark.parametrize("text, fragment", [
    ("[complexty]\nccn_fail = 3\n", "unknown section"),
    ("[complexity]\nccn_fial = 3\n", "ccn_fial"),
    ('[boundaries]\nforbid = ["a -> b"]\n', "a !-> b"),
    ('[profiles]\nfast = ["sintax"]\n', "sintax"),
    ("[toolchain]\nlinter = 'x'\n", "linter"),
    ("[tests.unit]\ntimeout_seconds = 5\n", "needs a command"),
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
    entries = {"complexity/ccn:a.py::f": 20}
    same, better, worse = (baseline.apply([finding(value=v)], entries)[0] for v in (20, 18, 21))
    assert same.baselined and better.baselined
    assert not worse.baselined and worse.blocks()


def test_baseline_never_covers_tests_lint_or_warnings():
    entries = {"unit:exit-code": None, "k": 5}
    test_failure = finding(key="unit:exit-code", check="unit", value=None)
    warning = finding(key="k", value=5, severity="warn")
    assert [f.baselined for f in baseline.apply([test_failure, warning], entries)] == [False, False]


def test_current_keeps_worst_value_for_shared_key():
    assert baseline.current([finding(value=17), finding(value=30)]) == {"complexity/ccn:a.py::f": 30}


def test_prune_only_shrinks():
    recorded = {"fixed": 20, "improved": 20, "worse": 20}
    now = {"improved": 16, "worse": 25, "brand-new": 40}
    assert baseline.pruned(recorded, now) == {"improved": 16, "worse": 20}


def test_save_is_sorted_and_round_trips(tmp_path):
    baseline.save(tmp_path, {"b": 2, "a": 1})
    text = (tmp_path / baseline.FILE).read_text()
    assert text.index('"a"') < text.index('"b"')
    assert baseline.load(tmp_path) == {"a": 1, "b": 2}
