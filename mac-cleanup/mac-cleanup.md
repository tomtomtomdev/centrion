---
description: Run the Xcode/DerivedData/simulator/build-output/Claude-scratchpad cleanup right now
argument-hint: [apply|dry-run|status|schedule HH:MM]
allowed-tools: Bash(crontab:*), Bash(launchctl:*), Bash(git:*), Bash(xcrun:*), Bash(du:*), Bash(df:*), Bash(find:*), Bash(ls:*), Bash(stat:*), Bash(rm:*), Bash(plutil:*), Bash(brew:*), Bash(chmod:*), Bash(mkdir:*), Bash(id:*), Bash(readlink:*), Bash(sh:*), Bash(~/.local/bin/daily-cleanup.sh:*), Read, Write, Edit
disable-model-invocation: true
---

## Live state

- Script: !`readlink ~/.local/bin/daily-cleanup.sh 2>/dev/null || echo "(not installed)"`
- Schedule: !`launchctl print gui/$(id -u)/com.tommy.mac-cleanup 2>/dev/null | grep -m1 "state =" || echo "(not scheduled)"`
- Disk before: !`df -h /System/Volumes/Data | tail -1`
- Repos: !`/usr/bin/find ~/Projects ~/Documents -maxdepth 4 -name .git 2>/dev/null | head -40 || true`
- Claude scratch: !`du -sh /private/tmp/claude-$(id -u) 2>/dev/null || echo "(none)"`
- Last run: !`ls -t ~/Library/Logs/mac-cleanup/*.log 2>/dev/null | head -1 || echo "(no logs)"`

## Task

**This command runs the cleanup immediately. It is not a scheduler.**

Mode is `$1`. If empty, default to `dry-run`.

| `$1` | Behaviour |
|---|---|
| *(empty)* / `dry-run` | Run the cleanup now in preview mode. Delete nothing. Print exactly what would be removed and the bytes each would reclaim, then ask if I want to apply it. If I say yes, re-run with `--apply` in the same session. |
| `apply` | Run the cleanup now with `--apply`. No confirmation prompt — I already decided. |
| `status` | Report last run, current disk usage, and sizes of each cleanup target. Change nothing. |
| `schedule` | *(optional)* Install the launchd agent that runs this unattended every day at `$2` (default `09:15`), via `install.sh --schedule` (below). Only do this if I explicitly pass `schedule`. |

The script and this command live in the centrion repo, in `mac-cleanup/`, and are installed as
symlinks into it by `sh mac-cleanup/install.sh`. If `~/.local/bin/daily-cleanup.sh` is missing,
run that installer from the centrion checkout (default `~/Projects/centrion`) — do not write a new
script. To change the script's behaviour, edit the repo copy (the symlink target) and keep the spec
below in step with it. Do not install any schedule unless I asked for one.

Stream progress as you go — I want to see each category finish, not one summary at the end.
Finish with total bytes reclaimed per category, the **protected-but-large** report, and
`df -h /System/Volumes/Data` before/after.

---

## Script: `mac-cleanup/daily-cleanup.sh` (installed as `~/.local/bin/daily-cleanup.sh`)

- **Dry run is the default.** `--apply` is required to delete anything. Every deletion path
  prints its target and reclaimable bytes in both modes.
- `set -euo pipefail`. Absolute paths for every binary (`/usr/bin/find`, `/usr/bin/xcrun`,
  `/opt/homebrew/bin/brew`, …); verify each exists before use. This is not optional hygiene:
  under launchd the `PATH` is minimal, and in my interactive shell `find` is a **shell function
  shadowed to `bfs`**, where `-newermt` fails silently. Always `/usr/bin/find`.
- Lockfile so an interactive run and a scheduled run cannot race.
- Log to `~/Library/Logs/mac-cleanup/YYYYMMDD-HHMMSS.log`; prune logs older than 90 days.
- Exit non-zero if any category fails, but keep going through the other categories first.
- Report disk against **`/System/Volumes/Data`**, never `/`. On an APFS volume with a sealed
  system snapshot, `df -h /` reports the read-only system volume and its numbers are meaningless
  for cleanup (it shows ~36% used while the data volume is at 90%).

### Flags

