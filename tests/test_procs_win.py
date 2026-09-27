#!/usr/bin/env python3
"""WINDOWS.md — the Windows process mechanisms, tested against real processes.

SPEC.md §2 has one non-negotiable: the runner outlives the listener. On the Mac that is
`setsid()`, verified in slice 7 with a control. On Windows it is a set of `CreateProcess`
flags on the *listener's* side, and whether they work depends on who started the listener —
a console, or Task Scheduler with a job of its own. This file holds the console half; the
scheduled-task half is a hand-run recorded in WINDOWS.md §11 (W0c, W5c), because a unit test
cannot register a task and log the user out.

Windows only. Everything here spawns real processes and cleans up after itself.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

# Imports on either platform — session_win's top level is stdlib only, and the win32 calls are
# inside the functions that make them — so this is safe above the WIN guards below.
import session_win

WIN = sys.platform == "win32"

#: WINDOWS.md §6: how the listener starts a runner. **Not** `CREATE_BREAKAWAY_FROM_JOB`: W0c
#: (2026-09-14) found that Task Scheduler runs the task in a job whose LimitFlags are 0 — no
#: BREAKAWAY_OK — so that flag makes `CreateProcess` fail with "Access is denied", and the
#: runner never starts. It is also unnecessary: `Stop-ScheduledTask` terminated the parent and
#: left both a plain child and a detached child running. The two flags that remain are about
#: not sharing the listener's console and not receiving its Ctrl-C, not about survival.
#:
#: **Taken from the program, not spelled again here**, and W4c is why. Until that slice this
#: line was its own copy of the same two `getattr`s, which meant every test below — the
#: breakaway assertion and the real-process survival test that spawns `PARENT % DETACH_FLAGS`
#: — was evidence about a lookalike constant in this file. W4c mutated `session_win`'s
#: `DETACH_FLAGS` to add breakaway back and mutated `spawn_flags()` to return `{}`, and all
#: 611 tests stayed green through both. A test that reimplements the value it is checking
#: cannot fail.
DETACH_FLAGS = session_win.DETACH_FLAGS
BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)


def pid_alive(pid):
    """`tasklist` says whether a pid is there.

    W4a gave `session_win` a `psutil.pid_exists` and this stayed a subprocess, on purpose: the
    tests below are about whether a *detached child survives*, and answering that with the
    same library the program uses would verify one mechanism with another one that has not
    been checked either. `tasklist` is the outside opinion. The class W4a added at the end of
    this file is where `procs.alive` itself is on trial, and it uses psutil.
    """
    out = subprocess.check_output(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                  stderr=subprocess.DEVNULL, text=True)
    return str(pid) in out


def kill(pid):
    subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


#: A parent that spawns a detached grandchild, prints the grandchild's pid, and exits.
#: The grandchild sleeps for a minute so the test can see it after the parent is gone.
PARENT = textwrap.dedent("""
    import subprocess, sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=%d)
    print(child.pid, flush=True)
