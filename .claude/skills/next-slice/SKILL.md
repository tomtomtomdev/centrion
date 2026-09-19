---
name: next-slice
description: Resume a plan-driven port or migration in this repo — read the plan document, audit its progress table against git history to prove the record is true, then execute the next slice by the plan's own ritual and close it with a commit and a filled-in progress row. Use for "next slice", "continue the plan", "check state and continue", "what is next on the plan", or any request to carry on work whose state lives in a markdown plan rather than in chat.
---

# Next slice

This project is driven by a **plan document**, not by chat. The document is the record: what
is done, what it cost, what it taught, and what is next. A slice is one increment of that
plan, and it is not finished until the document says so.

The skill is three phases, in order. Do not start phase 3 before phase 2 has answered.

## 1. Find the plan and the ritual

The plan is a tracked markdown file at the repo root with a `Status:` line near the top and a
progress table of one row per slice. `WINDOWS.md` is this repo's; `SPEC.md` beside it is the
*specification*, not the plan — it describes the program, not the work. If more than one
candidate exists, prefer the one whose table has unfilled rows, and say which you picked.

Read, in this order:

- the `Status:` line and the paragraph around it — the plan's own claim about where it is;
- the **progress table** (`## 11. Progress` here): the last filled rows, and the first row
  whose status is `todo`;
- the **ritual** that governs a slice (`## 9. Slices` here): the numbered steps, and the entry
  for the slice you are about to do — red test, green code, run step, test step;
- every section the slice's entry points at. A slice entry that says "Green: §4's last
  paragraph" means that paragraph is the specification and you have not read the slice until
  you have read it.

Also read any project memory about the ritual before deciding how to work: the conventions
that bite (line endings, which interpreter runs the suite, how commit messages are written)
are recorded there and are not re-derivable from the file.

## 2. Audit the record against git

The plan claims history. Git holds it. Before doing any new work, prove the two agree, because
a slice built on a false record repeats work or skips it.

Check, and report each as a line:

1. **Every `done` row's commit exists.** `git log --oneline` for each hash in the table;
   `git cat-file -t <hash>` if a row's hash is short or ambiguous. A hash that does not
   resolve is the finding that stops everything until it is explained.
2. **Every commit is on a row.** `git log --oneline` since the baseline row's hash, minus the
   docs-only follow-ups that fill hashes in. A slice commit with no row is work the record
   lost.
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

Report the audit in a few lines before touching anything. If a check fails in a way that
changes what the next slice is, stop and say so rather than working around it.

## 3. Do the slice, by the plan's ritual

Follow the plan's own numbered ritual exactly; it outranks anything here. In this repo it is:
red test, green code, build, run, test, commit, progress row. What this skill adds is the
discipline around it:

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

Finish by telling the user, briefly: what the audit found, what the slice did, what the run
step showed, the suite numbers, and which slice is next.
