---
name: ios-next-slice
description: Reconcile a plan's slices against git, then implement the next open slice test-first — inline at Tier 0/1, through @ios-implementer at Tier 2 — commit it, and tick it off in the plan
argument-hint: "[plan path or name fragment] [--all] [--dry-run] [--slice <id>] [--agent] [--row-open] [--plan-local]"
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Agent, SendMessage, Skill, TodoWrite, AskUserQuestion
---

Take one plan document, work out **what is actually done** (the plan lies; git does not), then
implement the next open slice **test-first** (inline at Tier 0/1, one `@ios-implementer` at Tier 2), commit it, and write the progress back
into the plan.

`$ARGUMENTS`

| Flag | Effect |
|---|---|
| *(bare path/fragment)* | reconcile, then do **one** slice — the first open one |
| `--slice <id>` | do that slice instead of the first open one (`T5`, `Slice 3`, `Task 2`) |
| `--all` | keep going slice by slice; a blocked slice is recorded and skipped (§9a), and the run stops only when no unblocked slice is left |
| `--dry-run` | reconcile and report only — no task row, no agent, no edits, no commit |
| `--agent` | every slice goes to one `@ios-implementer`, Tier 0/1 included (§6) — the caller's standing choice |
| `--row-open` | the caller already opened this slice's task row: skip §5 entirely; still close it in §8 |
| `--plan-local` | the plan is gitignored: write progress back to the file, never `git add` it (§8) |

`/feature-work` passes all three with `--slice`.

---

## 1 · Resolve the plan

```bash
setopt NULL_GLOB 2>/dev/null   # zsh: an unmatched glob must not abort the listing
ls -t docs/plans/*.md Documentation/**/*.md RefactorDocumentations/*.md .tuntun/plans/*.md 2>/dev/null
```

In this repo the plans live in `RefactorDocumentations/` (e.g. `AppTarget_Modularization_Plan.md`)
and `Documentation/`; `docs/plans/` does not exist. A Tier 2 breakdown already recorded with
`tuntun task plan` is also a plan: `tuntun task list --ticket <KEY>` shows its pieces, and those
rows are the ledger's claims.

- Argument is a path that exists → use it.
- Argument is a fragment → case-insensitive match over those paths. One hit → use it. Several →
  `AskUserQuestion` with the candidates. Zero → say so and stop.
- No argument → list the plans with their last-modified date and ask which one.

Read the plan **in full** before anything else. Never work a slice off a grep.

## 2 · Build the slice ledger

Plans in this repo are not uniform. Recognise whichever of these the file uses, in this order:

| Shape | Example |
|---|---|
| checkbox backlog | `- [ ] T5 \`feat(account)\`: …` / `- [x] T4 …` |
| numbered headings | `## Task 3: Create RankingInteractor.swift`, `## Slice 2 — …`, `## Phase 4 · …` |
| status column / marker | a table row with `✅` / `DONE` / `Status:` |

Produce one row per slice, **in plan order**: id, one-line intent, the files it names, the
acceptance criteria it states, and what the plan *claims* (`open` / `done`). Plan order is the
dependency order unless the plan says otherwise — a slice that names another slice as a
prerequisite waits for it.

A plan with no discernible slices is not a plan for this command: report that and stop.

## 3 · Cross-check every slice against git — this is the point of the command

A ticked box is a claim, not evidence. An unticked box may already be shipped.

```bash
git log --oneline -40
git status --short
git log --oneline --all --grep '<slice id>' --grep '<ticket key>' -i
# per slice, for each file it names:
git log --oneline -5 -- '<path>'
```

Then, for the files the slice names, check the code itself — does the type/method/test the slice
promises exist, and does it do what the slice describes? `git log -- path` only proves the file was
touched, never that this slice's work is in it.

Reconcile each row into exactly one state:

| State | Evidence | Action |
|---|---|---|
| **DONE** | plan ticked **and** the code/commit backs it | skip |
| **DONE, UNTICKED** | code is there, plan says open | tick the plan (§8 write-back only) — no agent |
| **CLAIMED, UNPROVEN** | plan ticked, nothing in git or the code | downgrade to OPEN and say so loudly in the report |
| **IN FLIGHT** | matching changes sit uncommitted in `git status` | do **not** start a new slice on top — report it and stop unless the user says otherwise |
| **OPEN** | nothing | candidate |

**Report the full reconciliation table to the user before touching anything**, with the evidence
column (sha or "code present at `File.swift:NN`" or "—"). On `--dry-run`, stop here.

## 4 · Pick the slice

First `OPEN` row in plan order that is not `BLOCKED`, or the `--slice` one. If it depends on a row
that is still open, it is blocked: say which. If the slice's acceptance criteria have **no named way
to check them**, that is a blocker too — never a box to hand to an agent (`/ios-assign` rule).
Without `--all`, stop and report either one; with `--all`, record it (§9a, *work blocker*) and pick
the next row.

