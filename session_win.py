#!/usr/bin/env python3
"""The process and terminal mechanisms of session.py, Windows side. WINDOWS.md §2, §4, §6.

Slice W1c made this a stub with the whole surface, so that `import session` and `import bot`
succeed on Windows and the portable tests run natively here. What is not built yet still
raises NotImplementedError naming the slice that fills it in — W4 (the listener: `alive`,
`started`, the mutex). A stub that returned
plausible values instead would let a runner get as far as writing `starting` before failing,
which is the phone waiting out forty-five seconds for nothing; failing at the first call is
the honest version.

Slice W3b filled in the terminal: `spawn`, and the `Terminal` it hands back. `session.py`'s
`Runner` gets the same two-value answer it gets from the Mac — a pid, and something to read —
and the differences that belong to ConPTY rather than to a pty live behind `Terminal`: a
command line instead of an argv list, a poll instead of `select`, and `str` re-encoded to
bytes so `pump`, `Scrape` and `Transcript` see one type on both platforms.

Slice W3e filled in the ending: a Job Object created before the child and holding it since
before `spawn` returned, and a `terminate` that is a Ctrl-C twice over before it is a kill.
Both of them live on the `Terminal`, because neither is reachable from a pid — which is why
`terminate` takes one, and why the seam is a shape wider than the Mac needed it to be.

The two things that were already decided are here for real: `spawn_flags()` — W0c measured
that `CREATE_BREAKAWAY_FROM_JOB` is refused under Task Scheduler and unnecessary, so the flags
are `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` — and `request_stop`/`stop_requested`, which
are two lines over a marker file and have no reason to wait.

Slice W3f finished that last pair off at the runner's end and settled what is left of signals
here. `Runner.pump` polls the marker every tick on both platforms, and `catch_signals` takes
`SIGINT` and `SIGBREAK` and nothing else: `SIGHUP` does not exist here, and `SIGTERM` cannot be
delivered to another process without also killing it outright, which is the reason `stop` is a
file rather than a signal in the first place.

Dependencies: pywinpty (`Terminal`, from W3b), pywin32 (the Job Object, W3e) and psutil
(`_reaped` here, `alive`/`started` in W4a) — `requirements-win.txt`. The imports are inside
the functions that need them, so the module still loads on a bare interpreter.
"""
import os
import signal
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

#: Ctrl-C, and on this platform that is all it is: a byte on the terminal. §4 said the
#: pseudoconsole turns it into a `CTRL_C_EVENT` for the console; W3e measured that nothing
#: does, and that it is delivered to whatever is *reading* the console like any other key. So
#: it is the polite stop for claude, which reads the console, and it is nothing at all for a
#: `ping` or a build — see `terminate`.
INTERRUPT = b"\x03"

#: The gap between the two Ctrl-Cs, and its own number rather than `session.SETTLE`'s — which
#: happens to be the same 0.4 for a different reason (a keystroke into a panel that is still
#: drawing). This one is W0b's measurement: two `\x03` 0.4s apart ended a real session with
#: exit status 0 after 1.71s, and one alone was never tried, so the pair is what is specified
#: and this is the gap it was measured with. Importing `session` to share the constant is not
#: available anyway — `session` imports this module, not the other way round.
SETTLE = 0.4

#: What `TerminateJobObject` is told to make the exit code. Any non-zero would do; 1 is what §4
#: names, and it being non-zero is the point: a session that exited 0 chose to, and a session
#: that exited 1 was killed. `test_terminate_graceful_first` is that distinction as a test.
JOB_KILL_CODE = 1

#: How long a kill gets before it is called a failure. A job kill is not synchronous — the call
#: starts the termination and returns — so an answer read the instant it comes back is an
#: answer about a race. The same 1.0s `session_posix.terminate` allows after its SIGKILL.
AFTER_KILL = 1.0

#: How often the waits above ask whether the session has gone. Same as the Mac's.
POLL = 0.05


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


def _win32job():
    """Job Objects, from pywin32. Same shape as `_winpty` and for the same two reasons."""
    import win32job
    return win32job


