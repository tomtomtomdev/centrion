---
name: feature-work
description: One command from a PRD page id to an MR — structure the PRD, plan it in slices, publish the iOS plan beside the PRD, then implement ONLY the unblocked slices (one @ios-implementer each), commit, push and open the MR. Re-run it with the same id when an API tech doc arrives or a design/PM decision is made; it re-checks every blocker and carries on from where it stopped.
argument-hint: "<PRD_PAGE_ID> [--api <wiki id|url>] [--decide \"<Q-n or D-n>: <answer>\"]... [--figma <url>] [--base <branch>] [--dry-run]"
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Agent, SendMessage, Skill, TodoWrite, AskUserQuestion
---

`$ARGUMENTS`

One feature (one PRD) at a time. The **local plan is the state**: every run reads it, refreshes
its blockers, works what is unblocked, and writes it back. A first run and a resume are the same
command; the only difference is whether the plan already exists.

| Flag | Effect |
|---|---|
| `<PRD_PAGE_ID>` | required; the Confluence page id of the PRD (`?pageId=…`) |
| `--api <id\|url>` | the API tech doc for this feature, when it is not a child page of the PRD |
| `--decide "Q-1: …"` | record a PM / BE / design answer; repeatable |
| `--figma <url>` | Figma link when the PRD's is missing or access was just granted |
| `--base <branch>` | MR target; default resolved in §2.2 |
| `--dry-run` | refresh blockers and report the slice table only; no rows, no code, no push |

## Rules this command never bends

- **Only unblocked slices are worked.** A blocked slice is never guessed at, stubbed against an
  invented API, or built on placeholder design. It waits, with its blocker named.
- **One checkpoint per run, at most.** Every question that blocks work is asked at one point in
  the run: §2.7 on a first run (it also takes whatever §3 surfaces), §3.7 on a resume. The tool
  takes 4 questions per call, so more than 4 means back-to-back calls at that same point — never a
  second checkpoint later in the run. A question that blocks only some slices never stops the
  rest: those slices stay BLOCKED and the run carries on. No one answering → proceed with what is
  unblocked.
- **Every slice is written by `@ios-implementer`** — including Tier 1 (the user's standing choice
  for this command, passed to `/ios-next-slice` as `--agent`, which is how that skill knows the
  exception to CLAUDE.md's below-Tier-2 rule was asked for).
- **Every slice is closed by its own slice agent, not by this session** (§5). A slice is test
  output, lint, review rounds and a reconcile table; none of that belongs in the context that
  holds the plan and decides what is next. This session opens the row, dispatches, verifies the
  close by reading git, and owns the plan's slice states, the checkpoint, the MR and the wiki.
  It never edits a source file, runs a test or makes a slice's commit — reaching for one means
  the slice agent should be doing it.
- The harness is an uncommitted overlay: switch branches only with
  `~/.tuntun/bin/tt-harness-switch <git args>`; never commit `CLAUDE.md` or `.claude/`.
- One heavy run per Mac: a `/ticket-workflow` pass file younger than 3 hours means stop (§0).
- No AI attribution on commits or MRs.

## 0 · Preflight

```bash
PASS=.tuntun/ticket-workflow.pass        # /ticket-workflow's rule: younger than 3h → a pass owns this Mac
if [ -f "$PASS" ] && [ $(( $(date +%s) - $(stat -f %m "$PASS") )) -lt 10800 ]; then
  echo "HOLD: ticket-workflow pass since $(stat -f %Sm "$PASS"): $(cat "$PASS")"
fi
git status --short                       # must be clean apart from the harness overlay
tuntun task list | sed -n 1,12p          # a row already in progress from another feature → stop
ls .claude/settings.json                 # no hooks → run `tuntun init` first
git fetch origin --prune                 # §1 and §4 read remote branches; never from a stale view
```

- `HOLD` → stop and say what the pass is doing. An older pass file is not this command's to delete;
  report it and stop — `/ticket-workflow` clears its own residue.
- **A previous run of this feature died mid-slice** — this ticket's row is in progress, or the tree
  is dirty on this feature's branch. Never discard it. Report the row and the changed files, and
  ask now, as this run's checkpoint (questions §2/§3 surface later stay blockers until the next
  run): *resume* (that slice goes first in §5, skipping §5.1 since its row is already open, with
  the diff handed to the implementer as a hypothesis to verify) or *stash* (`git stash push -m "<KEY> <slice> abandoned run"`, then `tuntun task comment … --kind
  blocker` on the row and the slice starts over). No answer → stop; this is the one case where
  nothing proceeds, because every slice after it would build on unknown work.

