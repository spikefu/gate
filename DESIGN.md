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
| `boundaries` | `a !-> b` forbidden imports, and "only X may import Y" rules for ports and adapters |
| `suppressions` | Markers that switch a check off: lint and type ignores, skipped tests, coverage exclusions |
| `lint`, `typecheck`, `format`, `security`, `deps` | The repo's own command, or the first installed tool for each language present |
| `[tests.<name>]` | The repo's own test commands, judged by exit code |
| `[[command]]` | Any other script with an exit code: end-to-end tests, accessibility scans, migration drift |

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

- Only `complexity`, `size`, `boundaries` and `suppressions` can be baselined. A failing test
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
  checks/           one module per check
  lang/             import graph and JS/TS module resolution
adapters/
  claude-code/      plugin: Stop hook, cleanup skill and review skill
  github-actions/   workflow template
```

## Not built yet

- Checks: test quality (tests with no real assertion), coverage, scope (diff
  stays inside declared files), required READMEs, imports of symbols a module
  does not export.
- Adapters: pre-commit, and other agent harnesses.
- Reviewer prompts that take `gate check --json` as input.

A profile that names an unbuilt check reports it as skipped.

## Known limits

- `--changed` compares against a git ref, so once work is committed a hook
  using it has nothing to check. CI on the whole repo covers that.
- Complexity and length are measured per function. Nested closures and UI
  components are not measured separately.
- The import graph is regex-based: an import inside a comment counts.
- Requires Python 3.13.
