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
| `complexity` | Cyclomatic complexity (warn and fail limits), function length, parameter count |
| `size` | File line count, with per-glob overrides |
| `boundaries` | `a !-> b` forbidden imports, and "only X may import Y" rules for ports and adapters |
| `lint`, `typecheck`, `format`, `security`, `deps` | The repo's own command, or the first installed tool for each language present |
| `[tests.<name>]` | The repo's own test commands, judged by exit code |
| `[[command]]` | Any other script with an exit code: end-to-end tests, accessibility scans, migration drift |

Boundaries follow relative imports, tsconfig `paths` aliases and workspace
package names for JS/TS, and absolute and relative imports for Python.

On a `--changed` run, a project-wide tool that fails somewhere outside the
changed files warns instead of blocking.

## Baseline

`.gate/baseline.json` maps finding keys to their values, sorted, and is meant
to be committed.

- Only `complexity`, `size` and `boundaries` can be baselined. A failing test
  suite or linter is never grandfathered.
- A baselined finding whose value gets worse fails again.
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
- **headroom**: for each limit, the tightest value that would fail nothing
  that passes today
- **blind spots**: files matched by `[files] exclude`, and size overrides
- **upkeep**: a stale baseline, a non-empty baseline, stale rules prose
- **ci**: workflows exist but none runs `gate check`

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
