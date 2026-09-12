#!/bin/sh
# What launchd actually runs (SPEC.md §8), and the right way to start the listener by hand.
#
# Its whole job is to be the thing that holds the single-instance lock. The lock has to be taken
# by a shell rather than by python, because `exec 9>>` is how fd 9 gets opened without
# close-on-exec, and that is what lets the flock survive the exec into python and last for the
# listener's whole life. See lock.sh — including the warning about what the runner must do with
# that inherited fd in slice 6.
#
# Order matters more than it looks: the lock is taken before the listener starts, so a second
# copy is refused before it has spent a single getUpdates knocking the first one off the air. A
# 409 is mutual (SPEC.md §7) — the copy that loses the race takes the incumbent down with it —
# so "start and then detect the conflict" is not good enough.
#
# Under `KeepAlive true` a refused copy is restarted by launchd every ThrottleInterval seconds,
# which writes a line to var/bot.log each time. That is deliberate: if a hand-run listener is
# holding the lock, the LaunchAgent saying so once every ten seconds is how you find out, and it
# stops the moment the hand-run copy exits. Quiet would be worse.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/.." || exit 1
. "$HERE/lock.sh"

PY=/usr/bin/python3

echo "=== $(date '+%Y-%m-%d %H:%M:%S %z') centrion listener starting ==="

if ! take_lock; then
    echo "another centrion already holds $LOCK ($(lock_holder)) — this copy exits rather than"
    echo "409ing the one that is working (SPEC.md §7). Stop it first if you meant to replace it:"
    echo "  launchctl bootout gui/\$(id -u)/com.tommy.centrion.bot"
    exit 0
fi

# exec, so the listener is this pid: launchd's KeepAlive watches it directly, `launchctl
# kickstart -k` signals it directly, and no shell sits in between doing nothing.
exec "$PY" bot.py --serve