## 1 · Find the plan — new or resume

The local plan is gitignored, so another Mac may hold the only copy. Look in this order and stop at
the first hit:

```bash
grep -l "^prd_page_id: \"<PRD_PAGE_ID>\"" .tuntun/plans/*.md 2>/dev/null
```

1. **Local plan found → resume.** Read it in full, then go to §3.
2. **The PRD has a child page titled `[iOS] Implementation Plan — …`** (Confluence REST
   `…/content/<PRD_PAGE_ID>/child/page`) → the feature was started on another Mac. Rebuild the local
   plan from that page — its `Plan metadata` table is the frontmatter (§2.8), its sections are the
   body — and save it at `.tuntun/plans/<KEY>-<slug>.md`. Then resume at §3; §3.5's reconcile
   against the remote branch is what proves which slices are done, not the page's word.
3. **Neither → new.** §2. Before drafting, `git ls-remote --heads origin 'features/*/<KEY>-*'` —
   a hit means a branch exists with no plan anywhere; report it and stop rather than plan over it.

## 2 · New feature — PRD to plan (first run only)

1. **`/ios-prd <PRD_PAGE_ID>` phases 1–2** — fetch and structure into
   `.tuntun/prd/<PRD_PAGE_ID>.md` (FR / AC / OQ ids). Stop `/ios-prd` there; this command does
   phases 3–5 its own way.
2. **Ticket key and base.** The key comes from the PRD title (`T-250015 / …`). The base, first
   rule that names a branch that **exists** on `origin`:
   1. `--base`.
   2. The ticket's Affects Version, or a `vX.Y.Z` label on it (`/ticket-workflow` §0's rule:
      strip a leading `V`) → `release_candidate/<X.Y.Z>`.
   3. The PRD's version, only if it matches exactly one existing RC: `V2.50` → candidates
      `release_candidate/2.5.0` and `release_candidate/2.50.0`; `V2.5.1` → `2.5.1`.
   4. None or several → a checkpoint question listing the live RCs. It blocks every slice (no
      branch, no work), so an unanswered run stops after publishing the plan.
Steps 3 and 4 read only and consume nothing from each other: dispatch both in one message, and
draft (step 5) once both have returned.

3. **Code map** — one `Explore` agent, "very thorough": where each PRD area lives today, what shape
   it is (VIP or legacy SwiftUI/MVVM/Combine), which Worker and endpoints feed it, which tests
   exist. It returns a table; the raw search stays out of this session. Spot-check key paths with
   `git ls-tree`.
4. **Design** — one `general-purpose` agent: Figma link from the PRD or `--figma`. Access denied →
   the PRD's embedded images are the design source, and every UI slice is marked *unverified
   against Figma*. It returns the screens and the PRD-vs-design conflicts, recorded as `D-n`.
