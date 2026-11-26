# gate — design

## Purpose

One command that answers "does this repo still obey its rules?" the same way
for every caller: an AI coding agent, a git hook, CI, or a person at a
terminal.

The rules belong to the repo that adopts gate, not to gate. It ships checks
and defaults; each repo declares its own limits, boundaries and commands in
`gate.toml`.

## Principles

- **Read-only.** `gate check` never edits the repo.
- **Deterministic.** The same tree and config give the same findings in the
  same order.
- **No model calls.** AI reviewers can sit on top and consume its JSON; they
  are not part of it.
- **No agent loop.** It does not plan, edit, or retry. The harness calling it
  does that.
- **Adoptable on an existing repo.** A baseline records today's violations so
  only new or worsened ones fail.
- **A false positive is worse than a miss.** A gate that blocks on something
  that is not wrong teaches people to ignore it. Where a check cannot be sure,
  it warns.
- **A check that cannot run says so.** Missing tools are reported as skipped,
  never as a pass. `--strict` turns a skip into a failure for CI.
- **The rules invite criticism.** Whoever works under the rules, person or
  agent, is told plainly to look for where they fall short and to propose
  better ones. A gate nobody questions stops matching the code it guards.
- **Agents propose; a person decides.** No command an agent is expected to
  run changes what the rules are.

## Commands

| Command | What it does |
|---|---|
| `gate init` | Write a starter `gate.toml` from what the repo already does |
| `gate check [paths]` | Run the checks, print findings, exit 0 or 1 |
| `gate baseline` | Report, accept (`--accept`) or shrink (`--prune`) recorded violations; `--check` fails if it is stale |
| `gate todo` | List the baseline as a work queue, worst file first |
| `gate gaps` | List weaknesses in the repo's rules that gate can see for itself; always exits 0 |
| `gate metrics` | Report facts about each file and folder, for whoever is judging the design; always exits 0 |
| `gate dependencies` | Show packages nobody has approved; `--accept` approves the current set |
| `gate rules` | Write the rules as prose into `AGENTS.md` / `CLAUDE.md`; `--check` fails if they lag `gate.toml` |

`gate check` flags: `--changed [--base REF]` for the git diff plus untracked
files, `--profile NAME`, `--json`, `--strict`.

Exit codes: 0 clean, 1 findings at fail level, 2 config or usage error.

## Finding shape

Every check emits the same record. Adapters and reviewers depend only on this,
so changing it is a breaking change.

```json
{
  "check": "complexity",
  "rule": "ccn",
  "key": "complexity/ccn:src/billing/invoice.ts::buildInvoice",
  "path": "src/billing/invoice.ts",
  "line": 42,
  "symbol": "buildInvoice",
  "severity": "fail",
  "baselined": false,
  "value": 17,
  "limit": 15,
  "message": "CCN 17 > 15: refactor",
  "detail": null
}
```

`key` has no line number, so a finding keeps its identity when code above it
moves. The baseline matches on it.

## Checks

| Check | Rules |
|---|---|
| `syntax` | Parse errors. Blocks when a native parser agrees (Python, TOML) or the language is listed in `[syntax] fail_on`; otherwise warns, because tree-sitter grammars can lag the language they parse |
| `complexity` | Cyclomatic complexity (warn and fail limits), function length, parameter count, with per-glob overrides |
| `size` | File line count, a longer limit for test files, and per-glob overrides |
| `boundaries` | `a !-> b` forbidden imports, "only X may import Y" rules for ports and adapters, both also against outside packages (`pkg:name`), module entry points, and import cycles |
| `suppressions` | Markers that switch a check off: lint and type ignores, skipped tests, coverage exclusions |
| `duplication` | Blocks of code identical to code elsewhere in the repo, apart from layout and comments |
| `dead-code` | Top-level definitions whose name appears nowhere else in the repo, and JS/TS exports only their own file uses |
| `new-dependencies` | A package declared in a manifest that nobody has approved |
| `change-size` | On a `--changed` run: the change adds more non-test code than a person can review well. Warns |
| `coverage` | On a `--changed` run: the tests run too few of the lines the change adds. Needs the repo's coverage report |
| `mutable-state` | Module-level variables that code reassigns: top-level `let`/`var`, Python `global` |
| `test-integrity` | On a `--changed` run: the change leaves fewer tests, or fewer assertions, than the base ref had |
| `lint`, `typecheck`, `format`, `security`, `deps` | The repo's own command, or the first installed tool for each language present |
| `[tests.<name>]` | The repo's own test commands, judged by exit code |
| `[[command]]` | Any other script with an exit code: end-to-end tests, accessibility scans, migration drift |

