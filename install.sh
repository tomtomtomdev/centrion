#!/bin/sh
# Set up this Mac from one command: every part this checkout installs, in one run.
#
# Each part keeps its own installer, which stays the place to run one part again or pass it its own
# flags; this only runs them in order. The skills and /mac-cleanup go first, because they need
# nothing. The LaunchAgents go last, because they restart the listener and end in power.sh's sudo
# prompt, the one step that asks for anything.
#
#   skills    .claude/skills/install.sh   ticket-workflow, ios-next-slice, feature-work, plan-build, asking settings once
#   cleanup   mac-cleanup/install.sh      daily-cleanup.sh and /mac-cleanup, no daily run unless asked
#   figma     figma-to-claude/install.sh  figma-spec CLI; the plugin itself is one manual import
#   agent     launchd/install.sh          the bot, the lock, tt-lcmp-pull, the 09:15–17:45 ticket loop,
#                                         the figma-spec receiver, then pmset's power schedule
#
#   sh install.sh                     all four
#   sh install.sh <part> [part…]      only those, in the order above
#   sh install.sh --schedule [HH:MM]  also schedule the daily cleanup --apply (default 09:15)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PARTS=""
SCHEDULE=""

while [ $# -gt 0 ]; do
    case "$1" in
        skills|cleanup|figma|agent) PARTS="$PARTS $1" ;;
        --schedule)
            SCHEDULE=09:15
            case "${2:-}" in [0-9]*) SCHEDULE=$2; shift ;; esac ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) echo "usage: sh $0 [skills] [cleanup] [figma] [agent] [--schedule [HH:MM]]" >&2; exit 2 ;;
    esac
    shift
done
[ -n "$PARTS" ] || PARTS=" skills cleanup figma agent"

wants() { case "$PARTS " in *" $1 "*) return 0 ;; esac; return 1; }

if wants skills; then
    echo "== skills"
    sh "$HERE/.claude/skills/install.sh"
fi
if wants cleanup; then
    echo "== cleanup"
    if [ -n "$SCHEDULE" ]; then
        sh "$HERE/mac-cleanup/install.sh" --schedule "$SCHEDULE"
    else
        sh "$HERE/mac-cleanup/install.sh"
    fi
fi
if wants figma; then
    echo "== figma"
    if [ -f "$HERE/figma-to-claude/install.sh" ]; then
        sh "$HERE/figma-to-claude/install.sh"
        # The CLI installs itself; the plugin half cannot. A Figma plugin is
        # registered through the desktop UI, so this prints the one step no
        # installer can take.
        echo "   plugin: Figma → Plugins → Development → Import plugin from manifest…"
        echo "           $HERE/figma-to-claude/manifest.json"
    else
        echo "   figma-to-claude/ is empty — run: git submodule update --init" >&2
    fi
fi
if wants agent; then
    echo "== agent"
    sh "$HERE/launchd/install.sh"
fi
