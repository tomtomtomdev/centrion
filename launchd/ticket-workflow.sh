#!/bin/sh
# What com.tommy.ticket-workflow runs at 09:15: a headed Claude Code in a Warp tab, in the app
# checkout, running `/loop /ticket-workflow` until com.tommy.ticket-workflow.stop ends it at 17:45.
#
# Headed on purpose. The loop runs for hours unattended, and a Warp tab is where a person can look
# in on it, scroll back through what it did, or type into it, which `claude -p` under launchd
# would not allow. Warp opens it from a launch configuration this writes into
# ~/.warp/launch_configurations, through its warp://launch/ URL.
#
#   sh launchd/ticket-workflow.sh [checkout]   open it now (default: ~/Projects/ttsecuritas-2)
#   sh launchd/ticket-workflow.sh --stop [checkout]   end the loop's claude; the Warp tab stays
#
# Stopping mid-pass is safe by the pipeline's own design: a pass starts by finishing this Mac's
# batch in flight, so tomorrow's first pass picks up whatever 17:45 interrupted. What the killed
# pass would have cleaned up on its way out is cleaned up here instead: its serve-sim helper, and
# its .tuntun/ticket-workflow.pass, which would otherwise hold a restart for three hours (§00).
#
# Two things would make a second session a bug, so both are checked before anything opens:
#   - the pipeline runs one session per Mac (ticket-workflow's SKILL.md), so an open loop from
#     yesterday or from a hand start means this one is skipped;
#   - com.tommy.mac-cleanup also runs at 09:15 and shuts simulators down and deletes build output
#     unless it sees them in use. The pipeline's first build would be starting just then, so this
#     waits for the cleanup to finish, for up to 30 minutes.
set -eu
STOP=""
[ "${1:-}" = "--stop" ] && { STOP=1; shift; }
CHECKOUT=${1:-$HOME/Projects/ttsecuritas-2}
CLAUDE=$HOME/.local/bin/claude
PROMPT="/loop /ticket-workflow"
CONFIG_DIR=$HOME/.warp/launch_configurations
CONFIG=ticket-workflow.yaml

END=1745   # HHMM; keep in step with com.tommy.ticket-workflow.stop.plist

stamp() { date '+%Y-%m-%d %H:%M:%S'; }

if [ -n "$STOP" ]; then
    if ! pgrep -qf "claude.*$PROMPT"; then
        echo "$(stamp) no $PROMPT session to stop"
        exit 0
    fi
    # SIGTERM is Claude Code's clean exit. One that ignores it for a minute is stuck, and killed.
    pkill -TERM -f "claude.*$PROMPT"
    i=0
    while pgrep -qf "claude.*$PROMPT" && [ $i -lt 30 ]; do
        sleep 2
        i=$((i + 1))
    done
    pkill -KILL -f "claude.*$PROMPT" 2>/dev/null && echo "$(stamp) killed a $PROMPT session that ignored SIGTERM"
    pkill -TERM -f serve-sim 2>/dev/null && echo "$(stamp) stopped the pass's serve-sim helper"
    pkill -TERM -f tt-board-wait 2>/dev/null && echo "$(stamp) stopped the loop's board watcher"
    rm -f "$CHECKOUT/.tuntun/ticket-workflow.pass"
    echo "$(stamp) stopped $PROMPT"
    exit 0
fi

# launchd runs a missed calendar job when a sleeping Mac wakes, so a wake after 17:45 would fire
# the morning start in the evening. Past the end of the working day, it waits for tomorrow.
# test(1) reads 0915 as decimal; only $(( )) would take it for octal.
if [ "$(date +%H%M)" -ge $END ]; then
    echo "$(stamp) past $END, not starting until tomorrow"
    exit 0
fi

if [ ! -d "$CHECKOUT/.git" ] && [ ! -f "$CHECKOUT/.git" ]; then
    echo "$(stamp) no checkout at $CHECKOUT, not starting" >&2
    exit 1
fi
if [ ! -x "$CLAUDE" ]; then
    echo "$(stamp) $CLAUDE is not installed, not starting" >&2
    exit 1
fi
if pgrep -qf "claude.*$PROMPT"; then
    echo "$(stamp) a $PROMPT session is already running, not starting a second"
    exit 0
fi

i=0
while pgrep -qf daily-cleanup.sh && [ $i -lt 180 ]; do
    [ $i -eq 0 ] && echo "$(stamp) waiting for daily-cleanup.sh to finish"
    sleep 10
    i=$((i + 1))
done

# Unattended, so permissions are skipped the way the bot's own sessions skip them. The prompt is
# quoted for Warp's shell: it is one argument, and its `/`s are not paths.
mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_DIR/$CONFIG" <<EOF
---
name: ticket-workflow
windows:
  - tabs:
      - title: ticket-workflow
        layout:
          cwd: "$CHECKOUT"
          commands:
            - exec: "'$CLAUDE' --dangerously-skip-permissions '$PROMPT'"
EOF

open "warp://launch/$CONFIG"
echo "$(stamp) opened $PROMPT in Warp, in $CHECKOUT"