| Flag | Effect |
|---|---|
| *(none)* | Dry run. Deletes nothing. |
| `--apply` | Actually delete. |
| `--include-active` | Also delete build output newer than the 14-day guard. **Costs a full rebuild** — opt-in only, never scheduled. |
| `--force-sims` | Shut down simulators even if Simulator.app is running. |

## Repo and worktree discovery

1. Find every repo under `~/Documents` (a dir containing `.git`, file or directory).
2. `git -C <repo> worktree list --porcelain`; dedupe the resulting paths.
3. Treat plain single-checkout repos as first-class targets — **do not require a linked worktree**.
   Most repos here have none, and their build output is the largest reclaimable on the machine.

## The 14-day recency guard (applies everywhere)

One consistent rule for every build artifact, whether it lives in DerivedData, XCTestDevices, or
a repo's `build/`: **if it was modified within 14 days, it is protected** unless `--include-active`.
Instead of silently keeping it, add it to the **protected-but-large** report with its size and the
cost of deleting it (e.g. "full rebuild of TTSecuritas"). I decide on those, not the script.

## Cleanup targets

**1. Xcode DerivedData** (`~/Library/Developer/Xcode/DerivedData`)
Path-keyed, so deleted or moved checkouts leave orphans. Delete a DerivedData dir only if **either**:
- its `info.plist` `WorkspacePath` points at a path that no longer exists, **or**
- it fails the 14-day recency guard.

Guard the plist read: `plutil -extract … || true`. Most entries here have **no `info.plist`**
(`ModuleCache.noindex`, `SDKStatCaches.noindex`, custom `-derivedDataPath` dirs), and an unguarded
read aborts the whole script under `set -e`. No plist ⇒ fall back to the recency guard alone.

Same rules for `ModuleCache.noindex` and the Xcode `Products` dir.

**2. XCTestDevices** (`~/Library/Developer/XCTestDevices`)
Ephemeral XCTest runner devices, recreated on demand. Separate from `CoreSimulator/Devices` and
easily 10 GB+. Delete under the recency guard, and **only when Xcode and Simulator.app are both
not running**. Otherwise report.

**3. Caches**
- `~/Library/Developer/Xcode/iOS DeviceSupport` — keep only the newest 2 OS versions
- `~/Library/Developer/Xcode/Archives` — older than 30 days
- `~/Library/Caches/org.swift.swiftpm`, `~/Library/org.swift.swiftpm` — stale entries
- `~/Library/Caches/CocoaPods` — older than 30 days
- `~/.gradle/caches/build-cache-*`, `~/.gradle/daemon` — older than 14 days
- `brew cleanup --prune=30` if Homebrew is present. Also report the size of
  `~/Library/Caches/Homebrew` separately — `--prune=30` barely touches it, and clearing it
  costs re-downloads, so it is my call.

Do **not** touch `~/.m2/repository` or SwiftPM/Gradle *dependency* caches that would force a
full re-resolve. Prefer build caches over artifact caches.