def _psutil():
    """Process facts. Same shape again.

    §6 has this arriving with the listener in W4a, for `alive` and `started`. W3e needs it one
    slice earlier and for a smaller thing: `_reaped` below, which is the *pid* question
    `terminate` is built on, and which the Mac answers with `waitpid` and `kill(0)` — neither
    of which exists here.
    """
    import psutil
    return psutil


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

    `pump` used to hold a pty master fd and call `select` and `os.read` on it. ConPTY has no fd
    to wait on — `pywinpty.PTY.read(blocking=False)` answers immediately, with whatever is
    there or with nothing — so the waiting is a poll, and it lives here rather than in `pump`
    so that the loop in session.py stays one implementation for both platforms. W3d did the
    other half of that: `session_posix.Terminal` now holds the fd and the `select`, `spawn`
    hands back one of these on either platform, and `pump` names nothing but `read`, `write`,
    `alive` and `close`.

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

    def __init__(self, pty, job=None):
        self._pty = pty
        self.pid = pty.pid
        #: The session's Job Object, created before the child and holding it since before
        #: `spawn` returned (W3e). `terminate` is the only thing that uses it, and it is here
        #: rather than in a table keyed by pid because this is already the object the runner
        #: holds for the life of the session — and because the other half of a Windows stop,
        #: the Ctrl-C, is a write to this same terminal.
        self.job = job
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

    def exit_status(self):
        """How the child went: its exit code, or None while it is still running.

        Not used by `pump` — `alive()` is the only question the loop asks — but it is how a
        stop can be told from a kill, which is what `terminate`'s graceful path claims to be
        doing. `TerminateJobObject` forces `JOB_KILL_CODE`, so a 0 here is a program that shut
        itself down and a `JOB_KILL_CODE` is one that had to be ended.
        """
        if self._pty is None:
            return None
        try:
            return self._pty.get_exitstatus()
        except Exception:
            return None

    def close(self):
        """Release the job and the ConPTY. Idempotent, because it is called from a `finally`.

        **This ends the session — all of it.** Two mechanisms, and W3e measured them as two:
        closing the pseudoconsole ends the child within half a second (W3b), and closing the
        job takes everything the child started, because `_create_job` sets
        `KILL_ON_JOB_CLOSE`. Either one alone leaves the other half running. That is why
        WINDOWS.md §4 puts this *last* — after the Ctrl-C and after the job kill — and not
        first: everything polite has to have happened already.

        The job goes first of the two only so that the tree cannot spend half a second
        reparenting itself while the pseudoconsole is coming down; both are gone by the time
        this returns either way.
        """
        pty, self._pty = self._pty, None
        job, self.job = self.job, None
        if job is not None:
            try:
                job.Close()                # and with it, per `_create_job`, whatever is inside
            except Exception:
                pass
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
    job = _create_job()                    # before the child. See the docstring's last bullet.
    try:
        pty = winpty.PTY(cols, rows)
        started = pty.spawn(argv[0], cmdline=cmdline or None, cwd=cwd,
                            env=environment_block(env))
    except winpty.WinptyError as e:
        raise OSError("could not start %s: %s" % (argv[0], e)) from None
    if not started:
        raise OSError("could not start %s: pywinpty refused the spawn" % argv[0])
    terminal = Terminal(pty, job)
    try:
        _assign_to_job(job, pty.pid)
    except OSError:
        # Nothing may survive a spawn that did not finish: a child outside the job is a child
        # `terminate` can never end, and the caller is about to record this as `failed`.
        terminal.close()
        raise
    return pty.pid, terminal


def _create_job():
    """A Job Object for this session, set to kill on close. W3e.

    Unnamed, because a name is a thing two runners could collide on. It is not a quota — the
    only thing wanted from a job is that it is a handle on *everything the session starts*,
    which it gives for free: a process assigned to one cannot leave it and neither can its
    children.

    **`KILL_ON_JOB_CLOSE` is the one limit set, and it is what covers the runner dying without
    getting to `terminate`** — a crash, or §6's `Sessions.stop` reaching its last resort, which
    on this platform is `TerminateProcess` and catches nothing. This was first written the
    other way round, reasoning that dropping the handle should not be a way to end a session by
    accident. Measuring it retired that reason: when the runner dies, the pseudoconsole it
    held closes, and *that alone* ends claude within half a second (the same thing W3b measured
    from `Terminal.close()`). So the session is already over by the time this flag has an
    opinion, and the only question left is whether what claude started goes with it. Without
    the flag it does not — measured, a detached grandchild outlives the runner, the session and
    the reconciliation that follows. There is no accident left to protect against and a real
    orphan to prevent, so the flag goes in.

    Note it is therefore `Terminal.close()`, not this handle alone, that the session's life
    hangs on — which is the same sentence §4's ordering has always been about, now with the
    tree included. `test_a_runner_that_dies_without_warning_leaves_nothing_behind` is both
    halves of the measurement above, kept as one test.

    A failure here is an `OSError` *before anything has been spawned*, which is the useful
    half of §4's ordering: the alternative is a running session with no job to end it by,
    which fails silently and only at `stop`.
    """
    try:
        win32job = _win32job()
        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, limits)
        return job
    except Exception as e:
        raise OSError("could not create the job object for the session: %s" % e) from None