### Import cycles

Modules that import each other in a loop cannot be understood, tested or
moved one at a time, and which loads first decides whether a name exists yet.
The boundaries check reports each group of modules that can all reach each
other through imports, with the shortest loop spelled out. It needs no
configuration; `[boundaries] cycles = false` switches it off.

An import only counts if it runs while the importing module loads. Type-only
imports, dynamic `import()`, and Python imports inside a function or an
`if TYPE_CHECKING:` block do not, since those are the usual, legitimate ways
to break a cycle. A Python module importing its own package is not counted
either. Each group is one finding carrying its size, keyed by its first
member, so a baselined tangle may shrink but not grow.

### Suppressions

The quickest way past a failing check is to silence it, and an agent asked to
finish will. So every marker is a finding: `# noqa`, `eslint-disable`,
`# type: ignore`, `@ts-ignore`, `as any`, a skipped test, a coverage
exclusion, and their equivalents in Go, Rust, Java, Kotlin, Swift, C#, PHP,
Ruby and C. One finding per file and kind carries the count, so the baseline
makes it a ratchet: existing markers are accepted once, the count may fall,
and it cannot rise without a person running `--accept`.

Markers are counted only where they act. Python is tokenized, so a marker
quoted in a string or docstring is not counted. Other languages are matched
by line against the comment or call syntax. `[suppressions] kinds` narrows
which of `lint`, `type`, `test` and `coverage` count.

### Duplication

Writing a second copy is cheaper than finding the first, and an agent that
has not read the whole repo does it often. The check finds blocks of at least
10 meaningful lines and 100 tokens that are identical to code elsewhere, in
another file or the same one. Whitespace, comments, imports and bracket-only
lines are ignored, so reformatting a copy does not hide it.

Only exact copies are found; renaming a variable hides one. That is the price
of the false-positive principle: matching "similar" code flags code that
merely has the same shape. The thresholds follow the conservative end of
common practice for the same reason.

A file being judged is compared with every code file in the repo, so new code
copying old code is caught on a `--changed` run. Each file gets one finding
carrying its number of copied lines, so the baseline is a ratchet. Test files
are exempt by default, since tests repeat setup so each reads on its own.

### Dead code

An agent that changes approach halfway leaves the first attempt behind. The
check flags a top-level definition (a Python function or class; an exported
JS/TS function, class, constant, type, interface or enum) whose name appears
exactly once in the whole repo: the definition itself. Any other mention, in
code, a test, a config file or a document, counts as a use.

That is crude on purpose. It misses dead code whose name is shared with
something live, and in exchange almost never accuses code that is in use.
Three guards cover what a text search cannot know:

- only modules that other code imports are judged, so a file a framework
  loads by convention (a route, a page, a migration) is left alone
- a module re-exported wholesale (`export * from`, `from x import *`) is
  skipped
- a decorated Python definition is skipped, since the decorator may register it

Default exports and files that nothing imports are not judged, so a wholly
unused file is not found; that needs a tool that knows the framework's entry
points. `[dead-code] ignore` lists names used in ways the text does not show,
and `exclude` leaves out files such as a published package's public API.

### New dependencies

A dependency is outside code that runs with the repo's permissions, and
adding one takes a single line. An agent will add one to save writing twenty
lines, and now and then names a package that does not exist, which someone
else can then register. So a new package name is a person's decision.

`.gate/dependencies.json` holds the approved names per ecosystem, and is
meant to be committed. `gate dependencies --accept` writes it from what is
declared today; like `baseline --accept`, no adapter runs it. The check fails
for any declared name not on the list. Until a list exists the check reports
itself as skipped, so adopting gate does not start with a wall of failures,
and `--strict` in CI still forces the decision.

Direct dependencies are read from `package.json`, `pyproject.toml`,
`requirements*.txt`, `go.mod`, `Cargo.toml` and `Gemfile`; lockfiles are not
read. Only names are tracked: a version change is not a new dependency, and a
name approved once is approved in every manifest. It is not baselined, because
an approved package is not a violation waiting to be cleaned up.