**4. Simulators**
- `xcrun simctl shutdown all`
- `xcrun simctl delete unavailable`
- Stranded device deletions: `simctl delete` (and XCTest's clone teardown) only *renames* a
  device to `$(getconf DARWIN_USER_TEMP_DIR)Deleting-<uuid>/` and frees it later in the
  background — which often never happens (43G sat there once). Remove `Deleting-*` dirs older
  than 60 minutes (dir mtime = rename time); younger ones may still be mid-delete. The
  hard stop below does not apply — they are already detached from every device. `du` overstates
  the reclaim: clones share APFS blocks with their source device.
- **Report only** (never auto-delete): unused simulator runtimes with sizes, and devices
  created but never booted. I decide on those.

**Hard stop:** if Xcode or Simulator.app is running, or any device is booted, **skip the shutdown
entirely** and print a warning. Do not shut down and do not prompt-then-proceed — a booted
simulator is usually a live debugging or serve-sim session, and killing it destroys work in
progress. Only `--force-sims` overrides this.

**5. Per-repo build output**
For each repo/worktree found above:
- Show `git clean -ndX` (ignored files only) and `git -C <wt> status --porcelain` separately.
  **`git clean -ndX` is for display only. NEVER run `git clean` with `-f`.** On ttsecuritas its
  output includes `CLAUDE.md`, `.claude/`, `.tuntun/`, `.runconfig`, and all 30+ generated
  `Frameworks/*/*.xcodeproj` — executing it would destroy project instructions and force an
  xcodegen regen. Deletion is driven **solely by the allowlist below**, never by git's output.
- Remove only ignored build output, by exact directory name: `build/`, `.build/`, `DerivedData/`,
  `target/`, `out/`, `*.xcresult`, and a local `.scratch/` or `scratch/` dir if the repo has one.
- Also remove a top-level custom `-derivedDataPath` dir of any name (e.g. `.dd-<ticket>/`): one
  whose `info.plist` has a `WorkspacePath` key. Same ignored-check and recency guard; `*.xcresult`
  bundles inside it are not counted twice.
- Verify each candidate is actually git-ignored (`git check-ignore -q`) before removing it.
- **Hard exclusions, never delete:** `.env*`, `*.local`, `local.properties`, `secrets*`,
  anything untracked-but-not-ignored, anything inside `.git/`.
- Apply the 14-day recency guard per candidate.

**Uncommitted tracked changes do NOT skip the repo.** The old rule skipped the entire worktree on
any dirty tracked file, which permanently excluded the single largest reclaimable target on this
machine: ttsecuritas carries a permanent ` M Podfile.lock` (real state — reverting it fails
*Check Pods Manifest.lock*), so the repo is never clean and its 37 GB `build/` was never eligible.
Removing git-ignored build output cannot lose tracked work. Log the dirty files for visibility
and proceed.

**6. Claude Code scratchpads** (`/private/tmp/claude-$(id -u)/<project-slug>/<session-uuid>/`)
Each Claude Code session gets a `scratchpad/` (plus `tasks/`) here, and nothing ever deletes
them — one ttsecuritas project dir alone grew to 10 GB. The unit is the **session dir**.
- Delete a session dir only if **nothing inside it** (files or dirs, `/usr/bin/find -mtime -1`)
  was modified in the last **24 hours**. The dir's own mtime is not enough — a live session
  writes deep inside it without touching the top.
- Otherwise protect and report it: it is almost certainly a live session, and deleting its
  scratchpad breaks that session mid-task. `--include-active` does **not** override this guard —
  nothing is rebuilt from a scratchpad, so there is no "cost it" trade to make.
- After deleting, remove project-slug dirs left empty (`rmdir`, never `rm -rf`).
- Only project-slug dirs are scanned — path-encoded names starting with `-`
  (`-Users-…-Projects-ttsecuritas`). Leave everything else at the top level of
  `/private/tmp/claude-$(id -u)` alone (`bundled-skills/`, `bash-edit-diff/`, stray dirs and
  files) — those are Claude Code internals, not per-session scratch. `bundled-skills/` in
  particular holds the built-in skills; deleting it breaks them.

## Optional scheduling (`$1` = `schedule` only)

Run the installer the script's symlink points back to:

```sh
sh "$(dirname "$(readlink ~/.local/bin/daily-cleanup.sh)")/install.sh" --schedule "${2:-09:15}"
```

It renders `mac-cleanup/com.tommy.mac-cleanup.plist` (label `com.tommy.mac-cleanup`, daily, no `Weekday`,
the script with `--apply` and nothing else) into `~/Library/LaunchAgents`, then boots it out and
back in — editing an installed plist alone does not take effect. `--unschedule` removes it.

- launchd, not cron — cron silently skips runs while the Mac is asleep; launchd
  `StartCalendarInterval` fires at next wake.
- Never add `--include-active` or `--force-sims` to the scheduled invocation.
- Verify it loaded (`launchctl print gui/$(id -u)/com.tommy.mac-cleanup`). Full Disk Access is
  not needed for `~/Library/Developer`, `~/Library/Caches`, `~/Projects` or
  `/private/tmp/claude-<uid>`. `~/Documents` *is* TCC-protected: a scheduled run may be denied
  there, which the script tolerates (it scans what it can). Tell me if any repos I care about live
  under `~/Documents`, because covering them unattended means granting `/bin/bash` Full Disk Access.