5. **Draft the plan** at `.tuntun/plans/<KEY>-<slug>.md` using the shape below. Slice rules: one
   verification per slice, Tier per CLAUDE.md, every FR/AC claimed by a slice (an AC no slice
   claims is a gap → an `OQ`/`Q-n`). A slice that is only partly unblocked is split now (`S7a`
   ready part, `S7b` the part that waits on the API).
   - **Deps are data, not habit.** Sy depends on Sx only if Sy consumes what Sx produces; "I would
     do it first" is not an edge. Challenge every one.
   - **Depend on the shape, not the build.** A slice that needs only another's model, protocol or
     `TT*Api` contract depends on a small contract slice, not on the full implementation.
   - Mark each edge **hard** (cannot start without it) or **soft** (can land flagged partial
     without it), and write the **critical path** — the longest dep chain — under the table.
6. **Run §3 now, up to and including step 6**, so the checkpoint below also carries every blocker
   §3 finds (an API doc that is already out, a newer PRD, Figma access). §3.7 does not ask again on
   this run.
7. **Checkpoint** (the one per run): P0 questions, the base when §2.2 could not settle it, anything
   §3 surfaced, and scope approval (slice list, tiers, order). Record answers in §Decisions with
   the date.
8. **Publish the plan** as a child page of the PRD (Confluence REST with the wiki token;
   `tuntun … page create` has returned 500 before). The page opens with a **`Plan metadata`**
   table holding every frontmatter field, so another Mac can rebuild the local plan from it (§1.2).
   Re-read the page and confirm its ancestor is the PRD. Put the page id in the frontmatter.
   Then go to §4 — §3 has already run.

### Plan shape

```markdown
---
prd_page_id: "<PRD_PAGE_ID>"
prd_version: <n>
plan_page_id: "<wiki id>"
ticket: <KEY>
branch: features/<X.Y.0>/<KEY>-<slug>
base: release_candidate/<X.Y.0>
assignee: <gitlab username of the user>
mr: <iid or empty>
api_doc: <wiki id or empty>
figma: <url> (<accessible | not shared>)
---
# [iOS] Implementation Plan — <KEY> / <title>
1. What the PRD asks for · 2. How the code is shaped · 3. PRD vs design (D-n)
4. Slices  — | # | Slice | Serves | Files | Verify | Tier | Deps | State | Blocked by |
   Critical path: S1 → S2 → S5
5. Open questions (Q-n, each naming the slices it blocks)
6. Decisions — dated answers, each naming the Q-n / D-n it closes
## Progress — | Slice | State | Commit | Test | Date |
```

`Deps` lists the slices whose output this one consumes, each marked hard or soft: `S2`, `S3?`
(`?` = soft). A hard dep clears when that slice is DONE (has a commit on the branch); a soft one
never holds a slice back.

`Blocked by` holds what is **outside** the plan, and is **typed** so a resume can check it
mechanically:

| Type | Example | Clears when |
|---|---|---|
| `api:<what>` | `api:detail-by-period` | the API tech doc defines it (§3.2) |
| `q:<Q-n>` | `q:Q-1` | §Decisions answers Q-n |
| `design:<D-n\|figma>` | `design:D-1`, `design:figma` | a decision answers D-n / Figma opens for this account |
| `prd:<OQ-n>` | `prd:OQ-1` | the PRD's new version answers it |

A plan written before `Deps` existed carries `after:<Sx>` in `Blocked by`: on resume, move each
into `Deps` as a hard dep and derive the critical path.

## 3 · Refresh every blocker (every run)

1. **Flags first.** Each `--decide` → a dated row in §Decisions. `--api` → `api_doc`. `--figma` →
   `figma`.

Steps 2–4 are independent reads: dispatch each one that has work as its own `general-purpose`
agent, all in one message, each returning its findings only; this session applies them to the
plan, in step order, once all have returned. Step 5 is where they join.

2. **API tech doc.** If `api_doc` is empty, look for it: child pages of the PRD and of its parent
   (`tuntun jira wiki page search` for "API", "Tech Doc", "Contract", the ticket key, the feature
   name). Found → read it in full, add a `## API contract` section to the plan (endpoint, request,
   response fields, the slices each serves) and set `api_doc`. A doc that defines only some of
   the needed endpoints clears only those `api:` blockers.