## 5 · Open the task row — main session only

`--row-open` → skip this section: the row is open, and a second `tuntun task start` or
`tuntun task next` would open another slice's row. Check `tuntun task list --ticket <KEY>` shows
this slice's row in progress; anything else → stop and report.

Before the first edit (CLAUDE.md, non-negotiable):

```bash
tuntun task start "<slice id> — <intent>" \
  --kind <feature|improvement|refactor|bugfix|chore|documentation|test|hotfix|migration> \
  --ticket <KEY>   # or --prd <n|name>, or --no-source
  --description "<Markdown: the slice text + acceptance criteria + files>"
```

Source order: the plan's Jira key > its PRD > `--no-source`. **The main session owns the row.** The
subagent never opens one, never closes one. One slice = one row. If the plan was recorded with
`tuntun task plan`, use `tuntun task next` to open the slice's existing row instead of starting a
new one.

## 6 · Size the slice, then write it or hand it to ONE agent — test-first

Size it by CLAUDE.md's tiers **before** choosing who writes it:

| Tier | The slice | Who writes it |
|---|---|---|
| **0 / 1** | ≤5 files, one scene, no Tier 2 trigger | **the main session, inline**, following the procedure skill below. Never an agent: CLAUDE.md forbids handing code to an agent below Tier 2 |
| **2** | a new scene · migration · >5 files · money, auth or keychain · a Worker or `TT*Api` contract · concurrency · a screen built to a design | **`/ios-assign`**, then spawn **`@ios-implementer`** (the only implementing agent in this repo) with that assignment |

`--agent` → every tier takes the Tier 2 column: `/ios-assign`, then one `@ios-implementer` with the
TDD contract below verbatim. The caller asked for this explicitly, and that request is the
exception to CLAUDE.md's below-Tier-2 rule; the review gate in §7 still follows the slice's real
tier.

The procedure skill is chosen the same way at either tier; at Tier 2, name it in the assignment:

| Slice is | Procedure skill |
|---|---|
| new scene / module from scratch | `/ios-scaffold` |
| a bug, crash, regression | `/ios-fix` |
| VIP conversion, service → TTServices Worker, SwiftUI → UIKit, module move, `TT*Api` contract, removing Combine / RxSwift / singletons / storyboards | `/ios-migration` |
| a screen built to a Figma design | `/ios-design` (it produces the Design Spec) |
| anything else inside an existing scene | none; the component rules load themselves. At Tier 2 add `tuntun for <what you are building>` to the assignment |

Whoever writes it follows this **TDD contract**. Whenever an agent writes it (Tier 2, or any tier
under `--agent`), put it in the prompt verbatim, together with the slice text, its acceptance criteria, its files, and everything §3 found, labelled as
*hypothesis to verify*:

> 1. Write the failing test FIRST, in the framework's existing `*Tests` target.
> 2. Run it and paste the RED output: `./run.sh test <Framework> [SuiteName]`.
> 3. Implement the smallest change that passes it — no speculative extras.
> 4. Run it again and paste the GREEN output. Report the test names.
> 5. `tuntun lint <touched files>` — clear every P0 and P1, including pre-existing ones in files
>    you changed.
> 6. Do not commit. Do not touch `tuntun task`. Report files changed + red/green evidence.

`./run.sh` writes `./build.log`, so **never run two of them at once** — one agent at a time, and no
parallel build in the main session while it runs. Never run bare `./run.sh`: with no command it
builds staging and launches the app. Invoking `/ios-next-slice` is the explicit request for the slice's
tests that CLAUDE.md requires before any test run; it is not a request to build or launch the app.
If a `/ticket-workflow` pass is in flight (`.tuntun/ticket-workflow.pass` exists and is fresh), do
not start a test run: the pass owns the compiler and the simulator. Report it and stop.

If the slice genuinely has no testable behaviour (a pure token/layout change), say so explicitly in
the report and substitute the real check — `tuntun lint`, and `/ios-design` STEP 8 screenshot-vs-
render when the criteria are visual. Never fabricate a test to satisfy the contract.

## 7 · Verify before the commit

1. `tuntun lint <touched files>` in the main session — trust nothing reported second-hand.
2. **`/ios-review`** on the changed files; fix every BLOCKER and MAJOR. When an agent wrote the
   slice, the fixes go back to that same agent by `SendMessage`, as with FINDINGS below.
3. **`@code-reviewer`**, given the changed files as its scope, whenever the slice hits a Tier 2
   trigger (money/auth/keychain, a Worker or `TT*Api` contract, a new scene or migration,
   concurrency, a screen built to a design, >5 files). FINDINGS is a rejection: the same
   `@ios-implementer` fixes (use `SendMessage` to the agent you spawned, never a new one), and the
   same reviewer re-checks the findings and what the fix could have broken. Two rounds on one
   finding → the user decides (under `--all`: record the slice as a *work blocker*, §9a, and move
   on; the question goes in the final report). Never approve on its behalf, never re-spawn a
   reviewer for a nicer answer. Design slices add `/ios-design` STEP 8. A Tier 0/1 slice closes on
   `/ios-review`.

