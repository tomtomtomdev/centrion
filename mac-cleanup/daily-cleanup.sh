#!/bin/bash
# daily-cleanup.sh — Xcode / simulator / repo build-output / Claude scratchpad cleanup.
# Dry run is the DEFAULT. --apply is required to delete anything.
set -euo pipefail

APPLY=0; INCLUDE_ACTIVE=0; FORCE_SIMS=0
for arg in "$@"; do
  case "$arg" in
    --apply)          APPLY=1 ;;
    --include-active) INCLUDE_ACTIVE=1 ;;
    --force-sims)     FORCE_SIMS=1 ;;
    -h|--help) sed -n '2,5p' "$0"; echo "flags: --apply --include-active --force-sims"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# --- absolute binaries (launchd has a minimal PATH; `find` is shadowed to bfs interactively) ---
DU=/usr/bin/du;    DF=/bin/df;      FIND=/usr/bin/find;  GIT=/usr/bin/git
XCRUN=/usr/bin/xcrun; PLUTIL=/usr/bin/plutil; STAT=/usr/bin/stat; RM=/bin/rm
PGREP=/usr/bin/pgrep; DATE=/bin/date; AWK=/usr/bin/awk;  SORT=/usr/bin/sort
MKDIR=/bin/mkdir;  RMDIR=/bin/rmdir; BREW=/opt/homebrew/bin/brew; ID=/usr/bin/id
for b in "$DU" "$DF" "$FIND" "$GIT" "$XCRUN" "$PLUTIL" "$STAT" "$RM" "$PGREP" "$DATE" "$AWK" "$SORT" "$MKDIR" "$ID"; do
  [ -x "$b" ] || { echo "FATAL: missing required binary: $b" >&2; exit 3; }
done

# --- lock ---
LOCK=/tmp/mac-cleanup.lock
$MKDIR "$LOCK" 2>/dev/null || { echo "FATAL: another run holds $LOCK" >&2; exit 4; }
cleanup_lock() { $RMDIR "$LOCK" 2>/dev/null || true; }
trap cleanup_lock EXIT

# --- logging ---
LOGDIR="$HOME/Library/Logs/mac-cleanup"
$MKDIR -p "$LOGDIR"
LOG="$LOGDIR/$($DATE +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
$FIND "$LOGDIR" -name '*.log' -type f -mtime +90 -delete 2>/dev/null || true

GUARD_DAYS=14
FAILED=0
PROTECTED=""     # "kb<TAB>path<TAB>reason" per line
TOTAL_KB=0
CAT_KB=0

