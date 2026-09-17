#!/usr/bin/env python3
"""The process and terminal mechanisms of session.py, Windows side. WINDOWS.md §2, §4, §6.

Slice W1c made this a stub with the whole surface, so that `import session` and `import bot`
succeed on Windows and the portable tests run natively here. What is not built yet still
raises NotImplementedError naming the slice that fills it in — W3e (the Job Object and
`terminate`), W3f (the signal half of the stop marker) and W4 (the listener: psutil, the
mutex). A stub that returned plausible values instead would let a runner get as far as writing
`starting` before failing, which is the phone waiting out forty-five seconds for nothing;
failing at the first call is the honest version.

Slice W3b filled in the terminal: `spawn`, and the `Terminal` it hands back. `session.py`'s
`Runner` gets the same two-value answer it gets from the Mac — a pid, and something to read —
and the differences that belong to ConPTY rather than to a pty live behind `Terminal`: a
command line instead of an argv list, a poll instead of `select`, and `str` re-encoded to
bytes so `pump`, `Scrape` and `Transcript` see one type on both platforms.

The two things that were already decided are here for real: `spawn_flags()` — W0c measured
that `CREATE_BREAKAWAY_FROM_JOB` is refused under Task Scheduler and unnecessary, so the flags
are `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` — and `request_stop`/`stop_requested`, which
are two lines over a marker file and have no reason to wait.

Dependencies: pywinpty (`Terminal`, from W3b), psutil and pywin32 (W3e, W4) —
`requirements-win.txt`. The imports are inside the functions that need them, so the module
still loads on a bare interpreter.
"""
import os
import subprocess
import sys
import time

