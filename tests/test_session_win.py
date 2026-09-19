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
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

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

    def test_the_environment_the_runner_really_passes_can_find_a_program(self):
        """W3g, and the reason this test exists is that the suite could not see W3b's bug.

        `start` above hands `dict(os.environ)` to every other test in this file, because they
        are about ConPTY and not about the environment. The runner does not: it passes
        `session.child_env()`, and through W3b that was still the Mac's — `/usr/bin:/bin` on a
        box with no such directories — so the test above passed with 200 columns while the hand
        -run of the *same command* answered `'mode' is not recognized`. The only difference
        between them was the one thing neither of them tested.

        So this is `test_spawn_reports_size` asked again through the real environment, and it
        is the cheapest guard there is against a `PATH` regression that the rest of the file is
        constructed not to notice.
        """
        pid, terminal = session_win.spawn([CMD, "/c", "mode", "con"], os.getcwd(),
                                          session.child_env(), session.ROWS, session.COLS)
        self.addCleanup(terminal.close)
        text = drain(terminal, until="Code page")
        self.assertNotIn("not recognized", text, "child_env handed the child an unusable PATH")
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


#: A child that takes §4's Ctrl-C the way claude does — by *reading the console*, which is what
#: W3e found the byte actually reaches. It says it caught the interrupt and exits 0 of its own
#: accord, which is what W0b measured the real thing doing: two `\x03` 0.4s apart, exit status 0
#: after 1.71s. `msvcrt.getwch` rather than `sys.stdin`, because that is exactly the difference
#: — a console read gets the byte, and `sys.stdin.buffer.read(1)` gets EOF straight away
#: (measured in W3e's probe, `scratch/w3e_probe.py`).
TUI = (
    "import msvcrt, sys\n"
    "print('ready', flush=True)\n"
    "while True:\n"
    "    if msvcrt.getwch() == '\\x03':\n"
    "        print('caught', flush=True)\n"
    "        sys.exit(0)\n"
)

#: The same, plus the thing a session leaves behind: a `ping` started before it goes, which
#: keeps running after the Ctrl-C has been taken and obeyed. This is the ordinary shape of a
#: real session — claude, and whatever it was asked to start — and the reason `terminate` waits
#: on the job being empty rather than on claude's pid having gone.
TUI_WITH_A_CHILD = (
    "import msvcrt, os, subprocess, sys\n"
    "child = subprocess.Popen(['ping', '-n', '100', 'localhost'],\n"
    "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "print('ready %d %d' % (os.getpid(), child.pid), flush=True)\n"
    "while True:\n"
    "    if msvcrt.getwch() == '\\x03':\n"
    "        print('caught', flush=True)\n"
    "        sys.exit(0)\n"
)

#: A tree that never sees the Ctrl-C — which, after W3e's measurement, is not the exotic case
#: but the default one: a program that is not reading its console never gets the byte, and that
#: is every build, every dev server and every `ping`. This one also starts a second copy of
#: itself, so what has to be reached includes a process that was never on the terminal at all.
#: Given an argument it is the parent and prints both pids; otherwise it is the child and sits.
DEAF = (
    "import os, subprocess, sys, time\n"
    "if len(sys.argv) > 1:\n"
    "    child = subprocess.Popen([sys.executable, sys.argv[0]])\n"
    "    print('deaf %d %d' % (os.getpid(), child.pid), flush=True)\n"
    "while True:\n"
    "    time.sleep(0.05)\n"
)

#: A session that starts something of its own and then sits — the same shape as
#: `TUI_WITH_A_CHILD`, minus the console reading, because the test that uses it never sends a
#: Ctrl-C. Its job is to exist in two generations when the runner above it is shot.
LEAVES_A_CHILD = (
    "import os, subprocess, sys, time\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],\n"
    "                     creationflags=0x00000008)\n"
    "print('up %d %d' % (os.getpid(), g.pid), flush=True)\n"
    "time.sleep(300)\n"
)

