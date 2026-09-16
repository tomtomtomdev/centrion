#!/usr/bin/env python3
"""WINDOWS.md W3b — the terminal the Windows runner reads: ConPTY, through `session_win`.

The Mac's half of this is `test_session.py::TestSpawningForReal`, which forks, execs and asks
a live pty what size it is. This is the same question asked of the other mechanism, and it is
asked the same way — against real processes, not a mock of `winpty` — because every fact W3b
turns on is a fact about how ConPTY and pywinpty behave, and a mock would only repeat what
this file already believed:

- **The size has to be right before the child exists** (SPEC.md §6, WINDOWS.md §6).
  `winpty.PTY` takes `(cols, rows)` and `session.spawn` passes `(rows, cols)`, so the two are
  one transposition apart, and the transposition produces a 50-column terminal that wraps the
  link onto two rows — which `Scrape` would find as half a URL. `mode con` is the child that
  says which way round it ended up.
- **`spawn` gets an argv list and ConPTY wants a command line.** pywinpty takes the program
  and the *arguments* separately and prepends the program itself, so passing the whole argv as
  the cmdline gives the child its own path as argv[1]. Measured in W3b's probe; the test below
  is that measurement kept.
- **`read` has no `select` behind it.** It polls, so the thing to test is the timeout: an idle
  terminal must answer empty at roughly the deadline rather than blocking the pump forever.

Windows only. Every test spawns a real process and closes its terminal in a cleanup.
"""
import os
import time
import unittest

import session
from tests.support import POSIX

WIN = not POSIX
if WIN:
    import session_win

