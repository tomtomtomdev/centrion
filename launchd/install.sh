#!/bin/sh
# Install the LaunchAgent for *this* checkout (SPEC.md §8).
#
# The committed plist says __CHECKOUT__ wherever it needs the repo's path, because the repo lives
# under a different home on each Mac and launchd expands nothing. This writes the real path in,
# copies the result to ~/Library/LaunchAgents, and (re)loads it.
#
#   sh launchd/install.sh           install, or replace the installed copy and restart it
#   sh launchd/install.sh --print   the rendered plist on stdout, and nothing else
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CHECKOUT=$(cd "$HERE/.." && pwd)
LABEL=com.tommy.centrion.bot
TEMPLATE="$HERE/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

render() {
    # `|` as the delimiter: the path is full of `/`. A `|` or `&` in it would be misread, and
    # neither is in any home on this box.
    sed "s|__CHECKOUT__|$CHECKOUT|g" "$TEMPLATE"
}

if [ "${1:-}" = "--print" ]; then
    render
    exit 0
fi

# var/ first: launchd opens StandardOutPath itself, before bot.sh runs, and a missing directory
# is a job that never starts and says nothing.
mkdir -p "$CHECKOUT/var" "$(dirname "$TARGET")"
render > "$TARGET.tmp"
plutil -lint -s "$TARGET.tmp"
mv "$TARGET.tmp" "$TARGET"

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
# bootout returns before the job is gone, and bootstrapping over it fails with an I/O error.
i=0
while launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 && [ $i -lt 50 ]; do
    sleep 0.2
    i=$((i + 1))
done
launchctl bootstrap "$DOMAIN" "$TARGET"
echo "installed $TARGET for $CHECKOUT"
