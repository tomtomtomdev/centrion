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
    """`tasklist` says whether a pid is there. No psutil yet — that is W4a."""
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


if __name__ == "__main__":
    unittest.main()