def _assign_to_job(job, pid):
    """Put `pid` in `job`. Called between the spawn and `spawn` handing the pid back.

    `PROCESS_SET_QUOTA | PROCESS_TERMINATE` is what `AssignProcessToJobObject` asks for, and
    the handle is closed again immediately: the job holds the process, not this handle.

    A pid that will not assign has almost always already exited — a child that failed in its
    first milliseconds — and §4 treats it the same way as a binary that was not there: an
    `OSError`, a `failed` record with the reason in it, and no session.
    """
    import win32api
    import win32con
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid)
    except Exception as e:
        raise OSError("could not open pid %d to put it in the job: %s" % (pid, e)) from None
    try:
        _win32job().AssignProcessToJobObject(job, handle)
    except Exception as e:
        raise OSError("could not put pid %d in the job: %s" % (pid, e)) from None
    finally:
        try:
            win32api.CloseHandle(handle)
        except Exception:
            pass


def _reaped(pid):
    """True once `pid` is gone. False only while something is genuinely still running there.

    One call, where `session_posix._reaped` needs two, and neither of its reasons survives the
    port: Windows has no zombie to clear, so there is no `waitpid` half, and a pid query here
    does not care whose child the process is — which is the case the Mac needed `kill(0)` for,
    since every runner stops being the listener's child the moment it detaches.

    Something that is not a pid at all reads as gone, because the only caller is a `finally`
    and the alternative is a `TypeError` replacing a session's outcome with a traceback.
    """
    try:
        return not _psutil().pid_exists(int(pid))
    except (TypeError, ValueError, OverflowError):
        return True


def terminate(pid, grace, log=_stderr, terminal=None):
    """End the session at `pid` and everything it started. Returns True once it is gone. W3e.

    §4's order, and the order is the point on this platform as much as on the Mac (§9.10):

    1. **Ctrl-C twice, `SETTLE` apart**, written to the terminal. W0b measured a real session
       taking that and exiting 0 in 1.71s, and it is the path that matters, because claude is
       the process holding the session's state.
    2. **`TerminateJobObject`**, this platform's `killpg`. A session that started a dev server
       has it in the job too, and §5's `stop` must not leave it behind.
    3. **The terminal is closed last**, by the caller and not here. Closing it ends the child
       outright within half a second (measured, W3b), which would make step 1 a kill wearing a
       Ctrl-C's clothes.

    **The byte is a keystroke and not a signal, and that is not a detail.** §4 had it the other
    way round — that the pseudoconsole turns `` into a `CTRL_C_EVENT` — and W3e measured
    that nothing does. It is delivered to whatever is *reading the console*, like any other
    key. claude reads the console, so W0b's 1.71s is real and this path does what it claims
    for the one program the runner ever starts. `cmd`, `ping` and a python process sitting in
    `time.sleep` do not read it, and two Ctrl-Cs leave all three of them running — measured,
    all three. So step 1 reaches claude and step 2 reaches the session; there is no version of
    this in which step 1 alone ends a tree.

    **Which is why what this waits on is whether the *job* is empty and not whether `pid` has
    gone.** The Mac can ask about the pid because `killpg` addressed the whole group, so the
    polite signal already reached everything in it; here the polite byte reached one process.
    Stopping at `_reaped(pid)` would mean the *ordinary* case — claude takes the Ctrl-C, exits
    in 1.71s, and the dev server it started is still there — answers True with that server
    running under a session the phone has been told is over. The job knows what is left; the
    pid does not. `test_a_graceful_child_that_left_a_process_behind_still_loses_it` is that
    case, and it is the one this function exists for.

    **`terminal` is why this signature is wider than the Mac's**, which needs nothing but the
    pid because `killpg` and `kill` are addressed by number. Neither mechanism above is
    reachable from a pid: the Ctrl-C is a write to the terminal, and the job has been held by
    that same object since before `spawn` returned. That is the one place the Mac's shape did
    not survive the port. `session_posix.terminate` takes the argument and ignores it, so the
    seam stays one shape.

    Without a terminal there is neither, and the honest answer is a single hard kill and a line
    in the log saying so. That is `bot.py`'s caller (§6), which is ending a *runner* rather than
    a session: W4b's `stop` writes the stop marker first and it is the runner's own `terminate`
    — this function, with its terminal — that takes the tree. What must not happen there is
    §9.10's failure in Windows dress: answering True having done nothing at all, so the phone is
    told a session stopped while it is still running.
    """
    job = getattr(terminal, "job", None)
    if _settled(pid, job):
        return True

    if terminal is not None:
        for wait in (SETTLE, grace):
            if not _interrupt(terminal, pid, log):
                break                      # nothing left to write to: the job is what is left
            if _wait_for(pid, job, wait):
                return True
    else:
        log("no terminal for pid %s: no Ctrl-C to send and no job to kill" % pid)

    if job is not None:
        try:
            _win32job().TerminateJobObject(job, JOB_KILL_CODE)
        except Exception as e:
            log("could not terminate the job holding pid %s: %s" % (pid, e))
    else:
        _kill(pid, log)

    if _wait_for(pid, job, AFTER_KILL):
        return True
    log("session %s is still there after the kill: %s" % (pid, _job_pids(job)))
    return False


