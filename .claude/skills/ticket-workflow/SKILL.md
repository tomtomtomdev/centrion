---
name: ticket-workflow
description: One pass on ONE batch (1–3 related tickets sharing a PRD, design, epic or feature) — finish this Mac's batch in flight (rework or smoke it), or, only when none is in flight, group related tickets off the board, fix them on one branch with one commit per ticket, ship their smoke steps in one MR; then smoke that MR with serve-sim, post proof, merge it and mark every ticket fixed. Runs once and stops.
argument-hint: "(none) — ONE pass per invocation, never loops on its own; run it as /loop /ticket-workflow. Two Macs run it, one session each."
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Agent, Skill, TodoWrite, ScheduleWakeup
---

**One batch at a time.** This Mac has at most **one batch in flight**: one to three related tickets,
claimed, `fixing`, and not yet merged, sharing **one branch and one MR** (see *Batches*). A pass
works that batch and nothing else, and new tickets are claimed only once none is in flight (§1a).
Every pass has exactly one batch or none. Wherever this file says *the pass ticket*, read **every
ticket of the pass batch**; a batch of one is exactly the single-ticket flow.

One pass, two phases, on the **same** ticket, in this order:

- **Phase A · fix** (§0–§7) — a ticket already in flight gets whatever it still needs: a rework if
  its last smoke came back `needs-rework` (§5a), a fix if it never shipped one, nothing if its MR is
  waiting to be smoked. Only with nothing in flight: claim one ticket off my board, fix it, write
  the **smoke steps** that prove the fix, and open an MR against its release candidate.
- **Phase B · smoke** (§8–§12) — take **the pass ticket's** MR, **drive its smoke steps on the
  simulator with `/serve-sim:serve-sim`**, and only if what you see actually matches: post the
  proof, merge it, mark the ticket `fixed`. No other MR is smoked in this pass.

Then **stop and report**. When the pass ticket ends Phase A without a smokeable MR (not
reproducible, not reachable, needs a version, a blocked fix), Phase B has nothing to do. That is
the end of the pass. It never falls through to smoking some other ticket's MR.

**This command never schedules itself.** No wakeup armed from inside, no cron job, no second lap,
no sweep. One ticket, carried as far as it can go this pass, then the turn ends. Repetition is the caller's:
`/loop /ticket-workflow`. See *Looping is the caller's job*.

**Unattended, every choice resolves to the recommended option.** Where a step offers a choice and
nobody is watching, take the one marked recommended and record it in the report — never block the
loop waiting for an answer. Where there is no recommendation, take the first option and say so.

## Batches — related tickets ship together

Tickets that share context (the same PRD, the same Figma file, the same epic or parent, the same
feature label or `[Feature]` summary prefix) touch the same scenes and the same smoke path. Fixing
them one pass each rebuilds and re-drives the same screens three times. So §2 claims them together
and they travel as one unit:

- **At most 3 tickets per batch.** More makes one failed `EXPECT:` hold back too much work, and a
  batch's smoke drive already runs longest on this 8 GB M1.
- **Same target RC, always.** Grouping only joins tickets whose §3 answer is the same (the same
  Affects Version, the same `vX.Y.Z` label, or both resolved by the RC-floor rule). A different RC
  is never a batch: one MR has one target.
- **One branch, one commit per ticket.** Subject `fix(<area>): [<KEY>] <what changed>`, in the
  order the tickets were fixed. Code two tickets need goes in the first commit that needs it. Every
  later fix to a ticket's code — a review follow-up, a rework — is folded into **that ticket's
  commit** with `git commit --fixup=<its sha>` and `GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash
  origin/<TARGET_RC>`, never left as a loose commit. `GIT_SEQUENCE_EDITOR=:` keeps it non-interactive.
- **One MR, merged without squash.** Project 49 is `squash_option: default_on`, so a batch MR must
  be created **without** `--squash-before-merge`, have `squash=false` set on it (§7), and merge with
  `squash=false` (§12). Otherwise GitLab collapses the per-ticket commits into one.
- **One smoke drive at the end, for all of them.** The MR's `## Smoke steps` holds one `### <KEY>`
  subsection per ticket (§6). Phase B builds once, logs in once, drives every subsection and grades
  every `EXPECT:`.
- **The batch passes or fails as one.** Every `EXPECT:` of every ticket met → merge, and every
  ticket goes to `fixed`. Any `EXPECT:` failed → the whole MR is `needs-rework`, the marker names the
  failing tickets (`failed=<KEY>,<KEY>`), and §5a reworks only those tickets' commits. Nothing is
  merged piecemeal.
- **The `batch-<SEED KEY>` label** marks the members in Jira, stamped at claim on every ticket of a
  batch of two or more (a batch of one carries none). It is how §1a finds the siblings, including
  when a fix run died before an MR existed.
- **One task row per batch**, titled after the shared feature, `--ticket <SEED KEY>`, the other keys
  in its description. Its `task done` names each ticket's outcome.
- **Per-ticket exits stay per-ticket.** Inside a batch, one ticket can still end `not-reproducible`,
  `not-reachable` or `needs-version` (§3/§4): do that ticket's Jira steps, remove its `batch-…`
  label, give it no commit, and carry on with the rest. The branch is deleted only when every ticket
  of the batch has left.
- **Parking is per batch.** §1a's `needs-human`, `blocked` and `blocked-no-data` rows apply to every
  ticket of the batch at once.

## Two Macs, one session each

Two Macs run this. They stay off each other with **one mechanism: a sticky Jira label** naming the
claiming Mac by its device type, looked up from `TT_HOST_ID` in `TW_HOSTS` (`A` here → `macmini`;
Mac B → `macbookpro`).

- Phase A skips any ticket already carrying *another* Mac's label, then stamps its own on claim.
- Phase B reads only tickets carrying **its own** label.

So a Mac fixes and later smokes only its own work, and the two never contend. The consequence is
deliberate: **if one Mac stops, its unsmoked MRs wait for it** rather than being picked up. Nothing
else — no GitLab labels, no queue file, no cross-Mac lock.

There is **no `ready-to-smoke` or `under-smoke-test` label.** If you find one on an MR it is
residue from the retired pipeline; ignore it, and do not create either.

## The split — one pass, three contexts

The main session **orchestrates and never handles a ticket's material itself**. Phase A's work runs
inside a **fix agent**; Phase B's runs inside a **smoke agent**. Each starts on a clean context and
dies with it — so a ticket's description, video digest, Figma spec, diff and lint output, and a
smoke run's build log and screenshots, never pile up in the session that has to report.

| § | Runs in | Why there |
|---|---|---|
| 1a rework check | main session | One queue read and one notes read per MR; it decides whether Phase A is a rework or a new claim. |
| 0 preflight · 1 board · 2 claim | main session | Cheap, and the claim has to land before anything else — it is the only thing keeping the other Mac off this row. |
| 3 branch · 4 reproduce · 5 fix · 6 smoke steps · 7 ship — or §1a's rework | **fix agent** | Where Phase A's context is: ticket, PRD, video, design, code, lint, diff, git. |
| 7b review the MR | **`@code-reviewer`**, spawned by the main session | Agents never spawn agents, so the fix agent cannot gate itself; a clean context is the point of the gate. |
| 8 queue | main session | One board read, one MR check; it costs almost nothing to carry. |
| 9 rebase+build · 10 smoke · 11 proof · 12 merge | **smoke agent** | Every heavy thing in Phase B: `xcodebuild` output, the serve-sim drive, the screenshots. |
| Report | main session | Assembled from the two handoff blocks, never from their working material. |

**One agent at a time, never two in parallel, and never `isolation: "worktree"`.** This Mac has one
checkout, one simulator and one compiler; two agents would fight over `git checkout`, `install.sh`,
the compiler and the simulator inside the same directory. Spawn one, wait for its handoff, then move on. A
fresh worktree has no pods, no `tt-slot` pin and no project instructions, and §0's `tt-slot check`
exists precisely because a session that cannot see `CLAUDE.md` writes SwiftUI.

## The installed harness rides on top — never switch branches with bare git

