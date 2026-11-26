---
name: gate-design-review
description: Judge the design of a change or a module against encapsulation, decoupling, side effects, modularity and separation of responsibilities, and propose improvements. Use when the user asks for a design review, an architecture review, whether code is well structured or well factored, or what is wrong with a module's design; or after you finish a substantial change in a repo with a gate.toml and want a second look at its shape before handing it over. Proposes changes; never makes them.
---

# gate design review

`gate check` enforces what can be measured without being wrong: limits,
declared boundaries, counts. It cannot tell whether a module has one job or
whether an interface exposes the right things. Those need a reader. This
skill is that reading, done with the facts in hand, and it ends in a short
list of proposals for the human to accept or refuse.

It does not block anything and it changes no code.

## 1. Fix the scope

Review what was asked: a change, a module, or a folder. If nothing was named,
review the current change.

- The change: `git diff` against the base the user is working from, and
  `gate metrics --changed --json`.
- A module or folder: read it whole, and run `gate metrics --json`, then look
  at its files and its folder row.

Say in one line what you are reviewing and what you are not.

## 2. Read the facts first

`gate metrics` reports, without judging:

- per file: lines, functions, the most complex and the longest function, how
  many of the repo's files it imports and how many import it, the outside
  packages it uses, how many things it offers, and its shared mutable state
- per folder: how many folders it depends on and how many depend on it, its
  instability (0 is depended on and depends on nothing; 1 is the reverse),
  whether it has an entry point, and how many imports go past it

Also run `gate check --json` on the same scope and read the warnings. A
warning is where the gate was unsure; that is where a reader is most useful.

Numbers point at where to look. None of them is a finding by itself: a file
everything imports may be a well-chosen foundation, and a long file may be a
table.

## 3. Read the code against five questions

For each, the question, what counts as evidence, and what does not.

**Separation of responsibilities. Does each unit have one reason to change?**
Describe what the file or function is responsible for in one sentence. If the
sentence needs "and", name both halves and ask whether they would change
together. Evidence: two halves that change for different reasons, such as
parsing input and deciding policy, or a function whose first half and second
half share no variables. Not evidence: length alone.

**Encapsulation. Can the inside change without its users changing?**
Look at what the unit exposes and what its users actually touch. Evidence:
callers reaching past an entry point (`imports_past_entry`), a caller that
knows the shape of another module's internal data, a function returning an
internal structure that callers then mutate, fields that are set from
outside. Not evidence: a small helper being public.

**Decoupling. Does it depend on what it must, and only that?**
Follow the imports. Evidence: a core rule that imports a framework, a
database client or the clock directly; two modules that each need the other;
a change here that forces a change in an unrelated module; a parameter passed
through three layers that use none of it. Not evidence: depending on
something stable and shared.

**Side effects. Could you predict what a function does from its arguments?**
Evidence: writes to module-level state (`shared_state`), I/O in a function
whose name describes a calculation, an argument modified in place when the
caller would not expect it, work done when a module is merely imported,
hidden dependence on call order. Not evidence: an effect in a function whose
whole job is that effect and whose name says so.

**Modularity. Could a newcomer find where a thing lives, and change it there
alone?** Evidence: the same decision made in two places, a folder whose
purpose cannot be stated, a feature whose code is spread across files that
all have to change together, a "utils" that everything imports and that keeps
growing. Not evidence: a module being small.

## 4. Report

Give at most five findings, ranked by what would cause real trouble first.
Three that someone will act on beat ten they will skim. For each:

- **Quality.** Which of the five.
- **Evidence.** File and line, and the fact from `gate metrics` if one bears
  on it. Quote the code that shows the problem. "This module does too much"
  is not a finding; "`load()` at line 40 parses the file and decides which
  records are valid, and those change for different reasons" is.
- **Consequence.** What goes wrong, concretely: what change becomes hard,
  what bug becomes likely.
- **Change.** The smallest change that fixes it: what moves where, what the
  new unit is called, what its interface is. Not a rewrite.
- **Cost.** How many files and callers it touches, and what tests pin the
  behaviour today.
- **Rule.** If a gate rule would keep this from coming back, write it: a
  `[boundaries] forbid` line, a `pkg:` rule, a `[[boundaries.module]]`
  block. Say that adding it is the human's decision.

Then say what you looked at and found sound. That is part of the result: it
tells the human what the review covered.

If you find nothing worth changing, say so. That is a valid review.

## Judgement

- A design is not wrong because you would have written it differently. If you
  cannot name the consequence, it is a preference: leave it out.
- Weigh the fix against the problem. A structure that is slightly off in code
  that rarely changes is not worth a refactor.
- Prefer the fix that removes something over the one that adds a layer. An
  abstraction with one implementation and one caller is usually cost without
  benefit.
- Look for the pattern the code is already reaching for, and name it. Do not
  import one it has no use for.

## Never

- Never edit code as part of a review. If the human accepts a proposal, that
  is a separate piece of work: show the shape of the change, get one
  go-ahead, then do it.
- Never present a number as a verdict. Say what the number made you look at
  and what you found there.
- Never pad the list or soften a real problem to seem balanced.
- Never propose loosening a gate rule here to make the design fit; that
  belongs in `gate-review`, with its own evidence.
