#!/bin/sh
# Install /mac-cleanup on this Mac: the cleanup script and the Claude Code command that drives it.
#
# Both are symlinks back into this checkout, so a `git pull` here is the upgrade and an edit to
# either one is an edit to the committed copy. A plain file of the same name in the way is moved
# aside to *.bak, never deleted.
#
#   ~/.local/bin/friday-cleanup.sh     -> mac-cleanup/friday-cleanup.sh
#   ~/.claude/commands/mac-cleanup.md  -> mac-cleanup/mac-cleanup.md
#
# The Friday run is opt-in, because it deletes things with nobody watching:
#
#   sh mac-cleanup/install.sh                    the script and the command, no schedule
#   sh mac-cleanup/install.sh --schedule [HH:MM] also run it with --apply on Fridays (default 20:00)
#   sh mac-cleanup/install.sh --unschedule       remove the Friday run, keep the rest
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CHECKOUT=$(cd "$HERE/.." && pwd)
LABEL=com.tommy.mac-cleanup
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

link() {   # link <source in checkout> <target>
    src=$1 target=$2
    mkdir -p "$(dirname "$target")"
    if [ -L "$target" ]; then
        rm "$target"
    elif [ -e "$target" ]; then
        mv "$target" "$target.bak"
        echo "moved the old $target to $target.bak"
    fi
    ln -s "$src" "$target"
    echo "installed $target -> $src"
}

unschedule() {
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$AGENT"
}

schedule() {   # schedule HH:MM
    case "$1" in
        [0-9]:[0-5][0-9]|[01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) ;;
        *) echo "not a time: $1 (want HH:MM, 24-hour)" >&2; exit 2 ;;
    esac
    # 08 is octal to the shell and an invalid integer to a plist, so strip the leading zero.
    hour=${1%%:*}; minute=${1#*:}
    hour=${hour#0}; minute=${minute#0}
    [ -n "$hour" ] || hour=0
    [ -n "$minute" ] || minute=0

    mkdir -p "$(dirname "$AGENT")" "$HOME/Library/Logs/mac-cleanup"
    # `|` as the delimiter: the paths are full of `/`.
    sed -e "s|__CHECKOUT__|$CHECKOUT|g" -e "s|__HOME__|$HOME|g" \
        -e "s|__HOUR__|$hour|g" -e "s|__MINUTE__|$minute|g" \
        "$HERE/$LABEL.plist" > "$AGENT.tmp"
    plutil -lint -s "$AGENT.tmp"
    mv "$AGENT.tmp" "$AGENT"

    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    # bootout returns before the job is gone, and bootstrapping over it fails with an I/O error.
    i=0
    while launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 && [ $i -lt 50 ]; do
        sleep 0.2
        i=$((i + 1))
    done
    launchctl bootstrap "$DOMAIN" "$AGENT"
    launchctl print "$DOMAIN/$LABEL" >/dev/null
    printf 'scheduled %s: Fridays at %02d:%02d, --apply\n' "$LABEL" "$hour" "$minute"
}

case "${1:-}" in
    --unschedule)
        unschedule
        echo "removed the Friday run ($LABEL)"
        exit 0 ;;
    --schedule|"") ;;
    *) echo "usage: sh $0 [--schedule [HH:MM] | --unschedule]" >&2; exit 2 ;;
esac

chmod +x "$HERE/friday-cleanup.sh"
link "$HERE/friday-cleanup.sh" "$HOME/.local/bin/friday-cleanup.sh"
link "$HERE/mac-cleanup.md" "$HOME/.claude/commands/mac-cleanup.md"

if [ "${1:-}" = "--schedule" ]; then
    schedule "${2:-20:00}"
fi
