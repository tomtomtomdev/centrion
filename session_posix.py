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
(SPEC.md §8). W3f did teach POSIX the stop marker — `request_stop`/`stop_requested` are the
same two lines over the same file as on Windows, so the runner has one place that hears a stop
and this file's tests are one set — and the Mac keeps SIGTERM as well (§5, §9.10), because a
runner outside `pump` hears only that.

Stdlib only, same as session.py (SPEC.md §3).
"""
import errno
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import time

#: launchd/bot.sh's flock (SPEC.md §8). Closed before setsid, guarded for the hand-run case.
LOCK_FD = 9

#: The listener → runner stop request (WINDOWS.md §4, W3f). Windows has no signal one process
#: can deliver to another, so the marker is the whole channel there; here it is the second way
#: of saying what SIGTERM says. The same name as `session_win.STOP` and deliberately not shared
#: through an import — neither platform module imports the other, and both ends of this
#: protocol are always the same module.
STOP = "stop"

#: One read off the master, and the amount the transcript cap can overshoot by (§10.7): the
#: size is checked after a write, so a chunk this big is briefly over it either way. Here
#: rather than in session.py since W3d, because the chunk size is the pty's business — ConPTY
#: hands `session_win.Terminal` whatever it has and is never asked for a length.
READ_SIZE = 65536


def _stderr(message):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


class Terminal:
    """One pty master, and the four things `Runner.pump` does with a terminal. W3d.

    Until W3d `pump` held this fd itself and called `select` and `os.read` on it. That loop
    cannot exist on Windows — a pseudoconsole has no descriptor to wait on — so the waiting
    moved in here, where each platform can do it its own way, and what is left in `pump` is
    the part that was always portable. The `select` below is the one that used to be `pump`'s
    first statement, unchanged.

    **`read` and `alive` answer two questions that used to be one.** On a pty they arrive
    together: EIO off the master means the last slave closed, which is the end of the output
    *and* the end of the session, and the old loop broke on either. ConPTY separates them —
    the child is gone while the last of its output is still buffered, and that last part is
    the error text §4.6's tail is made of — so the shared loop needs them separated here too:
    `read` says what arrived, `alive` says whether anything more is coming. On this platform
    `alive` is therefore about the *terminal* and not the process: it goes false when the pty
    hangs up, which is what `pump` is asking about, and `_reaped()` is what asks after a pid.
    """

    def __init__(self, pid, master):
        self.pid = pid
        self._master = master
        self._finished = False

    def read(self, timeout=0.0):
        """Up to one chunk of output, as bytes, waiting at most `timeout` seconds for it.

        Empty means nothing arrived in time — `alive()` is the only thing that says the
        terminal is finished. An interrupted `select` is empty for the same reason it used to
        be a `continue`: the handler that interrupted it has set `stopping`, and the loop's
        own condition is what reads that.
        """
        if self._master is None:
            return b""
        try:
            ready, _, _ = select.select([self._master], [], [], max(timeout, 0.0))
        except OSError as e:
            if e.errno == errno.EINTR:
                return b""
            self._finished = True
            return b""
        if not ready:
            return b""
        try:
            chunk = os.read(self._master, READ_SIZE)
        except OSError as e:
            # EIO is how a pty master reports the last slave closing. That is the ordinary
            # end of a session, not a failure.
            if e.errno not in (errno.EIO, errno.EBADF):
                raise
            self._finished = True
            return b""
        if not chunk:
            self._finished = True
        return chunk

    def write(self, data):
        """Send `data` to the child: §9.3's arrow keys, and §4's prompt."""
        if self._master is None:
            raise ValueError("write to a closed terminal")
        os.write(self._master, data)

    def alive(self):
        """Is there more output coming? False once the pty has hung up, and once this is
        closed."""
        return self._master is not None and not self._finished

    def close(self):
        """Drop the master. Idempotent, because it is called from a `finally`.

        **This hangs up the child** — the kernel SIGHUPs the foreground process group about
        100ms after the last master fd goes, but only once the child has taken the pty as its
        controlling terminal, which is the window `terminate()` exists to cover. It is why
        `run()` closes last, after the signals and not instead of them (§2, §9.10).
        """
        master, self._master = self._master, None
        if master is None:
            return
        try:
            os.close(master)
        except OSError:
            pass


def spawn(argv, cwd, env, rows, cols):
    """Run `argv` on a real terminal of the size §6 requires. Returns (pid, Terminal).

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
    return pid, Terminal(pid, master)


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