#: A runner that dies without warning: everything `Runner.run` does up to the pump, and then
#: nothing at all. It is a separate process rather than a fake because the fact under test is
#: what Windows does when a *process* dies holding a job handle and a pseudoconsole, and no
#: amount of arranging objects in this one can answer that. Argv: pidfile, project root to
#: import `session_win` from, the script to run as the session.
RUNNER_THAT_DIES = (
    "import os, sys, time\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "import session, session_win\n"
    "pid, terminal = session_win.spawn([sys.executable, sys.argv[3]],\n"
    "                                  os.path.dirname(sys.argv[1]), dict(os.environ),\n"
    "                                  session.ROWS, session.COLS)\n"
    "text = ''\n"
    "deadline = time.time() + 30\n"
    "while 'up ' not in text and time.time() < deadline:\n"
    "    text += terminal.read(0.2).decode('utf-8', 'replace')\n"
    "with open(sys.argv[1], 'w') as fh:\n"
    "    fh.write(text[text.index('up '):].split('\\n')[0] if 'up ' in text else 'never')\n"
    "while True:\n"
    "    terminal.read(0.5)\n"
)

#: The slice's own tree, from WINDOWS.md W3e: one `ping` `start`ed so that it outlives the
#: `cmd` that launched it, and one in the foreground so that `cmd` is still there to be
#: terminated. A hundred pings is a hundred seconds, which is long enough that anything still
#: alive at the end of a test is alive because nothing killed it.
TREE = "start /b ping -n 100 localhost & ping -n 100 localhost"

#: How long to wait for a process to actually go after the thing that kills it has returned.
#: `TerminateJobObject` starts the termination and does not wait for it, so an assertion made
#: the instant it returns is asserting against a race rather than against the kill.
SETTLED = 3.0


def gone(pid, seconds=SETTLED):
    """Is `pid` no longer there? Polled, because a kill is not synchronous. Windows only."""
    import psutil
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.05)
    return not psutil.pid_exists(pid)


def children_of(pid):
    import psutil
    try:
        return [c.pid for c in psutil.Process(pid).children(recursive=True)]
    except Exception:                      # NoSuchProcess, AccessDenied: no children to see
        return []


def wait_for_children(pid, count, seconds=PATIENCE):
    """The pids under `pid` once there are `count` of them, or whatever there are at the end."""
    deadline = time.time() + seconds
    found = []
    while time.time() < deadline:
        found = children_of(pid)
        if len(found) >= count:
            break
        time.sleep(0.05)
    return found


def reap(pid):
    """Leave no tree behind. `/T /F` because a test that failed to kill its own tree must not
    hand the next one a box with four `ping`s on it."""
    subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipUnless(WIN, "Job Objects and ConPTY Ctrl-C: session_win.terminate")
