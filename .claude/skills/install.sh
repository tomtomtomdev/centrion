#!/bin/sh
# Install this checkout's user-level skills into ~/.claude/skills, so every project on this Mac can
# run them (ticket-workflow, ios-next-slice and feature-work work in the app repos, not in this one).
#
# Each skill is a symlink back into the checkout, so a `git pull` here is the upgrade. A plain
# directory or a command file of the same name in its way is moved aside to *.bak, never deleted.
# A skill that ships a configure.sh is asked its settings the first time; after that, re-run the
# skill's own configure.sh to change them.
#
#   sh .claude/skills/install.sh                  install ticket-workflow, ios-next-slice, feature-work
#   sh .claude/skills/install.sh <name> [name…]   install those skills instead
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DEST="$HOME/.claude/skills"
[ $# -gt 0 ] || set -- ticket-workflow ios-next-slice feature-work

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
    # A command of the same name is a second /$name that drifts from this one, and so is a command
    # the skill was renamed from (ios-next-slice was the user-level /next-slice).
    olds=$name
    case "$name" in ios-next-slice) olds="$olds next-slice" ;; esac
    for old in $olds; do
        cmd="$HOME/.claude/commands/$old.md"
        if [ -f "$cmd" ]; then
            mv "$cmd" "$cmd.bak"
            echo "moved the old $cmd to $cmd.bak"
        fi
    done
    ln -s "$src" "$target"
    echo "installed $target -> $src"
    if [ -f "$src/configure.sh" ]; then
        sh "$src/configure.sh" --if-missing
    fi
done