3. **PRD drift.** `tuntun prd fetch <PRD_PAGE_ID>`. A newer version → re-structure, diff FR/AC
   against the plan, flag every slice whose FRs changed (back to OPEN if it was done and the change
   is behavioural — say so loudly), clear answered `prd:` blockers, add new OQs.
4. **Figma.** If `design:figma` is present and a link exists, try it once (headed Chrome, never
   click in the user's own Figma view). Still denied → leave it.
5. **Reconcile with git** (`/ios-next-slice` §3 logic): every slice → DONE / OPEN / IN FLIGHT from
   commits on the branch and the code itself, not from the plan's word.
6. Recompute each slice's `State`: **READY** (no `Blocked by` left and every hard dep DONE),
   **BLOCKED** (list what remains), **DONE** (`<sha>`). Mirror the states into the todo list.
7. **Resume only:** new blocking questions surfaced here → the run's one checkpoint (on a first run
   they went to §2.7). Unanswered → they stay blockers.

`--dry-run` stops here with the table.

## 4 · Branch and rows

```bash
if git rev-parse --verify -q <branch> >/dev/null; then
  ~/.tuntun/bin/tt-harness-switch switch <branch>
  git rev-parse --verify -q origin/<branch> >/dev/null &&
    git merge --ff-only origin/<branch>             # another Mac pushed slices; not ff → stop, report
elif git rev-parse --verify -q origin/<branch> >/dev/null; then
  ~/.tuntun/bin/tt-harness-switch switch -c <branch> origin/<branch>   # started on another Mac
else
  ~/.tuntun/bin/tt-harness-switch switch -c <branch> origin/<base>
fi
git branch --unset-upstream 2>/dev/null   # never let a bare push reach the release branch
tuntun task list --ticket <KEY>
```

`merge --ff-only` failing means this Mac and the remote both have commits the other lacks: stop
and report both sides, never rebase or force past it.

Record on the board, with `tuntun task plan --ticket <KEY>`, **only the READY slices that have no
row yet**, in §5's pick order; keep each slice's piece id from its output for §5.1. Blocked slices get their row on the run that unblocks
them — so `tuntun task next` never opens something that cannot be worked.

## 5 · Work the READY slices

One READY slice at a time, picked by: on the critical path first, then the one whose DONE readies
the most slices, then the riskiest (unfamiliar API, legacy code, soft deps). Re-evaluate `Deps`
after every commit — S3 landing readies S4:

1. `tuntun task next --id <piece id>` (it must open this slice's row; a mismatch → stop and
   report). Skip it for a
   slice resumed from §0 — its row is already in progress.