### Change size

An agent writes faster than anyone can read, and the person approving the
result is the scarce resource. Review studies find the share of defects a
reviewer catches falls off beyond a few hundred lines; past that a change is
skimmed. On a `--changed` run the check adds up lines added or modified since
the base ref in code files that are not tests, and warns past 400.

Deleting code, writing tests, editing documents and regenerating a lockfile
do not count. It warns instead of failing because the remedy (stop, have it
reviewed, commit, carry on) is the person's, not something an agent can do by
editing files; a blocked agent would only try to shrink the diff. Committing
resets the count, which is why the rules tell agents not to commit on their
own to get past it. The Claude Code Stop hook shows this warning to the
person even though the gate passed; other warnings do not interrupt.

### Coverage of changed lines

A passing suite says nothing about code it does not execute, and new code is
where that matters. A whole-repo percentage hides it: a thousand untested new
lines barely move a large total, and the figure punishes old code nobody is
touching. So on a `--changed` run the check asks a narrower question: of the
lines this change adds, how many did the tests run? Below 80% fails.

gate does not run tests under coverage. The repo's own test command writes a
report, lcov or Cobertura XML, and `[coverage] report` says where. The check
is not in a default profile because it means nothing until that is set; list
it in a profile after the tests that write the report.

- Only lines the report marks as executable count, in code files that are not
  tests, so comments and blank lines are free.
- A changed file missing from the report is a warning, not part of the
  figure: it is untested or simply not measured, and the report cannot say
  which.
- A report older than the code it would judge is refused, and the check
  reports itself as skipped. Its line numbers describe a different file.
- Reports name files as their tool saw them. Each name is resolved against
  the repo, then against the folders above the report, so a workspace
  package's own report works.

`min_percent` judges the whole-repo figure as well, on any run; it is off by
default for the reason above.

### Test integrity

When a test will not pass, the cheapest fix is to delete it or remove the
assertion that fails. The suite stays green and nothing else notices. On a
`--changed` run this check counts tests and assertions in every test file the
change touches, adds or deletes, at the base ref and now, and fails on a net
loss of either.

Counting across all touched files together means moving tests, renaming a
file or splitting one is free. Only test files count, so a runtime `assert`
removed from source is not a finding. The counts are by line against each
language's syntax and so approximate, but the same on both sides.

It cannot tell a test deleted to hide a failure from one deleted along with
the feature it covered. It says what was lost and stops there: the agent is
told to restore it or explain, and committing the change is the person's
acceptance, since after that the default base no longer shows a loss. A repo
that compares against a branch in CI can set `[test-integrity] severity =
"warn"`. A whole-repo run has no "before", so the check reports nothing.

### Default limits

| Limit | Default | Reasoning |
|---|---|---|
| Cyclomatic complexity | warn above 10, fail above 15 | 10 is McCabe's original threshold and a common linter default; 15 is the usual ceiling for code that is still testable path by path |
| Function length | 80 lines | Roughly what fits in view with its context. Markup-heavy UI components are the usual exception: give them a `[[complexity.override]]` |
| Parameters | 5 | Past this, callers start passing arguments in the wrong order |
| File length | warn above 300, fail above 500 | Long enough for one cohesive module, short enough to read in a sitting and to review as a whole |
| Test file length | 1000 | Tests are many small independent cases; length there is not tangled logic |

A limit is a judgement about what stays readable, not a record of what the
code happens to be today. So gate never suggests lowering one just because
nothing would fail, and a file that is long because it is declarative (a
schema, seed data) should get an override rather than an artificial split.

A nesting-depth limit was tried and left out: the available measure
miscounted often enough on real code to break the false-positive principle.

Commands run with the repo's own `.venv/bin` and `node_modules/.bin` ahead of
`PATH`, and tool auto-detection looks there first. A repo's linter is the one
it installed, not whichever version the machine happens to have.

Boundaries follow relative imports, tsconfig `paths` aliases and workspace
package names for JS/TS, and absolute and relative imports for Python.

### Module entry points

