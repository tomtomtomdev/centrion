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
import json
import os
import shutil
import sys
import tempfile
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


#: A prompt shaped like the worst thing a phone can send, and every character in it is there
#: for a reason: `&` `|` `<` `>` chain, pipe and redirect in `cmd`; `^` is its escape; `%PATH%`
#: is a variable it would expand; the quote in the middle is what `list2cmdline` has to escape;
#: and the trailing backslash is the classic way to get a closing quote eaten and the next
#: argument swallowed with it. None of it should mean anything, because `CreateProcess` is not
#: a shell — but on Windows the argv list is joined into one string and split again by the
#: child, and that round trip is the only thing standing between a phone and this machine.
#: `{out}` is filled in with a path the test then asserts was never created.
HOSTILE = 'fix "the" probe & echo pwned > {out} | find ^ 50% %PATH% C:\\dir\\'

#: A prompt in the language the phone is actually held in. The command line is UTF-16 all the
#: way down on Windows, so this should be a non-event; it is here because "should be" is what
#: this whole document keeps turning out to be wrong about.
NON_ASCII = "réparer la sonde — 100 % \u2713"

#: Writes its own argv to the file named by its first argument, and renames it into place, so
#: a test that polls for the path never reads a half-written one. Deliberately *not* read back
#: through the terminal: what is under test is the argv the child parsed, and a renderer at 200
#: columns between the assertion and the answer is a second thing that can be wrong.
ARGV_STUB = (
    "import json, os, sys\n"
    "with open(sys.argv[1] + '.tmp', 'w', encoding='utf-8') as fh:\n"
    "    json.dump(sys.argv[2:], fh)\n"
    "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
)