class TestEndingTheSessionAndItsTree(unittest.TestCase):
    """WINDOWS.md W3e — `terminate`, and the Job Object that makes it cover the whole tree.

    The Mac ends a session by signalling its *process group*: the child ran `setsid()`, so
    everything it started is in that group and `killpg` reaches all of it (§9.10). Windows has
    no process group in that sense — `CREATE_NEW_PROCESS_GROUP` exists, but a grandchild is
    not obliged to stay in one and `start` is the ordinary way a session leaves it. A Job
    Object is the equivalent that does hold: a process assigned to one cannot escape, neither
    can anything it spawns, and `TerminateJobObject` is this platform's `killpg`.

    Three claims, and each of them is a way the Mac's version silently does nothing here:

    - **Graceful first.** A `stop` on a live session is a Ctrl-C and not a kill, because claude
      writes state out on its way down. The job is the fallback, and W0b measured the polite
      path working on the real thing — two `\\x03` 0.4s apart, exit 0 after 1.71s.
    - **The job holds the whole tree**, including a process that has left the console behind.
      A stop that ends only the child leaves a dev server running under a session the phone
      has been told is gone.
    - **The job is created before the child, and the child is assigned before `spawn` returns**
      (§4: *the order is the point*). A process not yet in the job when the job is killed
      survives the kill, and the window for that is exactly the spawn — the Windows twin of the
      Mac's TIOCSCTTY window in `session_posix.terminate`'s docstring.

    Real processes throughout, and every test cleans up after itself with `taskkill /T /F`.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="centrion-w3e-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.notes = []

    def script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def start(self, argv):
        pid, terminal = session_win.spawn(argv, self.tmp, dict(os.environ),
                                          session.ROWS, session.COLS)
        self.addCleanup(terminal.close)
        self.addCleanup(reap, pid)
        return pid, terminal

    def end(self, pid, terminal, grace=session.GRACE):
        """`terminate` as `Runner.run` calls it, with the log kept for the failure message."""
        return session_win.terminate(pid, grace, self.notes.append, terminal)

    def test_terminate_kills_tree(self):
        """The slice's named test: the tree §4's job exists for, gone after one `terminate`.

        The children are collected *before* the terminate and asserted after, because their
        pids are the only evidence that survives the thing under test — asking afterwards what
        children the session had would be asking a process that is no longer there.
        """
        pid, terminal = self.start([CMD, "/c", TREE])
        tree = wait_for_children(pid, 2)
        self.assertEqual(len(tree), 2, "the tree never came up: %r" % (tree,))
        self.assertTrue(self.end(pid, terminal, grace=0.5), self.notes)
        for child in [pid] + tree:
            self.assertTrue(gone(child),
                            "pid %d outlived the session (%r)" % (child, self.notes))

    def test_terminate_graceful_first(self):
        """The slice's named test: a child that handles the interrupt gets to handle it.

        The child is a console reader, because that is what the byte reaches and what claude
        is. Three assertions, and all of them are about the job *not* being what ended this.
        The output says the child saw the Ctrl-C and ran its own shutdown path. The exit status
        says it chose its own — `TerminateJobObject` forces `JOB_KILL_CODE`, a caught interrupt
        here is 0, and the two are therefore distinguishable. And the elapsed time says the
        poll noticed: a graceful stop returns as soon as the session is over rather than
        sitting out the full grace, which is five seconds of which the two settles are 0.8.
        """
        pid, terminal = self.start([sys.executable, self.script("tui.py", TUI)])
        self.assertIn("ready", drain(terminal, until="ready"))
        began = time.time()
        self.assertTrue(self.end(pid, terminal), self.notes)
        waited = time.time() - began
        self.assertIn("caught", drain(terminal, seconds=2.0, until="caught"),
                      "the child never saw the Ctrl-C (%r)" % (self.notes,))
        self.assertEqual(terminal.exit_status(), 0,
                         "the child did not exit on its own terms (%r)" % (self.notes,))
        self.assertLess(waited, 3.0, "terminate sat out the grace on a session already over")

    def test_a_graceful_child_that_left_a_process_behind_still_loses_it(self):
        """The case `terminate` exists for, and the one the plan's pid-shaped wait would miss.

        This is what an ordinary session looks like: claude, plus whatever it was asked to
        start. It takes the Ctrl-C and exits 0 in about a second — and the `ping` it started is
        still running, because the byte only ever reached the one process that was reading the
        console. On the Mac this cannot happen: `killpg` addressed the whole process group, so
        the polite signal already went everywhere. Here a `terminate` that stopped at
        `_reaped(pid)` would answer True with a live process under a session the phone has
        just been told is over — §9.10's failure in its Windows dress, and the reason the wait
        is on the job being empty rather than on claude's pid.

        The exit status is asserted too, because the outcome has to be *both* things: the child
        got its graceful exit and the leftover still got killed.
        """
        script = self.script("tui_child.py", TUI_WITH_A_CHILD)
        pid, terminal = self.start([sys.executable, script])
        match = re.search(r"ready (\d+) (\d+)", drain(terminal, until="ready "))
        self.assertIsNotNone(match, "the session never came up")
        child, leftover = int(match.group(1)), int(match.group(2))
        self.addCleanup(reap, leftover)
        self.assertTrue(self.end(pid, terminal, grace=2.0), self.notes)
        self.assertEqual(terminal.exit_status(), 0,
                         "the child was killed rather than asked (%r)" % (self.notes,))
        self.assertTrue(gone(child), "the child survived (%r)" % (self.notes,))
        self.assertTrue(gone(leftover),
                        "what the session started outlived it (%r)" % (self.notes,))

    def test_a_tree_that_ignores_ctrl_c_is_killed_by_the_job(self):
        """The other half of graceful-first: the fallback has to actually work.

        claude is not the only thing in a session — §5's `stop` has to end whatever it started
        too, and a build or a dev server is exactly the kind of process that ignores an
        interrupt and outlives the shell that launched it. Both processes here refuse the
        Ctrl-C, and the second one is not on the terminal any more, so the job is the only
        thing left that can reach them.

        The grace is short on purpose: what is under test is the kill and not the waiting.
        """
        deaf = self.script("deaf.py", DEAF)
        pid, terminal = self.start([sys.executable, deaf, "--parent"])
        text = drain(terminal, until="deaf ")
        match = re.search(r"deaf (\d+) (\d+)", text)
        self.assertIsNotNone(match, "the tree never announced itself: %r" % text)
        parent, grandchild = int(match.group(1)), int(match.group(2))
        self.addCleanup(reap, grandchild)
        self.assertTrue(self.end(pid, terminal, grace=0.5), self.notes)
        self.assertTrue(gone(parent), "the deaf child survived (%r)" % (self.notes,))
        self.assertTrue(gone(grandchild),
                        "the grandchild outlived its session (%r)" % (self.notes,))

    def test_the_child_is_in_the_job_before_spawn_returns(self):
        """§4: *the order is the point* — create the job, spawn, assign, hand the pid back.

        A `stop` arriving moments after a `new` lands in the window between the spawn and the
        assignment, and a process not yet in the job when the job is killed survives the kill.
        That is the same shape of bug as the Mac's TIOCSCTTY window, where closing the master
        hangs up a child that has not yet taken the pty and the session lives on with
        permissions bypassed and nobody watching it (§9.10).

        Asked of the job rather than of the outcome, because the outcome is what every other
        test here already covers. This is the one that says *when*.
        """
        import win32job
        pid, terminal = self.start([CMD, "/c", "ping -n 20 localhost >nul"])
        ids = win32job.QueryInformationJobObject(terminal.job,
                                                 win32job.JobObjectBasicProcessIdList)
        self.assertIn(pid, list(ids), "the child was not in the job when spawn returned")

    def test_a_job_that_cannot_be_created_is_a_spawn_failure(self):
        """And it fails before there is a child, which is the other half of that order.

        If the job came second, a box where Job Objects were unavailable would get a running
        session that `terminate` can never end — the worse of the two failures, because it is
        the silent one. Failing the spawn instead is §4.6's `failed` record with a reason in
        it, by the same route W3b's missing binary takes.
        """
        with mock.patch.object(session_win, "_win32job",
                               side_effect=ImportError("no pywin32 here")):
            with self.assertRaises(OSError) as caught:
                session_win.spawn([CMD, "/c", "rem"], self.tmp, dict(os.environ),
                                  session.ROWS, session.COLS)
        self.assertIn("job", str(caught.exception).lower())

    def test_terminate_answers_true_for_a_child_that_has_already_gone(self):
        """Most sessions end by themselves, and `Runner.run` calls this on every one of them.

        `session_posix.terminate` opens with the same question for the same reason: asking
        first is what keeps a session that exited on its own from being Ctrl-C'd and job-killed
        on its way out, and from spending 0.8s of settle doing it.
        """
        pid, terminal = self.start([CMD, "/c", "rem"])
        drain(terminal, seconds=PATIENCE)
        began = time.time()
        self.assertTrue(self.end(pid, terminal), self.notes)
        self.assertLess(time.time() - began, 1.0, "it went through the whole ritual anyway")

    def test_a_second_terminate_does_not_raise(self):
        """It is called from a `finally`, so an exception here replaces the session's own
        outcome with a traceback — and `Sessions.stop` may have got there first."""
        pid, terminal = self.start([CMD, "/c", "ping -n 20 localhost >nul"])
        self.assertTrue(self.end(pid, terminal, grace=0.5), self.notes)
        self.assertTrue(self.end(pid, terminal, grace=0.5), self.notes)

    def test_terminate_without_a_terminal_still_ends_the_process(self):
        """`bot.py`'s caller has a pid and nothing else (§6), and silence is not an option.

        With no terminal there is no Ctrl-C to send and no job handle to kill, so this can only
        reach the one process — which is all the listener wants, since W4b's `stop` writes the
        marker first and the runner's own `terminate` is what takes the tree. What must not
        happen is §9.10's failure in its Windows dress: answering True having done nothing, so
        the phone is told a session stopped while it is still running.
        """
        pid, terminal = self.start([CMD, "/c", "ping -n 20 localhost >nul"])
        self.assertTrue(session_win.terminate(pid, 0.5, self.notes.append), self.notes)
        self.assertTrue(gone(pid),
                        "terminate answered True for a live process (%r)" % (self.notes,))

    def test_a_runner_that_dies_without_warning_leaves_nothing_behind(self):
        """A runner killed outright takes its whole session with it — §4, and §6's `stop`.

        This is the case no `terminate` can cover, because `terminate` does not run: the
        listener's last resort on this platform is `psutil.Process(runner).kill()`, which is
        `TerminateProcess` and catches nothing, and a runner can also simply crash. The Mac's
        answer is that there is no answer — a hard-killed runner orphans its session there —
        and Windows can do better for free, because the runner holds two handles the session
        lives under.

        What was *measured* here, and it is two different mechanisms with a gap between them:

        - The **pseudoconsole** closes when the runner's handles do, and that alone ends claude
          within half a second (W3b saw the same thing from `Terminal.close()`).
        - It does **not** reach anything claude started. The dev server had left the console
          behind and survives the runner, the session and the reconciliation that follows.

        So the gap is exactly the one the Job Object exists to close, and
        `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` closes it: the handle dies with the runner and
        takes the tree. W3e first shipped *without* that flag, reasoning that dropping the
        handle should not be a way to end a session by accident — which the first bullet
        above disproves, since dropping it already ends the session by the other route.
        """
        pidfile = os.path.join(self.tmp, "pids.txt")
        root = os.path.dirname(os.path.abspath(session.__file__))
        runner = subprocess.Popen(
            [sys.executable, self.script("stub.py", RUNNER_THAT_DIES), pidfile, root,
             self.script("leaves_a_child.py", LEAVES_A_CHILD)],
            creationflags=session_win.DETACH_FLAGS)
        self.addCleanup(reap, runner.pid)

        match, deadline = None, time.time() + PATIENCE
        while match is None and time.time() < deadline:
            if os.path.exists(pidfile):
                with open(pidfile) as fh:
                    match = re.search(r"up (\d+) (\d+)", fh.read())
            time.sleep(0.05)
        self.assertIsNotNone(match, "the stub runner never got a session up")
        claude, leftover = int(match.group(1)), int(match.group(2))
        self.addCleanup(reap, leftover)
        self.addCleanup(reap, claude)

        import psutil
        psutil.Process(runner.pid).kill()
        runner.wait(SETTLED)               # `Popen` warns about a child it never reaped
        self.assertTrue(gone(claude), "claude outlived the runner that was reading it")
        self.assertTrue(gone(leftover),
                        "what the session started outlived the runner, the session and the "
                        "only handle that could still reach it")

    def test_reaped_says_whether_the_pid_is_gone(self):
        """`_reaped` is the question `terminate` is built on, and it is a question about a pid
        — not `Terminal.alive()`, which is about the process on this platform and about the
        terminal on the Mac (§4)."""
        pid, terminal = self.start([CMD, "/c", "ping -n 20 localhost >nul"])
        self.assertFalse(session_win._reaped(pid), "a live process read as a dead one")
        self.assertTrue(self.end(pid, terminal, grace=0.5), self.notes)
        self.assertTrue(session_win._reaped(pid))
        self.assertTrue(session_win._reaped(0x7FFFFFF0), "a pid that cannot exist read as live")


@unittest.skipUnless(WIN, "the Windows half of the stop: a marker, and a console's signals")
class TestTheStopMarkerOnWindows(unittest.TestCase):
    """WINDOWS.md W3f, the half that is a fact about this platform rather than about `pump`.

    `tests/test_session.py::TestTheStopMarker` has the protocol and the loop, portably, over
    whichever module the platform chose. What is left here is why the marker exists at all:
    on this box the listener has no other way to reach a runner it did not keep a handle to.
    `TerminateProcess` runs nothing in the target, `GenerateConsoleCtrlEvent` needs a console
    the runner was deliberately spawned without (`DETACHED_PROCESS`, W0c), and there is no
    SIGTERM to catch. A file is the whole channel, so the file has to behave under two
    processes reaching for it at once — which is the ordinary case, not the exotic one: the
    listener writes it from its poll thread while the runner stats it every tick.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_request_stop_is_atomic_and_idempotent(self):
        """The slice's named test. Twenty threads asking at once leave one empty marker.

        Idempotent because §5's `stop` is a button on a phone and the phone is held by
        somebody who will press it again. Atomic because the reader is a different process
        polling at `TICK` and existence is the entire message: there is no half-written state
        for it to read, so a marker that is there is a stop that was asked for.
        """
        seen, errors = [], []
        start = threading.Event()

        def ask():
            start.wait()
            try:
                session_win.request_stop(self.tmp)
            except OSError as e:
                errors.append(e)

        def poll():
            start.wait()
            for _ in range(200):
                try:
                    seen.append(session_win.stop_requested(self.tmp))
                except OSError as e:
                    errors.append(e)

        threads = [threading.Thread(target=ask) for _ in range(20)]
        threads += [threading.Thread(target=poll) for _ in range(4)]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(PATIENCE)

        self.assertEqual(errors, [], "asking twice at once is an error the listener would hit")
        self.assertTrue(session_win.stop_requested(self.tmp))
        marker = os.path.join(self.tmp, session_win.STOP)
        self.assertEqual(os.listdir(self.tmp), [session_win.STOP])
        self.assertEqual(os.path.getsize(marker), 0,
                         "the marker carries content, so an empty one could mean something")
        # Once true it stays true: a poll that flickered would be a runner that read a stop
        # and then went back to reading the terminal.
        self.assertNotIn(False, seen[seen.index(True):] if True in seen else [])

    def test_a_marker_from_another_process_is_seen(self):
        """The two ends are two processes, and on this platform that is the only reason the
        marker exists. A file written by this interpreter proves nothing about that."""
        asked = subprocess.run(
            [sys.executable, "-c",
             "import sys, session_win; session_win.request_stop(sys.argv[1])", self.tmp],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True, timeout=PATIENCE)
        self.assertEqual(asked.returncode, 0, asked.stderr)
        self.assertTrue(session_win.stop_requested(self.tmp))

    def test_a_marker_written_by_another_process_ends_a_live_conpty_session(self):
        """The whole mechanism at once, on the real one: a child that would never stop, a
        marker written by a process that is not this one, and a `pump` that comes back.

        `TestTheStopMarker` drives this loop over a fake, and every branch of it is pinned
        there. What a fake cannot be asked is the question this platform actually turns on —
        whether a runner sitting in a ConPTY read notices the file at all. `ping -n 100` is a
        session that is up and producing the odd line and going nowhere, which is what every
        session the phone stops looks like; the assertion that it is still alive afterwards is
        what says the marker ended the pump rather than the child ending on its own.
        """
        r = session.Runner("3f2a91", self.tmp, "beacon-3f2a", root=self.tmp,
                           project="beacon", argv=[CMD, "/c", "rem"], log=lambda m: None)
        r.begin()
        pid, terminal = session_win.spawn([CMD, "/c", "ping -n 100 localhost"], self.tmp,
                                          dict(os.environ), session.ROWS, session.COLS)
        self.addCleanup(terminal.close)
        transcript = session.Transcript(os.path.join(r.dir, session.TRANSCRIPT),
                                        log=lambda m: None)
        self.addCleanup(transcript.close)

        asked = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time, session_win; time.sleep(float(sys.argv[2]));"
             " session_win.request_stop(sys.argv[1])", r.dir, "1.0"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.addCleanup(asked.wait)

        began = time.time()
        r.pump(terminal, transcript)
        took = time.time() - began
        self.assertTrue(r.stopping, "the pump came back without having seen a stop")
        self.assertLess(took, PATIENCE, "the pump outlived the stop by more than a tick")
        self.assertTrue(terminal.alive(), "the child ended on its own; the marker proved "
                                          "nothing about a session that was still running")
        self.assertTrue(session_win.terminate(pid, grace=2.0, terminal=terminal), "not ended")