#: WINDOWS.md §6, verified in W0c. Not CREATE_BREAKAWAY_FROM_JOB: Task Scheduler runs the
#: listener in a job whose LimitFlags are 0, so breakaway is refused with "Access is denied",
#: and a child survives the task being stopped without it. These two give the runner no
#: shared console and no inherited Ctrl-C.
DETACH_FLAGS = (getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

#: The listener → runner stop request (WINDOWS.md §4). Windows cannot deliver a catchable
#: signal to another process; SPEC.md §2 already has the two processes talking only through
#: files, so this is that design extended rather than a new channel.
STOP = "stop"


def _stderr(message):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


def _later(slice_name):
    raise NotImplementedError("session_win: WINDOWS.md %s has not been built" % slice_name)


def _winpty():
    """The ConPTY binding. A function so the ImportError has one place to be raised from, and
    so that importing this module still costs nothing on a box without the venv (W1c)."""
    import winpty
    return winpty


def environment_block(env):
    """`child_env()`'s dict as the single string `CreateProcess` wants.

    `name=value` pairs separated by NUL and terminated by one more. Sorted case-insensitively
    because that is the order the documentation asks for and the order the CRT writes it in;
    nothing here depends on it, and a block that is sorted is one fewer difference to wonder
    about on the day a child says it cannot find something.
    """
    pairs = sorted(("%s=%s" % (name, value) for name, value in env.items()), key=str.upper)
    return "\0".join(pairs) + "\0"


class Terminal:
    """One ConPTY, and the four things `Runner.pump` does with a terminal.

    The Mac's `pump` holds a master fd and calls `select` and `os.read` on it. ConPTY has no fd
    to wait on — `pywinpty.PTY.read(blocking=False)` answers immediately, with whatever is
    there or with nothing — so the waiting is a poll, and it lives here rather than in `pump`
    so that the loop in session.py stays one implementation for both platforms (W3d).

    Two conversions are this class's whole reason for existing beyond that:

    - **bytes.** pywinpty decodes the terminal to `str` itself, and takes `str` to write. It is
      the only decoder in this stack that is not ours, and `Transcript` is specified to hold
      what the terminal produced — so `read` re-encodes to UTF-8 with `surrogateescape`, which
      is how W0a captured `tests/fixtures/rc_startup_win.log`, and a fixture and a live read
      are therefore the same bytes. 3.0.5 exposes no raw-bytes API; WINDOWS.md §4 asks for one
      if a later version ever does.
    - **the end of the stream.** A pty master reports the last slave closing as `EIO`, which
      `pump` treats as the ordinary end of a session. pywinpty raises `WinptyError` for the
      same thing and it carries no errno to tell that from anything else, so a read that raises
      is the end of the output here, and `alive()` is the flag `pump` decides on.
    """

    #: How long a read sleeps before asking again. The Mac's `select` wakes on the first byte;
    #: this is what not having it costs — up to 10ms of latency on a link that took six
    #: seconds to arrive, against twenty calls across an idle `TICK`.
    POLL = 0.01

    #: **The first thing a pseudoconsole does is ask the terminal what it is**, and if nothing
    #: answers it holds the child's output for three seconds before giving up. The terminal
    #: here is a python loop, so nothing answered. Measured (W3b): 3.04s from spawn to the
    #: child's first byte, every time, on every shape of child — and 0.04s with this reply
    #: sent. It is three seconds off every session start, which is three seconds of the
    #: forty-five the phone is waiting (§11's W4e checklist), and it is why W0a's link took
    #: 6.2 seconds rather than three.
    #:
    #: Answered only once the query has actually been seen, because a reply sent before it
    #: would be input like any other and would reach the child. Answered *after* it, the
    #: pseudoconsole consumes it: an interactive `cmd` driven through this never sees it.
    DA_QUERY = b"\x1b[c"
    DA_REPLY = b"\x1b[?1;0c"          # VT100, no options — the least a terminal can claim

    #: How far into the stream to keep looking for the query. It arrives in the first two
    #: chunks (`ESC[1t`, then `ESC[c ESC[?1004h ESC[?9001h`); past this it is not coming, and
    #: the buffer stops growing rather than holding a copy of the session.
    PREAMBLE = 256

    def __init__(self, pty):
        self._pty = pty
        self.pid = pty.pid
        self._preamble = bytearray()
        self._answered = False

    def read(self, timeout=0.0):
        """Up to one chunk of output, as bytes, waiting at most `timeout` seconds for it.

        Empty means nothing arrived in time — not that the session ended. `alive()` is the only
        thing that says that, and it is deliberately a separate question: output is still
        readable after the child has gone, and `pump` has to drain it or lose the last of a
        failure's error text (§4.6).
        """
        if self._pty is None:
            return b""
        deadline = time.time() + max(timeout, 0.0)
        while True:
            try:
                chunk = self._pty.read(blocking=False)
            except Exception:              # WinptyError: the pipe has gone. See the class.
                return b""
            if chunk:
                data = chunk.encode("utf-8", "surrogateescape")
                self._answer_the_startup_query(data)
                return data
            left = deadline - time.time()
            if left <= 0:
                return b""
            time.sleep(min(self.POLL, left))

    def _answer_the_startup_query(self, data):
        """Tell the pseudoconsole what kind of terminal it has, once. See DA_QUERY."""
        if self._answered:
            return
        self._preamble += data
        if self.DA_QUERY in self._preamble:
            self.write(self.DA_REPLY)
        elif len(self._preamble) < self.PREAMBLE:
            return                       # still early enough for the query to arrive
        self._answered = True
        self._preamble = bytearray()     # nothing reads it again

    def write(self, data):
        """Send `data` to the child. Bytes, so callers pass `DOWN`, `ENTER` and the prompt.

        pywinpty's return value is not a byte count — it answered 0 for a write that arrived
        intact and whole (measured, W3b) — so it is neither passed on nor checked.
        """
        if self._pty is None:
            raise ValueError("write to a closed terminal")
        self._pty.write(data.decode("utf-8", "surrogateescape"))

    def alive(self):
        """Is the child still running? False once it has exited, and once this is closed."""
        if self._pty is None:
            return False
        try:
            return bool(self._pty.isalive())
        except Exception:
            return False

    def close(self):
        """Release the ConPTY. Idempotent, because it is called from a `finally`.

        **This ends the child.** Dropping the last reference closes the pseudoconsole and the
        process behind it is gone within half a second (measured, W3b). That is why WINDOWS.md
        §4 puts the close *last* — after the Ctrl-C and after the job kill — and not first.
        """
        pty, self._pty = self._pty, None
        if pty is None:
            return
        try:
            pty.cancel_io()
        except Exception:
            pass                           # already gone; there is nothing left to cancel
        del pty


def spawn(argv, cwd, env, rows, cols):
    """Run `argv` on a ConPTY of the size §6 requires. Returns (pid, Terminal). W3b.

    Three things differ from the Mac's openpty-and-fork, and each of them was measured rather
    than assumed (W3b's probes, recorded in WINDOWS.md §11):

    - **The size is a constructor argument**, and it is `(cols, rows)` — the transposition of
      what this function is handed. There is no window to race, which is the one thing here
      that is easier than the Mac; getting the order wrong instead gives a 50-column terminal
      that wraps the link across two rows.
    - **ConPTY takes a command line, not an argv list**, and pywinpty takes the program and its
      *arguments* separately: it prepends the program itself, quoted, so this passes `argv[1:]`
      and lets `subprocess.list2cmdline` do the quoting. That is the rule Windows programs
      actually parse by, and it has to be the library's and not a hand join, because a
      `--prompt` from a phone goes through it (§10). W3c is the hostile case, and it found
      nothing to fix here: the round trip through `list2cmdline` and the child's own parser is
      exact for quotes, backslashes and argument boundaries alike. What W3c did find is that
      this is the *second* such round trip and not the first — `Sessions.start` makes one too,
      because `Popen` has no execve to hand a list to.
    - **A binary that is not there fails here**, synchronously, with nothing written to the
      terminal. §4 expected ConPTY to carry the failure onto the pty the way the Mac's forked
      child does; it cannot, because there is no child yet to write it. So `WinptyError`
      becomes `OSError` — the class `Runner.run` catches — and the session is recorded as
      `failed` with the reason in it, rather than the runner dying with a traceback over a
      record that still says `starting`.
    """
    winpty = _winpty()
    cmdline = subprocess.list2cmdline(argv[1:])
    try:
        pty = winpty.PTY(cols, rows)
        started = pty.spawn(argv[0], cmdline=cmdline or None, cwd=cwd,
                            env=environment_block(env))
    except winpty.WinptyError as e:
        raise OSError("could not start %s: %s" % (argv[0], e)) from None
    if not started:
        raise OSError("could not start %s: pywinpty refused the spawn" % argv[0])
    return pty.pid, Terminal(pty)


def _reaped(pid):
    _later("W3e")


def terminate(pid, grace, log=_stderr):
    """Two Ctrl-C bytes on the terminal, then TerminateJobObject. W3e."""
    _later("W3e")


def alive(pid):
    """psutil.pid_exists. W4a."""
    _later("W4a")


def started(pid):
    """psutil.Process(pid).create_time(). W4a."""
    _later("W4a")


def detach():
    """Nothing: the listener detaches the runner at spawn (spawn_flags). W3."""


def spawn_flags():
    """Popen kwargs for starting a runner. See DETACH_FLAGS."""
    return {"creationflags": DETACH_FLAGS}


class Lock:
    """A named mutex, taken in serve() before the first getUpdates. W4d."""

    def __init__(self, path):
        self.path = path

    def take(self):
        _later("W4d")


def request_stop(directory):
    """Ask the runner in `directory` to stop: create the marker. Idempotent."""
    with open(os.path.join(directory, STOP), "ab"):
        pass


def stop_requested(directory):
    """Has the listener asked this runner to stop? Polled by the runner every TICK (W3f)."""
    return os.path.exists(os.path.join(directory, STOP))


def catch_signals(handler):
    """SIGINT and CTRL_BREAK_EVENT for a hand-run console; stops otherwise arrive by marker.
    W3f."""
    _later("W3f")


def restore_signals(previous):
    _later("W3f")
