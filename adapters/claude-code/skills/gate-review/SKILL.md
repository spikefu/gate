---
name: gate-review
description: Look for weaknesses in a repo's gate rules and checks and propose improvements. Use when the user asks to review, audit, tighten, challenge or improve the gate rules, limits, boundaries or checks; asks what gate is missing or why a problem got through; or after you finish a substantial piece of work in a repo with a gate.toml and noticed something the gate did not catch. Proposes changes; never applies them.
---

# gate review

A repo's rules are only as good as the last time someone asked where they fall
short. This skill is that question, asked on purpose. The output is a short
list of proposed changes for the human to accept or refuse.

Do this unprompted too: whenever work in a gated repo shows you a gap, say so
in your report, in the format below, even though nobody asked.

## Where to look

1. **What gate can see for itself.** Run `gate gaps --json`. It lists checks
   that never run, import directions nothing protects, limits looser than the
   code needs, files gate is told to skip, and upkeep that is overdue.

2. **What almost failed.** Run `gate check --profile full --json` and read
   the warnings and the `skipped` list. A cluster of functions just under a
   limit says the limit is doing the designing. A skipped check is a rule
   nobody is enforcing.

3. **What got through.** Read recent fixes: `git log` for bug fixes, reverts
   and follow-up commits. For each, ask what check would have caught it
   before it merged. This is the most valuable source; a rule that would have
   prevented a real bug is worth ten that might prevent an imagined one.

4. **What no tool here can see.** Read the code the gate passes. Look for:
   - tests that would still pass if the code under them were wrong
   - the same logic written twice
   - a module that knows about something it should reach through an interface
   - errors swallowed, or handled differently in each place
   - comments that narrate instead of saying why
   - a directory whose purpose a newcomer could not state

5. **What is wrong with the gate itself.** A finding that was a false
   positive. A check that is slow enough that people will skip it. A message
   that did not say what to do. A rule that produced a worse design in order
   to satisfy a number.

## What to propose

For each gap, give:

- **Problem.** What can go wrong, with evidence: a file and line, a commit, a
  count. Not "tests could be better" but "these four tests assert only that
  the function returns".
- **Change.** The exact edit: the `gate.toml` lines, or a script and the
  `[[command]]` entry that runs it. When gate has no check for something,
  writing a small project script is the right answer; anything with an exit
  code can be a check.
- **Cost today.** How many existing violations the change would create, and
  how likely it is to flag something that is fine. Measure rather than guess:
  copy `gate.toml` somewhere outside the repo, edit the copy, and run
  `gate check --config <copy>`; that changes nothing in the repo.
- **Scope.** Whether this is for this repo's `gate.toml`, or a missing
  capability in gate itself that belongs upstream.

Rank by what would have prevented real trouble. Three proposals someone will
read beat fifteen they will skim.

## Tightening and loosening

Both are legitimate. A limit that only produces arbitrary splits should be
raised. A limit nothing comes near should be lowered so it starts to mean
something. Say which and why. What is not legitimate is loosening a rule in
the same change as the code it would have failed.

## Never

- Never edit `gate.toml`, the baseline, or a check script as part of a
  review. Propose, and let the human decide.
- Never present a guess as a finding. If you did not verify it, say so.
- Never pad the list. No gaps found is a valid result; say what you looked at.
