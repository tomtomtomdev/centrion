#!/usr/bin/env python3
"""The process and terminal mechanisms of session.py, macOS/POSIX side. WINDOWS.md §2.

Moved here from session.py in slice W1a, unchanged: `spawn`, `_reaped`, `_signal`,
`terminate` and `detach` are the code SPEC.md verified on the Mac, with the same docstrings,
and the seam exists so that `session_win.py` can stand next to them without a line of this
having to change. `session.py` picks one of the two by `sys.platform` and calls through it;
nothing else imports this module directly.

Every platform module exports the same names — `tests/test_session.py::TestThePlatformSeam`
lists them — and the ones that have no work to do on POSIX say so here rather than being
absent: `spawn_flags()` is empty because `os.setsid()` in the runner does the detaching;
`Lock.take()` is always True because launchd/bot.sh holds the `lockf` before python starts
(SPEC.md §8); `request_stop`/`stop_requested` do nothing because the listener SIGTERMs the
runner (§5, §9.10). W3f may teach POSIX the stop marker too, so that the two platforms share
one set of tests; until then the marker is Windows-only and these are the honest no-ops.

Stdlib only, same as session.py (SPEC.md §3).
"""
import errno
import fcntl
import os
import signal
import struct
import sys
import termios
import time

#: launchd/bot.sh's flock (SPEC.md §8). Closed before setsid, guarded for the hand-run case.
LOCK_FD = 9


def _stderr(message):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


def spawn(argv, cwd, env, rows, cols):
    """Run `argv` on a real terminal of the size §6 requires. Returns (pid, master_fd).

    `os.openpty()` and an explicit fork rather than `pty.fork()`, because TIOCSWINSZ has to be
    set on the slave *before* the exec. Setting it on the master after pty.fork() returns does
    work and races the child's first render, and what that produces is an occasional missed URL
    — the worst kind of bug to go looking for.
    """
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    pid = os.fork()
    if pid == 0:
        try:
            os.close(master)
            # Its own session, with the pty as its controlling terminal — without which
            # --remote-control will not start an interactive session at all (§9.1).
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            if slave > 2:
                os.close(slave)
            os.chdir(cwd)
            os.execve(argv[0], argv, env)
        except BaseException as e:                      # pragma: no cover - the child never returns
            # On the pty, so it lands in pty.log and reaches the phone as the error tail (§4.6).
            # 126 is the exit code TCC produces for the same shape of failure (§9.2).
            try:
                os.write(2, ("centrion: could not start %s: %r\r\n" % (argv[0], e)).encode())
            except OSError:
                pass
            os._exit(126)
    os.close(slave)
    return pid, master


def _reaped(pid):
    """True once `pid` is gone. False only while it is genuinely still running.

    Both halves, because neither call answers the question on its own:

    - `waitpid` is the only one that can clear a **zombie**. A child that has exited and not
      been waited on still answers `kill(pid, 0)`, so asking that alone would wait out the full
      grace period on a process that died instantly.
    - `kill(pid, 0)` is the only one that can speak for a process that is **not our child**,
      which every runner becomes the moment the listener that forked it exits — detached, and
      reparented to launchd. `waitpid` answers ECHILD there, and ECHILD means *not mine*, which
      reads exactly like *already gone*. Trusting it made `terminate()` return True having
      signalled nothing at all, which is §9.10's failure again in a different disguise: the
      session lives on with permissions bypassed and the reply says it was stopped.

    EPERM is deliberately *not* gone: it means the process is there and belongs to someone else.
    """
    try:
        if os.waitpid(pid, os.WNOHANG)[0]:
            return True
    except OSError:
        pass                 # not ours to wait on; kill(0) below is what knows
    try:
        os.kill(pid, 0)
    except OSError as e:
        return e.errno == errno.ESRCH
    return False


def _signal(pid, sig, log):
    """Signal the session's process group *and* the process itself.

    Both, because which one exists depends on how far the spawn got. Once the child has run
    setsid() it leads its own group and the group is what matters — a session that started a
    dev server has it in there too, and §5's `stop` must not leave that behind. Before setsid()
    there is no such group: `killpg(pid)` fails with ESRCH and the only thing that reaches the
    child is a plain kill. Getting this wrong is silent — the call "succeeds" at signalling
    nothing and the session lives on.

    `killpg(pid)` is safe to attempt either way: pid is a fresh child, so it can never be this
    process's own group id, and there is no way for this to signal the runner itself.
    """
    reached = False
    for send, target in ((os.killpg, "group"), (os.kill, "process")):
        try:
            send(pid, sig)
            reached = True
        except OSError as e:
            if e.errno not in (errno.ESRCH, errno.EPERM):
                log("could not signal %s %d: %s" % (target, pid, e))
    return reached


