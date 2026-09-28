---
name: next-slice
description: Resume a plan-driven port or migration in this repo — read the plan document, audit its progress table against git history to prove the record is true, then hand the next slice to a fresh subagent that executes it by the plan's own ritual and closes it with a commit and a filled-in progress row. Use for "next slice", "continue the plan", "check state and continue", "what is next on the plan", or any request to carry on work whose state lives in a markdown plan rather than in chat.
---

# Next slice

This project is driven by a **plan document**, not by chat. The document is the record: what
is done, what it cost, what it taught, and what is next. A slice is one increment of that
plan, and it is not finished until the document says so.

Because the document is the record, a slice needs no conversational history — only the plan,
the repo, and git. So **the slice itself is executed by a fresh subagent**, one per slice, and
this session keeps only the plan's state. A slice is hours of test output, failed runs and
half-read sections; none of that belongs in the context that decides *which* slice is next.

Four phases, in order. Do not start a later phase before an earlier one has answered.

**This session does not execute slices. It reads, audits, dispatches, verifies and relays —
and nothing else.** Concretely: in this session you never write or edit a source file, a test,
or the plan's body; you never run the suite, the build or the hand-run; you never make the
slice's commit or fill its row. Every one of those is the subagent's, without exception. The
only writes this session makes are the ones the *user* asks for outside a slice. Your own tool
use here is `git log`, `git show`, `git status`, and reading the few parts of the plan that
phase 1 names — if you are about to reach for `Edit`, `Write`, or a command that runs tests,
you have already left the skill, and the fix is to dispatch, not to continue.

## 1. Find the plan, and read only what selects the slice

The plan is a tracked markdown file at the repo root with a `Status:` line near the top and a
progress table of one row per slice. `WINDOWS.md` is this repo's; `SPEC.md` beside it is the
*specification*, not the plan — it describes the program, not the work. If more than one
candidate exists, prefer the one whose table has unfilled rows, and say which you picked.

Read, in this order, and no further:

- the `Status:` line and the paragraph around it — the plan's own claim about where it is;
- the **progress table** (`## 11. Progress` here): the last filled rows, and the first row
  whose status is `todo`;
- the **ritual's numbered steps** (`## 9. Slices` here) — enough to know how a slice opens and
  closes, and what the commit and row convention is. Not the slice entries.

Stop there. The slice's own entry, the sections it points at, and the code it touches are the
subagent's reading, not yours — pulling them in here is the context this skill exists to keep
out. You need the slice's *name* and its place in the order; the subagent needs its meaning.

Then collect the **conventions that bite** — line endings, which interpreter runs the suite,
how commit messages are written, what a hand-run needs that a fresh checkout lacks, who fills a
row's hash — because the subagent has never seen them and phase 3's prompt must carry them.
Memory is per machine, and this plan has been worked from more than one, so look in three
places and say which each came from:

1. **Project memory**, if this machine has any about the ritual.
2. **The ritual itself.** `WINDOWS.md` §9's steps name the interpreters (`.venv\Scripts\python`
   on Windows, `/usr/bin/python3` on the Mac), the commit format (`W<n>: <what>`), and the CI
   run that stands in for the Mac suite.
3. **Git history.** `git log --format=%s -20 -- WINDOWS.md` shows the hash-fill convention as
   it is actually practised (`WINDOWS.md: W<n>'s commit hash in its section 11 row`).

A convention the prompt needs that none of the three records — typically line endings or a
hand-run's setup on this box — is a question for the user, not a guess to pass on. Ask it
before dispatching, and once answered, save it to project memory so the next sitting on this
machine has it.

## 2. Audit the record against git

The plan claims history. Git holds it. Before dispatching any new work, prove the two agree,
because a slice built on a false record repeats work or skips it. This stays in this session:
it is cheap, it is all `git log`, and its conclusion is the thing worth remembering.

Check, and report each as a line:

1. **Every `done` row's commit exists.** `git log --oneline` for each hash in the table;
   `git cat-file -t <hash>` if a row's hash is short or ambiguous. A hash that does not
   resolve is the finding that stops everything until it is explained.
2. **Every slice commit is on a row.** The plan shares its history with unrelated work
   (`skills:`, `launchd:`, `bot:`, `mac-cleanup:` commits run through the same range), so
   count only the plan's own commits:
   `git log --oneline <baseline>..HEAD | grep -E '^[0-9a-f]+ W[0-9]+[a-z]?: '`. Every hit must
   be on a row — a `Commit` cell may hold several (`12100d6, ef04618` for W1c), so match the
   hash anywhere in the cell. The `WINDOWS.md: …commit hash…` follow-ups are the hash-fill and
   need no row of their own. Anything else in the range is not the plan's and is not a finding — unless it
   touches a file the next slice names, in which case say so, because it moved the ground the
   slice's text was written on. A slice commit with no row is work the record lost.
3. **Order and dates.** Row order matches commit order; a row's date matches its commit's
   author date (`git show -s --format=%ad --date=short <hash>`).
4. **The hash-fill convention.** In this repo a row's `Commit` cell is filled by the *next*
   commit, so the newest slice's row may legitimately be one commit behind. Confirm that is
   what you are looking at rather than a genuinely empty cell.
5. **The `Status:` line agrees with the table.** This is the one that drifts: the header
   sentence is prose and the table is data, and they are edited at different moments. If they
   disagree, the table wins — the header is stale and fixing it belongs to this slice's commit.
6. **The working tree is clean** (`git status --porcelain`) and you are on the branch the plan
   assumes. Uncommitted changes before a slice are somebody's unfinished slice; stop and ask.
7. **The claims a row makes about the code are still true** for anything the next slice
   depends on — the function it says it added, the test it says it un-gated. Spot-check the two
   or three the next slice builds on, not all of them.

Report the audit in a few lines before dispatching anything. If a check fails in a way that
changes what the next slice is, stop and say so rather than working around it.

## 3. Hand the slice to a fresh subagent

One slice, one subagent, fresh context — `general-purpose` (it needs the full toolset: shell,
file edits, git). Never `fork`: inheriting this conversation defeats the point. Never two at
once — slices are strictly ordered, and the plan forbids starting one before the previous
row is filled in.

**There is no slice small enough to do inline, and the small ones are the trap.** A slice that
reads like a one-line fix still owes the full ritual — a red test, a build, a hand-run against
the real thing, the whole suite, a commit, a row — and the sitting where that gets skipped is
the sitting the record stops being true. The size of the diff is not the size of the slice:
W5c was one line of `bot.cmd` and took a four-item checklist against a registered scheduled
task to find it. Dispatch it. The same holds when the previous subagent has just finished and
its findings are fresh in your context: that is an argument for putting them in the next
prompt, never for keeping the work.

If a slice turns out to be genuinely trivial, the cost of dispatching it is one subagent and a
few minutes. The cost of getting that judgement wrong is a plan whose table says a ritual ran
that never did.

The subagent has no history, so the prompt must stand alone. It carries pointers, not content:
name the sections and let it read them, rather than quoting the plan into the prompt.

> You are executing one slice of a plan-driven port. The plan document is the record, not this
> prompt. Repo: `<path>`. Plan: `<file>`. Slice: **`<id> <title>`** — and only that slice.
>
> Read first, in this order: the plan's ritual section (`<§>`), the slice's own entry there,
> and every section that entry points at — an entry that says "Green: §4's last paragraph"
> means that paragraph is the specification and you have not read the slice until you have read
> it. Then the progress table (`<§>`) for the rows of the slices this one builds on.
>
> Then follow the ritual's numbered steps exactly, in order, without skipping: `<the steps,
> named>`. It outranks any instinct of your own about how to do the work.
>
> Conventions that will bite you, from this repo's record: `<the memory's conventions —
> interpreter and command for the suite, line endings, how to write files and commit messages,
> what a hand-run needs that a fresh checkout lacks, the commit-name format, who fills the
> row's hash>`.
>
> The audit of the record is already done and passed; do not redo it. `<Any finding from
> phase 2 the slice must carry — a stale `Status:` line to fix, a debt to record.>`
>
> Report back, briefly: what the run step showed (the figure, if the plan named a measurement),
> the suite counts, anything that contradicted the plan and what you amended, any debt you
> could not discharge, the commit hash, and the row you wrote.

The discipline the subagent is being held to, and which its report is checked against:

- **One commit per slice**, named in the plan's convention (`W<n>: <what>` here). The message
  says what the slice found, not what the diff shows.
- **The run step is not optional and is not a test.** It is the slice's one contact with
  reality, and what it showed — a number, a failure, a surprise — is the most valuable thing
  the row will carry. If the plan names a measurement, take it and record the figure.
- **Write down what contradicted the plan.** A slice that finds the plan wrong is a successful
  slice; amend the section that was wrong, in place, and say in the row that you did. Never
  silently correct a prediction — the correction is the finding.
- **Record the debt you could not discharge.** Anything the plan asks for that this box cannot
  run (the other platform's suite, hardware you do not have) goes in the plan's pending list,
  named, rather than being quietly dropped from the row.
- **Fill the progress row last**, at the ritual's step for it and nowhere else: status, date,
  suite counts, and what the run step showed. Then the follow-up commit that writes the row's
  own hash, if that is the convention.
- **Update the `Status:` line** in the same commit as the row, to name the slice just finished
  and the one that is next. Phase 2 exists because this is the line that rots.

If the subagent comes back blocked — a fixture it cannot build, a decision that is the user's —
do not start a second subagent on the same slice from a clean slate. Relay the block, get the
answer, and continue that subagent with `SendMessage`; its context is the half-done slice.

## 4. Verify the close, then relay

A subagent's report is a claim about the record, and this skill does not take claims. Before
telling the user anything, run phase 2's checks against the new row only: the commit exists,
the row is filled, the date matches, the `Status:` line names this slice as done and the next
one as next, the tree is clean. That is four `git` commands, and it is the whole reason the
audit is cheap enough to repeat.

The subagent's report is not shown to the user, so relay it: what the audit found, what the
slice did, what the run step showed, the suite numbers, what contradicted the plan, what debt
was recorded, and which slice is next.

Verifying is reading, not repairing. If the close is wrong — a row half-filled, a stale
`Status:` line, a commit that never landed — that is the subagent's to finish: relay the gap
and continue it with `SendMessage`. Fixing it yourself puts the main session's hand in the
record and hides, from the next audit, that the ritual did not close on its own.

For several slices in one sitting, loop phases 2–4 — a fresh audit, then a fresh subagent, per
slice. The record has changed between them, which is exactly why the next slice gets a context
that reads it anew rather than one that remembers writing it.