## 8 · Commit and write the progress back

Commit **only this slice's files** — `git add <paths>`, never `git add -A`:

```
type(scope): [<TICKET>] lowercase description of the slice
```

No `Co-Authored-By`, no AI attribution, ever.

Then update the plan in the same commit:

- Checkbox plan → `- [ ] T5 …` becomes `- [x] T5 … — \`<sha>\``.
- Heading plan → append `**Status:** ✅ done — \`<sha>\`, <test name>` under the slice heading.
- Neither → create a `## Progress` table once at the end of the plan, then keep appending to it:

  | Slice | State | Commit | Test | Date |
  |---|---|---|---|---|

`--plan-local` → make the same edits to the plan file but leave it out of `git add`, and make no
`docs(plan)` commit below — the plan is gitignored and the caller owns keeping it.

Rows that §3 found **DONE, UNTICKED** get written back too, in a separate
`docs(plan): reconcile <plan> with git` commit — the plan file only, and only when there is no
slice commit to fold them into.

Close the row:

```bash
tuntun task done --changes "…" --verification "…" [--heads-up "…"]
```

## 9 · Loop or stop

`--all` → back to §4 with the ledger refreshed from the new commit. A blocked slice does **not**
stop the run: record it (§9a) and continue with the next unblocked slice. Stop only when:

- the plan is finished, or every slice still open is `BLOCKED`;
- the tree is broken for everyone — the app or test target no longer builds, or a suite outside
  the slice goes red — because the next slice would be built on top of it;
- a `/ticket-workflow` pass takes the Mac (§6).

Without `--all`, stop after one slice, and on any blocker.

Under `--all` never stop to ask mid-run. Every question a blocked slice raises goes into the final
report (§10), all at once.

### 9a · Recording a blocked slice

Decide which kind it is first — they end differently:

| Kind | What it is | End state |
|---|---|---|
| **Check owed** | the code is done and every gate passed **except** one check the environment cannot run yet: an undeployed route, a device, a login, a backend fixture. The reviewer's only open finding, if any, is that check | commit it (§8), close the row with the owed check in `--heads-up`, and queue the check as its own row: `tuntun task add "<drive/verify …> once <condition>" --ticket <KEY> --parent <row> --kind chore --description "…"` |
| **Work blocker** | anything else: a decision only the user, PM, BE or design can make · a finding still open after two rounds · a red test the slice cannot fix · a dependency on an open row · criteria with no check | `tuntun task block <row> --reason "<what is in the way — and what unblocks it>"`; stash its uncommitted files **by path**, never the whole tree: `git stash push -m "<KEY> <slice> blocked: <reason>" -- <paths>`; name the stash in the block reason so a resume can `git stash apply` it |

A *check owed* is only allowed when the reviewer agrees the check is environment-bound or the user
has said so — never to get round a finding. Its row records the check as **not verified**, and the
reviewer's finding stays on the row as unresolved, never as approved.

Then write it down where the next run will read it:

- **Plan file** → add a `## Blocked` table once, below the slices, and keep it current (remove a row
  when its slice closes):

  | Slice | Kind | Blocked by | Unblocks when | Row | Stash | Since |
  |---|---|---|---|---|---|---|

  Commit it with the slice (*check owed*) or as `docs(plan): record blocked <slice>` (*work
  blocker*, plan file only). `--plan-local` → edit the file, never `git add` it.
- **Board-only plan** (recorded with `tuntun task plan`, no file) → the row's block reason or
  heads-up is the record; add `tuntun task comment "<what unblocks it>" --kind blocker` so the
  board says it in one place.

A work-blocked slice is not `OPEN` for §4 until its blocker is gone. A resume checks each
`## Blocked` row (and each blocked board row) first, and `tuntun task resume <id>` +
`git stash apply` the ones that are clear.

Never push, never open an MR — that is **`/ios-git`**, and only when the user asks.

## 10 · Report (per CLAUDE.md "Communicating results")

- **Reconciliation** — the table from §3, compressed: N done, N open, and by name anything the plan
  claimed that git did not back.
- **What Changed** — the slice, in behaviour terms, and its sha.
- **Verified** — the test that went red then green, the lint result, the review verdict; and plainly
  what you did *not* run (nothing builds the app automatically).
- **Blocked** — every slice recorded in §9a this run: kind, what blocks it, what unblocks it, its
  row and stash. Then the questions only the user can answer, together, once.
- **Heads Up** — only if something is actually left broken or risky.
- **Recommended Next Step** — the next open slice by name, or "nothing needed".
