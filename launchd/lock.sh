#!/bin/sh
# The single-instance lock the listener takes before it polls. Sourced, not executed.
#
# Lifted from stock-watch-project/launchd/lock.sh — same mechanism, different reason. There it
# stops two ingest runs from racing a single-use Stockbit refresh token. Here it stops two
# centrions from existing at all, because two consumers on one bot token 409 each other
# (SPEC.md §7) and the Bot API gives no way to share a getUpdates stream. The failure is mutual
# and continuous: neither copy gets the message, and the only sign of it is a log line every
# thirty seconds. A hand-run copy started to "just check something" while the LaunchAgent is
# loaded is exactly how that happens, so the guard is on the path both of them take.
#
# lockf(1) holds a BSD flock(2) on fd 9, so the kernel drops it when the shell exits for any
# reason at all — kill -9, a panic, launchd's SIGKILL at shutdown. File *existence* is not the
# lock, so there is no such thing as a stale one to detect or clear, and no cleanup trap to get
# right.
#
# shlock(1) is the obvious choice for this and is wrong. On macOS 25.6 it spots a dead holder
# ("process N is dead") and then keeps the lock anyway on a following "lock time changed" test,
# so a single kill -9 wedges the job until the file is deleted by hand. Its own man page
# points at lockf(1) instead.
#
# THE FD OUTLIVES THE EXEC, AND THAT IS BOTH THE POINT AND THE TRAP.
# `exec 9>>` sets no close-on-exec flag, so fd 9 survives into python — which is what keeps the
# lock held for the listener's whole life rather than only for this shell's. It also means every
# process the listener forks inherits it. SPEC.md §8, verified on this box: a detached grandchild
# still held fd 9 and therefore still held the flock. For session.py (slice 6) that is a nasty,
# delayed failure — the listener exits, its detached runners keep the lock alive, launchd's
# restarted listener can never acquire it, and the bot goes permanently silent while its sessions
# look perfectly healthy. The runner must `os.close(9)` before `setsid()`, guarded with
# try/except OSError for the hand-run case where fd 9 was never opened.
#
# The lock lives under var/ because that whole directory is gitignored (SPEC.md §3).

LOCK=var/.bot.lock

# 0 = the lock is ours until this shell (and anything it execs into) exits; 1 = another copy.
take_lock() {
    mkdir -p "$(dirname "$LOCK")" || return 1
    # Append, never truncate: opening with > would wipe the running holder's pid line below.
    exec 9>> "$LOCK" || return 1
    if /usr/bin/lockf -s -t 0 -k /dev/fd/9; then
        echo $$ > "$LOCK"   # only so the loser can name us; the flock itself is on fd 9
        return 0
    fi
    return 1
}

lock_holder() {
    echo "pid $(cat "$LOCK" 2>/dev/null || echo '?')"
}