`[[boundaries.module]]` declares folders to be modules: `path = "src/billing"`
for one, or `path = "src/modules/*"` for one per folder matched. Code outside
a module may then import only its entry point, `index.*` or `__init__.py`
unless `entry` lists other files. What sits behind the entry is the module's
own business and can be reshaped without touching its users, which is the
point of encapsulation and the thing a folder alone does not give you.

A module's own files import each other freely, and tests are exempt, since a
unit test may need the internals it tests. Type-only imports count.

In Python, `from pkg import name` asks the package for `name`, which may be
something `__init__` exports or a submodule of the same name; the text cannot
say which, so it is read as going through the entry. `from pkg.name import x`
and `import pkg.name` name the submodule and are findings.

### Encapsulation and coupling without configuration

Three rules rest on conventions wide enough to assume, so they are on without
anything being declared.

- **Private names (Python).** A module or name starting with `_` belongs to
  the folder it sits in. Importing it from another folder is a finding;
  inside the same package it is shared freely. `[boundaries] private = false`
  switches it off.
- **Needless exports (JS/TS).** An exported function, class, constant or enum
  that only its own file mentions should not be exported: the module's
  surface should be what others rely on. It is part of `dead-code` and under
  the same guards. Types and interfaces are left alone, because a compiler
  writing declaration files can require one to be exported that nobody
  imports by name. `[dead-code] exports = false` switches it off.
- **Import fan-out.** A file importing more than 15 of the repo's own modules
  gets a warning. It never fails: the place a program is wired together is
  long on imports for a good reason. `[boundaries] max_imports` sets the
  figure, and 0 switches it off.

### Shared mutable state

A function that reads or writes a module-level variable gives different
results depending on what ran before it. `mutable-state` counts what the
syntax makes certain: a top-level `let` or `var` in JS/TS (a `const` cannot
be reassigned), and each variable a Python `global` statement rebinds. One
finding per file carries the count, so the baseline is a ratchet. Mutating a
top-level object in place is not seen, and test files are exempt.

### Outside packages in boundary rules

A rule's target can be `pkg:<name>`: an outside package or a built-in module.
`src/domain !-> pkg:fs` keeps I/O out of a folder; `src/domain !-> pkg:express`
keeps a framework out; an only-rule with `target = "pkg:pg"` gives a
dependency a single owner. Together these express a functional core and
ports and adapters for third-party code, which path rules alone cannot: the
things most worth isolating are usually not in the repo.

A pattern covers the package and everything inside it, so `pkg:fs` matches
`node:fs` and `fs/promises`, and `pkg:os` matches `os.path`, while
`pkg:express` does not match `express-session`. Wildcards work
(`pkg:@aws-sdk/*`). Type-only and deferred imports count: the rule is about
depending on the package at all.

An import is outside when it does not point into the repo: not relative, not
a tsconfig alias, not a workspace package. In Python, a name is the repo's
own only if a module or package of that name sits at a source root, so a
`logging.py` inside a package does not hide the standard library's.

On a `--changed` run, a project-wide tool that fails only in files outside the
change warns instead of blocking. Files are recognised in its output as whole
paths (absolute, repo-relative, or relative to a subdirectory), never as
substrings. A tool that fails without naming any file blocks: it crashed or is
misconfigured, so it checked nothing.

## Baseline

`.gate/baseline.json` maps each finding key to the values recorded under it,
worst first, and is meant to be committed.

- Each recorded value covers exactly one finding. A key is a file and a
  function name, and names repeat: every anonymous function shares one. If a
  key held a single value, any number of new functions could hide behind the
  first. Findings under one key are paired with its recorded values worst with
  worst, and any left over fail.

- Only `complexity`, `size`, `boundaries`, `suppressions`, `duplication`,
  `dead-code` and `mutable-state` can be baselined. A failing test
  suite or linter is never grandfathered.
- A baselined finding whose value gets worse fails again, and its message
  says what the baseline allows.
- `--prune` only shrinks: it drops fixed entries and tightens improved ones.
- `--accept` is the only way entries are added. It is a human decision; no
  adapter runs it.
- `--check` fails when `--prune` would change the file. In CI that makes the
  baseline a ratchet: a fix has to be recorded before it can merge, and from
  then on it cannot be undone without failing.

## Rules as prose