`tuntun`'s harness update writes `CLAUDE.md` and `.claude/` into the worktree, and both are
**tracked**. Branches cut before an update carry the old version, so the installed one sits there as
uncommitted changes. Bare `git checkout` / `rebase` / `reset --hard` then either refuses ("commit
your changes or stash them") or silently throws the installed harness away. The first stalls the
pass; the second leaves every later agent reading rules that no longer match the tools it has.

So every branch-switching command in this runbook goes through **`tt-harness-switch <git args…>`**
(`~/.tuntun/bin`). It saves `CLAUDE.md` and `.claude/` aside, resets them to the committed version,
runs the git command, and puts the saved copy back, on success and on failure alike. When the
harness is already clean it is a plain `git` call.

- Never stash, discard, reset or commit the harness paths yourself, and never add them to a fix.
  Stage fixes with the pathspec in §7, never a bare `git add -A`.
- A fix MR whose diff lists `CLAUDE.md` or `.claude/` is wrong: drop those paths from it before
  pushing.
- Shipping a harness update to the team is its own MR, and only when the user asks for one.

## Settings — the board, the repo and the build are configuration

Every Jira query, GitLab call, branch name and build line below reads a `TW_*` variable, never a
literal. `env.sh` (next to this file) sets them, and every shell block loads it right after
`tt-slot`'s eval, because it builds the claim labels from `TT_HOST_ID`. Values come from, last
winning: env.sh's defaults (the Tuntun iOS pipeline), this Mac's
`~/.config/ticket-workflow/config.env` (written by `configure.sh`, which `install.sh` runs), and
the checkout's own `.tuntun/ticket-workflow.env` for a repo on a different board.

| Variable | Default | Used for |
|---|---|---|
| `TW_BOARD_JQL` | `filter = 11001` | §1's pickup board |
| `TW_FIXING_JQL` | `filter = 10550` | §1a, bucket 2 and §8: my `fixing` tickets |
| `TW_COMPONENT` | `ios` | the one component every query requires |
| `TW_EXCLUDE_COMPONENTS` | `BE` | pickup skips tickets carrying any of these |
| `TW_JIRA_USER` / `TW_NOT_QA` | `tommy.yohanes` / `admin tommy.yohanes` | the pipeline's own account; authors that are never a brief (§4) |
| `TW_HOSTS` | `A=macmini B=macbookpro` | every Mac as `<TT_HOST_ID>=<device-type label>`, in claim precedence order |
| `TW_GITLAB_HOST` / `TW_GITLAB_PROJECT` / `TW_MR_ASSIGNEE` | `git.tuntun.co.id` / `49` / the Jira user | every `glab` call |
| `TW_MAIN_BRANCH` / `TW_RC_PREFIX` / `TW_RC_FLOOR` / `TW_BRANCH_PREFIX` | `main` / `release_candidate` / `2.4.0` / `tommy` | §3's target and branch name |
| `TW_WORKSPACE` / `TW_SCHEME` / `TW_PRODUCTS` / `TW_APP_NAME` / `TW_SIM_DEVICE` | `TTSecuritas.xcworkspace` / `TTSecuritas Staging` / `Staging-iphonesimulator` / `Tuntun Sekuritas.app` / `iPhone 17` | §9's build and §10's drive |

env.sh also derives `TW_MINE` (this Mac's label, the one it stamps), `TW_MINE_JQL` (it plus this
Mac's legacy `mac-<id>`, the ones it reads as its own), `TW_OTHERS` and `TW_ALL_MACS` (the JQL-quoted
labels, legacy ones included, of the other Macs and of all of them), `TW_CLAIM_RE` (a regex matching
any claim label) and `TW_EXCLUDE_JQL` (` AND component NOT IN (…)`,
empty when nothing is excluded). Where the prose below names a default (filter 11001, project 49,
`release_candidate/`, the staging scheme), read it as *the configured one*; the facts it states
about that default (squash on by default, fast-forward only, no CI) are to be re-checked on any
other project. The accounts table in §4 is not configuration: it is this app's.

## 00 · Is a pass already running? — hold, don't start a second one

**Check this before anything else, including §0.** A `/loop` wakeup or a repeated invocation can
fire while an earlier pass is still in flight — a fix agent mid-rework, a `@code-reviewer` gate, a
smoke agent holding the simulator. Starting a second pass then claims a second ticket, fights the
first over `git checkout`, `install.sh`, the compiler and the simulator, and leaves two half-finished rows.
So an invocation that finds a pass in progress **does nothing**: no board read, no claim, no smoke.

A pass is in progress when **either** of these is true:

1. **This session still has an agent from an earlier pass running** — a fix agent, a reviewer or a
   smoke agent you spawned and whose completion notification has not yet arrived, or one you sent
   back with `SendMessage` (a rework, a re-check). The orchestrator knows this from its own
   conversation; no command answers it.
2. **The pass file exists and is fresh.** §0 writes it as the pass starts and the Report removes it
   on every path:
   ```bash
   PASS=.tuntun/ticket-workflow.pass           # gitignored, per checkout
   if [ -f "$PASS" ] && [ $(( $(date +%s) - $(stat -f %m "$PASS") )) -lt 10800 ]; then
     echo "HOLD: pass in progress since $(stat -f %Sm "$PASS"): $(cat "$PASS")"
   fi
   ```
   Younger than **3 hours** → hold. Older → treat it as the residue of a crashed session: say so in
   the report, delete it, and continue. Do not delete a fresh one to get past this check.

Either → write one line saying which, and what the pass is waiting on, then end the turn.
Under `/loop` that is a quiet hold (`noop: true`) with a **~1800s** fallback, because the running
agent's own completion notification is what actually wakes the loop. Its notification is handled as
the continuation of **that** pass (close the row, move to the next step it was on), never as a cue to
start a new one.

Neither → start the pass: write the pass file, then run §0.

```bash
mkdir -p .tuntun && echo "started $(date '+%F %T') slot=${TT_SLOT:-?}" > .tuntun/ticket-workflow.pass
```

Keep it current as the pass advances (`echo "phase A: <KEY> fix agent" > .tuntun/ticket-workflow.pass`,
`… review gate`, `phase B: smoking !<IID>`), so a held invocation can say what it is waiting on.

## 0 · Preflight (fail loudly, never silently idle)

```bash
export PATH="$HOME/.tuntun/bin:$PATH"
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"      # serve-sim runs through npx; node is nvm-only here
eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
tt-slot check
node --version                                         # must print v18+ or §10 cannot drive anything
curl -sS -k -o /dev/null -w 'jira=%{http_code}\n' -H "Authorization: Bearer $JIRA_TOK" "$JIRA_URL/myself"
echo "slot=$TT_SLOT host=$TT_HOST_ID rc=$TT_RC"
echo "board=[$TW_BOARD_JQL] fixing=[$TW_FIXING_JQL] component=$TW_COMPONENT$TW_EXCLUDE_JQL mine=$TW_MINE others=[$TW_OTHERS]"
[ -n "$TW_MINE" ] || echo "STOP: no claim label for TT_HOST_ID=$TT_HOST_ID in TW_HOSTS=[$TW_HOSTS]"
echo "gitlab=$TW_GITLAB_HOST project=$TW_GITLAB_PROJECT rc=$TW_RC_PREFIX/≥$TW_RC_FLOOR branch=$TW_BRANCH_PREFIX/…"
```

`tt-slot` is the identity and credential source for this pipeline — do not hand-roll either.
`env --with-jira` reads `JIRA_URL` / `JIRA_TOK` straight out of `~/.tuntun.yaml`, tolerating the
quoted and unquoted forms the file has had, and never copies them to a second file. `~/.tuntun/bin`
is **not on `PATH`** by default, hence the export.

**Node is nvm-only on this Mac**, so nothing node-shaped is on the default `PATH` and `npx
serve-sim` fails with "node not found" even though Node is installed. Sourcing `nvm.sh` is what
makes §10 possible; check it here rather than discovering it after a 12-minute build.

**Re-run `eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh` at the top of every block that reads a `$TT_*` or
`$JIRA_*` or `$TW_*` variable.** Shell state does not survive from one tool call to the next, so a block that
assumes an earlier export silently sees an empty token — which then looks exactly like an empty
board.

`$JIRA_URL` already ends in `/rest/api/2` — never append it. Bearer, never Basic.

`tt-slot check` verifies this worktree actually carries the project instructions; a freshly created
worktree starts with none.

`TT_HOST_ID` is this **Mac's** identity and the whole basis of the claim label. Empty, or not one
of `TW_HOSTS` (then `TW_MINE` is empty) → stop; an
unlabelled claim is indistinguishable from no claim and the two Macs will collide.

Not `200`, or `tt-slot` fails → **stop and report**, and skip Phase B too: a dead token cannot
comment on a ticket or move it to `fixed`, so smoking anything would strand a merged MR behind a
ticket nobody moved.

## 1a · The ticket in flight — finish it before claiming another

**Run this before §1, every pass.** This Mac's tickets in flight are its own `fixing` tickets:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
JQL="($TW_FIXING_JQL) AND component = \"$TW_COMPONENT\" AND labels IN ($TW_MINE_JQL) AND labels NOT IN (\"needs-human\") ORDER BY created ASC"
curl -sS -k -G -H "Authorization: Bearer $JIRA_TOK" --data-urlencode "jql=$JQL" \
  --data-urlencode "fields=summary,labels" --data-urlencode "maxResults=50" "$JIRA_URL/search" \
| python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'errorMessages' in d: print('ERROR', d['errorMessages']); raise SystemExit(1)
print('in_flight=%d' % d.get('total', 0))
for i in d.get('issues', []): print('%s|%s' % (i['key'], i['fields']['summary']))
"
```

`needs-human` tickets are parked for a person and do not count. Tickets another Mac claimed carry
its label, never this one's, so they do not count either.

**`in_flight=0` → continue at §1** and claim a new ticket. That ticket becomes the pass ticket, and
Phase B smokes its MR in this same pass.

**`in_flight≥1` → no board read and no claim.** The **oldest** in-flight ticket, plus every
in-flight ticket sharing its `batch-…` label, is the pass batch (no batch label → a batch of one).
If more than one batch is in flight (residue from before this rule), they drain oldest first, one
per pass. Find its open MR the way §8 does, read the MR's notes newest first, and take the newest
**marker** (below). The pass ticket's state decides Phase A:

| State | Phase A | Phase B |
|---|---|---|
| **No open MR, no agent running on it** — a fix run that died before shipping | re-run the fix agent on it, §3–§7, with the normal fix prompt (it is already claimed) | smokes the MR if one now exists |
| **Newest marker `needs-rework` whose `head=` is the MR's current head** — failed, nothing pushed since, and no recipe-only rework note after it (§8) | the **rework**, §5a (see the rework prompt under §2) | re-smokes it after the rework's push |
| **A QA/design comment (§4) newer than the MR's last push** — the brief moved while the MR waited | the **rework**, §5a, with that comment as its failure note | re-smokes it after the rework's push |
| **No `review=approved` marker (§7b) whose `head=` is the MR's current head** — a gate that never ran, died, or predates the last push | §7b only: the `@code-reviewer` gate on the MR as it stands, no fix agent unless it returns FINDINGS | smokes it once APPROVED |
| **No marker, or a marker whose `head=` is older than the MR's current head** — waiting to be smoked | nothing; say so | smokes it |
| **Newest marker `blocked-no-data` whose `head=` is the MR's current head, and the market has not traded since** (a new trading day, or 09:00–16:00 WIB when it was posted outside it) | nothing | nothing: the pass ends with *waiting for market data on <KEY>*, and it **still does not claim** a new ticket |
| **Three or more `needs-rework` markers on the MR** | stop reworking it: CLAUDE.md sends a finding that survived two fix rounds to a person. Add `needs-human` to the ticket, comment on the MR and the ticket with the failure notes' links. That frees the slot, so continue at §1 | as for a new claim |
| **A `blocked` smoke verdict** (not `blocked-no-data`) | the same `needs-human` parking: a run that could not reach a verdict needs a person, and leaving it `fixing` would hold the slot forever | as for a new claim |

Open the task row for a rework as §2 does, titled `Rework !<IID>: <what the failure note says is
wrong>`, `--kind bugfix --ticket <KEY>`. A pass with nothing for Phase A opens no row.

**The marker.** Every smoke note Phase B posts (§9 build failure, §10 failure, §11 pass, §10's
no-data `blocked`, §9's `blocked`) opens with one hidden line. It and §7b's `review=` marker — the
same shape, `<!-- ticket-workflow review=<approved|findings> head=<sha> round=<n> -->` — are the
only things this step reads:

```
<!-- ticket-workflow smoke=<passed|needs-rework|blocked|blocked-no-data> head=<the MR's head sha on GitLab before §9's rebase> sha=<full sha that was driven> -->
```

**Compare `head=`, never `sha=`, with the MR's current head.** §9 rebases locally and only §12 pushes,
so after any failed run `sha=` is a rebased commit that never reaches GitLab. Matching it against the
MR's head would never match, and a failed MR would read as *waiting to be smoked* forever. `head=` is
what GitLab showed when the run started, so it equals the MR's head exactly until someone pushes. An
old marker with no `head=` → treat its `sha=` as `head=`.

On a batch MR a `needs-rework` marker also carries ` failed=<KEY>,<KEY>`: the tickets whose
`EXPECT:` failed, which are the only ones §5a reworks.

## 1 · Read the board

`TW_BOARD_JQL` is the pickup board. Its default, filter 11001, is *"Open ticket / tommy"*:
`assignee = tommy.yohanes` AND status in (Open, Reopened, Issues, Backlog), Frontend excluded. A
configured board must mean the same: my tickets, in the statuses §1's ranking knows. Four clauses
are added on top of it, and all four matter:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
JQL="($TW_BOARD_JQL) AND component = \"$TW_COMPONENT\"$TW_EXCLUDE_JQL AND (labels IS EMPTY OR labels NOT IN (${TW_OTHERS:+$TW_OTHERS,}\"not-reachable\",\"needs-version\"))"
echo "JQL: $JQL"

curl -sS -k -G -H "Authorization: Bearer $JIRA_TOK" \
  --data-urlencode "jql=$JQL" \
  --data-urlencode "fields=summary,status,issuetype,labels" \
  --data-urlencode "maxResults=50" "$JIRA_URL/search" \
| python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'errorMessages' in d:
    print('ERROR', d['errorMessages']); raise SystemExit(1)
RANK = {'open': 0, 'issues': 0, 'backlog': 0, 'reopened': 1}
rows = []
for i in d.get('issues', []):
    f = i['fields']
    st = (f['status']['name'] or '').lower()
    rows.append((RANK.get(st, 0), i['key'], st, f['issuetype']['name'],
                 ','.join(f.get('labels') or []) or '-', f['summary']))
rows.sort(key=lambda r: r[0])
print('total=%d bucket0=%d' % (len(rows), sum(1 for r in rows if r[0] == 0)))
for r in rows:
    print('%d|%s|%s|%s|%s|%s' % r)
"
```

- **`component = $TW_COMPONENT`** (`ios`) — neither default filter carries it. It leaves 11001 unchanged today but is the
  clause that keeps an Android or web row off this pipeline the day one lands.
- **`$TW_EXCLUDE_JQL`** (`component NOT IN (BE)`) — a ticket that also carries the **BE** component is backend work,
  or at least waits on it, even when `iOS` is tagged alongside. Never pick it up; this Mac cannot
  fix or smoke the backend half. `BE` is the component's exact name in UATP, TUNTUN and PBT.
  This clause is on the pickup board only. A ticket this Mac already claimed stays in §1a and §8,
  so it is never stranded in `fixing` if someone adds BE to it later.
- **The label exclusion** — the other Mac's claim, plus the two terminal labels §3 and §4 write.
  Without it a `not-reachable` ticket parked in Reopened is re-picked on the very next pass, forever.
- **Bucket 0 before bucket 1** — Open/Issues/Backlog outrank Reopened. This is not cosmetic:
  11001's own `ORDER BY status DESC` sorts Reopened to the **top**, the exact inverse of what we
  want, so the ordering has to be redone here rather than trusted.

Judge the *output*, never an exit code:

| Output | Meaning | Do |
|---|---|---|
| `total=0` | nothing Open/Reopened/Issues/Backlog, and §1a found nothing in flight | run the **orphan read** below before ending anything |
| rows | tickets waiting | continue at §2 |
| `ERROR …` | broken auth / VPN / filter | **stop and report**, skipping Phase B, as in §0 |

### Bucket 2 — orphaned `fixing` tickets, only when the board is empty

A `fixing` ticket with **no** Mac label is in no other query: §1a and §8 read only this Mac's label,
and the pickup board has no `fixing`. The released claim in *When the fix agent comes back empty* makes them
whenever no Open-ward transition exists, and a hand-moved ticket makes them too. Without this read
they sit there forever. Run it **only** after the query above printed `total=0`.

The same read also takes over a **stale claim**: a `fixing` ticket that carries the **other** Mac's
label and has not been updated in Jira for **72 hours**. Such a ticket is in no query on this Mac,
so if the other Mac dies mid-ticket, it stays stuck in `fixing` until someone clears the label by hand.
72h is deliberately long. A live pass runs 25–35 min, and a legitimate `blocked-no-data` hold can
run from Friday 16:00 to Monday 09:00 (~65h) without touching Jira. Taking over a ticket that was
only holding is harmless: this Mac's §1a reads the same marker and holds too.

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
PARKED='"not-reachable","needs-version","needs-human"'
STALE=${TW_OTHERS:+" OR (labels IN ($TW_OTHERS) AND labels NOT IN ($TW_MINE_JQL,$PARKED) AND updated <= -72h)"}
JQL="($TW_FIXING_JQL) AND component = \"$TW_COMPONENT\"$TW_EXCLUDE_JQL AND (labels IS EMPTY OR labels NOT IN ($TW_ALL_MACS,$PARKED)$STALE) ORDER BY updated ASC"
curl -sS -k -G -H "Authorization: Bearer $JIRA_TOK" \
  --data-urlencode "jql=$JQL" --data-urlencode "fields=summary,status,issuetype,labels,updated" \
  --data-urlencode "maxResults=50" "$JIRA_URL/search" \
| python3 -c "
import sys, json, re
d = json.load(sys.stdin)
if 'errorMessages' in d: print('ERROR', d['errorMessages']); raise SystemExit(1)
print('total=%d bucket2=%d' % (d.get('total', 0), d.get('total', 0)))
for i in d.get('issues', []):
    f = i['fields']
    labels = f.get('labels') or []
    stale = next((l for l in labels if re.fullmatch(r'$TW_CLAIM_RE', l)), None)
    kind = 'stale-claim:%s since %s' % (stale, f['updated'][:16]) if stale else 'orphan'
    print('2|%s|fixing|%s|%s|%s|%s' % (i['key'], f['issuetype']['name'], ','.join(labels) or '-', kind, f['summary']))
"
```

This Mac's own label is always excluded: a `fixing` ticket carrying **this** Mac's label is already
§1a's, never an orphan. The other Mac's label is excluded unless the claim is 72h stale.

| Output | Do |
|---|---|
| `total=0` | board genuinely empty — **end the pass**: there is no pass ticket, so Phase B has nothing to smoke |
| rows | these are the §2 rows, all bucket 2; claim exactly as §2 says, with the differences in §2 step 5 (and step 6 for a `stale-claim` row) |
| `ERROR …` | stop and report, as above |

Orphans are never read into §1a. There they would count as in flight on **both** Macs at once; here
the claim label and §2's double-stamp check decide who takes each one.

## 2 · Claim one batch — before any git work

1. Pick the **seed** **at random** from the **bucket-0** rows. Only if there are none, pick at
   random from bucket 1; bucket 2 only ever arrives alone, when §1's board was empty. Random, not first: both Macs read the same list in the same order.
2. **Group before you claim.** Read every board row's context tokens — never its description, only
   what this prints — and find the rows that share context with the seed:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
for k in <every key from §1's rows>; do
  curl -sS -k -H "Authorization: Bearer $JIRA_TOK" \
    "$JIRA_URL/issue/$k?fields=summary,labels,versions,parent,issuelinks,description"; echo
done | python3 -c "
import sys, json, re
SKIP = re.compile(r'^($TW_CLAIM_RE|v\d+\.\d+\.\d+|needs-.*|not-reachable|batch-.*)$')
for ln in sys.stdin:
    if not ln.strip(): continue
    i = json.loads(ln); f = i['fields']; d = f.get('description') or ''
    labels = f.get('labels') or []
    ctx = {'label:' + l for l in labels if not SKIP.match(l)}
    m = re.match(r'\s*\[([^\]]+)\]', f.get('summary') or '')
    pre = m.group(1).strip().lower() if m else ''
    if pre and pre not in ('ios', 'android', 'be', 'fe', 'web', 'bug'): ctx.add('prefix:' + pre)
    if f.get('parent'): ctx.add('parent:' + f['parent']['key'])
    ctx |= {'prd:' + x for x in re.findall(r'pageId=(\d+)', d)}
    ctx |= {'prd:' + x for x in re.findall(r'/pages/(\d+)', d)}
    ctx |= {'figma:' + x for x in re.findall(r'figma\.com/(?:file|design|proto)/([A-Za-z0-9]+)', d)}
    ctx.add('link:' + i['key'])
    for l in f.get('issuelinks') or []:
        o = l.get('outwardIssue') or l.get('inwardIssue') or {}
        if o.get('key'): ctx.add('link:' + o['key'])
    rc = ([v['name'].lstrip('Vv') for v in f.get('versions') or []]
          + [l[1:] for l in labels if re.fullmatch(r'v\d+\.\d+\.\d+', l)] + ['auto'])[0]
    print('%s|rc=%s|%s' % (i['key'], rc, ','.join(sorted(ctx))))
"
```

   The batch is the seed plus **up to two** rows (from either bucket) that share **at least one
   token** with the seed **and** the same `rc=`. More candidates than fit → the most shared tokens
   first, then bucket 0 first. `link:` tokens match a ticket linked to another candidate. No match →
   a batch of one, which is the single-ticket flow unchanged. Record which tokens joined them; the
   report names them.
3. Stamp the claim label (and, for a batch of two or more, `batch-<SEED KEY>`) on **each** member,
   then transition each:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
curl -sS -k -X PUT -H "Authorization: Bearer $JIRA_TOK" -H "Content-Type: application/json" \
  -d "{\"update\":{\"labels\":[{\"add\":\"$TW_MINE\"}]}}" "$JIRA_URL/issue/<KEY>"   # plus {"add":"batch-<SEED KEY>"} in a batch
tuntun-ios jira issue issue transition <KEY> --status fixing --insecure
curl -sS -k -H "Authorization: Bearer $JIRA_TOK" "$JIRA_URL/issue/<KEY>?fields=labels"
```

4. **Re-read the labels.** Jira has no compare-and-set, so two Macs reading the board seconds apart
   can both stamp. If a ticket now carries **two** claim labels (`macmini` and `macbookpro`), the
   Mac listed **first in `TW_HOSTS`** wins. If you lost one: remove your own labels from it (claim and `batch-…`) and drop it from
   the batch. If you lost the seed, the next surviving member stands in for it; lost them all → take
   another seed from §1's list. If that was the only row, Phase A ends — go to §8.

```bash
curl -sS -k -X PUT -H "Authorization: Bearer $JIRA_TOK" -H "Content-Type: application/json" \
  -d "{\"update\":{\"labels\":[{\"remove\":\"$TW_MINE\"}]}}" "$JIRA_URL/issue/<KEY>"
```

5. **A bucket-2 (orphan) claim differs in two ways.** Skip step 3's transition — the ticket is
   already `fixing`, and a same-status move is rejected. Strip any stale `batch-…` label it still
   carries before stamping yours. Then, after step 4, look for its open MR the way §8 does: **an
   MR exists** → do not spawn the fix agent; the ticket is now this Mac's in-flight work, so route
   it through **§1a's table** (review, rework or smoke) exactly as if §1a had found it. **No MR** →
   the fix agent runs as for any claim.
6. **A `stale-claim` row also needs the other Mac's label taken off, in the same PUT that adds
   yours.** Step 4's rule is that when both labels are present, the lower host id wins. If both
   labels were left on, Mac B could never take over from a dead Mac A. So replace it:

```bash
curl -sS -k -X PUT -H "Authorization: Bearer $JIRA_TOK" -H "Content-Type: application/json" \
  -d "{\"update\":{\"labels\":[{\"remove\":\"<STALE label>\"},{\"add\":\"$TW_MINE\"}]}}" "$JIRA_URL/issue/<KEY>"
tuntun-ios jira issue issue comment <KEY> --insecure \
  --body "Claim by <STALE label> idle since <updated>; taken over by $TW_MINE."
```

   The comment is for the person who finds the ticket later. It also bumps `updated`, so the other
   Mac, if it comes back, never sees this ticket as stale. Its §1a no longer sees the ticket either,
   because §1a matches on its own label and that label is now gone. Take over only the stale ticket
   itself, never its `batch-…` siblings: each sibling is judged by its own row.

`fixing` and `fixed` are real statuses, lowercase. Every Jira call needs `--insecure`.

Then open a task row on the team board — **if this Mac has the board at all.** `task` is not a
`tuntun-ios` subcommand; it lives on a separate `tuntun` binary that is not installed everywhere.
Probe, never assume, and never let a missing board stop the work:

```bash
command -v tuntun >/dev/null && \
  tuntun task start "<summary>" --kind <bugfix|feature|improvement|refactor|chore> \
    --ticket <KEY> --description "<what you are about to do, Markdown>"
```

`--kind` and a source are both **required** and the CLI rejects the row without them. `--ticket`
writes the title's `[<KEY>]` prefix for you, so do not repeat the key in the title.

If `tuntun` is present, the row must be closed in §7 on **every** path, including the abandon paths
in §3 and §4 — its `Stop` hook refuses to end a session with one open, and Phase B may not start
with it open. If `tuntun` is absent, skip every task-board step in this file and say so once in the
report.

The row belongs to the **main session** and stays open across the whole fix-agent run. CLAUDE.md is
explicit: a subagent works to the row already open for the work, never opens a second one and never
closes it. The agent's handoff is what the orchestrator later writes into `task done`.

**Phase B opens no row of its own.** Merging an MR is the last step of the row whose work it
carries, and CLAUDE.md is explicit that shipping is not its own row.

**Do not run `tuntun-ios task …`** on any path. Removed upstream, clap exits 2 for an unknown
subcommand, and a `Stop` hook reads exit 2 as *block* — that wedges the session end with nothing
but a usage message to explain it.

### Spawning the fix agent

Route by CLAUDE.md Phase 3, off the summary and issue type **already in §1's row** — do not open the
ticket yourself to decide. The one implementing agent is **`@ios-implementer`**; routing is now the
*procedure skill* the prompt names: `/ios-fix` for a bug, crash, regression or reopened sub-task,
`/ios-scaffold` for a new scene, `/ios-migration` on its signals, none for a change inside an
existing scene. Ambiguous summary → take the closest and record the assumption in the report; the
agent corrects course from the ticket itself and names the correction in its handoff.

The prompt stays short, because the runbook is this file and the agent reads it itself:

> You own ticket **<KEY>** end to end, in the worktree at `<this working directory>`. It is already
> claimed with the label `<TW_MINE, e.g. macmini>` and transitioned to `fixing` by the session that spawned you,
> and a task row is already open for it.
> Read `~/.claude/skills/ticket-workflow/SKILL.md` and execute **§3 through §7** exactly as written,
> then stop. CLAUDE.md's HARD CONSTRAINTS and pipeline apply throughout.
> Every shell block of yours starts with §0's preamble — `export PATH="$HOME/.tuntun/bin:$PATH"`,
> `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"` and `eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh` —
> because shell state does not survive between tool calls.
> Nobody is watching: at any choice point take the recommended option and record it, never ask.
> Ticket summary: `<summary from §1>`.
> Do **not**: spawn another agent, open or close a task row, claim a second ticket, smoke or merge
> anything, schedule a wakeup, or write a user-facing report. Every one of those is the
> orchestrator's.
> Finish with the handoff block below and nothing after it — no diffs, no file dumps, no command
> output.

**A batch of two or more** uses the same prompt with *ticket **<KEY>*** replaced by *tickets
**<KEY>, <KEY>**, one batch labelled `batch-<SEED KEY>`, on one branch with one commit per ticket as
the file's* Batches *section says*, and one `Ticket summary:` line per ticket. Its handoff adds a
`TICKETS:` line giving each ticket's own outcome (`<KEY> shipped · <KEY> not-reproducible`).

**The rework prompt** (§1a found a `needs-rework` MR):

> You rework MR **!<IID>** (`<source_branch>` → `<target_branch>`, ticket **<KEY>**) in the
> worktree at `<this working directory>`. The ticket is claimed and `fixing`, and a task row is open.
> Its last smoke run failed: note <note url>. Read `~/.claude/skills/ticket-workflow/SKILL.md` and
> execute **§5a**, then stop. (Same preamble, same unattended rule, same *Do not* list and the same
> handoff block as the fix prompt, with `OUTCOME: reworked`.)

### The fix agent's handoff block

An agent's report is never shown to the user, so the orchestrator relays it — and can only relay
what the block says. Thirteen lines, nothing after them:

```
OUTCOME:    shipped | reworked | not-reproducible | not-reachable | needs-version | blocked
TICKET:     <KEY> — <summary>
RC:         <target branch, and where it came from: Affects Version, a vX.Y.Z label, or the RC-floor rule>
BRANCH:     <branch, or "deleted" on any abandon path>
MR:         !<IID> <url> | none
SMOKE:      <n> steps in the MR description | none — <why>
CHANGED:    <n> files — <one line on what changed and why it matters>
FILES:      <every changed file, repo-relative, comma-separated — §7b's review scope>
VERIFIED:   <what you actually ran: lint, /ios-review, a build only where §7 forces one>
UNVERIFIED: <what you did not run — "no build, smoke steps not driven" is the normal answer>
JIRA:       <the status you left the ticket in, and any label you added>
ASSUMPTION: <any reading of the ticket you had to choose, or "none">
HEADS UP:   <something genuinely left broken, or "none">
```

`blocked` is a ticket you could not finish: say why on HEADS UP and leave the tree clean.

### When the fix agent comes back empty

A crashed or truncated agent leaves a ticket claimed with nobody working it, which is worse than an
unclaimed one — the label is also Phase B's filter. Do not re-drive the work in the orchestrator;
release the claim instead:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
tuntun-ios jira issue issue comment <KEY> --insecure \
  --body "Automated fix run ended without a result; releasing the claim. No MR was opened."
curl -sS -k -X PUT -H "Authorization: Bearer $JIRA_TOK" -H "Content-Type: application/json" \
  -d "{\"update\":{\"labels\":[{\"remove\":\"$TW_MINE\"}]}}" "$JIRA_URL/issue/<KEY>"
tuntun-ios jira issue issue transition <KEY> --list --insecure     # what Open-ward moves exist
```

Removing the label matters as much as the status: leave it on and **no Mac** ever picks the ticket
up again — this one skips it as already-claimed-and-fixed, the other as somebody else's. Move it
back to `Open`/`Reopened` if the workflow offers one, otherwise leave it in `fixing` and say so in
the report. Then close the task row (§7) and carry on into Phase B.

## 3 · Pick the branch off Affects Version

> **§3–§7 run inside the fix agent** (see *The split*). If you are that agent: this is your whole
> job, the ticket is already claimed, and you finish with the handoff block — nothing else.

`tuntun-ios jira issue issue view --json` exposes a **fixed subset** of fields — summary,
description, status, issuetype, priority, assignee, reporter, created, updated, comment,
attachment, labels, environment, issuelinks. **Affects Version is not among them.** Raw curl it:

```bash
curl -sS -k -H "Authorization: Bearer $JIRA_TOK" \
  "$JIRA_URL/issue/<KEY>?fields=versions,fixVersions,labels" | python3 -m json.tool
```

- `versions` = **Affects Version** — this is the one to use.
- `fixVersions` is empty on effectively every ticket. Ignore it.
- Names carry a capital-V prefix (`V2.4.0`); older ones do not (`1.8.2`). Strip a leading `V`.

Resolve the target branch, in this order, taking the first that names a branch that **exists**:

1. `versions` non-empty → candidate `$TW_RC_PREFIX/<name minus leading V>`.
2. A **`vX.Y.Z` label** on the ticket → `$TW_RC_PREFIX/<X.Y.Z>`. Tickets routinely carry the
   version as a label with Affects Version left empty, and the label is a better answer than any
   rule below because it came from the ticket.
3. **Neither → this is a question, and unattended it auto-answers "the oldest live RC at
   `$TW_RC_FLOOR` (2.4.0) or above"** — the RC-floor rule. Never "the earliest RC on the remote": taken literally that is
   `release_candidate/1.7.0`, which is years dead. Never the `tt-slot` pin either — it goes stale
   silently and has pointed at a branch that no longer exists.

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
git fetch origin --prune
git ls-remote --heads origin "refs/heads/$TW_RC_PREFIX/*" \
| sed "s#.*refs/heads/$TW_RC_PREFIX/##" \
| python3 -c "
import sys, re
FLOOR = tuple(int(x) for x in '$TW_RC_FLOOR'.split('.'))
out = []
for ln in sys.stdin:
    n = ln.strip()
    m = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', n)     # plain X.Y.Z only
    if m:
        t = tuple(int(x) for x in m.groups())
        if t >= FLOOR: out.append((t, n))
for _, n in sorted(out): print(n)
" \
| while read -r v; do
    if git merge-base --is-ancestor "origin/$TW_RC_PREFIX/$v" "origin/$TW_MAIN_BRANCH" 2>/dev/null; then
      continue                      # already released, keep looking
    fi
    echo "TARGET=$TW_RC_PREFIX/$v"; break
  done
```

Plain `X.Y.Z` **only** — the remote carries `2.4.0-txs-endpoints`, `2.1.0-clean`, `2.0.0-hyperion`,
`AO` and a dozen more suffixed spurs, and none of them is a release. `--is-ancestor` against `$TW_MAIN_BRANCH`
drops the ones already shipped, so what survives is the nearest release still open.

Nothing survives → hand back `OUTCOME: needs-version` after posting the question where a human will
see it:

```bash
tuntun-ios jira issue issue comment <KEY> --insecure \
  --body "No Affects Version and no vX.Y.Z label, and no unreleased $TW_RC_PREFIX at $TW_RC_FLOOR or
above. Cannot pick a target branch — please set Affects Version and remove the needs-version label."
curl -sS -k -X PUT -H "Authorization: Bearer $JIRA_TOK" -H "Content-Type: application/json" \
  -d "{\"update\":{\"labels\":[{\"add\":\"needs-version\"},{\"remove\":\"$TW_MINE\"}]}}" \
  "$JIRA_URL/issue/<KEY>"
tuntun-ios jira issue issue transition <KEY> --status Open --insecure
```

**Verify whichever you picked**, however you got there:

```bash
git ls-remote --exit-code --heads origin "$TW_RC_PREFIX/<X.Y.Z>"
```

Jira carries versions with no branch behind them, so an unchecked name is a dead checkout. A named
version that does not exist as a branch → `OUTCOME: blocked`, saying which candidates you tried; do
not silently fall through to the RC-floor rule, because a wrong Affects Version is worth seeing.

```bash
tt-harness-switch checkout -b "$TW_BRANCH_PREFIX/<TARGET_RC>/<KEY>-<slug>" "origin/<TARGET_RC>"
git push -u origin HEAD          # remote tracking ref
```

**`install.sh` runs only when the build says the workspace is stale**, never as a routine step after
a checkout. It is slow (≈ minutes on this 8 GB M1) and most branch switches do not need it. Run
`./install.sh` when, and only when, a build or `pod` step reports one of:

- `The sandbox is not in sync with the Podfile.lock. Run 'pod install' …`
- any other message telling you to run `pod install` (a missing `Pods/` file, a missing
  `Pods-*.xcconfig`, a workspace that no longer opens)
- `no such module 'TT…'` — a stale workspace, typically after moving to a different RC

Then build again once. `pod install` on its own never repairs this — always `./install.sh`. If the
same error survives `install.sh`, it is not a stale workspace: report it as the real build failure.

Report which RC the ticket landed on and where that came from, so a wrong version is visible rather
than silent.

## 4 · Understand and reproduce

Follow the CLAUDE.md pipeline, Phase 1 first: ticket → prior work on it
(`tuntun task list --ticket <KEY>`, only if `tuntun` exists) → PRD → video
(`tuntun-ios jira issue issue video <KEY> --insecure`) → Figma. Then FEATURES_MAPPING.yaml before
any project-wide grep.

### The last QA or design comment is the brief

Reopened tickets carry a comment history, and the description is usually the **oldest** statement
of the work. What QA or design wrote **last** is what they are waiting on now. It is often a
checklist with finished items marked `(fixed ✅)` and the rest still open. Read the whole thread for
context, but take the current work from the latest QA/design comment:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
curl -sS -k -H "Authorization: Bearer $JIRA_TOK" "$JIRA_URL/issue/<KEY>?fields=comment" \
| python3 -c "
import sys, json
NOT_QA = set('$TW_NOT_QA'.split())      # GitLab's 'mentioned this' bot, and this pipeline's own account
cs = json.load(sys.stdin)['fields']['comment']['comments']
qa = [c for c in cs if c['author']['name'] not in NOT_QA]
print('comments=%d qa_design=%d' % (len(cs), len(qa)))
if qa:
    c = qa[-1]
    print('LAST QA/DESIGN:', c['created'], c['author']['displayName'], '(%s)' % c['author']['name'])
    print(c['body'])
"
```

- **QA or design is anyone but `TW_NOT_QA`** (`admin` and `tommy.yohanes`). `admin` posts GitLab's automatic
  *mentioned this* notes. `tommy.yohanes` is the account this pipeline comments as, so its smoke
  and not-reproducible notes are this pipeline talking to itself, not a brief.
- **Every open item in that comment is in scope. Every `(fixed ✅)` item is not**, unless your drive
  shows it has regressed. An item you judge not fixable on iOS (a backend gap, another ticket's
  scope) is still answered: name it and say why in the MR description, as `ASSUMPTION:` on the
  handoff.
- **Open every image and video it attaches or embeds** (`!image.png!`, attachment links). The
  screenshot in a reopen comment usually *is* the repro and the design reference.
- **It overrides the description where they disagree.** Say which comment you followed (its date
  and author) on `ASSUMPTION:`.
- `qa_design=0` → the description is the brief, as before.
- A `$TW_JIRA_USER` (`tommy.yohanes`) comment that is **not** pipeline boilerplate (not a smoke, not-reproducible,
  release or claim note; for example "target retry with dev env") is the owner's instruction. Follow
  it too.

**Look at the design before you read it.** Three sources, in order; move down only when the one
above fails, and name the one you used on `ASSUMPTION:`. **Never call Figma's REST API** — not
`tuntun-ios figma`, not `figma-spec <KEY>` without `--links-only`, not `/ios-design`'s REST
fallback. Its `/files` quota is spent for days at a time (a 54-hour and a 65-hour `Retry-After`
so far), and an early retry only pushes the reset further out.

1. **A plugin spec in `~/figma-specs`.** The figma-to-claude plugin renders a frame from inside
   Figma, where nothing is metered, and its **Send** posts it to `com.tommy.figma-spec.serve`,
   which writes it there. A person presses Send, so the spec is there only if someone sent it —
   look, never wait. First the ticket's Figma link, off Jira alone:
   ```bash
   figma-spec <KEY> --links-only    # the URL, and which ticket it came from when <KEY> has none of its own
   ```
   A spec matches when its header names the link's node (`node-id=1-19216` is `1:19216`), or when
   its file name starts with `<KEY>` or with the key the link came from. The second is how a spec
   sent from a **draft copy** is filed: a development plugin only runs in a file you can edit, so
   handoff files are copied into a draft, and a copy has new node ids and no file key.
   ```bash
   grep -rlE --include='*.txt' '^# Nodes?: (.* \| )?<NODE> "' ~/figma-specs
   ls -t ~/figma-specs | grep -E '^(<KEY>|<SOURCE KEY>)'     # newest first
   ```
   No globs: the Bash tool's shell is zsh, where a glob matching nothing aborts the command.
   More than one → the newest. Read its `# Figma:` and `# Node:` lines and quote them, with the
   file's date, on `ASSUMPTION:`. This is the full spec — sizing (HUG/FILL/FIXED), strokes and
   their side, radii, effects, every text run's font and colour, and a colour/text-style summary
   to map onto `tuntun-ios designsystem token '<hex>'` — so it is never `degraded`. It does not
   carry gradient angles, and text is clipped at 400 characters; take those from the render.

   **No match → carry on to step 2, and ask for it.** Put `design: no plugin spec — Send <URL>,
   save as ~/figma-specs/<KEY>.txt` on `ASSUMPTION:`, so the report names the frame to send and
   the next run on this ticket (a rework, or a re-run) reads it at step 1.
2. **Figsnap MCP**, through its daemon, since you have no `figma_*` tools. Anything but a 200
   (`000` = daemon not running, or no `~/.figsnap-mcp/agent-token`) → step 3.
   ```bash
   curl -s -m 5 -X POST http://127.0.0.1:3058/tool -H 'content-type: application/json' \
     -H "x-figsnap-token: $(cat ~/.figsnap-mcp/agent-token)" \
     -d '{"name":"figma_resolve_url","arguments":{"url":"<URL>"}}'
   ```
   Then `/ios-design` STEP 0–3 through the same endpoint (`figma_export_png`, `figma_ios_spec`).
   A rate-limit answer from Figsnap → step 3, not a retry.
3. **Figma web in the Chrome already open on this Mac**, which is signed in and not under the
   API's rate limit:
   ```bash
   tt-figma-shot '<URL with node-id>' .tuntun/design/<KEY>.png   # Read it
   ```
   It borrows an open figma.com tab (else a new tab, else a new window), loads the link zoomed to
   the node, captures that window, and puts the tab back where it was. The capture is the whole
   window, so Figma's right-hand panel is in it: for the selected node it shows width, height and
   fill hex, and those are real numbers — quote them before measuring anything. Everything else
   is measured off the render the way the wiki-attachment fallback below does, solving scale
   from a known constant such as the 16pt content inset. Exit 3 = Chrome not running, or its
   window minimized or on another Space; exit 4 = the tab is not signed in or has no access to
   the file. Both need a person, so return `blocked` naming which, rather than working around it.

**A ticket that says the UI does not match the design always gets source 3 as well**, even when
source 1 or 2 answered. The spec gives the numbers. The headed-Chrome render at the node is the
pixel reference: put your simulator screenshot beside it at the same point scale and fix every
visible difference (gradient edges, borders, dividers, font size and weight, spacing) before
shipping. Keep it at `.tuntun/design/<KEY>.png` so Phase B grades against the same image.

Source 3 alone makes the spec `degraded`: read sizing (HUG/FILL/FIXED) and whether a stroke is
really drawn off the render. It also resolves colours by sampling the render and taking the
nearest token via `tuntun-ios designsystem token '<hex>'`. The numbers it yields are §6's
`EXPECT:` values. Name the artboard width; if it is not 402pt, say so in the
smoke steps. Ticket images on `wiki.tuntun.co.id/download/attachments/…` need the **wiki** token.
Try them before recording a design as blocked, and fall back to them when Figma is rate-limited.

Reproducing means driving the app. Build first with no simulator open, then `tt-sim-wait`, then
boot and open it (§10's *Build, then wait, then open*). Make sure **exactly one simulator is booted** first (§10's
UDID check): two booted simulators make **both** swallow every tap while the stream and the AX tree
still look healthy, which reads as "not reproducible" when nothing was ever driven. **Stop
`serve-sim` before you hand back** (`npx serve-sim --kill`) — Phase B's smoke agent starts its own
in the same pass.

### Accounts and the environment matrix

**Work on staging unless the ticket says otherwise.** Staging is this pipeline's environment for
both the repro drive and the smoke run — scheme `TTSecuritas Staging`, bundle `com.tuntunios.stg`,
the bundle §10 installs and drives. Leave it only when the ticket
names a different backend, says the bug is dev- or canary-only, or carries a build number from one;
say which environment you used and why on every handoff.

Read the live host off Xpector **before logging in** (`tuntun-ios xpector summary`), because a
stored `debugApiEnvironment=true` in the simulator container beats the Info.plist `BASE_URL` and has
silently pointed a Debug build at production. If what Xpector reports is not the environment you
meant to be on, fix that before driving anything — the account table below is keyed on the host you
actually reach, not the one you intended.

| Backend | Scheme | Bundle id | Account | Password |
|---|---|---|---|---|
| **staging — the default** | `TTSecuritas Staging` | `com.tuntunios.stg` | `tomtomtomgame6@outlook.com` (Email tab; OTP `111111`; registered 2026-09-24, no PIN set yet: the first PIN prompt creates it, use `111111`) | `tuntun1234` |
| staging — fallback | `TTSecuritas Staging` | `com.tuntunios.stg` | `tommyyohanesnew@gmail.com` (Email tab; OTP and PIN `111111`) | `tuntun1234` |
| staging | `TTSecuritas Staging` | `com.tuntunios.stg` | `85215318984` | `01oktober` |
| staging | `TTSecuritas Staging` | `com.tuntunios.stg` | `87742605816` | `tuntun123` |
| **dev / debug — the default** | `TTSecuritasDev` | `com.tuntunios.tt` | `tomtomtomgame6@outlook.com` (Email tab; OTP `111111`; registered 2026-09-24, no PIN set yet: the first PIN prompt creates it, use `111111`) | `tuntun1234` |
| dev / debug — fallback | `TTSecuritasDev` | `com.tuntunios.tt` | `tuntunrdn13@mailsac.com` | `tuntun123` |
| dev / debug | `TTSecuritasDev` | `com.tuntunios.tt` | `Makmur15@mailsac.com` — CS-manager, only when the flow needs that role | `Tuntun1234` |
| canary / release | `TTSecuritas Canary` | `com.tuntunios.cnr` | `82117236762` | `Tuntun1234` |
| canary / release | `TTSecuritas Canary` | `com.tuntunios.cnr` | `81285965506` | `tuntun123` |
| *backend unconfirmed* | — | — | `tommy.yohanes@gmail.com` | `tuntun123` |

Debug **Prefill Login is broken** — the phone tab rejects its own prefill. Use the **Email tab**.

Two different `111111` live here and they are not the same thing. The app's six-digit **trading
PIN** is six ones, so driving it means tapping the `1` key on the in-app `TTNumpadView` six times —
it is not the system keyboard, so `serve-sim type` does not reach it. Pace those taps: six fast taps
fill all six dots and then nothing happens, no submit and no error, and the stalled attempt is not
counted against the 5-try lockout. Separately, the **dev backend's login OTP** is a fixed `111111`,
which expires in ~60 s, so fire its six taps back-to-back. Keep the VPN **on** for dev backends; it
is what makes them reachable.

### Three outcomes, and only one of them continues

**Reproduced** → go to §5.

**Reachable but it does not happen** — you got to the screen and the ticket's symptom is not there.
Close it as `fixed`; do not just skip. The ticket is already `fixing`, and Phase B's filter is
exactly `status = fixing`, so abandoning it there parks a ticket in that queue forever with no MR
behind it. `fixed` is terminal for **both** boards, so it neither reaches a smoke run nor comes back
around. **Comment before you transition** — a `fixed` ticket with no MR behind it is
indistinguishable from a smoke-tested, merged one unless the comment says otherwise:

```bash
tuntun-ios jira issue issue comment <KEY> --insecure \
  --body "Not reproducible. Drove <flow> on <backend> as <account>, build <sha> off <TARGET_RC>.
Expected <what the ticket claims>; saw <what actually happened>. Closing as fixed without a change.
Reopen with a build number and the exact steps if it still occurs."
tuntun-ios jira issue issue transition <KEY> --status fixed --insecure
tt-harness-switch checkout <TARGET_RC> && git branch -D <branch> && git push origin --delete <branch>
```

Hand back `OUTCOME: not-reproducible`.

**Not reachable** — you could not get to the screen at all: the entry point is gone, the account
lacks the role or the holdings, a prerequisite feature is off, the build will not reach that state.
This is *not* the same as the symptom being absent, and it must not be closed as `fixed`, because
nothing was actually tested:

```bash
tuntun-ios jira issue issue comment <KEY> --insecure \
  --body "Repro steps are not reachable. Drove <how far you got> on <backend> as <account>, build
<sha> off <TARGET_RC>, and stopped at <the step that could not be reached> because <why>.
Nothing was verified. Reopened and labelled not-reachable so the automated pipeline skips it;
remove that label once the steps are reachable."
curl -sS -k -X PUT -H "Authorization: Bearer $JIRA_TOK" -H "Content-Type: application/json" \
  -d '{"update":{"labels":[{"add":"not-reachable"}]}}' "$JIRA_URL/issue/<KEY>"
tuntun-ios jira issue issue transition <KEY> --status Reopened --insecure
tt-harness-switch checkout <TARGET_RC> && git branch -D <branch> && git push origin --delete <branch>
```

Leave your `$TW_MINE` claim label on. The ticket sits in Reopened where a human sees it, and §1's
exclusion keeps **both** Macs off it until somebody removes `not-reachable`. Hand back
`OUTCOME: not-reachable`.

On every abandon path the task row is not yours to cancel — the orchestrator closes it (§7).

If a transition is rejected, `tuntun-ios jira issue issue transition <KEY> --list --insecure` shows
what the workflow allows. Report it rather than leaving the ticket stranded.

That ends your run. Picking a different ticket is not a second lap, and Phase B is the
orchestrator's.

## 5 · Fix it

**You are that agent.** CLAUDE.md's Phase 3 routing already happened when the orchestrator spawned
you, so write the fix yourself and never spawn another agent — agents never spawn agents. That makes
this CLAUDE.md's solo-edit path, where `tuntun-ios for <task>` is a **blocking** gate before any
Swift: run it, read it, then write. UIKit + SnapKit, VIP only, `TTColor` and `.applyTypography`
only.

Ambiguous ticket → take the reading it best supports, record it on `ASSUMPTION:`, and do not stop to
ask; nobody is watching. If the work turns out to belong to a different specialist than the one you
were spawned as, note that on `ASSUMPTION:` and do it anyway where it is within your role's reach —
hand back `blocked` only for something you genuinely should not be writing, such as a whole new
scene when you are not the scaffolder.

## 5a · Rework a `needs-rework` MR

You were spawned with the rework prompt. The branch and MR exist; skip §3 and §4 — the failure
note **is** the repro, and it names the backend, the account and what was on screen.

1. `git fetch origin --prune && tt-harness-switch checkout <source_branch> && tt-harness-switch reset --hard origin/<source_branch>`,
   then `tt-harness-switch rebase origin/<target_branch>`. `./install.sh` only if a build reports the pods out of sync (§3's rule).
2. Re-read the ticket's **last QA/design comment** (§4) first: a newer one may have changed the
   brief since the MR was opened. Then read the failure note and decide **which half was wrong**,
   and write that down on `ASSUMPTION:`:
   - **The code** — the drive saw the symptom the ticket describes, or a case the fix does not
     handle (a format the endpoint really sends, an edge the unit tests missed). Fix it per §5 and
     add the unit test that would have caught it.
   - **The recipe** — an `EXPECT:` contradicts the fix's own documented intent (its unit tests, the
     ticket, the design), or the steps named a backend that cannot exercise the fix. Correct the
     steps. This is Phase A's to do and is not softening: the change must be justified by the
     ticket or design, never by what the build happened to show. An `EXPECT:` may be made more
     precise, never removed and never loosened to accept the symptom the ticket reports.
   - Often both. Fix both.
3. §7's lint and `/ios-review` on what you touched, then commit on top — never amend the reviewed
   commit (on a batch MR, rework only the tickets named in the marker's `failed=`, and fold each fix
   into that ticket's own commit with `--fixup` + autosquash, as *Batches* says) — and `git push --force-with-lease origin <source_branch>` (the rebase rewrote history).
4. Update the MR description: add or extend a `## Rework after smoke (note <id>)` section — what
   the drive showed, which half was wrong, what changed — above `## Smoke steps`, and edit the steps
   in place. Post a short MR note replying to the failure note that links the new commit.
5. Leave the ticket in `fixing`. Hand back `OUTCOME: reworked`.

A failure caused by **missing backend data** is not a rework — §10 retries it on another backend,
and if none has the data it returns `blocked-no-data`, which §1a never picks.

## 6 · Write the smoke steps that prove it

The steps ship **inside the MR description**, so the recipe that proves the fix travels with the
fix. You write them here, not Phase B, because you are the one who just drove the repro and knows
the path.

**Why steps and not a committed test.** Phase B drives the simulator by hand with
`/serve-sim:serve-sim` — taps at normalized coordinates against a live screen, judged from
screenshots. There is no committed artifact for it to execute, so what Phase B needs from you is an
unambiguous recipe plus, for every claim, **the one observation that decides pass or fail**. A step
with no stated expected observation is a step Phase B cannot grade, and it will come back
`needs-rework` rather than guess.

Put a `## Smoke steps` block in the MR description:

```markdown
## Smoke steps
Env: staging · scheme `TTSecuritas Staging` · bundle `com.tuntunios.stg` · account `tomtomtomgame6@outlook.com`
Appearance: light | dark | both — <state it whenever the fix is appearance-specific>

1. Log in on the Email tab, land on the tab bar.
2. Tap **Stock** → the Market page.
3. <one action per line, each naming the on-screen label it targets>
4. EXPECT: <the single observable fact the ticket claims, stated so a screenshot settles it>
```

**A batch** puts one `### <KEY> — <summary>` subsection per ticket under that one heading, each
with its own numbered steps and `EXPECT:` lines. Write the shared `Env:` / login once at the top and
order the subsections so the drive walks the screens once (a later subsection may start from where
the previous one ended; say so in its first step). A ticket that left the batch gets no subsection.

Rules that are not negotiable here:

- **One `EXPECT:` line per open item in the last QA/design comment (§4), and nothing else.** With
  no such comment, one per claim the description makes. Phase B reports against these
  lines verbatim. "Looks right" is not an EXPECT; "the sheet body and the footer band are the same
  colour, with no seam between them" is.
- **Give a colour, size or count its exact value.** A fidelity ticket is graded on a number, and
  Phase B samples pixels rather than eyeballing. Write `EXPECT: sheet body reads #1B1B1F
  (bgT6Pure), not #0E0E12 (bgPure)` — resolve the token to hex yourself, from the `.colorset`, and
  put both values in so the failure case is recognisable too.
- **Name the appearance when it matters**, and say what the other one should do. A dark-mode-only
  fix needs `EXPECT:` lines for dark *and* a line saying light is unchanged — otherwise a no-op
  claim in the commit message never gets tested. Phase B switches with
  `xcrun simctl ui <udid> appearance dark|light`.
- **Navigate by the label a human reads on screen.** Phase B finds elements in the accessibility
  tree by `AXLabel` and converts their frames to taps, so a step written as "tap the row labelled
  *Sentiment*" is directly executable and "tap the second card" is not. Where an element carries no
  label at all, add an `accessibilityIdentifier` in Swift as `<scene>.<element>` while you are in
  the file, and say in the step which one you added.
- **Only the ticket's flow.** Do not ask Phase B to check anything the ticket does not claim. Five
  unrelated checks fail for five unrelated reasons and tell you nothing about this fix.

Do **not** drive it here — the branch is not built yet and Phase B compiles and drives it. Writing
steps you never executed is expected at this step; say so on `UNVERIFIED:`.

## 7 · Verify, ship, and close the row

1. `tuntun-ios lint <touched files>` — **≈5 files per call**. Bigger batches truncate findings
   silently and the gate passes bad code. Fix every P0 *and* P1, pre-existing ones included.
2. Invoke `/ios-review` on the changed files; fix all P0/P1.
3. **No build here by default** — that is the project's rule, and Phase B compiles this branch
   anyway before it drives it, so a routine fix does not need a second compile. Never claim it
   builds, though: lint does not compile.

   The exception is a change lint structurally cannot see — a cross-file `private extension`, a
   moved or renamed symbol, a new file's module membership. That class of mistake is lint-clean and
   uncompilable, and shipping it burns Phase B's whole build. Build only then: `xcodebuild …`. Never read the verdict through a pipe:
   `xcodebuild … | tail` reports exit 0 over BUILD FAILED. Anchor error greps to `file:line:col:`.
4. Expect the format-on-save hook to widen the diff (it rewrites whole Swift files — a 5-line fix
   once became 64 lines). Review the diff and drop unrelated reformatting before committing to an
   RC branch.

```bash
git add -A -- . ':!CLAUDE.md' ':!.claude' && git commit && git push   # the fix only, never the harness; §6's steps go in the description
glab mr create --source-branch "$(git branch --show-current)" \
  --target-branch "<TARGET_RC>" --assignee "$TW_MR_ASSIGNEE" \
  --squash-before-merge \
  --title "fix(<area>): [<KEY>] <what changed>" \
  --description "<why, how verified>

<§6's ## Smoke steps block, verbatim>" --no-editor
```

**The `## Smoke steps` block must be in that description.** It is the only place Phase B looks, and
§9 blocks the pass when it is missing — there is no committed flow file to fall back on any more.

Target the RC picked in §3 — **never `main` or `dev`**; both are Maintainers-only and I am
Developer. The assignee is me by design: the MR's review is §7b's independent `@code-reviewer`
gate, which the orchestrator runs after your handoff.

**Always `--squash-before-merge`** — for a single ticket. It ticks "Squash commits when merge
request is accepted", so the fix, the review follow-ups and any rebase land on the RC as one commit.
Never omit it to fall back on the project default.

**A batch is the opposite: never squash.** Commit once per ticket, drop `--squash-before-merge`,
title it `fix(<area>): [<KEY>][<KEY>] <the shared feature>`, open the description with a
`Tickets:` line listing each key and its one-line change, and then turn the project's default-on
squash off explicitly:

```bash
glab api --hostname "$TW_GITLAB_HOST" --method PUT "projects/$TW_GITLAB_PROJECT/merge_requests/<IID>?squash=false" \
| python3 -c "import sys, json; print('squash=%s' % json.load(sys.stdin)['squash'])"   # must print False
```

Review follow-ups on a batch are folded into the owning ticket's commit (*Batches*), then
`git push --force-with-lease`.

**Do not review the MR yourself and post no review note.** That is §7b: the orchestrator spawns
`@code-reviewer` on your `FILES:` once you hand back. If it returns FINDINGS you are sent back with
`SendMessage` — fix each one, re-run step 1–2 on what you touched, commit on top (a batch folds each
fix into its ticket's commit, as *Batches* says), push, and hand back the same block again.

**Apply no labels to the MR.** There is no queue label in this pipeline; Phase B finds work through
Jira. Leave the ticket in `fixing` — that is what a smoke run expects to find. Do not set `fixed`;
that is §12's call, after the app has actually been driven.

Project 49 has **no CI at all**, so there is no pipeline to wait on.

Then stop: that is the end of your run. Emit the handoff block and nothing after it.

Closing the task row is the **main session's** step, written from that handoff and §7b's verdict
rather than from anything it re-reads. It happens **after §7b and before Phase B starts** — the row
covers the fix and its review, not the smoke run. An abandon outcome has no MR and no §7b, so its
row closes straight from the handoff:

```bash
command -v tuntun >/dev/null && \
  tuntun task done --changes "<what the fix does, in behaviour terms>" \
    --verification "<what the agent actually ran — lint, /ios-review — plus §7b's verdict and round count, that nothing was built and the smoke steps were not driven>" \
    --heads-up "<only when the handoff or §7b names one>"
```

`--changes` and `--verification` are both required; there is no `--description` on `done`. Write
both from the handoff's `CHANGED:` / `VERIFIED:` / `UNVERIFIED:` lines and §7b's verdict, and never
claim a gate neither names. On any abandon outcome say so in `--changes` — the row still closes.

## 7b · Review the MR — the `@code-reviewer` gate

**Main session.** Runs when the fix agent hands back `OUTCOME: shipped` or `reworked` with an MR, and
when §1a finds an MR with no `review=approved` marker at its current head. Every other outcome
skips it. It is the only review the MR gets before Phase B: **Phase B never starts on an MR that
is not APPROVED at its current head.**

Spawn it with the **Agent** tool, `subagent_type: "code-reviewer"` — one at a time, like every
other agent in this pass, and never `isolation: "worktree"`:

> Gate MR **!<IID>** (`<source_branch>` → `<target_branch>`, ticket(s) **<KEY>**) in the worktree at
> `<this working directory>`, on branch `<source_branch>`, committed and pushed.
> Scope: `git diff origin/<target_branch>...HEAD` — these files: `<FILES: from the handoff>`.
> Ticket summary: `<summary from §1>`. The MR description holds the acceptance intent and the
> `## Smoke steps`; read it with `glab mr view <IID>`.
> Every shell block of yours starts with `export PATH="$HOME/.tuntun/bin:$PATH"` and
> `eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh`.
> Nothing was built and the smoke steps were not driven — Phase B does both; do not ask for them.
> Nobody is watching: never ask. Return APPROVED or FINDINGS, each finding with `file:line`,
> severity and evidence, and nothing after the verdict.

The spawn prompt is the handoff's account; the reviewer reads the diff before it. Do not paste the
fix agent's summary into it beyond the `FILES:` scope.

**Post the verdict on the MR** — one note per round, opening with the marker §1a reads:

```bash
glab mr note create <IID> -m "<!-- ticket-workflow review=<approved|findings> head=<the MR's current head sha> round=<n> -->
**@code-reviewer — <APPROVED | FINDINGS>** (round <n>)

<the verdict's findings, verbatim, or \"No findings.\">"
```

**FINDINGS is a rejection.** Send the findings to the **same** fix agent with `SendMessage` (its
agent id from the spawn) — "§7b returned FINDINGS on !<IID>; fix them per §7 and hand back the
same block" plus the findings verbatim. When it hands back, send the **same** reviewer, again with
`SendMessage`, the new head and this instruction: re-check each earlier finding **and** what the fix
could have broken. Post that round's note. Never re-spawn a reviewer for a different answer, never
overrule or approve on its behalf, and never fix a finding in the main session.

**Two rounds on one finding → a person decides.** The same finding in a second FINDINGS verdict
stops the loop: add `needs-human` to every ticket of the batch, comment on the MR and each ticket
with the review notes' links, close the row saying so in `--heads-up`, and **skip Phase B** for this
MR. The label frees the slot, so the next pass claims fresh work.

**A reviewer or fix agent that comes back empty** leaves no `review=approved` marker, so the MR is
not smoked; close the row with a `--heads-up` and end the pass — the next pass's §1a re-runs §7b
from a clean spawn. That is a crash recovery, not a second opinion.

Then close the row (§7) and continue to Phase B.

---

# Phase B · Smoke the pass ticket's MR

Phase A is over. Phase B smokes **the pass ticket's MR and no other**. It runs when that ticket
has an open MR that §1a's table sends to Phase B: freshly shipped, freshly reworked, or waiting.
It does nothing when the ticket left `fixing` (`not-reproducible`, `not-reachable`,
`needs-version`), when its fix came back `blocked` or with no MR, when it is waiting on market
data, or when the board was empty. It never goes looking for another MR to fill the pass. A §0/§1
credential failure skips it too: a dead Jira token cannot mark a ticket `fixed`, and a merged MR
behind a `fixing` ticket is worse than an unmerged one.

## 8 · Find the pass ticket's MR

This is no longer a queue read. §1a already chose the pass ticket; §8 only confirms its MR and
guards against the ticket having moved. The filter below is kept because it is also §1a's source.

`TW_FIXING_JQL` is the in-flight queue. Its default, filter 10550, is *"Fixing"*: `status = fixing
AND assignee = tommy.yohanes` across UATP/TUNTUN/PBT. Two clauses are added: `component =
$TW_COMPONENT`, and **this Mac's own claim label**.

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
JQL="($TW_FIXING_JQL) AND component = \"$TW_COMPONENT\" AND labels IN ($TW_MINE_JQL) AND labels NOT IN (\"needs-human\")"
curl -sS -k -G -H "Authorization: Bearer $JIRA_TOK" \
  --data-urlencode "jql=$JQL" --data-urlencode "fields=summary,labels" \
  --data-urlencode "maxResults=50" "$JIRA_URL/search" \
| python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'errorMessages' in d: print('ERROR', d['errorMessages']); raise SystemExit(1)
print('total=%d' % d.get('total', 0))
for i in d.get('issues', []): print('%s|%s' % (i['key'], i['fields']['summary']))
"
```

The label clause is the whole cross-Mac story: it is sticky from §2, so these are the tickets **this
Mac** fixed. The other Mac's rows are invisible here and that is correct — it will smoke its own.

The pass ticket is missing from it (it moved, or someone added `needs-human`) → **nothing to
smoke**; report and end the pass.

**Then confirm the pass ticket's MR exists:**

```bash
glab api --hostname "$TW_GITLAB_HOST" \
  "projects/$TW_GITLAB_PROJECT/merge_requests?state=opened&per_page=100" \
| python3 -c "
import sys, json
KEYS = ['<KEY>']                     # every ticket of the pass batch
for m in json.load(sys.stdin):
    if any(k in (m['title'] + ' ' + m['source_branch'] + ' ' + (m.get('description') or '')) for k in KEYS):
        print('%s|%s|%s|%s' % (m['iid'], m['source_branch'], m['target_branch'], m['title']))
"
```

This check is load-bearing, not a formality: §2 sets `fixing` the moment it *claims* a ticket, so
the fixing queue also holds tickets still being fixed with no MR behind them yet. No open MR for the
pass ticket → nothing to smoke; report and end. Do **not** try another row.

Fetch every open MR and match client-side. Do not use `scope=assigned_to_me` — it silently drops an
MR opened without an assignee. Do not use `glab mr list --state` — that flag does not exist on the
installed glab (1.111.0); `--all` works if you prefer the CLI.

**Do not smoke an MR that would fail identically.** A newest marker of `smoke=needs-rework` whose
`head=` is still the MR's head means this pass's rework did not push. Report it and end; do not smoke,
**unless** the rework was recipe-only (§5a: the code was right, an `EXPECT:` was not). That rework
pushes nothing but edits the MR description and posts a reply note, and a changed recipe will not
fail identically. So a `## Rework after smoke` section plus a rework note newer than the marker →
smoke it. §1a reads the same pair as *waiting to be smoked*, not as a rework still to do.

**Exactly one MR per pass, and it is the pass ticket's.**

### No device lock

There is **no device lock** in this pipeline. This pass runs one agent at a time (*The split*), so
its own compiler and simulator use is sequential by construction. Call `xcodebuild`, `xcrun simctl`
and `serve-sim` directly. If you find an old `tt-device-lock` holder left behind, ignore it; do not
wait on it.

**But another session on this Mac may be using the simulator** — a person driving it by hand, or
another Claude session's serve-sim, `xcodebuild test` or Maestro run. Installing over it swaps the
app out from under that session's drive, and `serve-sim --kill` takes its helper down. So before
you boot or open the simulator for a drive (§4's repro, §10's smoke) — which is always **after**
the build has finished, never before it — **wait for it**:

```bash
tt-sim-wait          # exit 0 = free; exit 3 = still busy after 9 minutes
```

`tt-sim-wait` (`~/.tuntun/bin`) polls every 30 s for a live process of another session that drives
or rebuilds onto a simulator (serve-sim, `simctl install|launch|io`, `xcodebuild test`, Maestro,
an XCTest runner) and prints who holds it. It reads only what is running now, so unlike the old
lock nothing can go stale. Run it **before** your own `serve-sim --kill` and while no helper of
yours is up, or it waits on itself.

Exit 3 → run it again, up to **4 calls (~36 min)** in all; one call stays under the Bash tool's
10-minute cap. Still busy after that → do **not** install, kill or drive anything:

- **§10 (smoke agent)** → `VERDICT: sim-busy`, naming the holder from its output. Post **no** MR
  note and **no** marker; leave the ticket in `fixing` and the rebase unpushed. With no marker at the
  MR's `sha`, the next pass's §1a sees it as *waiting to be smoked* and Phase B tries again.
- **§4 (fix agent)** → `OUTCOME: blocked`, `HEADS UP: simulator in use by <holder>`. It is not
  `not-reproducible` or `not-reachable`: nothing was driven.

### Spawning the smoke agent

Use the **Agent** tool with `subagent_type: "general-purpose"` — no `ios-*` specialist covers
building and driving a running app. Keep the prompt short; the runbook is this file:

> You smoke-test MR **!<IID>** (`<source_branch>` → `<target_branch>`, ticket **<KEY>**) in the
> worktree at `<this working directory>`.
> Read `~/.claude/skills/ticket-workflow/SKILL.md` and execute **§9 through §12** exactly as written,
> then stop.
> Every shell block of yours starts with `export PATH="$HOME/.tuntun/bin:$PATH"`, `export
> NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"` and `eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh` — shell state
> does not survive between tool calls, and serve-sim needs nvm's node.
> Invoke the **`/serve-sim:serve-sim` skill** before you drive anything; it owns the CLI surface.
> Nobody is watching: at any choice point take the recommended option and record it, never ask.
> Do **not**: re-read the queue, claim another MR, claim or fix a ticket, soften or rewrite the
> MR's smoke steps to make them pass, spawn another agent, schedule a wakeup, or write a
> user-facing report.
> Finish with the verdict block below and nothing after it — no build logs, no file dumps.

### The smoke agent's verdict block

```
VERDICT:    passed-and-merged | needs-rework | blocked | blocked-no-data | sim-busy
TICKET:     <KEY>        MR: !<IID>
BRANCH:     <source_branch> @ <sha after the rebase>
BUILD:      ok | failed — <the verdict line, never the log>
SMOKE:      <n> steps driven — every EXPECT met | failed at step <n> | steps missing from the MR
BACKEND:    <host read off Xpector>   ACCOUNT: <the one you logged in with>
SAW:        <what the run actually showed, including whether the proof screenshot exists>
PROOF:      gitlab note <id/url> | jira attachment <filename> | none — <why>
JIRA:       fixed | fixing   (a batch: one entry per ticket)
HEADS UP:   <something genuinely left broken, or "none">
```

A batch lists every key on `TICKET:`, and `SMOKE:` / `SAW:` report per `### <KEY>` subsection,
naming the tickets whose `EXPECT:` failed.

`needs-rework` is a **handled** MR, not a failure of the pass. The ticket stays in flight and the
next pass's §1a reworks it automatically. A `blocked` verdict parks the ticket with `needs-human`
on the next pass (§1a), which frees the slot. `blocked` is a run that could not reach a verdict at all: a broken rebase, an
unmergeable MR, smoke steps missing from the MR. `blocked-no-data` is §10's: no backend returned
the data an `EXPECT:` depends on, so nothing about the fix was learned either way. `sim-busy` is
another session holding the simulator past `tt-sim-wait`'s ~36 min: nothing was installed, no note
posted, and the MR stays *waiting to be smoked* for the next pass. It parks nothing.

### When the smoke agent comes back empty

Nothing to unwind — there is no label to restore. Stop any `serve-sim` helper it left running
(`npx serve-sim --kill`), note it in the report, and end the pass. Do not take
another MR.

## 9 · Rebase onto the live target, then build

> **§9–§12 run inside the smoke agent** (see *The split*). If you are that agent: this is your whole
> job, and you finish with the verdict block — nothing else.

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
git fetch origin --prune
tt-harness-switch checkout <source_branch> && tt-harness-switch rebase "origin/<target_branch>"
```

Do not run `./install.sh` here by default. If the build below reports the pods out of sync, a
`pod install` prompt or `no such module 'TT…'`, run it then and rebuild once (§3's rule). A build
that fails that way before `install.sh` has run is not yet a `needs-rework`.

Project 49 merges **fast-forward only**, so a rebase is required, not optional.

**Leave it unpushed here.** §12 pushes the rebased branch once the smoke has actually passed, so a
branch that fails the drive never gets its history rewritten on the remote.

A clean rebase is not a working build. RC-side code that references APIs this branch renamed
**never conflicts** and still breaks the build, and `rerere` will happily replay resolutions from a
rebase attempt you threw away. So build before you trust it:

```bash
xcodebuild -workspace "$TW_WORKSPACE" \
  -scheme "$TW_SCHEME" -destination "platform=iOS Simulator,name=$TW_SIM_DEVICE" build
```

**A build needs no booted simulator and no Simulator.app** — `xcodebuild build` resolves the named
destination without booting it. So do **not** boot or open the simulator before or during the
build: it would sit there for the whole ~10-minute compile, fighting the compiler for this Mac's
8 GB while another session may still be using the device. The simulator is opened in §10, only
after the build has succeeded and `tt-sim-wait` says it is free.

Use the scheme matching the environment you are driving, from §4's table. **`TTSecuritas Staging` is
the default** — switch only when the ticket named another backend, and build the scheme that matches
the `APP_ID` §10 will pass, or you will drive a bundle you did not build. Never pipe the verdict through `tail` — that turns BUILD FAILED into
exit 0. Grep loosely for the verdict line, anchor error greps to `file:line:col:`, and never pass
`-derivedDataPath` (it forces a cold build). The built app is **`$TW_APP_NAME`** (`Tuntun Sekuritas.app`,
not `TTSecuritas.app`).

Also confirm §6's smoke steps are actually on the MR, and pull them out to drive from:

```bash
glab mr view <IID> --output json | python3 -c "
import sys, json
d = json.load(sys.stdin).get('description') or ''
i = d.find('## Smoke steps')
print(d[i:] if i >= 0 else 'MISSING SMOKE STEPS')
"
```

Missing, or present with no `EXPECT:` line → `VERDICT: blocked`, `SMOKE: steps missing from the MR`;
say so on the MR and leave the ticket in `fixing`. **Do not write the steps yourself**: Phase A owns
them, and a test written by the thing that grades it proves nothing.

Build fails after the rebase → do not smoke it. Comment the failure on the MR — §1a's marker line
first (`smoke=needs-rework`, the sha you built), then the verdict line and the first real
`file:line:col:` errors, never the whole log — leave the ticket in `fixing`, and end
with `VERDICT: needs-rework`, `BUILD: failed`.

## 10 · Drive the smoke steps with serve-sim

Install the freshly built app, then drive the MR's `## Smoke steps` and nothing else.

**Invoke the `/serve-sim:serve-sim` skill before you touch the simulator.** It owns the CLI surface
and the gesture JSON shape; this section carries only what is specific to this Mac and this app —
which is most of what has ever gone wrong here.

### Build, then wait, then open the simulator — in that order

§9's build is finished before anything here runs. Only now, and only once the simulator is free,
boot it and bring Simulator.app up:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"; eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
tt-sim-wait || echo "SIM BUSY — call tt-sim-wait again (max 4 calls) before opening the simulator"
SIM=$(xcrun simctl list devices available -j | python3 -c "
import sys, json
print(next(x['udid'] for v in json.load(sys.stdin)['devices'].values() for x in v if x['name']=='$TW_SIM_DEVICE'))
")
xcrun simctl boot "$SIM" 2>/dev/null || true      # already booted is fine
open -a Simulator --args -CurrentDeviceUDID "$SIM"
xcrun simctl bootstatus "$SIM" -b
```

Busy after four `tt-sim-wait` calls → `VERDICT: sim-busy` (*No device lock*, §8): nothing is
booted, opened or installed.

### Exactly one booted simulator, with Simulator.app attached

Two booted sims swallow every tap, and a headless-booted sim swallows them too while the MJPEG
stream and the AX tree both keep looking healthy. Resolve the UDID rather than hardcoding it —
every UDID on this Mac changed when the toolchain was reinstalled:

```bash
export PATH="$HOME/.tuntun/bin:$PATH"
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
eval "$(tt-slot env --with-jira)"; . ~/.claude/skills/ticket-workflow/env.sh
UDID=$(xcrun simctl list devices booted -j | python3 -c "
import sys, json
ids = [x['udid'] for v in json.load(sys.stdin)['devices'].values() for x in v if x['state']=='Booted']
print(ids[0] if len(ids) == 1 else 'AMBIGUOUS:%d' % len(ids))
")
echo "udid=$UDID"
```

`AMBIGUOUS:0` or `AMBIGUOUS:2` → fix that before driving anything. With two booted devices `tap`
and `gesture` **report success and silently do nothing**, even with the right `-d` — everything
looks healthy, so it reads as an app bug rather than an input-routing one. `xcrun simctl shutdown
<other-udid>`, then restart the helper.

### Install, check the backend, set the appearance

`tt-sim-wait` already passed above, before the simulator was opened.

```bash
xcrun simctl install "$UDID" \
  "$HOME/Library/Developer/Xcode/DerivedData/${TW_WORKSPACE%.xcworkspace}-"*"/Build/Products/$TW_PRODUCTS/$TW_APP_NAME"
```

Then read the live backend off Xpector — `tuntun-ios xpector summary` — because a stored
`debugApiEnvironment=true` in the container overrides the Info.plist `BASE_URL` and has pointed a
Debug build at production. You should see staging; pick the account from §4's table by the host you
actually reach, and report the mismatch if it is not what §9 built.

**No data is not a verdict.** When an `EXPECT:` grades figures the backend supplies (prices,
volumes, counts, a list's rows) and Xpector shows that response came back empty or all zeros, the
fix was never exercised — staging's market endpoints have returned `0` for every field on a trading
day. Prove it from the response, not the screen, then **rebuild and drive again on the next
backend** in this order, inside the same run: staging → dev (`TTSecuritasDev`, `-configuration
Debug`, `com.tuntunios.tt`, `Debug-iphonesimulator`) → canary. Use §4's accounts for each. The
steps' `Env:` line names where Phase A expected to drive; a data fallback overrides it and is
reported on `BACKEND:` with the reason. Every other step and `EXPECT:` is graded verbatim.

No backend has the data → `VERDICT: blocked-no-data`, a note with the `smoke=blocked-no-data`
marker naming each backend and the empty response field, ticket left in `fixing`. It is not
`needs-rework`: the code is not at fault and a rework would have nothing to fix.

If the steps name an appearance, set it before you drive, and set it back for the other half:

```bash
xcrun simctl ui "$UDID" appearance dark     # or light
```

### Start the helper

```bash
npx serve-sim --kill
npx serve-sim --detach --no-preview "$UDID" -q
```

`--detach` takes the device **positionally** and rejects `-d`; `tap`, `type` and `gesture` do take
`-d <udid>`. Parse the `-q` JSON, never the human-readable output.

### Find things, then tap them

The accessibility tree is **per-device**: `curl -s "http://127.0.0.1:3100/helper/$UDID/ax"`. The
root `/ax` path the skill documents returns `{"elements":[],"errors":["Accessibility unavailable on
this simulator."]}` here. Check the `/helper/` path first; it returns a top-level list with nested
`children`, each carrying `AXLabel` / `type` / `frame`, **frames in points** (iPhone 17 Pro =
402×874). Divide by those to get the normalized 0..1 values `tap` wants.

If that path ever comes back empty, the fallback is root `/ax` served as **SSE** — `curl -s -N -m 4
http://127.0.0.1:3100/ax`, take the last `data: ` line, parse
`{"screen":{...},"elements":[...]}` as a **flat** list with `label`/`role`/`frame`.

```bash
npx serve-sim tap 0.5 0.42 -d "$UDID"
```

**`tap` takes positional bare numbers, not JSON** — `tap '{"x":..,"y":..}'` fails with `missing
required argument 'y'`. Only `gesture` takes JSON. In zsh, `for c in "0.1 0.5"` does *not*
word-split, so every call silently loses its second argument; pass the two coordinates literally.

**An AX read can lag a transition by a beat.** A read 2.5–3 s after a tap has twice shown the *old*
screen, which reads exactly like dead input — the tap had landed. Re-read before concluding
anything failed.

### Scrolling, which is where this app bites

```bash
npx serve-sim gesture '{"type":"begin","x":0.025,"y":0.78}' -d "$UDID"
npx serve-sim gesture '{"type":"move","x":0.025,"y":0.62}' -d "$UDID"
# ...3 more move phases...
npx serve-sim gesture '{"type":"end","x":0.025,"y":0.32}'  -d "$UDID"
```

- **One CLI call per touch phase.** Passing an array of phases in a single call is accepted and
  ignored, and so is driving the WebSocket directly with properly-timed frames.
- **The payload must be FLAT.** `{"touches":[{"type":"begin",...}]}` exits 0, prints nothing, and
  the screen never moves — which reads as "this page refuses to scroll".
- **Anchor vertical drags at `x=0.025`**, the left padding, with 4–5 `move` phases. A drag that
  starts on an interactive row **registers as a tap on that row**: on the Stock pages this has
  opened the *Input Your PIN* modal and, once, the settings page that carries **Logout**.
- **Never tap to escape a PIN screen** — `simctl terminate` then `launch` is the clean exit.
- A scroll that appears blocked is the flat-payload bug until two screenshots prove otherwise.

### The verdict is yours — there is no exit code any more

This is the one thing that changed hardest in moving off Maestro. `maestro test` exited non-zero on
a failed assertion, so the exit code *was* the verdict. **Every serve-sim command exits 0 whether or
not anything happened.** Nothing grades the run but you, so:

- **Screenshot after every step that carries an `EXPECT:`**, and grade that line against the image.
- **Quote the value you measured** in `SAW:` — the hex you sampled, the count you counted. "Looks
  correct" is not a smoke result and will be treated as an unverified pass.
- **A step you could not reach is a failure, not a skip.** Report which step and what was on screen.

### Screenshots and pixel measurements

```bash
mkdir -p .tuntun/proof
xcrun simctl io "$UDID" screenshot .tuntun/proof/<KEY>-proof.png
```

That needs no helper and works whenever the sim is booted — prefer it to the MJPEG frame endpoint.

For a design-fidelity `EXPECT:`, grade against Figma in headed Chrome too: capture the node with
`tt-figma-shot` (§4 source 3), or use Phase A's `.tuntun/design/<KEY>.png`, and compare it with your
proof screenshot at the same point scale. Any visible difference the ticket covers is a failure.

For a colour `EXPECT:`, sample the pixels rather than eyeballing them. `python3` here is 3.9.6 and
**does** carry PIL:

```bash
python3 -c "
from PIL import Image
im = Image.open('.tuntun/proof/<KEY>-proof.png').convert('RGB')
W, H = im.size
for name, (nx, ny) in {'body': (0.5, 0.55), 'footer': (0.5, 0.93)}.items():
    print(name, '#%02X%02X%02X' % im.getpixel((int(nx * W), int(ny * H))))
"
```

The screenshot is at **device scale** — iPhone 17 Pro is 1206×2622, three times the 402×874 points
the AX tree reports — so normalize as above rather than hardcoding pixel coordinates.

`.tuntun/` is gitignored, so the proof is safe to leave sitting there through §12's push. **A
missing screenshot means the drive never reached the end** — treat it as a failure whatever else
you saw.

### Other things that have quietly wrecked runs here

- Keep the VPN **on** for dev backends; that is what makes them reachable. The AI/chat host
  (`10.194.2.20`) routes over the LAN, so chat dies off-site even with the VPN up.
- A **canary session can expire mid-run**: a blank body, then the login carousel. Your final
  screenshot is what catches this — take it even when you are sure.
- **Never uninstall the app to reset state** — the login carousel has no skip. Back up and restore
  the session from UserDefaults while the sim is **shut down**.
- App logs go to stdout, so `simctl launch --console-pty` sees them and `log stream` never does.
- **`xcodebuild test` shuts down the booted simulator when it finishes.** After any test run:
  re-boot the device, relaunch the app, and `serve-sim --kill` then `--detach` again — the old
  helper is dead and its `/frame.jpeg` returns "No serve-sim device".
- On the login screen, typing the email with `serve-sim type` has **auto-advanced past Continue**,
  leaving the password field reading as already filled so the next type appended and burned an
  attempt (`Incorrect password 1/5`). Clear the field first, and stop well short of 5 — a locked
  shared account is worse than an unfinished smoke test.

**Never soften the steps to make them pass.** A failing EXPECT is either a real regression or a
recipe Phase A got wrong, and both are `needs-rework` — rewriting the test to match the code
destroys the only signal this phase produces.

**Failed → do not merge.** Comment on the MR — §1a's marker line first (`smoke=needs-rework`, the
sha you drove), then what you drove, the backend, and what you saw against each failed `EXPECT:`,
precisely enough that §5a can tell a code fault from a recipe fault — leave the ticket in `fixing`,
stop `serve-sim`, and stop with `VERDICT: needs-rework`. The next pass reworks it.

## 11 · Post the proof

**GitLab** — upload, then reference the returned markdown. `glab api` **cannot do this upload**:
both `-F file=@shot.png` and `--field file=@shot.png` come back `HTTP 400`, because `glab api` has
no real multipart encoder. Use plain curl with a `PRIVATE-TOKEN` header — `glab` already holds the
token, so read it back rather than storing a second copy:

```bash
TOK=$(glab config get token --host "$TW_GITLAB_HOST")
curl -sS -k -X POST -H "PRIVATE-TOKEN: $TOK" -F "file=@.tuntun/proof/<KEY>-proof.png" \
  "https://$TW_GITLAB_HOST/api/v4/projects/$TW_GITLAB_PROJECT/uploads"
```

That returns JSON whose `markdown` field (`![shot](/uploads/<hash>/shot.png)`) is what you paste
into the note:

```bash
glab mr note create <IID> -m "<!-- ticket-workflow smoke=passed head=<MR head before the rebase> sha=<full sha> -->
Smoke test passed on <branch> @ <sha>, <backend>, <account>.
Smoke steps: <n> driven from this MR description, every EXPECT met.
<!-- paste the upload markdown -->"
```

**Jira** — there is no CLI attach command; raw curl it, and note the extra header:

```bash
curl -sS -k -X POST -H "Authorization: Bearer $JIRA_TOK" -H "X-Atlassian-Token: no-check" \
  -F "file=@.tuntun/proof/<KEY>-proof.png" "$JIRA_URL/issue/<KEY>/attachments"
```

Then comment in **raw wiki markup**, via `--raw` — which posts the body as-is and skips the Markdown
conversion:

```bash
tuntun-ios jira issue issue comment <KEY> --raw --insecure \
  --body "Smoke test passed on <branch>.
!<KEY>-proof.png|thumbnail!"
```

Without `--raw` the converter mangles any bullet carrying emphasis and **drops image embeds
entirely**, so the proof silently vanishes from the comment. Write the `!file|thumbnail!` embed
yourself and keep bullets plain.

## 12 · Push, resolve the threads, merge, mark it fixed

The rebase in §9 was **local**. GitLab still has the MR at its pre-rebase SHA, and project 49 merges
fast-forward only — merge without pushing and you get either a refusal or, worse, a merge of the
commit you never built and never drove. So push first, and only now that the smoke has passed:

```bash
git push --force-with-lease origin <source_branch>
```

`--force-with-lease`, never `--force`: the rebase rewrote history, and the lease is what makes the
push abort rather than clobber a commit somebody pushed while you were building.

**Then resolve the open threads, or the merge is refused.** Project 49 sets
`only_allow_merge_if_all_discussions_are_resolved`, and the notes this runbook tells you to post are
resolvable threads: §7b's review notes and §11's proof note all land `resolved=false`. Leave them and
`detailed_merge_status` reads `discussions_not_resolved`, which ends a perfectly good smoke run in a
false `blocked`:

```bash
glab api --hostname "$TW_GITLAB_HOST" "projects/$TW_GITLAB_PROJECT/merge_requests/<IID>/discussions" \
| python3 -c "
import sys, json
for d in json.load(sys.stdin):
    n = (d.get('notes') or [{}])[0]
    if n.get('resolvable') and not n.get('resolved'): print(d['id'])
" | while read id; do
  glab api --hostname "$TW_GITLAB_HOST" --method PUT \
    "projects/$TW_GITLAB_PROJECT/merge_requests/<IID>/discussions/$id?resolved=true"
done
```

**Close only your own bookkeeping threads.** A human reviewer's unresolved thread is not yours to
resolve: that is review feedback nobody has answered, and silently closing it merges past a person.
Check the authors before running the loop above — anything not written by this pipeline means stop
with `VERDICT: blocked`, say why on the MR, and leave the ticket in `fixing`.

**Once the merge has succeeded, take every Mac's claim label off each ticket**, `macmini`, `macbookpro`
and the legacy `mac-A` / `mac-B` alike, not only `$TW_MINE`. `unclaim` builds one PUT removing each label in `$TW_ALL_MACS`. Removing
a label the ticket doesn't have is a no-op, so the stale-claim case needs no special handling.
Define it before the merge, run it only after the merge succeeded, and never on a `blocked` or
`needs-rework` verdict, where the label is still the live claim:

```bash
unclaim() {
  curl -sS -k -X PUT -H "Authorization: Bearer $JIRA_TOK" -H "Content-Type: application/json" \
    -d "{\"update\":{\"labels\":[$(printf '%s' "$TW_ALL_MACS" | sed 's/"[^"]*"/{"remove":&}/g')]}}" \
    "$JIRA_URL/issue/$1"
  curl -sS -k -H "Authorization: Bearer $JIRA_TOK" "$JIRA_URL/issue/$1?fields=labels"   # check: no claim label left
}
```

A failed unclaim doesn't undo the merge. Say so on `JIRA:` in the verdict block and carry on.

```bash
glab mr merge <IID> --squash --yes &&
tuntun-ios jira issue issue transition <KEY> --status fixed --insecure
unclaim <KEY>
```

**A batch MR merges without squash**, so each ticket keeps its own commit on the RC, and every
ticket moves to `fixed` with its own proof comment (§11 attaches the screenshot covering its
subsection to each ticket):

```bash
glab api --hostname "$TW_GITLAB_HOST" --method PUT "projects/$TW_GITLAB_PROJECT/merge_requests/<IID>/merge?squash=false"
for k in <every KEY of the batch>; do tuntun-ios jira issue issue transition "$k" --status fixed --insecure; unclaim "$k"; done
```

`$TW_RC_PREFIX/*` branches (`release_candidate/*`) are unprotected, so I can merge them as Developer — `main` and `dev`
are Maintainers-only and will refuse. `--squash` matches the box §7 ticked at create time — pass
it anyway, so an MR opened without it still lands as one commit. Fast-forward only, so if the target moved while you were
testing, rebase again (§9), re-push and re-merge. There is **no CI on project 49**, so there is no
pipeline to wait for and no green check to read: your smoke run is the gate.

`fixed` is a real status, lowercase. It drops the ticket out of both filters, so it will not come
back around. **Its claim labels are gone** (the `unclaim` above), so if QA reopens the ticket, either
Mac can pick it up. A leftover `macmini` would keep Mac B off it forever, because §1 excludes the other
Mac's label. Which Mac did the work is recorded in §11's proof comment and the MR, not in the label.

Cannot merge for any other reason → say why on the MR, leave the ticket in `fixing`, and stop with
`VERDICT: blocked`.

Then stop: stop `serve-sim`, emit the verdict block, and nothing after it. The main session
writes the report.

---

## Looping is the caller's job

This pass does **one** ticket and **one** MR, then ends. It arms no `ScheduleWakeup` of its own, no
`CronCreate`, no chain of one-shots, and it never goes back to §1 or §8 for a second row inside the
same turn.

To keep it running, wrap it: **`/loop /ticket-workflow`**. Repetition, pacing and stopping all
belong to `/loop` and to the user, which is the whole point — a board read at §1 is already stale by
the time a handoff lands, and a self-armed schedule races the wrapper that is also firing.

On the pipeline Mac the wrapper is itself scheduled, from the centrion checkout: launchd opens
`/loop /ticket-workflow` in a Warp tab in `~/Projects/ttsecuritas-2` at 09:15 and ends it at 16:45
(`launchd/ticket-workflow.sh`, SPEC.md §8). The 16:45 stop can land mid-pass. That is expected, not
a crash to report: the next morning's first pass finds the batch in flight and finishes it (§1a),
and the stop has already removed the pass file (§00).

If `/loop` was invoked with no interval it paces itself and will ask for the delay when the turn
ends:

- A pass that fixed, reworked or smoked its ticket is **work done** (`noop: false`), so go back
  quickly. A pass that merged its ticket freed the slot, so the next one claims a new ticket. A `needs-rework` verdict is work queued for the
  very next pass's §1a, so it too means go back quickly, not wait for a person.
- A pass whose ticket is waiting on market data, or that found nothing in flight *and* an empty
  board, is a quiet hold (`noop: true`). That is
  where "wait an hour" lives: **~3600s**, and the command itself waits for nothing.
- A credential failure at §0/§1 should **stop** the loop, not sit in it — a dead token is
  indistinguishable from an empty board downstream and would idle for hours in silence.

Before the pass ends, on **every** path: the task row from §2 is closed, no `serve-sim` helper
is left running, and `.tuntun/ticket-workflow.pass` is removed. A turn that ends while an agent of this pass is
still running has **not** ended the pass. Leave the file in place and the row open with a
`heads_up` comment, so the next invocation holds (§00) instead of starting over.

- A `sim-busy` smoke verdict is a quiet hold too (`noop: true`, ~1800s): another session has the
  simulator, and the next pass re-smokes the same MR once it is free.
- An invocation that §00 held is a quiet hold (`noop: true`, ~1800s), not work done. It never
  counts as a pass, and it never reads the board.

## Report

Per CLAUDE.md **Communicating results**, for a junior engineer, **once at the end of the pass** and
covering both phases — assembled from the two handoff blocks, never from anything the orchestrator
re-reads:

- **What Changed** — Phase A: the ticket, the target RC and where it came from, the MR, the smoke
  steps that shipped in its description — or, for a rework, which MR, which half was wrong (code,
  recipe or both) and what changed. Phase B: which MR was smoked, whether every `EXPECT:` was
  met, whether it merged, whether the ticket moved to `fixed`. Say plainly when a phase did nothing
  and why ("board empty", "no MR yet for either fixing ticket").
- **Verified** — what was actually run. For Phase A that is lint, `/ios-review` and §7b's
  `@code-reviewer` verdict with its round count, normally **no build**, and smoke steps that were written but **not driven**. For Phase B it is the build, the
  drive, the backend and account, the measured values behind each `EXPECT:`, and whether the proof
  screenshot exists. Name what stayed unverified.
- **Heads Up** — only if something is genuinely left broken.
- **Designs to Send** — every `design: no plugin spec — Send <URL>` a handoff put on
  `ASSUMPTION:`, one line each with the ticket key, so the frames can be sent before the next run.
- **Recommended Next Step** — one thing, grounded in what you checked.

Never claim a gate, a build, a merge or a met `EXPECT:` that a handoff block does not report.
Remove `.tuntun/ticket-workflow.pass` as the last step of the report — only once both phases are
actually finished. And say
that the pass has ended and scheduled nothing, so nobody assumes a pipeline is still armed.
