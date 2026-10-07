---
name: plan-build
description: From a goal to a finished, pushed project in one command — write a 5W1H plan (why, what, who, where, when, how), have it reviewed and fold the fixes in, split it into small slices, then run every slice in its own subagent test-first (red → green → build, run, full suite), tick its progress row, commit and push, and keep going until no slice is left. Use for "plan and build", "plan this then do it all", "5W1H plan", "slice it and implement everything", or a re-run on an existing plan to carry on where it stopped.
argument-hint: "<goal, ticket or spec path> | <plan path to resume> [--plan-only] [--no-push] [--slice <id>]"
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Agent, SendMessage, TodoWrite, AskUserQuestion
---

# Plan, then build every slice

The **plan document** is the record. It says what is being built and why, how it was cut into
slices, which slices are done, what each one cost and what it found. Chat is not the record: a
re-run of this skill with nothing but the plan's path must be able to carry on.

Five phases, in order. A later phase never starts before an earlier one has answered.

1. **Plan** — 5W1H, written into the plan file.
2. **Review** — an independent reviewer critiques it; this session folds every fix in.
3. **Slice** — cut the plan into small, ordered, testable slices with a progress table.
4. **Build** — one fresh subagent per slice: test first, implement, build, run, full suite,
   tick the row, commit, push.
5. **Loop and close** — verify each close against git, then the next slice, until none is left.

**Resuming.** If the argument is an existing plan (or the repo already has a plan for this
goal under `docs/plans/`), skip to the first phase the plan has not finished: no `## Review`
section → phase 2; no progress table → phase 3; otherwise phase 4 at the first row that is not
`done`. Say which phase you resumed at and why.

## 1. Plan with 5W1H

Read before you write: the goal (the argument — a sentence, a ticket, a spec file), the repo's
`README`/`CLAUDE.md`, its layout, its test and build commands, and `git log --oneline -20`.
A plan that names commands the repo does not have is a plan the first slice will contradict.

Ask the user only what the repo cannot answer and the plan cannot proceed without — usually
scope boundaries or a deadline. Ask them together, in one `AskUserQuestion`, then write.

