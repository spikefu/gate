"""What each check asks of an agent, in prose, for the generated rules section.

One function per check, each silent unless a profile runs that check: an agent
should not be told about a rule that nothing enforces. Kept apart from
`rules` so that adding a check adds its paragraph here and nowhere else.
"""

from __future__ import annotations

from .config import Config


def sections(config: Config) -> list[str]:
    """Every check's paragraph, in the order an agent should read them."""
    writers = (_suppressions, _duplication, _dead_code, _dependencies, _change_size, _coverage, _seeded_bugs,
               _mutable_state, _stamps)
    return [line for writer in writers for line in writer(config)]


_MARKERS = {
    "lint": "lint ignores (`# noqa`, `eslint-disable`, and the like)",
    "type": "type-checker ignores (`# type: ignore`, `@ts-ignore`, `as any`)",
    "test": "skipped or expected-to-fail tests",
    "coverage": "coverage exclusions",
}


def _suppressions(config: Config) -> list[str]:
    scheduled = any("suppressions" in checks for checks in config.profiles.values())
    if not scheduled or not config.suppressions:
        return []
    return [
        "",
        "### Suppressions",
        "",
        "- Do not switch a check off to get past it. Adding any of these fails the gate: "
        + "; ".join(_MARKERS[kind] for kind in config.suppressions) + ".",
        "- Fix the cause. If a suppression is truly the right answer, say why in your report and leave it "
        "failing: accepting it is the human's decision.",
    ]


def _duplication(config: Config) -> list[str]:
    if not any("duplication" in checks for checks in config.profiles.values()):
        return []
    d = config.duplication
    return [
        "",
        "### Duplication",
        "",
        f"- Do not copy code. A block of {d.min_lines} or more lines identical to code elsewhere in the repo fails"
        + ("" if d.include_tests else " (test files are exempt)") + ".",
        "- Before writing a helper, search for one that already does it. If two places need the same code, "
        "move it somewhere both can use.",
    ]


def _dead_code(config: Config) -> list[str]:
    if not any("dead-code" in checks for checks in config.profiles.values()):
        return []
    return [
        "",
        "### Dead code",
        "",
        "- Do not leave code nothing uses. A function, class, constant or type whose name appears nowhere else "
        "in the repo fails.",
        *(["- Export only what another file uses. An `export` on something only its own file uses fails."]
          if config.dead_code.exports else []),
        "- When you change approach, delete what the first attempt left behind. Do not keep it \"in case\".",
    ]


def _dependencies(config: Config) -> list[str]:
    if not any("new-dependencies" in checks for checks in config.profiles.values()):
        return []
    return [
        "",
        "### Dependencies",
        "",
        "- Do not add a package without saying so. A dependency nobody has approved fails the gate.",
        "- If one is needed, say why in your report, and confirm the package exists under exactly that name. "
        "Never run `gate dependencies --accept`: approving a package is the human's decision.",
    ]


def _change_size(config: Config) -> list[str]:
    if not any("change-size" in checks for checks in config.profiles.values()):
        return []
    return [
        "",
        "### Change size",
        "",
        f"- Keep each change small enough to review. Past {config.change_size.max_lines} added lines of non-test code "
        "in one uncommitted change, gate warns.",
        "- When it does, stop at the next natural point and say so in your report. Do not carry on, and do not "
        "commit on your own to reset the count.",
    ]


def _stamps(config: Config) -> list[str]:
    if not config.stamps or not any("stamps" in checks for checks in config.profiles.values()):
        return []
    return [
        "",
        "### Made files",
        "",
        "- Some files here are made from others by a tool, and each has a stamp recording what it was made from: "
        + ", ".join(f"`{rule.file}`" for rule in config.stamps) + ".",
        "- When you change a file one of them was made from, the gate fails until it is made again. Run the command the "
        "finding names, and commit the new stamp.",
        "- Never edit a stamp by hand to make the finding go away. If you cannot run the command, say so in your report.",
    ]


def _coverage(config: Config) -> list[str]:
    if not config.coverage.reports or not any("coverage" in checks for checks in config.profiles.values()):
        return []
    return [
        "",
        "### Coverage",
        "",
        f"- New code must be tested: the tests have to run at least {config.coverage.min_changed_percent}% of the lines "
        "a change adds.",
        "- Write tests that exercise the new lines. Do not add coverage exclusions, and do not delete code paths "
        "to raise the figure.",
    ]


def _seeded_bugs(config: Config) -> list[str]:
    if not config.seeded_bugs.command or not any("seeded-bugs" in checks for checks in config.profiles.values()):
        return []
    outcome = "fails the gate" if config.seeded_bugs.severity == "fail" else "is reported"
    return [
        "",
        "### Seeded bugs",
        "",
        "- Tests must notice when the code is wrong, not only run it. For the lines a change adds, gate plants one "
        f"small fault at a time and runs the tests; a fault they still pass with {outcome}.",
        "- When one goes unnoticed, add a test that would fail with that fault. Assert on what the code returns "
        "or does, at the values where it changes its mind.",
        "- If the fault cannot change what the code does, say so in your report and leave the finding. Do not "
        "rewrite the code only to make the fault impossible to plant.",
    ]


def _mutable_state(config: Config) -> list[str]:
    if not any("mutable-state" in checks for checks in config.profiles.values()):
        return []
    return [
        "",
        "### Shared state",
        "",
        "- Do not add module-level variables that code reassigns: a top-level `let` or `var`, or a Python `global`. "
        "Adding one fails.",
        "- Pass the value in, return it, or keep it in an object with a clear owner.",
    ]