kb_of()  { [ -e "$1" ] || { echo 0; return; }; { $DU -sk "$1" 2>/dev/null || true; } | $AWK 'NR==1{print $1+0; f=1} END{if(!f) print 0}'; }
human()  { $AWK -v k="$1" 'BEGIN{ if(k>=1048576) printf "%.1fG", k/1048576; else if(k>=1024) printf "%.0fM", k/1024; else printf "%dK", k }'; }
age_days(){ local m; m=$($STAT -f %m "$1" 2>/dev/null || echo 0); [ "$m" = "0" ] && { echo 99999; return; }; echo $(( ( $($DATE +%s) - m ) / 86400 )); }
protect(){ PROTECTED="${PROTECTED}$1	$2	$3
"; }

# remove_target <path> <reason>
remove_target() {
  local p="$1" reason="$2" kb
  [ -e "$p" ] || return 0
  case "$(basename "$p")" in
    .env*|*.local|local.properties|secrets*|.git) echo "  !! refusing excluded path: $p"; return 0 ;;
  esac
  case "$p" in */.git/*) echo "  !! refusing path inside .git: $p"; return 0 ;; esac
  kb=$(kb_of "$p")
  if [ "$APPLY" = "1" ]; then
    if $RM -rf "$p" 2>/dev/null; then echo "  removed        $(human "$kb")	$p  ($reason)"
    else echo "  FAILED to remove $p"; FAILED=1; return 0; fi
  else
    echo "  would remove   $(human "$kb")	$p  ($reason)"
  fi
  CAT_KB=$((CAT_KB + kb)); TOTAL_KB=$((TOTAL_KB + kb))
}

# guarded_remove <path> <label>  — applies the 14-day recency guard
guarded_remove() {
  local p="$1" label="$2" age kb
  [ -e "$p" ] || return 0
  age=$(age_days "$p")
  if [ "$age" -gt "$GUARD_DAYS" ] || [ "$INCLUDE_ACTIVE" = "1" ]; then
    remove_target "$p" "${label}, ${age}d old"
  else
    kb=$(kb_of "$p"); protect "$kb" "$p" "active (${age}d old) — $label"
    echo "  protected      $(human "$kb")	$p  (modified ${age}d ago)"
  fi
}

banner() { CAT_KB=0; echo; echo "=== $1 ==="; }
finish() { echo "  --> category total: $(human "$CAT_KB")"; }

xcode_running() { $PGREP -qf "Xcode.app/Contents/MacOS/Xcode" 2>/dev/null; }
sim_running()   { $PGREP -qf "Simulator.app/Contents/MacOS/Simulator" 2>/dev/null; }

echo "mac-cleanup  $($DATE '+%Y-%m-%d %H:%M:%S')  mode=$([ $APPLY = 1 ] && echo APPLY || echo DRY-RUN)"
echo "include-active=$INCLUDE_ACTIVE  force-sims=$FORCE_SIMS  log=$LOG"
echo; echo "Disk before:"; $DF -h /System/Volumes/Data | tail -1

# ---------------------------------------------------------------- 1. DerivedData
cat_derived_data() {
  banner "1. Xcode DerivedData"
  local DD="$HOME/Library/Developer/Xcode/DerivedData" d wp age
  [ -d "$DD" ] || { echo "  (absent)"; finish; return 0; }
  for d in "$DD"/*; do
    [ -e "$d" ] || continue
    wp=""
    [ -f "$d/info.plist" ] && wp=$($PLUTIL -extract WorkspacePath raw "$d/info.plist" 2>/dev/null || true)
    if [ -n "$wp" ] && [ ! -e "$wp" ]; then
      remove_target "$d" "orphan: workspace gone ($wp)"
    else
      guarded_remove "$d" "DerivedData"
    fi
  done
  finish
}

# ---------------------------------------------------------------- 2. XCTestDevices
cat_xctest_devices() {
  banner "2. XCTestDevices"
  local X="$HOME/Library/Developer/XCTestDevices" d kb
  [ -d "$X" ] || { echo "  (absent)"; finish; return 0; }
  if xcode_running || sim_running; then
    kb=$(kb_of "$X")
    echo "  SKIPPED — Xcode or Simulator.app is running. $(human "$kb") held in $X"
    protect "$kb" "$X" "Xcode/Simulator running — rerun when idle"
    finish; return 0
  fi
  for d in "$X"/*; do [ -e "$d" ] || continue; guarded_remove "$d" "XCTest runner device"; done
  finish
}

# ---------------------------------------------------------------- 3. Caches
cat_caches() {
  banner "3. Caches"
  local DS="$HOME/Library/Developer/Xcode/iOS DeviceSupport" AR="$HOME/Library/Developer/Xcode/Archives"
  local d n kb

  if [ -d "$DS" ]; then
    n=0
    while IFS= read -r d; do
      n=$((n+1)); [ "$n" -le 2 ] && { echo "  keeping        $d"; continue; }
      remove_target "$d" "DeviceSupport beyond newest 2"
    done < <($FIND "$DS" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | while IFS= read -r p; do echo "$($STAT -f '%m' "$p" 2>/dev/null || echo 0) $p"; done | $SORT -rn | cut -d' ' -f2-)
  else echo "  iOS DeviceSupport: (absent)"; fi

  if [ -d "$AR" ]; then
    while IFS= read -r d; do remove_target "$d" "archive >30d"; done \
      < <($FIND "$AR" -maxdepth 2 -mindepth 1 -type d -mtime +30 2>/dev/null || true)
  else echo "  Archives: (absent)"; fi

  for c in "$HOME/Library/Caches/org.swift.swiftpm" "$HOME/Library/org.swift.swiftpm" "$HOME/Library/Caches/CocoaPods"; do
    [ -d "$c" ] || { echo "  $(basename "$c"): (absent)"; continue; }
    while IFS= read -r d; do remove_target "$d" "stale cache entry >30d"; done \
      < <($FIND "$c" -maxdepth 2 -mindepth 1 -mtime +30 2>/dev/null || true)
  done

  if [ -d "$HOME/.gradle" ]; then
    while IFS= read -r d; do remove_target "$d" "gradle build cache >14d"; done \
      < <($FIND "$HOME/.gradle/caches" -maxdepth 1 -name 'build-cache-*' -mtime +14 2>/dev/null || true)
    [ -d "$HOME/.gradle/daemon" ] && guarded_remove "$HOME/.gradle/daemon" "gradle daemon"
  else echo "  .gradle: (absent)"; fi

  if [ -x "$BREW" ]; then
    if [ "$APPLY" = "1" ]; then "$BREW" cleanup --prune=30 2>&1 | tail -3 || true
    else "$BREW" cleanup --prune=30 -n 2>&1 | tail -2 || true; fi
    kb=$(kb_of "$HOME/Library/Caches/Homebrew")
    [ "$kb" -gt 0 ] && { echo "  note: ~/Library/Caches/Homebrew holds $(human "$kb") of downloads (--prune=30 barely touches it; clearing costs re-downloads — your call)"
      protect "$kb" "$HOME/Library/Caches/Homebrew" "download cache — clearing costs re-downloads"; }
  else echo "  Homebrew: (absent)"; fi
  finish
}

# ---------------------------------------------------------------- 4. Simulators
cat_simulators() {
  banner "4. Simulators"
  local booted
  booted=$($XCRUN simctl list devices booted 2>/dev/null | grep -c "(Booted)" || true)
  if { xcode_running || sim_running || [ "$booted" -gt 0 ]; } && [ "$FORCE_SIMS" != "1" ]; then
    echo "  !! WARNING: skipping 'simctl shutdown all'"
    echo "     Simulator.app running=$(sim_running && echo yes || echo no), Xcode running=$(xcode_running && echo yes || echo no), booted devices=$booted"
    echo "     A booted sim is usually a live debug/serve-sim session. Use --force-sims to override."
    $XCRUN simctl list devices booted 2>/dev/null | grep "(Booted)" || true
  else
    if [ "$APPLY" = "1" ]; then $XCRUN simctl shutdown all 2>/dev/null || true; echo "  shut down all simulators"
    else echo "  would run: simctl shutdown all"; fi
  fi

  if [ "$APPLY" = "1" ]; then $XCRUN simctl delete unavailable 2>/dev/null || true; echo "  deleted unavailable devices"
  else echo "  would run: simctl delete unavailable"; fi

  echo "  -- report only (never auto-deleted) --"
  $XCRUN simctl list runtimes 2>/dev/null | grep -v '^==' | sed 's/^/     runtime: /' || true
  local devdir="$HOME/Library/Developer/CoreSimulator/Devices"
  [ -d "$devdir" ] && echo "     CoreSimulator/Devices: $(human "$(kb_of "$devdir")") across $($FIND "$devdir" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ') devices"
  finish
}

# ---------------------------------------------------------------- 5. Repo build output
cat_repo_output() {
  banner "5. Per-repo build output"
  local repos trees repo wt dirty cand r ddpaths dp
  local roots=""
  for r in "$HOME/Projects" "$HOME/Documents"; do [ -d "$r" ] && roots="$roots $r"; done
  [ -n "$roots" ] || { echo "  (no repo roots found)"; finish; return 0; }
  echo "  scanning roots:$roots"
  repos=$($FIND $roots -maxdepth 4 -name .git 2>/dev/null | sed 's|/\.git$||' | $SORT -u || true)
  trees=""
  while IFS= read -r repo; do
    [ -n "$repo" ] || continue
    trees="${trees}${repo}
"
    while IFS= read -r wt; do
      [ -n "$wt" ] && trees="${trees}${wt}
"
    done < <($GIT -C "$repo" worktree list --porcelain 2>/dev/null | $AWK '/^worktree /{print $2}' || true)
  done <<< "$repos"
  trees=$(echo "$trees" | $SORT -u | grep -v '^$' || true)

  while IFS= read -r wt; do
    [ -n "$wt" ] && [ -d "$wt" ] || continue
    dirty=$($GIT -C "$wt" status --porcelain 2>/dev/null | head -5 || true)
    [ -n "$dirty" ] && echo "  [$wt] dirty tracked files (informational, does NOT skip the repo):" && echo "$dirty" | sed 's/^/       /'
    for name in build .build DerivedData target out .scratch scratch; do
      cand="$wt/$name"
      [ -d "$cand" ] || continue
      if $GIT -C "$wt" check-ignore -q "$cand" 2>/dev/null; then
        guarded_remove "$cand" "$name/ in $(basename "$wt")"
      else
        echo "  skipped        $cand  (NOT git-ignored — refusing)"
      fi
    done
    # A custom -derivedDataPath dir (e.g. .dd-<ticket>) has any name; Xcode's info.plist
    # with a WorkspacePath key is its signature. Top level only; must still be git-ignored.
    ddpaths=""
    for cand in "$wt"/* "$wt"/.[!.]*; do
      [ -d "$cand" ] && [ -f "$cand/info.plist" ] || continue
      case "$(basename "$cand")" in build|.build|DerivedData|target|out|.scratch|scratch|.git) continue ;; esac
      $PLUTIL -extract WorkspacePath raw "$cand/info.plist" >/dev/null 2>&1 || continue
      if $GIT -C "$wt" check-ignore -q "$cand" 2>/dev/null; then
        guarded_remove "$cand" "custom DerivedData $(basename "$cand")/ in $(basename "$wt")"
        ddpaths="${ddpaths}${cand}/
"
      else
        echo "  skipped        $cand  (custom DerivedData, NOT git-ignored — refusing)"
      fi
    done
    while IFS= read -r cand; do
      [ -n "$cand" ] && [ -e "$cand" ] || continue
      # already counted as part of a custom DerivedData dir above
      [ -n "$ddpaths" ] && while IFS= read -r dp; do [ -n "$dp" ] && case "$cand" in "$dp"*) continue 2 ;; esac; done <<< "$ddpaths"
      $GIT -C "$wt" check-ignore -q "$cand" 2>/dev/null && guarded_remove "$cand" "xcresult"
    done < <($FIND "$wt" -maxdepth 4 -name '*.xcresult' -not -path '*/.git/*' 2>/dev/null || true)
  done <<< "$trees"
  finish
}

# ---------------------------------------------------------------- 6. Claude scratchpads
# Unit is the session dir (<root>/<project-slug>/<session-uuid>). A session with anything modified
# in the last 24h is treated as live; --include-active does NOT override (nothing to rebuild).
cat_claude_scratch() {
  banner "6. Claude Code scratchpads"
  local CR="/private/tmp/claude-$($ID -u)" proj s kb
  [ -d "$CR" ] || { echo "  (absent)"; finish; return 0; }
  for proj in "$CR"/*/; do
    proj="${proj%/}"
    [ -d "$proj" ] || continue
    [ "$(basename "$proj")" = "bash-edit-diff" ] && continue
    for s in "$proj"/*; do
      [ -d "$s" ] || continue
      if [ -n "$($FIND "$s" -mtime -1 -print -quit 2>/dev/null || true)" ]; then
        kb=$(kb_of "$s"); protect "$kb" "$s" "live Claude session (touched <24h) — scratchpad"
        echo "  protected      $(human "$kb")	$s  (touched <24h ago — live session?)"
      else
        remove_target "$s" "Claude session scratch, idle >24h"
      fi
    done
    [ "$APPLY" = "1" ] && $RMDIR "$proj" 2>/dev/null || true
  done
  finish
}

for fn in cat_derived_data cat_xctest_devices cat_caches cat_simulators cat_repo_output cat_claude_scratch; do
  if ! $fn; then echo "  !! category $fn failed"; FAILED=1; fi
done

echo; echo "================ SUMMARY ================"
echo "$([ $APPLY = 1 ] && echo "Reclaimed" || echo "Would reclaim"): $(human "$TOTAL_KB")"
if [ -n "$PROTECTED" ]; then
  echo; echo "PROTECTED BUT LARGE — your call (rerun with --include-active to include):"
  printf '%s' "$PROTECTED" | $SORT -rn | while IFS=$'\t' read -r kb path reason; do
    [ -n "${kb:-}" ] || continue
    [ "$kb" -ge 10240 ] || continue
    printf "  %-8s %s\n           %s\n" "$(human "$kb")" "$path" "$reason"
  done
fi
echo; echo "Disk after:"; $DF -h /System/Volumes/Data | tail -1
echo "Log: $LOG"
[ "$FAILED" = "0" ] || { echo "One or more categories failed."; exit 1; }
exit 0
