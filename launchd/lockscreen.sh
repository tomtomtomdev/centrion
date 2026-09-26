#!/bin/sh
# What com.tommy.centrion.lock runs at login (SPEC.md §8): lock the screen, but only after an
# automatic login.
#
# Automatic login is what gets the listener running after pmset's power-on; this is what keeps
# the desktop it lands on from being open. The check matters as much as the lock. If this Mac is
# not set to log this user in automatically, then whoever is logging in just typed the password,
# and locking the screen back in their face is a bug. So: no autoLoginUser for this user, no lock.
#
# install.sh copies the plist but does not bootstrap it, because bootstrapping runs RunAtLoad
# jobs there and then, and reinstalling would lock the screen of the person reinstalling.
# launchd loads it from ~/Library/LaunchAgents at the next login, which is the only time it
# should run.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/.." || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') login ==="

auto=$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null || true)
if [ "$auto" != "$(id -un)" ]; then
    echo "automatic login is not set for $(id -un) — this login typed a password; not locking"
    exit 0
fi

if [ ! -x var/lockscreen ]; then
    echo "var/lockscreen is missing — run sh launchd/install.sh. THE DESKTOP IS OPEN."
    exit 1
fi
exec var/lockscreen