def terminate(pid, grace, log=_stderr):
    """End the session at `pid` and everything it spawned. Returns True once it is gone.

    **The grace periods nest, and a caller ending a *runner* has to allow for it** (§9.11).
    This is two kills in sequence, not one: the runner catches SIGTERM, and then spends up to
    its own `GRACE` ending claude — which takes longer than it looks, over five seconds for a
    real session on this box. Pass a runner the same `grace` the runner passes claude and this
    SIGKILLs it in the middle of that, orphaning the session and leaving meta.json saying
    `live` for something that is on its way out.

    Signalled explicitly rather than by hanging up the pty, and that is not belt-and-braces.
    *Verified on this box:* closing the master does SIGHUP the child, in about 100ms — but only
    once the child has actually taken the pty as its controlling terminal. Close it inside the
    window between the fork and TIOCSCTTY and the child has no controlling terminal to be hung
    up from: it survives, orphaned, writing to a pty nobody holds. A `stop` arriving moments
    after a spawn lands exactly in that window, and the session it fails to kill is one with
    permissions bypassed and nobody watching it (§5, §12 slice 8).
    """
    if _reaped(pid):
        return True
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 1.0)):
        _signal(pid, sig, log)
        deadline = time.time() + wait
        while time.time() < deadline:
            if _reaped(pid):
                return True
            time.sleep(0.05)
    return _reaped(pid)


def alive(pid):
    """Does *something* answer to this pid? The first half of bot.py's §4 check (W1b).

    `kill(pid, 0)` says whether a process is there; it cannot say whether it is the one the
    record was written about — `started()` is the second half. EPERM is not death: it means
    the process is there and belongs to someone else, and `_reaped()` above draws the same
    line for the same reason (§9.11).
    """
    try:
        os.kill(pid, 0)
    except OverflowError:
        return False
    except OSError as e:
        return e.errno == errno.EPERM
    return True


def started(pid):
    """When that pid's process started, in epoch seconds, or None if there is no such process.

    Filled in by W1b, which moves bot.process_started() here. Until then bot.py keeps its own.
    """
    raise NotImplementedError("W1b")


def detach():
    """Leave the listener behind. SPEC.md §8, and the order matters.

    fd 9 is launchd/bot.sh's flock. `exec 9>>` sets no close-on-exec flag, so it survived into
    python and into this fork, and a runner that keeps it keeps the lock — verified on this box.
    The consequence is delayed and nasty: the listener exits, its detached runners hold the lock
    open, launchd's restarted listener can never acquire it, and the bot goes permanently silent
    while its sessions look perfectly healthy.
    """
    try:
        os.close(LOCK_FD)
    except OSError:
        pass          # hand-run: fd 9 was never opened
    os.setsid()


def spawn_flags():
    """Popen kwargs for starting a runner. Nothing on POSIX: the runner detaches itself."""
    return {}


class Lock:
    """The single-instance lock. On POSIX it is launchd/bot.sh's `lockf` on fd 9, taken by
    the shell before python starts (SPEC.md §8), so there is nothing for this process to
    take: `take()` is always True. `session_win.py` is where this does work."""

    def __init__(self, path):
        self.path = path

    def take(self):
        return True


def request_stop(directory):
    """Ask the runner in `directory` to stop. POSIX SIGTERMs it instead; nothing to write."""


def stop_requested(directory):
    """Has the listener asked this runner to stop? Never by marker on POSIX — see W3f."""
    return False


def catch_signals(handler):
    """Route SIGTERM, SIGINT and SIGHUP to `handler`; return what to hand restore_signals().

    So a `stop` ends the session rather than the runner, and meta.json is left truthful.
    """
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            previous[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass          # not the main thread; the caller's finally clause still cleans up
    return previous


def restore_signals(previous):
    for sig, handler in previous.items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