2. **Dispatch one slice agent** — a fresh `general-purpose` agent (never `fork`: inheriting this
   session is what the split exists to avoid), one per slice, never two at once. It is the
   session `/ios-next-slice` describes, so it spawns the `@ios-implementer` and `@code-reviewer`
   itself (subagents nest up to three deep). The prompt stands alone; it carries pointers, not the
   plan's content:

   > You are closing one slice of a feature plan. Repo: `<path>`. Branch: `<branch>` (already
   > checked out). Plan: `<plan path>` (gitignored). Slice: **`<id> <title>`** — only that one.
   >
   > Read `~/.claude/skills/ios-next-slice/SKILL.md` in full and carry it out as the session it
   > describes, with the arguments `<plan path> --slice <id> --agent --row-open --plan-local`:
   > - `--agent`: `/ios-assign`, then one `@ios-implementer` writes the slice at every tier, with
   >   the TDD contract in its prompt verbatim.
   > - `--row-open`: the task row is already open. Never run `tuntun task start` or
   >   `tuntun task next` (a second `next` opens the following slice's row); close it with
   >   `tuntun task done` at §8.
   > - `--plan-local`: write the Progress row to the plan file; never `git add` it.
   >
   > Where it says "the user", you cannot reach them: a question only the user can answer, a
   > second FINDINGS round on one finding, or a red test you cannot turn green → stop, commit
   > nothing, and report BLOCKED with the question or the evidence. Never push; never touch the
   > MR or the wiki; never edit the plan outside this slice's Progress row.
   > `<For a slice resumed from §0: the uncommitted diff is this slice's earlier attempt — hand it
   > to the implementer as a hypothesis to verify, not as done work.>`
   >
   > A transient failure (tool error, network, a flaky test) gets at most 3 attempts, changing
   > the approach between them; the third → BLOCKED with the evidence.
   >
   > Report back in exactly this block, nothing else:
   > ```
   > slice: <id>
   > status: DONE | BLOCKED(<reason>; question for PM | BE | design | none)
   > sha: <short sha or none>
   > files: <changed files>
   > verified: <tests red → green by name; lint; /ios-review and @code-reviewer verdicts>
   > progress_row: <the row you wrote>
   > notes: <anything that contradicted the plan, or none>
   > ```

3. **Verify the close by reading, never by repairing:** `git log -1 --format='%h %s'` (on
   `<branch>`, subject `type(scope): [<KEY>] …`), `git status --short` (clean apart from the
   overlay), the plan's Progress row carries that sha, and `tuntun task list --ticket <KEY>` shows
   the row done. A gap — no commit, a dirty tree, a missing row — goes back to **the same** agent
   with `SendMessage`; fixing it here hides from the next resume that the slice did not close on
   its own.
4. **DONE** → set the slice's `State` to DONE (`<sha>`) and re-evaluate `Deps`.
   **BLOCKED** (red test, FINDINGS twice on one finding, a question only the user can answer,
   three failed attempts) → mark it BLOCKED with the reason, `tuntun task comment … --kind
   blocker`, and at once mark every slice that holds it as a **hard** dep, directly or further
   down, BLOCKED (`dep <Sx> blocked`), so none of them waits on it this run. A slice holding it
   only as a **soft** dep stays READY and lands flagged *partial*, naming what is missing. Then go
   on to the next pick. A question it surfaced becomes a `Q-n` in the plan; the run's checkpoint
   has passed, so it is asked on the next run, not now.

Never run two `./run.sh` at once — one slice agent, and so one implementer, at a time.

## 6 · Ship

Steps 1–2 only when this run committed at least one slice. Step 3 whenever this run changed the
plan at all — a `--decide`, an API contract, a PRD re-diff, a state change — so the page never
falls behind the local plan. `--dry-run` skips all three.

1. `git push -u origin <branch>` — via `/ios-git`.
2. **No MR yet** → create it: source `<branch>`, target `<base>`, assignee `<assignee>`, title
   `feat(<scope>): [<KEY>] <feature title>`, description = the done slices with their ACs, the
   partial and blocked slices with what they miss, what was verified and what was not — every
   list in slice-id order, never the order slices finished. Save the iid as `mr`.
   **MR exists** → the push updates it; edit its description with the new done/blocked lists.
3. **Update the wiki plan page** (REST GET `body.storage` → edit → PUT version+1 → re-GET to
   confirm; the CLI update 405s): `Plan metadata` (the `mr` iid, `api_doc`, `figma`), slice states,
   §Decisions, API contract. Keep the page and the local plan saying the same thing.

## 7 · Report (CLAUDE.md "Communicating results")

Every per-slice list in slice-id order.

- **Done this run** — slices, commits, the MR link; a *partial* slice says what it lacks.
- **Still blocked** — one line per slice: what it waits on and who can give it (BE: API tech doc
  for X · PM: Q-n · Design: D-n / Figma access).
- **Verified** — per slice, from its slice agent's report and §5.3's check: the tests red → green,
  lint, review verdict; UI not driven unless it was.
- **Resume with** — the exact command, e.g.
  `/feature-work 120119194 --api 125100000` or `/feature-work 120119194 --decide "D-1: five labels"`.