Write `docs/plans/<slug>.md` (follow the repo's own plan location if it has one), with:

```markdown
# <Title>

Status: planning — next: review
Goal: <one sentence>

## Why
The problem, who has it, and what is true when this is done. The success criteria live here,
as checkable statements — every one must end up covered by some slice's test.

## What
In scope, as a short list of capabilities. **Out of scope**, as an explicit list — the
section the review leans on hardest.

## Who
Users of the result; owners/reviewers of the work; the agents doing the slices.

## Where
Repo, branch, the modules/files touched, and the environments it runs in
(simulator, server, CI). New files named with their paths.

## When
Order and dependencies, milestones, any deadline. Not dates per slice — the order is what matters.

## How
Approach and key design decisions with their reason, data/interface shapes, risks and how
each is retired, and the **commands**: the test command, the build command, how to run it.
These three commands are what every slice's close is checked against.
```

Commit the plan alone (`plan(<slug>): 5W1H`), so the review's changes show as their own diff.

## 2. Review, and fold the fixes in

The author is the worst reviewer of their own plan, so the review is a **fresh subagent**
that has not seen this conversation. Prompt it with the plan's path and the repo, and ask it to
read the code the plan touches, then report findings — each with the section, the problem, and
the fix — against:

- every success criterion in *Why* is testable, and nothing in *What* lacks one;
- *How*'s commands actually exist and work in this repo (it may run them);
- *Where* names real files, and the touched code is what the plan says it is;
- hidden dependencies, ordering mistakes, risks with no retirement, scope creep past *What*;
- anything ambiguous enough that two implementers would build different things.

Then fold it in, in this session: apply every fix you agree with **into the sections it
concerns** (the plan stays a plan, not a plan plus an errata list). For any finding you reject,
or that is a decision only the user can make, say so — reject with a one-line reason, ask the
user about the rest. Record the review at the end as `## Review` — one line per finding:
`fixed` / `rejected: <why>` / `decided: <answer>`. Commit (`plan(<slug>): review folded in`).

## 3. Slice

Cut *How* into slices. A good slice:

- is **small** — one behaviour, one test file's worth, roughly an hour of work, one commit;
- **leaves the build green and the app runnable** when it closes;
- has a **red test** you can name before writing any code — if you cannot, the slice is too vague;
- depends only on slices before it (say which).

Write them into the plan as `## Slices`, each entry:

```markdown
### S<n> — <title>
Depends on: S<…> | none
Red: <the failing test(s) to write first, and the file they go in>
Green: <what to implement, pointing at the How section that specifies it>
Run: <the hand-run that shows it working — a command, a screen, a request>
Done when: <the checkable condition, tied to a Why criterion where one applies>
```

And `## Progress`:

```markdown
| Slice | Status | Date | Commit | Tests | Run showed |
|-------|--------|------|--------|-------|------------|
| S1 <title> | todo | | | | |
```

Set `Status: slicing done — next: S1`, commit (`plan(<slug>): slices`), and push. With
`--plan-only`, stop here and report the plan's path and its slice list.

**Branch.** Before the first push, if the current branch is the default branch, create
`feat/<slug>` and work there; never push slices straight to `main`. Remember the branch in the
plan's *Where*.

## 4. Build one slice — in a subagent

**This session does not implement.** It reads the plan's progress, dispatches, verifies and
relays. A slice is hours of test output and dead ends; none of that belongs in the context that
decides what is next. So each slice runs in a **fresh subagent**, one per slice, sequentially
(slices share a branch and a plan file — parallel ones would race on both).

The subagent has no history, so the prompt stands alone, and it carries pointers, not content:

> You are implementing one slice of a plan. The plan is the record, not this prompt.
> Repo: `<path>`. Branch: `<branch>`. Plan: `<plan path>`. Slice: **S<n> — <title>**, only that.
>
> Read first: the plan's *Why*, *How* (especially its commands) and the slice's entry, plus
> every section the entry points at; then the progress rows of the slices it depends on.
>
> Then, in this order, without skipping:
> 1. **Red.** Write the slice's failing test(s). Run them and see them fail for the right
>    reason — a test that passes before the code exists tests nothing.
> 2. **Green.** Write the least code that makes them pass. Refactor with the tests green.
> 3. **Build** with `<build command>` — clean, no new warnings you introduced.
> 4. **Run** it: `<run step>`. This is the slice's contact with reality, not another test.
>    Note what it showed.
> 5. **Full suite** with `<test command>` — everything green, not just the new tests.
>    A failure you caused is yours to fix; one that was red before you started is reported,
>    not hidden.
> 6. **Record.** In the plan: fill the slice's progress row (status `done`, date, test counts,
>    what the run showed); update the `Status:` line to name this slice done and the next one
>    next. If the slice proved the plan wrong, amend the section that was wrong in place and
>    say so in the row — the correction is a finding, never a silent fix.
> 7. **Commit** code, tests and plan together as one commit: `<repo's commit style>`
>    (e.g. `feat(<slug>): S<n> <what>`), message saying what the slice found, not what the
>    diff shows. Then **push** `<branch>`. Fill the commit column with the hash in a follow-up
>    commit `plan(<slug>): S<n> hash`, and push again.
>
> If you are blocked — a decision that is the user's, a tool or credential you lack — stop
> before committing and report the block; do not guess past it.
>
> Report back briefly: red (what failed and why), green, build result, what the run showed,
> suite counts, anything that contradicted the plan, the commit hash, and whether the push landed.

Fill the angle brackets from the plan and the repo's conventions (commit style from
`git log`, any `CLAUDE.md` rules). A convention the prompt needs that neither records is a
question for the user before dispatch, not a guess.

## 5. Verify, then loop

A subagent's report is a claim. Before moving on, check it against git — reading only, never
repairing:

- `git log -2` shows the slice commit and the hash commit, on the right branch;
- the plan's row for the slice is `done` and filled, and `Status:` names the next slice;
- `git status` is clean, and `git status -sb` shows the branch is not ahead of its remote.

If something is off — a row half-filled, a push that did not land, a red suite — continue
**the same subagent** with `SendMessage` to finish it; its context is the half-done slice.
Fixing it here would put this session's hand in the record and hide that the slice didn't
close on its own. A **blocked** slice goes to the user; once answered, continue that subagent.
If the user cannot answer now, mark the row `blocked: <why>`, and carry on with the next slice
that does not depend on it.

Then tell the user in two or three lines what the slice did and what is next, and dispatch the
next slice. **Keep going until every row is `done`** (or `blocked` with only dependent slices
left). Do not stop between slices to ask permission — the plan was the permission.

With `--slice <id>`, build only that slice and stop. With `--no-push`, commit but do not push,
and say so in every relay.

## Close

When no slice is left: run the full suite and the build once more on the branch tip, check
every *Why* success criterion against the slices that covered it, set
`Status: done — <date>`, commit and push. Then report to the user: the plan's path, the branch,
the slices with their commits, the run results, any criterion not met, any `blocked` row, and
anything the slices found that the plan got wrong. Offer to open an MR/PR; don't open one unasked.