`gate rules` renders the config into the instruction files agents read. The
section sits between `<!-- gate:rules:start -->` and `<!-- gate:rules:end -->`;
everything outside the markers belongs to the file's author and is never
touched. Because the prose is generated from the same file the checks read,
an agent is never told one limit and held to another.

## Finding weaknesses

Three surfaces carry the same invitation, so it reaches an agent whichever
way it meets the repo.

`gate gaps` is the factual part. It reports:

- **unchecked**: a toolchain category with no tool or command; a check, test
  or command that no profile runs; no tests configured at all
- **boundaries**: import directions the code already respects that no rule
  protects (the same proposals `gate init` makes, minus those already covered)
- **headroom**: a limit raised above its default that nothing needs any more
- **blind spots**: files matched by `[files] exclude`, and size or complexity overrides looser than the general limit
- **upkeep**: a stale baseline, a non-empty baseline, stale rules prose
- **ci**: no CI configuration at all, or CI that never runs `gate check`

It ends by naming what it cannot see, so an empty report is not read as a
clean bill of health.

The generated rules prose ends with an "Improving these rules" section, and
the Stop hook's failure message tells the agent to report a wrong finding
rather than work around it. The `gate-review` skill covers what needs a
reader: bugs that got through, tests that prove nothing, and whether a limit
is producing better code or just smaller pieces.

## Judging the design

Some qualities cannot be checked without being wrong too often to trust:
whether a unit has one job, whether an interface exposes the right things,
whether an effect is surprising. gate leaves those to a reader and gives the
reader facts.

`gate metrics` reports, per file: size, function count, the most complex and
the longest function, how many of the repo's files it imports and how many
import it, the outside packages it uses, how many things it offers, and its
shared mutable state. Per folder: how many folders it depends on and how many
depend on it, their ratio as instability, whether it has an entry point, and
how many imports go past it. With `--json` it also lists every import between
product files as an edge, for tools that draw the structure, and marks each
file whose language it cannot read imports for, so a zero there is not read as none. Test files are left out, as subjects and as
importers. `--changed` narrows it to a change, with dependents still counted
from the whole repo. It judges nothing and always exits 0.

The `gate-design-review` skill is the reader. It takes a change or a module,
reads those facts and the gate's warnings, then reads the code against five
questions (separation of responsibilities, encapsulation, decoupling, side
effects, modularity), each with what counts as evidence and what does not. It
reports at most five findings with the code that shows each, the smallest
change that fixes it, and the gate rule that would keep it fixed. It never
edits code and it does not block: what to act on is the person's decision.

The split is deliberate. What a check can decide is in `gate check`, and
blocks. What needs judgement is proposed, with evidence, and does not.

## Extending

A check is a module in `src/gate/checks/` exposing `run(ctx) -> list[Finding]`
and raising `Skipped` when it cannot run. Adding one means adding the module,
a config table, and an entry in the runner. Nothing else changes.

An adapter is a thin translation from a harness's protocol to
`gate check --json`. All judgement stays in the CLI.

```
src/gate/
  cli.py            argument parsing, exit codes
  config.py         load and validate gate.toml
  files.py          which files a run looks at
  findings.py       Finding, Report, text and JSON output
  baseline.py       read, apply, prune
  runner.py         profile -> checks -> report
  init.py           starter config from an unseen repo
  propose.py        boundary rules from existing imports
  todo.py           the baseline as a work queue
  rules.py          the config as prose for agent instruction files
  gaps.py           weaknesses in the rules that gate can see itself
  metrics.py        facts per file and folder, for judging the design
  checks/           one module per check
  lang/             import graph and JS/TS module resolution
adapters/
  claude-code/      plugin: Stop hook; cleanup, rules-review and design-review skills
  github-actions/   workflow template
```

## Not built yet

- Checks: test quality (tests with no real assertion), scope (diff
  stays inside declared files), required READMEs, imports of symbols a module
  does not export.
- Adapters: pre-commit, and other agent harnesses.

A profile that names an unbuilt check reports it as skipped.

## Known limits

- `--changed` compares against a git ref, so once work is committed a hook
  using it has nothing to check. CI on the whole repo covers that.
- Complexity and length are measured per function. Nested closures and UI
  components are not measured separately.
- The import graph is regex-based: an import inside a comment counts.
- Requires Python 3.13.