""")


@unittest.skipUnless(WIN, "Windows process flags")
class TestADetachedChildOutlivesItsParent(unittest.TestCase):
    """The console half of WINDOWS.md W0c / W4c.

    This is §2's setsid experiment. The parent is what `Sessions.start` will be; the
    grandchild is the runner. If this fails from a console, nothing downstream can work;
    if it passes here and fails under Task Scheduler, that is the job-object case §6's
    fallback exists for, and the hand-run in §11 is what tells the two apart.
    """

    def test_detached_child_survives_parent_exit(self):
        parent = subprocess.run([sys.executable, "-c", PARENT % DETACH_FLAGS],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(parent.returncode, 0, parent.stderr)
        grandchild = int(parent.stdout.strip())
        self.addCleanup(kill, grandchild)

        # The parent has exited (run() returned). Give the OS a moment to tear down anything
        # it was going to tear down, then ask.
        time.sleep(2.0)
        self.assertTrue(pid_alive(grandchild),
                        "grandchild %d died with its parent — the detach flags do not work "
                        "from this context" % grandchild)

    def test_the_control_survives_too(self):
        """Without any flags the grandchild *also* survives — Windows has no SIGHUP — so the
        flags are not what keeps the runner alive. W0c showed the same under Task Scheduler:
        `Stop-ScheduledTask` ended the parent and left a flagless child running. What the
        flags buy is a runner with no console of its own to share and no Ctrl-C to inherit.
        This test records the control so the one above is not read as proving more than it
        does."""
        parent = subprocess.run([sys.executable, "-c", PARENT % 0],
                                capture_output=True, text=True, timeout=30)
        grandchild = int(parent.stdout.strip())
        self.addCleanup(kill, grandchild)
        time.sleep(1.0)
        self.assertTrue(pid_alive(grandchild))

    def test_breakaway_is_not_among_the_flags(self):
        """The one flag that *looks* like the answer is the one that fails under the
        supervisor this bot runs under. Pinned here so it does not come back with a
        plausible commit message."""
        self.assertNotEqual(BREAKAWAY, 0, "the constant should exist on this Python")
        self.assertFalse(DETACH_FLAGS & BREAKAWAY)

    def test_both_detach_flags_are_actually_set(self):
        """The vacuity guard on the assertion above, and W4c's reason for adding it.

        `DETACH_FLAGS & BREAKAWAY` is falsey for an empty `DETACH_FLAGS` too, so on its own
        that test is satisfied by a constant that has lost both of the flags it exists to
        carry — which is a runner sharing the listener's console and inheriting its Ctrl-C,
        the exact failure W4c is about. Named rather than recomputed: this asserts the two
        flags are *present*, where the line at the top of this file asserts nothing.
        """
        self.assertTrue(DETACH_FLAGS & subprocess.DETACHED_PROCESS,
                        "the runner would share the listener's console")
        self.assertTrue(DETACH_FLAGS & subprocess.CREATE_NEW_PROCESS_GROUP,
                        "the runner would take the listener's Ctrl-C with it")

    def test_spawn_flags_hands_popen_the_detach_flags(self):
        """`Sessions.start` spreads this dict straight into `Popen`, so it is the last place
        the flags can go missing — and until W4c nothing on either platform looked at what
        the Windows one returns. `test_session.py` asserts `session_posix.spawn_flags() == {}`
        and there was no twin for this side, so `return {}` here was caught by nothing.
        """
        self.assertEqual(session_win.spawn_flags(), {"creationflags": DETACH_FLAGS})


@unittest.skipUnless(WIN, "psutil against real Windows processes")
class TestWhetherARunnerIsStillThereOnWindows(unittest.TestCase):
    """The Windows half of `test_bot.py::TestWhetherARunnerIsStillThere`. WINDOWS.md §6, W4a.

    That class is `@posix_only` because every one of its mechanisms is the Mac's: `/bin/sleep`,
    `/usr/bin/true`, `kill(pid, 0)`, `EPERM`, `ps lstart`. The *properties* it asserts are not
    the Mac's, and they are what `ls`, `stop` and the reconciliation pass are built on — so
    they are tested three ways now. The branches bot.py takes without asking the platform have
    portable twins over a fake `procs` (`TestTheListenerUsesThePlatformSeam`); the two that are
    the platform have this class, against real processes, and the Mac's original against its.

    §6 is explicit that this guard does *more* work here than on the Mac: Windows hands a freed
    pid back within seconds, where macOS walks a 99999-wide range before wrapping. So the
    reused-pid case below is the one to read if this file ever goes red.
    """

    def child(self):
        """A process that will be there for the length of a test, and is cleaned up after.

        Holding the `Popen` also holds the process handle, which is what stops Windows handing
        the pid to somebody else while a test is still asking about it.
        """
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    def exited(self):
        """A pid whose process has run and finished. The Windows `/usr/bin/true`."""
        p = subprocess.Popen([sys.executable, "-c", ""], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.wait()
        return p.pid

    def test_alive_true_for_running_false_after_exit(self):
        import session
        self.assertTrue(session.procs.alive(self.child().pid))
        self.assertFalse(session.procs.alive(self.exited()))

    def test_started_matches_create_time(self):
        import psutil
        import session
        before = time.time()
        p = self.child()
        after = time.time()
        began = session.procs.started(p.pid)
        self.assertIsNotNone(began)
        self.assertEqual(began, psutil.Process(p.pid).create_time())
        # Not only self-consistent: it has to be in epoch seconds on the same clock the
        # record's `started` is written from, or PID_REUSE_SLACK compares two calendars.
        self.assertGreaterEqual(began, before - 2)
        self.assertLessEqual(began, after + 2)

    def test_a_pid_that_is_not_there_has_no_start_time(self):
        import session
        self.assertIsNone(session.procs.started(self.exited()))

    def test_a_pid_that_is_not_one_is_answered_rather_than_raised(self):
        """Parity with `session_posix`, which catches `OverflowError` in `alive` and guards
        `started` with an `int()`. The listener's type check means neither is reachable from
        `Sessions.alive`, but `procs` is a seam and the two sides have to answer alike.

        `"4242"` used to be the first of these and it was never a case either side promised
        (WINDOWS.md W4f). Both guards are `int(pid)`, and `int("4242")` is 4242 — so a numeric
        string is a *pid*, not a corrupt record, and what `started` answers for it is whatever
        the platform says about that pid. It passed for a year because pid 4242 happened to be
        free on this box; the day something took it, the assertion turned red about the
        machine rather than about the code. `"nope"` is the property that was meant: a pid the
        seam cannot read at all, which raises `ValueError` inside the guard on both platforms
        and comes back as `None` rather than as a traceback out of `Sessions.alive`.
        """
        import session
        self.assertFalse(session.procs.alive(2 ** 62))
        for bad in ("nope", None, 2 ** 62, -1):
            self.assertIsNone(session.procs.started(bad), repr(bad))

    def test_the_system_process_is_there_and_dates_from_before_every_record(self):
        """§6's Windows shape of `EPERM is not death`, and the shape is not the one it predicted.

        Pid 4 is System: it exists and it is not ours, so `alive` has to say it is there — the
        twin of the Mac asserting pid 1 is alive. What §6 expected of `started` was
        `AccessDenied`, and W4a measured otherwise: psutil answers **0.0** for pids 0 and 4,
        which is the epoch and not the boot time. It is therefore not `None`, so §4's `began is
        None` branch never sees it, and `0.0 <= record["started"] + PID_REUSE_SLACK` is true of
        every record that could ever exist — a corrupt record naming pid 4 reads as a live
        runner. The Mac has the same property for pid 1 by a different route (launchd's real
        start time is boot, which is older than any record), which is why this is recorded as
        a shared shape rather than patched here.

        Asserted as "no later than every record" rather than as `0.0` so that a psutil which
        starts returning a real time for these two pids passes: that would be a better answer
        to the same question, and bot.py cannot tell the two apart.

        **The yardstick is this process, not `psutil.boot_time()`, and W6 is why.** A GitHub
        Windows runner does answer a real `create_time` for pid 4 — the 0.0 above is this
        box's answer and not the platform's — and it lands **1.78s after** `boot_time()`,
        which fails an assertion that means to say "at boot". The two numbers come from
        different clocks: `boot_time` is derived from the tick count and `create_time` from
        `GetProcessTimes`, so a second or two of disagreement is the normal case and not a
        finding. `create_time` of *this* process is the same clock as pid 4's and is the
        thing the sentence actually means — every record was written by a runner this
        listener started, so nothing in `var/sessions` can predate the interpreter reading
        it. It is also the stricter of the two, by however long this box has been up.
        """
        import psutil
        import session
        self.assertTrue(session.procs.alive(4))
        began = session.procs.started(4)
        self.assertIsNotNone(began)
        self.assertLessEqual(began, psutil.Process(os.getpid()).create_time())

    def test_nothing_on_this_box_refuses_to_say_when_it_started(self):
        """The measurement behind the sentence above, kept as a test. W4a.

        `create_time` needs only `PROCESS_QUERY_LIMITED_INFORMATION`, which this account has
        for every process on the machine — 0 of 208 raised `AccessDenied` at W4a, including
        the 109 whose *owner* psutil could not read. So the `AccessDenied` handler in
        `session_win.started` is insurance rather than a path anything exercises, and this is
        what would notice if that stopped being true: a Windows or psutil release that tightens
        the access check turns a whole class of pid into "cannot date it", and §4's `began is
        None` branch — which would then be load-bearing — has never run outside a fake.
        """
        import psutil
        import session
        denied = [p.pid for p in psutil.process_iter()
                  if session.procs.started(p.pid) is None and psutil.pid_exists(p.pid)]
        self.assertEqual(denied, [])

    def test_the_listener_trusts_a_live_pid_and_refuses_a_reused_one(self):
        """End to end through `Sessions.alive`, which is the only caller either function has.

        The three cases the Mac's class covers with `/bin/sleep`: a runner that is there, a
        record that predates the process at its pid, and a record with no `started` at all.
        """
        import bot
        sessions = bot.Sessions(root=os.path.join(os.environ["TEMP"], "centrion-w4a"),
                                log=lambda *_: None)
        p = self.child()
        self.assertTrue(sessions.alive({"runner_pid": p.pid, "started": int(time.time())}))
        self.assertFalse(sessions.alive({"runner_pid": p.pid, "started": 1}))
        self.assertTrue(sessions.alive({"runner_pid": p.pid}))
        self.assertFalse(sessions.alive({"runner_pid": self.exited(),
                                         "started": int(time.time())}))


#: A process that takes the lock, says whether it got it, and then stays alive until it is
#: killed. The point of the second test below is what the *kernel* does when this process
#: dies without ever releasing anything — which is the whole reason §3 chose a mutex over a
#: lock file: there is no stale lock to clear because there is nothing on disk to go stale.
HOLDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, %(root)r)
    import session_win
    print(session_win.Lock(%(path)r).take(), flush=True)
    time.sleep(60)
""")


@unittest.skipUnless(WIN, "the single-instance lock is a named mutex here")
class TestTheSingleInstanceMutex(unittest.TestCase):
    """WINDOWS.md §6 and §3's Lock row, W4d — the last of the seam's NotImplementedErrors.

    On the Mac this is `launchd/bot.sh`'s `lockf` on fd 9, taken by the shell before python
    starts, and `session_posix.Lock.take()` is `return True` because there is nothing left for
    the process to do. There is no shell here: `bot.cmd` (§7) is a loop around `--serve`, so
    the listener has to hold its own lock, and it has to be one the kernel releases when the
    holder dies — a listener killed by Task Scheduler or Ctrl-C must not leave a lock behind
    that the next `--serve` cannot take.
    """

    def path(self):
        """A lock file of this test's own. The mutex is named after the path, so two tests
        that share one would be asking about the same kernel object."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return os.path.join(d, ".bot.lock")

    def test_second_lock_refused(self):
        """§7's 409 is mutual: the second listener must lose before its first getUpdates."""
        path = self.path()
        self.assertTrue(session_win.Lock(path).take(), "the first copy was refused")
        self.assertFalse(session_win.Lock(path).take(), "the second copy was let in")
        # Named after the path, not global: two checkouts of this bot are two bots, and one
        # must not lock the other out. (`bot.LOCK` is under the checkout — §6.)
        self.assertTrue(session_win.Lock(self.path()).take(),
                        "a different lock file was refused by an unrelated listener's mutex")

    def test_lock_released_on_owner_death(self):
        """The property that makes this a lock and not a flag: the kernel is the release.

        A held mutex whose owner is terminated goes away with the owner's last handle, so the
        next listener gets it immediately. Two things this catches that `test_second_lock_
        refused` cannot: a `take()` that leaks the handle it opened on a *refusal* (our own
        failed attempt would then keep the object alive after the holder is gone, and the
        bot would be locked out for the life of the machine), and an implementation that
        writes something to disk and checks that instead.
        """
        path = self.path()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        holder = subprocess.Popen([sys.executable, "-c", HOLDER % {"root": root, "path": path}],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(holder.stderr.close)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(kill, holder.pid)
        said = holder.stdout.readline().strip()
        if said != "True":
            # Killed first, and the order is the point: `stderr.read()` waits for the pipe to
            # close, which is the holder exiting. Written as an `assertEqual` message it is
            # evaluated whether the assertion fails or not — sixty seconds of waiting for the
            # process this test needs alive, after which the mutex is released and the next
            # line reads "another listener was let in". Found the hard way in W4d.
            kill(holder.pid)
            self.fail("the holder did not take the lock: %r %s" % (said, holder.stderr.read()))
        self.assertFalse(session_win.Lock(path).take(),
                         "let in while another process holds the lock")

        kill(holder.pid)
        holder.wait(timeout=10)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if session_win.Lock(path).take():
                return
            time.sleep(0.02)
        self.fail("the lock survived its owner by more than a second")


if __name__ == "__main__":
    unittest.main()