@unittest.skipUnless(WIN, "list2cmdline into CreateProcess: the Windows quoting round trip")
class TestTheCommandLineTheChildParsesBack(unittest.TestCase):
    """W3c: an argv list survives being flattened into a command line and split up again.

    The Mac never has this problem. `session_posix.spawn` hands `execve` a list of strings and
    the kernel hands the child the same list; there is no string in between and nothing to
    quote for. ConPTY takes a command line, so on Windows every argument is joined by
    `subprocess.list2cmdline` and taken apart again by the child's own parser, and the two have
    to agree — about quotes, about backslashes before quotes, and about where one argument
    stops. §10's threat model is that `--prompt` arrives from a phone, so the agreement is not
    a tidiness question.

    The child is a real interpreter reporting a real `sys.argv`, because the round trip is the
    thing under test and a test that asserted what `list2cmdline` returned would only be
    checking that the stdlib is the stdlib.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="centrion-w3c-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stub = os.path.join(self.tmp, "argv stub.py")   # a space, because paths have them
        with open(self.stub, "w", encoding="utf-8") as fh:
            fh.write(ARGV_STUB)
        self.out = os.path.join(self.tmp, "argv.json")

    def argv_of_a_child_given(self, *args):
        """Spawn the stub through `session_win.spawn` and return the `sys.argv[1:]` it saw."""
        pid, terminal = session_win.spawn([sys.executable, self.stub, self.out] + list(args),
                                          self.tmp, dict(os.environ),
                                          session.ROWS, session.COLS)
        self.addCleanup(terminal.close)
        self.assertGreater(pid, 0)
        deadline = time.time() + PATIENCE
        while time.time() < deadline and not os.path.exists(self.out):
            time.sleep(0.02)
        self.assertTrue(os.path.exists(self.out),
                        "the child never wrote its argv: %s" % drain(terminal, seconds=0.5))
        with open(self.out, encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_hostile_argument_arrives_as_one_argument_and_means_nothing(self):
        """§10: the prompt comes off a phone, and here it goes through a string.

        Two assertions, and the second is the one that matters. The first says the text
        survived; the second says none of it *ran* — `> pwned.txt` would be a file in the temp
        directory if a shell had ever seen this, and `CreateProcess` is not a shell.
        """
        pwned = os.path.join(self.tmp, "pwned.txt")
        hostile = HOSTILE.replace("{out}", pwned)
        self.assertEqual(self.argv_of_a_child_given("--prompt", hostile),
                         ["--prompt", hostile])
        self.assertFalse(os.path.exists(pwned), "a redirect in the prompt was carried out")

    def test_the_arguments_keep_their_boundaries(self):
        """The failure this is really about is not mangled text, it is a *different count*.

        A backslash before the closing quote eats it, the next argument joins this one, and
        `--cwd` ends up holding a project name — which is `config.resolve`'s whole subject
        arriving pre-broken. Every string below is a shape `list2cmdline` treats differently:
        an embedded space, an empty argument, a bare quote, one and two trailing backslashes.
        """
        awkward = ["a b", "", '"', "\\", "two\\\\", 'ends with "a quote"', "--flag=a b\\"]
        self.assertEqual(self.argv_of_a_child_given(*awkward), awkward)

    def test_a_non_ascii_argument_is_not_mangled(self):
        """A Windows command line is UTF-16; nothing here should narrow it to a code page."""
        self.assertEqual(self.argv_of_a_child_given(NON_ASCII), [NON_ASCII])

    def test_a_program_path_with_a_space_is_still_one_program(self):
        """The one piece of quoting this code does not do itself.

        pywinpty prepends the program to the command line and quotes it (W3b measured that it
        does); `spawn` passes `argv[1:]` on that promise. If the quoting ever stops, a
        `claude_bin` under `C:\\Program Files\\` becomes the program `C:\\Program` with
        `Files\\...` as its first argument — and the only symptom on the phone is a session
        that never comes up.
        """
        room = os.path.join(self.tmp, "a directory with spaces")
        os.makedirs(room)
        spaced = os.path.join(room, "my cmd.exe")
        shutil.copy(CMD, spaced)
        _, terminal = session_win.spawn([spaced, "/c", "echo", "spaced"], self.tmp,
                                        dict(os.environ), session.ROWS, session.COLS)
        self.addCleanup(terminal.close)
        self.assertIn("spaced", drain(terminal, until="spaced"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(WIN, "ConPTY: drives Runner.pump over a real pseudoconsole")
class TestThePumpOverAConpty(unittest.TestCase):
    """WINDOWS.md W3d — the runner's read loop, on this box, over a real child.

    `TestPumpOverATerminal` in `test_session.py` drives the same loop over a scripted fake and
    is where its branches are pinned. This is the one thing that fake cannot be asked: whether
    a real ConPTY, with its DA1 handshake and its own redraw of whatever the child wrote,
    actually ends up producing `live` with a URL in `meta.json`. Everything between `spawn`
    and the record, on the mechanism, once.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions = os.path.join(self.tmp, "sessions")

    def runner(self, **kw):
        kw.setdefault("project", "beacon")
        r = session.Runner("3f2a91", self.tmp, "beacon-3f2a", root=self.sessions,
                           argv=[CMD, "/c", "rem"], log=lambda m: None, **kw)
        r.begin()
        return r

    def pump(self, argv, **kw):
        r = self.runner(**kw)
        _, terminal = session_win.spawn(argv, self.tmp, dict(os.environ),
                                        session.ROWS, session.COLS)
        self.addCleanup(terminal.close)
        transcript = session.Transcript(os.path.join(r.dir, session.TRANSCRIPT),
                                        log=lambda m: None)
        try:
            r.pump(terminal, transcript)
        finally:
            transcript.close()
        return r

    def test_pump_real_echo(self):
        """The slice's named test: a link off a real pseudoconsole makes the session live.

        `echo` rather than an interpreter for the reason at the top of this file — and because
        what is under test is the loop and the scrape, not what printed the URL. ConPTY still
        re-renders it, which is the part no fake covers.
        """
        url = "https://claude.ai/code/session_w3d_test"
        r = self.pump([CMD, "/c", "echo", url])
        self.assertEqual(r.scrape.url, url)
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["state"], session.LIVE)
        self.assertEqual(meta["url"], url)

    def test_the_pump_returns_when_the_child_goes(self):
        """It has to come back by itself: nothing else ends a session that ended on its own,
        and a pump that sat on a dead ConPTY would hold `live` forever."""
        began = time.time()
        r = self.pump([CMD, "/c", "echo", "done"])
        self.assertLess(time.time() - began, PATIENCE, "the pump outlived its child")
        with open(os.path.join(r.dir, session.TRANSCRIPT), "rb") as fh:
            self.assertIn(b"done", fh.read())

    def test_the_transcript_holds_what_the_child_printed(self):
        """§4.6's tail is read off this file, and on a `failed` session it is all the phone
        gets. What ConPTY renders is not what the child wrote, but the text has to survive."""
        r = self.pump([CMD, "/c", "echo", "centrion-w3d-marker"])
        with open(os.path.join(r.dir, session.TRANSCRIPT), "rb") as fh:
            self.assertIn(b"centrion-w3d-marker", fh.read())