def terminate(pid, grace, log=_stderr, terminal=None):
    """End the session at `pid` and everything it spawned. Returns True once it is gone.

    `terminal` is accepted and ignored, and that is the whole of this platform's answer to it.
    It is in the seam for Windows (WINDOWS.md W3e), where a Ctrl-C is a byte written to the
    terminal and the Job Object that takes the tree is held by the same object; here the two
    kills below are addressed by pid and the terminal is not part of the mechanism. It is
    deliberately *not* used as a shortcut for hanging the child up either — see the last
    paragraph of this docstring for the window in which that kills nothing at all.

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

    SPEC.md §4 asks for this by name: `pid reuse is theoretically possible between reboots;
    started is in the record, so compare it against the process start time before trusting a
    pid that is alive.` It is not an exotic case — pids after a reboot are four-digit numbers
    handed out within a minute of login, and every record on disk names one.

    `ps` because macOS has no /proc and the alternative is a ctypes sysctl against a
    kinfo_proc layout, which is a great deal of fragile arithmetic to avoid one subprocess on
    a pass that runs at most a handful of times every fifty seconds. §9.6 also applies: the
    Mac has no third-party packages and is not getting one for this. (Moved from bot.py in
    W1b; the Windows answer is psutil, in session_win.py.)
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    try:
        out = subprocess.check_output(["/bin/ps", "-o", "lstart=", "-p", str(pid)],
                                      stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        return None
    text = out.decode("ascii", "replace").strip()
    try:
        # `Sun Sep 13 09:10:27 2026`, in the C locale ps always answers in.
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y"))
    except (ValueError, OverflowError):
        return None


def child_env(env):
    """The Mac's half of the child's environment. SPEC.md §6; WINDOWS.md §4, W3g.

    `session.child_env` has already taken the hazards out and put `COLUMNS`/`LINES` in. What is
    left is everything that is a fact about *this* platform rather than about Claude Code, and
    on the Mac that is a `PATH` chosen rather than inherited: `~/.local/bin` first because that
    is the version-pinned `claude` symlink (§9.8), and brew after it because the session's own
    Bash tool needs it to be useful. A `PATH` off whoever started the listener would put an
    unpinned `claude` first on the day somebody brew-installs one.

    This is today's code moved, not rewritten — the same rule W1a followed for the rest of the
    seam — with one line changed: `HOME` is read from the environment being built rather than
    from `os.environ`, which it already was.
    """
    home = env.get("HOME") or os.path.expanduser("~")
    env.update({
        "PATH": "%s/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" % home,
        "HOME": home,
        "USER": env.get("USER") or os.environ.get("USER", ""),
        "SHELL": env.get("SHELL") or "/bin/zsh",
        "LANG": "en_US.UTF-8",
        "TERM": "xterm-256color",
    })
    return env


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
    """Ask the runner in `directory` to stop: create the marker. Idempotent.

    A second way of saying what SIGTERM says here, not a replacement for it — `bot.py` on this
    platform still signals, and a runner that is anywhere other than `pump` hears only that.
    What the marker buys the Mac is that the runner has one place where a stop is heard
    (`Runner.pump`) instead of one per platform, and that the tests for it are one set. See
    WINDOWS.md W3f, and `session_win.request_stop`, which is this function and is the whole
    channel on the platform that has no deliverable signal.
    """
    with open(os.path.join(directory, STOP), "ab"):
        pass


def stop_requested(directory):
    """Has the listener asked this runner to stop? Polled by the runner every TICK (W3f)."""
    return os.path.exists(os.path.join(directory, STOP))


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
