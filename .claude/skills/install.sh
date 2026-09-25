#!/bin/sh
# Install this checkout's user-level skills into ~/.claude/skills, so every project on this Mac
# can run them (ticket-workflow and ios-next-slice work in the app repos, not in this one).
#
# Each skill is a symlink back into the checkout, so a `git pull` here is the upgrade. A plain
# directory or a command file of the same name in its way is moved aside to *.bak, never deleted.
#
#   sh .claude/skills/install.sh                  install ticket-workflow and ios-next-slice
#   sh .claude/skills/install.sh <name> [name…]   install those skills instead
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DEST="$HOME/.claude/skills"
[ $# -gt 0 ] || set -- ticket-workflow ios-next-slice

mkdir -p "$DEST"
for name in "$@"; do
    src="$HERE/$name"
    if [ ! -f "$src/SKILL.md" ]; then
        echo "no skill $name in $HERE" >&2
        exit 1
    fi
    target="$DEST/$name"
    if [ -L "$target" ]; then
        rm "$target"
    elif [ -e "$target" ]; then
        mv "$target" "$target.bak"
        echo "moved the old $target to $target.bak"
    fi
    # A command of the same name is a second /$name that drifts from this one.
    cmd="$HOME/.claude/commands/$name.md"
    if [ -f "$cmd" ]; then
        mv "$cmd" "$cmd.bak"
        echo "moved the old $cmd to $cmd.bak"
    fi
    ln -s "$src" "$target"
    echo "installed $target -> $src"
done
