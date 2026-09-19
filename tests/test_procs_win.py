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
import subprocess
import sys
import textwrap
import time
import unittest

WIN = sys.platform == "win32"

#: WINDOWS.md §6: how the listener starts a runner. **Not** `CREATE_BREAKAWAY_FROM_JOB`: W0c
#: (2026-09-14) found that Task Scheduler runs the task in a job whose LimitFlags are 0 — no
#: BREAKAWAY_OK — so that flag makes `CreateProcess` fail with "Access is denied", and the
#: runner never starts. It is also unnecessary: `Stop-ScheduledTask` terminated the parent and
#: left both a plain child and a detached child running. The two flags that remain are about
#: not sharing the listener's console and not receiving its Ctrl-C, not about survival.
DETACH_FLAGS = (getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
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
        `Sessions.alive`, but `procs` is a seam and the two sides have to answer alike."""
        import session
        self.assertFalse(session.procs.alive(2 ** 62))
        for bad in ("4242", None, 2 ** 62, -1):
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

        Asserted as "no later than boot" rather than as `0.0` so that a psutil which starts
        returning the real boot time for these two pids passes: that would be a better answer
        to the same question, and bot.py cannot tell the two apart.
        """
        import psutil
        import session
        self.assertTrue(session.procs.alive(4))
        began = session.procs.started(4)
        self.assertIsNotNone(began)
        self.assertLessEqual(began, psutil.boot_time())

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


if __name__ == "__main__":
    unittest.main()
