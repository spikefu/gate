---
name: gate-cleanup
description: Work down a repo's gate baseline one file at a time. Use when the user asks to clean up, pay down, shrink, or clear the gate baseline, to fix baselined violations, to reduce complexity or file size that gate reports, or says "gate cleanup" or "next cleanup". Requires a gate.toml and .gate/baseline.json in the repo.
---

# gate cleanup

The baseline (`.gate/baseline.json`) lists violations that existed when the
repo adopted gate. They are tolerated, not accepted. This skill removes them
one file per pass. The human sees the shape of each pass before it happens,
approves it once, and reviews the result.

One pass is one file. A whole-repo cleanup in one diff cannot be reviewed, and
an unreviewed refactor is worse than the complexity it removed.

## The pass

1. **Pick the file.** Run `gate todo --next 1 --json`. Take the file it
   returns unless the user named one. If it reports stale entries, run
   `gate baseline --prune` first.

2. **Read before touching.** Read the whole file and its callers. State in
   one or two sentences what the file is responsible for. If it has more than
   one responsibility, that is usually the refactor.

3. **Show the shape, then wait once.** Before changing anything, tell the
   human what this pass will do:
   - **Split**: what moves out, into which new file or function, and what it
     will be called
   - **Refactor**: each function you will change, its number today, and the
     change in a few words
   - **Leave alone**: anything that will still be over a limit afterwards,
     and why splitting it would make it worse
   - **Expected result**: the numbers you expect, and what you will not
     touch (limits, suppressions, test assertions, public interfaces)

   Keep it to what fits on one screen; a table per heading works well. Then
   wait for a single go-ahead. That one approval covers the whole pass: do not
   ask again per file or per edit. If the human already told you to proceed
   without asking, show the plan anyway and carry on.

   The point is that the human understands the change before it exists, at
   the cost of one decision, not one per step.

4. **Pin the behaviour first.** For each function you will change, find the
   tests that would fail if its behaviour changed. A test that only checks
   that something exists or does not throw does not count. If there are none,
   write characterization tests against the current behaviour and get them
   passing before you change any product code. Keep them.

5. **Refactor without changing behaviour.** Follow the plan you showed.
   - Split by responsibility, not by line count. Cutting a long function into
     `part1` and `part2` satisfies the number and helps no one.
   - Look for the pattern that is already there: a dispatch table hiding in an
     if-chain, a parameter group that is really one object, an adapter mixed
     into domain logic. Name it and extract it.
   - Keep the public interface the callers use. If it has to change, stop and
     ask: that was not in the plan the human approved.
   - Stay in this file, plus new files you extract into and the tests. Note
     other problems you see; do not fix them in this pass.
   - Comments say why, or what a unit is responsible for. Do not narrate.

6. **Verify.** Run `gate check --profile full`. It must pass, including the
   tests. Then run `gate todo` and confirm this file's entries are gone or
   smaller.

7. **Record the gain.** Run `gate baseline --prune`. It removes fixed entries
   and tightens improved ones, so the gain cannot be lost later.

8. **Stop and report.** Do not start the next file. Report:
   - where you departed from the plan, and why. A step that turned out
     wrong and had to be redone belongs here.
   - the file, and each violation before and after (for example CCN 48 to 9)
   - what you extracted and why it is a real seam
   - tests you added
   - anything you could not fix, and anything you noticed elsewhere

   Commit only if the user has asked for commits; then use one commit for this
   file, with the baseline change in it.

## Never

- Never run `gate baseline --accept`. Growing the baseline is the human's call.
- Never edit `.gate/baseline.json` by hand.
- Never raise a limit, add a size override, or add an exclude in `gate.toml`
  to make a finding go away. If a limit is wrong for this repo, say so and let
  the human change it.
- Never delete, skip, or loosen a test to get to green.

## When it will not come down

Some functions resist: a parser, a reducer over many event types, generated
code. Do what is safe, run `gate baseline --prune` to keep the partial gain,
and report what is left and why. A smaller honest number beats a clean number
reached by hiding the branching somewhere gate does not look.