@unittest.skipUnless(WIN, "signal.SIGBREAK and the Windows signal set")
class TestTheSignalsThatAreLeftOnWindows(unittest.TestCase):
    """WINDOWS.md §4 and W3f: two of them, and only for a console the runner usually has not.

    In service the runner is started with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
    (W0c), so no control event can reach it and the marker above is how it is stopped. These
    handlers are for the other way it runs: `python session.py --foreground` in a console
    somebody is watching, which is W3g's acceptance run and every hand-debug after it. Ctrl-C
    there has to end the *session* — with `meta.json` left saying `ended` and claude actually
    gone — rather than dropping a traceback over a record that still says `live`.

    `SIGTERM` and `SIGHUP` are not in the list. `SIGHUP` does not exist here at all, and
    `signal.SIGTERM` does exist but nothing can deliver it: Windows has no `kill(2)`, and
    `os.kill(pid, SIGTERM)` is `TerminateProcess` in a trench coat — it ends the process
    without running a handler, so registering one is a claim this module cannot keep.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.calls = []
        # One object and not a bound method: `signal.getsignal` hands back what was installed,
        # and `self.handler` would be a fresh bound method on every attribute access — so an
        # identity assertion against it would fail for a handler that was installed correctly.
        self.handler = lambda signum, frame: self.calls.append(signum)
        previous = {sig: signal.getsignal(sig)
                    for sig in (signal.SIGINT, signal.SIGBREAK, signal.SIGTERM)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in previous.items()
                                 if h is not None])
        self.before = previous

    def test_it_catches_sigint_and_sigbreak_and_leaves_sigterm_alone(self):
        session_win.catch_signals(self.handler)
        self.assertIs(signal.getsignal(signal.SIGINT), self.handler)
        self.assertIs(signal.getsignal(signal.SIGBREAK), self.handler)
        self.assertIs(signal.getsignal(signal.SIGTERM), self.before[signal.SIGTERM],
                      "SIGTERM was taken, and nothing on this platform can deliver one")

    def test_the_handler_actually_runs(self):
        """Installed is not the same as wired. `raise_signal` is the one way to ask that
        question in-process: `os.kill(pid, CTRL_C_EVENT)` goes to the whole process group,
        which here is the test runner."""
        session_win.catch_signals(self.handler)
        signal.raise_signal(signal.SIGINT)
        self.assertEqual(self.calls, [signal.SIGINT])

    def test_restore_puts_back_what_was_there(self):
        """`run()` calls it from a `finally`, and a runner that left the interpreter without
        its KeyboardInterrupt would be a hand-run console that cannot be quit."""
        previous = session_win.catch_signals(self.handler)
        session_win.restore_signals(previous)
        self.assertIs(signal.getsignal(signal.SIGINT), self.before[signal.SIGINT])
        self.assertIs(signal.getsignal(signal.SIGBREAK), self.before[signal.SIGBREAK])

    def test_a_ctrl_c_in_a_foreground_run_ends_the_session(self):
        """The two halves joined, which is what W3g's acceptance run does by hand: the
        handler `session.Runner` installs is the one that sets `stopping`, so the loop ends
        at the top of its next pass and `run()` goes on to `terminate` and the record."""
        r = session.Runner("3f2a91", self.tmp, "beacon-3f2a", root=self.tmp,
                           argv=[CMD, "/c", "rem"], log=lambda m: None)
        previous = r._catch_signals()
        self.addCleanup(r._restore_signals, previous)
        self.assertFalse(r.stopping)
        signal.raise_signal(signal.SIGINT)
        self.assertTrue(r.stopping, "Ctrl-C in a --foreground run did not end the session")


if __name__ == "__main__":
    unittest.main()