def _interrupt(terminal, pid, log):
    """Send one Ctrl-C. False if there was nothing to send it to, which is not a failure.

    A terminal whose child has gone raises on write (`WinptyError`, no errno — the same
    unhelpful shape `Terminal.read` treats as the end of the output), and so does one the
    caller has already closed. Either way the polite path is over and the job is next.
    """
    try:
        terminal.write(INTERRUPT)
        return True
    except Exception as e:
        log("could not send Ctrl-C to pid %s: %s" % (pid, e))
        return False


def _job_pids(job):
    """What is still running in `job`, or None if the job cannot be asked.

    None and `()` are different answers and the caller has to keep them apart: an empty tuple
    is a session that is over, and None is not knowing — which falls back to the pid, because
    a `stop` that cannot read the job is still better answered by claude's own pid than by a
    guess.
    """
    if job is None:
        return None
    try:
        return tuple(_win32job().QueryInformationJobObject(
            job, _win32job().JobObjectBasicProcessIdList))
    except Exception:
        return None


def _settled(pid, job):
    """Is there anything left of this session? The job is the authority when there is one."""
    pids = _job_pids(job)
    if pids is None:
        return _reaped(pid)
    return not pids


def _wait_for(pid, job, seconds):
    """True if the session goes within `seconds`. Polled: nothing arrives to wake this up."""
    deadline = time.time() + max(seconds, 0.0)
    while True:
        if _settled(pid, job):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(POLL)


def _kill(pid, log):
    """The one process, hard, and nothing it started. See `terminate`'s last paragraph."""
    try:
        _psutil().Process(int(pid)).kill()
    except Exception as e:
        log("could not kill pid %s: %s" % (pid, e))


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
    """Ask the runner in `directory` to stop: create the marker. Idempotent.

    `"ab"` rather than `"wb"`: opening for append creates the file if it is not there and
    touches nothing if it is, so two listeners — or one listener and the human pressing `stop`
    a second time — cannot truncate each other. Nothing is ever written to it. Existence is
    the entire message, which is also what makes it atomic for the reader: there is no
    half-written state for a runner polling at `TICK` to find.
    """
    with open(os.path.join(directory, STOP), "ab"):
        pass


def stop_requested(directory):
    """Has the listener asked this runner to stop? Polled by the runner every TICK (W3f)."""
    return os.path.exists(os.path.join(directory, STOP))


def catch_signals(handler):
    """SIGINT and CTRL_BREAK_EVENT for a hand-run console; stops otherwise arrive by marker.
    W3f.

    Two of the Mac's three are gone, and for different reasons. `SIGHUP` does not exist on
    this platform at all. `signal.SIGTERM` does exist, and registering a handler for it would
    be a claim this module cannot keep: there is no `kill(2)` here, and `os.kill(pid, SIGTERM)`
    is `TerminateProcess` — the target is ended without running anything, so a handler would
    be a promise nothing can call in. That is the whole reason `stop` is a file (§4).

    What is left is for the runner's *other* life, `python session.py --foreground` in a
    console somebody is watching — W3g's acceptance run, and every hand-debug after it. Ctrl-C
    there has to end the session the way a `stop` does, with `terminate` run and `meta.json`
    left truthful, rather than dropping a traceback over a record that still says `live`. In
    service the runner is spawned `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` (W0c) and has
    no console to be interrupted from, so in service this installs two handlers that are never
    called and the marker is what ends the session.
    """
    previous = {}
    for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue                 # SIGBREAK is Windows-only; this module still imports on the Mac
        try:
            previous[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass          # not the main thread; the caller's finally clause still cleans up
    return previous


def restore_signals(previous):
    for sig, handler in (previous or {}).items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass
