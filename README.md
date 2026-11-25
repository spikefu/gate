# gate

One read-only command that checks a repo against its declared rules. The same
command serves an agent hook, a git hook, CI, and you. It makes no model calls
and runs no agent loop.

See `DESIGN.md` for the reasoning and what is still to build.

## Install

```bash
pipx install git+https://github.com/spikefu/gate
```

The repository is private, so this needs git signed in to GitHub with access
to it. Needs Python 3.13 or newer.

To work on gate itself:

```bash
git clone https://github.com/spikefu/gate && cd gate
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## Adopt it on a repo

```bash
gate init                       # writes gate.toml from the repo's own scripts and imports
gate check                      # see what breaks the rules today
gate baseline --accept          # record that as the starting point (your decision)
gate rules                      # tell agents the rules, in AGENTS.md / CLAUDE.md
gate todo                       # the baseline as a work queue, worst file first
```

`gate init` fills in the lint, typecheck and test commands the repo already
defines, and proposes boundary rules that already hold today, for you to
prune. It never accepts a baseline.

## Use

```bash
gate check                      # whole repo, default profile
gate check --changed            # only the git diff plus untracked files
gate check --profile full --strict --json
gate baseline                   # report what is recorded, fixed, and new
gate baseline --accept          # record today's violations (a human decision)
gate baseline --prune           # drop fixed entries, tighten improved ones
gate todo --next 1 --json       # the next file to clean up
gate rules --check              # exit 1 if AGENTS.md / CLAUDE.md lag gate.toml
gate baseline --check           # exit 1 if fixed violations are still recorded
gate gaps                       # where these rules fall short, as far as gate can tell
```

`gate rules` renders `gate.toml` as prose between two markers in your
instruction files and leaves the rest of each file alone, so what an agent is
told and what is enforced cannot drift. The two `--check` forms are for CI:
one keeps that prose current, the other makes the baseline a ratchet that
only shrinks.

Exit codes: 0 clean, 1 findings, 2 config or usage error.

With no `gate.toml`, the defaults apply: CCN warn 10 / fail 15, 120 lines and
5 parameters per function, files warn 300 / fail 500 lines. `gate.example.toml`
shows every option. `--root` and `--config` let you try a config against a repo
without adding anything to it.

## The rules are meant to be challenged

A gate that only ever says pass or fail goes stale. gate asks for criticism of
itself in three places, so an agent working in the repo is told to look for
weaknesses and report them without being asked:

- **`gate gaps`** lists what gate can see for itself: checks that never run,
  import directions the code keeps to that no rule protects, limits that could
  be tightened today at no cost, files it is told to skip, and overdue upkeep.
  It always exits 0 and changes nothing.
- **The generated rules** end with an "Improving these rules" section. It
  tells the agent that passing is the minimum, what kinds of gap to look for,
  and to report them with evidence and the exact change proposed.
- **The `gate-review` skill** is the same question asked on purpose: where to
  look (including recent bug fixes, and what no tool can see), and how to
  write a proposal with its cost measured.

In all three the agent proposes and the human decides. Nothing here lets an
agent change `gate.toml`, a limit, or the baseline by itself.

## What is built

| Check | Blocks on |
|---|---|
| `syntax` | parse errors a native parser confirms (Python, TOML); others warn unless listed in `[syntax] fail_on` |
| `complexity` | cyclomatic complexity, function length, parameter count |
| `size` | file line count, with per-glob overrides |
| `boundaries` | `a !-> b` rules and "only X may import Y" rules, through relative imports, tsconfig aliases and workspace packages |
| `lint`, `typecheck`, `format`, `security`, `deps` | your command, or the first installed tool for the language |
| `[tests.<name>]`, `[[command]]` | exit code of your own command |

Only `complexity`, `size` and `boundaries` can be baselined. A baselined
finding that gets worse fails again.

## Adapters

- `adapters/claude-code/`: a Claude Code plugin. Its Stop hook runs
  `gate check --changed --json` and sends failures back to the agent, up to
  `GATE_HOOK_MAX_BLOCKS` times (default 3), then leaves it to you. It does
  nothing in a repo without a `gate.toml`. Try it with
  `claude --plugin-dir /path/to/gate/adapters/claude-code`.
  The plugin also ships the `gate-cleanup` skill
  (`adapters/claude-code/skills/gate-cleanup/SKILL.md`): one file per pass from
  `gate todo`, tests first, then `gate baseline --prune`, then stop for review.
  It also ships `gate-review` (`skills/gate-review/SKILL.md`), for finding
  weaknesses in the rules and proposing changes. For other harnesses, point
  `AGENTS.md` at those files; the generated rules already carry the short
  version.
- `adapters/github-actions/gate.yml`: a workflow to copy into a repo. Because
  gate is private, the repo using it needs a `GATE_READ_TOKEN` secret: a
  fine-grained token with read access to the contents of `spikefu/gate`.

## Tests

```bash
.venv/bin/pytest -q
```

`tests/unit` covers each check and the baseline arithmetic. `tests/integration`
drives the real CLI and the hook script against throwaway git repos.