#: `cmd.exe` rather than `python.exe` on purpose: an interpreter takes about three seconds to
#: produce its first byte under a fresh ConPTY on this box, and there are ten tests here.
CMD = os.path.join(os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32", "cmd.exe")

#: Long enough for a process to start and print under ConPTY, short enough that a wedged test
#: fails rather than hangs.
PATIENCE = 20.0


def drain(terminal, seconds=PATIENCE, until=None):
    """Read until `until` appears in the text, or the child goes, or `seconds` pass.

    The shape `Runner.pump` will have in W3d, minus everything that is not reading: one read
    per tick, and the child's death is not the end of the output — the last of it is still in
    the buffer after `alive()` has gone false, which is why the loop drains once more.
    """
    out = bytearray()
    deadline = time.time() + seconds
    while time.time() < deadline:
        chunk = terminal.read(0.05)
        out += chunk
        if until is not None and until in out.decode("utf-8", "replace"):
            break
        if not chunk and not terminal.alive():
            out += terminal.read(0.05)
            break
    return out.decode("utf-8", "replace")


@unittest.skipUnless(WIN, "ConPTY: session_win.Terminal, which needs pywinpty")
class TestTheConptyTerminal(unittest.TestCase):
    """W3b: `session_win.spawn` and the `Terminal` it hands back."""

    def start(self, argv, cwd=None, env=None, rows=session.ROWS, cols=session.COLS):
        pid, terminal = session_win.spawn(argv, cwd or os.getcwd(), env or dict(os.environ),
                                          rows, cols)
        self.addCleanup(terminal.close)
        return pid, terminal

    def test_spawn_reports_size(self):
        """§6 on Windows: the child sees 200 columns by 50 rows, not the transposition."""
        _, terminal = self.start([CMD, "/c", "mode", "con"])
        text = drain(terminal, until="Code page")
        self.assertRegex(text, r"Lines:\s+%d\b" % session.ROWS, text)
        self.assertRegex(text, r"Columns:\s+%d\b" % session.COLS, text)

    def test_spawn_returns_pid_and_terminal(self):
        """The same `(pid, terminal)` shape session.py's posix `spawn` returns."""
        pid, terminal = self.start([CMD, "/c", "echo", "hello"])
        self.assertIsInstance(pid, int)
        self.assertGreater(pid, 0)
        self.assertEqual(terminal.pid, pid)
        for name in ("read", "write", "alive", "close"):
            self.assertTrue(callable(getattr(terminal, name)), name)
        self.assertIn("hello", drain(terminal, until="hello"))

    def test_read_returns_bytes(self):
        """§4: bytes on both platforms, so `pump`, `Scrape` and `Transcript` see one type."""
        _, terminal = self.start([CMD, "/c", "echo", "hello"])
        chunks = []
        deadline = time.time() + PATIENCE
        while time.time() < deadline and not any(b"hello" in c for c in chunks):
            chunk = terminal.read(0.05)
            self.assertIsInstance(chunk, bytes)
            chunks.append(chunk)
            if not chunk and not terminal.alive():
                break
        self.assertTrue(any(b"hello" in c for c in chunks), chunks)

    def test_read_timeout_returns_empty(self):
        """An idle terminal answers empty at the deadline. No `select`, so this is a poll."""
        _, terminal = self.start([CMD, "/c", "ping -n 6 127.0.0.1 >nul"])
        drain(terminal, seconds=1.0)          # let the ConPTY's own opening bytes through
        started = time.time()
        chunk = terminal.read(0.5)
        waited = time.time() - started
        self.assertEqual(chunk, b"")
        self.assertGreaterEqual(waited, 0.4)
        self.assertLess(waited, 2.0)

    def test_argv_reaches_the_child_without_its_own_zeroth_element(self):
        """pywinpty prepends the program to the command line, so `spawn` must pass argv[1:].

        Passing the whole list gives the child its own path as argv[1] — measured, and silent:
        `claude.exe C:\\...\\claude.exe --remote-control name` is a command line Claude Code
        would refuse in a way that reaches the phone as an error tail, if it reaches it at all.
        """
        _, terminal = self.start([CMD, "/c", "echo", "one", "two"])
        text = drain(terminal, until="one two")
        self.assertIn("one two", text)
        self.assertNotIn("cmd.exe", text.lower())

    def test_the_startup_query_is_answered_so_the_child_is_not_held_up(self):
        """A pseudoconsole asks the terminal what it is, and waits three seconds to be told.

        Measured in W3b, on every shape of child: 3.04 seconds from spawn to the child's first
        byte with nothing answering ConPTY's `ESC[c`, and 0.04 seconds with `Terminal`
        answering it. It is a flat three seconds on every session start — three of the
        forty-five the phone is waiting in §11's W4e checklist, and most of the gap between
        W0a's 6.2-second link and the time Claude Code actually takes.

        The threshold is two seconds because the two measurements are two orders of magnitude
        apart: this cannot fail for being a slow morning, only for the reply having stopped
        going out. The second assertion is the other half — the pseudoconsole consumes the
        reply, so it must not turn up in the child's own output as a typed line.
        """
        started = time.time()
        _, terminal = self.start([CMD, "/c", "echo", "marker"])
        text = drain(terminal, until="marker")
        waited = time.time() - started
        self.assertIn("marker", text)
        self.assertLess(waited, 2.0, "the child was held up %.2fs: ESC[c went unanswered" % waited)
        self.assertNotIn("1;0c", text, "the reply reached the child instead of the console")

    def test_write_reaches_the_child(self):
        """The input half: `Trust`'s arrow keys and §4's prompt go through this."""
        _, terminal = self.start([CMD])
        drain(terminal, seconds=3.0, until=">")
        terminal.write(b"echo centrion-w3b\r")
        self.assertIn("centrion-w3b", drain(terminal, until="centrion-w3b"))
        terminal.write(b"exit\r")

    def test_alive_is_false_once_the_child_has_gone(self):
        _, terminal = self.start([CMD, "/c", "echo", "bye"])
        self.assertTrue(terminal.alive())
        drain(terminal, until="bye")
        deadline = time.time() + PATIENCE
        while time.time() < deadline and terminal.alive():
            time.sleep(0.05)
        self.assertFalse(terminal.alive())

    def test_close_is_idempotent_and_leaves_read_answering(self):
        """`Runner.run`'s `finally` closes the terminal, and W3e closes it after a job kill:
        a second close, and a read after one, must not raise inside a cleanup path."""
        _, terminal = self.start([CMD, "/c", "echo", "bye"])
        terminal.close()
        terminal.close()
        self.assertEqual(terminal.read(0.05), b"")
        self.assertFalse(terminal.alive())

    def test_a_binary_that_is_not_there_is_an_oserror(self):
        """WINDOWS.md §4 expected ConPTY to carry an exec failure onto the pty the way the
        Mac's forked child does. It does not: `CreateProcess` fails before there is a child,
        pywinpty raises `WinptyError`, and nothing is written to the terminal at all. So the
        failure has to leave `spawn` as an error, and it is an `OSError` — the class the
        callers of a spawn already catch, and not a pywinpty-shaped exception that reaches
        `Runner.run` as a traceback and leaves meta.json saying `starting`.
        """
        missing = os.path.join(os.getcwd(), "no-such-binary-w3b.exe")
        with self.assertRaises(OSError) as caught:
            session_win.spawn([missing, "--flag"], os.getcwd(), dict(os.environ),
                              session.ROWS, session.COLS)
        self.assertIn("no-such-binary-w3b.exe", str(caught.exception))


@unittest.skipUnless(WIN, "the environment block is a Windows argument shape")
class TestTheEnvironmentBlock(unittest.TestCase):
    """`child_env()` is a dict; `CreateProcess` wants one NUL-separated string."""

    def test_the_block_is_nul_separated_and_nul_terminated(self):
        block = session_win.environment_block({"A": "1", "B": "2"})
        self.assertTrue(block.endswith("\0"), repr(block))
        self.assertEqual(sorted(p for p in block.split("\0") if p), ["A=1", "B=2"])

    def test_the_child_gets_exactly_what_it_was_given(self):
        env = {"SYSTEMROOT": os.environ["SYSTEMROOT"], "CENTRION_W3B": "yes"}
        pid, terminal = session_win.spawn([CMD, "/c", "echo", "[%CENTRION_W3B%]"],
                                          os.getcwd(), env, session.ROWS, session.COLS)
        self.addCleanup(terminal.close)
        self.assertIn("[yes]", drain(terminal, until="[yes]"))


if __name__ == "__main__":
    unittest.main()
