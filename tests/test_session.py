#!/usr/bin/env python3
"""Slice 6 — the PTY runner: a real terminal, and the one line worth reading off it.

Everything the bot promises rests on scraping one URL out of a terminal that was never meant to
be read by a program. So the tests are in two halves, and the second half is the one that
catches the failures a unit test usually cannot.

**The scrape, against a real transcript.** `tests/fixtures/rc_startup.log` is a captured PTY
startup of `claude --remote-control` on this box — 3 KB of cursor positioning, truecolor SGR and
box drawing with the link somewhere inside it. A regex written against an imagined transcript
would pass; this one has to survive the real thing, and arriving in arbitrary chunks, because
that is how a PTY delivers it. Two boundary cases have their own tests because both produce a
*silently wrong* answer rather than an error: a chunk that splits the URL yields half a link
that looks plausible, and a chunk that splits an ANSI escape yields a stripper that leaks the
tail of the escape into the text it was cleaning.

**The terminal, for real.** SPEC.md §6: a fresh pty is 0x0, not 80x24, and Claude Code asks the
kernel rather than reading $COLUMNS. The URL fitting on one line at the fallback 80 columns was
luck. `TIOCSWINSZ` on the slave before the exec is the fix, and the only way to know it worked
is to run a process through the real setup and ask it — so these tests fork, exec and read a
live pty rather than mocking one. `test_a_child_reports_the_size_the_spec_requires` is that
check, and §14 makes it the thing to re-run after every Claude Code upgrade.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import errno
import json
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import shutil
import unittest
from unittest import mock

import session

#: WINDOWS.md W1c. The tests that fork, open a pty or send a signal are the Mac's — they test
#: session_posix.py against a real terminal — and are skipped on Windows, where their two
#: modules do not exist. The portable half of this file runs on both.
from tests.support import POSIX
if POSIX:
    import fcntl
    import termios
posix_only = unittest.skipUnless(POSIX, "forks, opens a pty or sends a signal: session_posix")
needs_session_posix = unittest.skipUnless(POSIX, "imports session_posix, which needs fcntl and termios")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "rc_startup.log")

#: The link in the captured transcript. ULID-shaped, not a UUID (SPEC.md §9.5).
CAPTURED = "https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF"

#: How long a test waits for something that should take a moment before calling it a failure.
#: Long enough for a loop ticking at `session.TICK` to get several passes in on a busy box,
#: short enough that a wedged run is a red suite rather than a coffee break.
PATIENCE = 5.0


def _silently_kill(pid):
    """Last resort cleanup for a process a test was supposed to have ended."""
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def trust_panel(marked=0, second="Yes, I trust this folder"):
    """§9.3's panel as the real one renders it: words joined by cursor jumps, no spaces.

    Module level since W3d, because two tests want the same panel from opposite directions —
    `TestTheTrustDialog` feeds it straight to the matcher, and `TestPumpOverATerminal` sends
    it through the pump the way a session delivers it. One definition, or the second one
    quietly stops being the panel the first one proved anything about.
    """
    rows = ["Is\x1b[10Gthis\x1b[15Ga\x1b[17Gproject\x1b[25Gyou\x1b[29Gcreated\x1b[37Gor"
            "\x1b[40Gone\x1b[44Gyou\x1b[48Gtrust?"]
    for i, label in enumerate(("No, exit", second)):
        rows.append(("❯" if i == marked else " ") + "\x1b[4G" + label)
    return ("\r\n".join(rows) + "\r\n").encode()


def drain(terminal, deadline=10.0):
    """Read a `Terminal` until the child hangs up. Returns bytes.

    A pty master does not give a clean EOF on macOS: when the last slave fd closes, the read
    fails with EIO. That is the normal end of a session, not an error, and any read loop over
    a pty has to know it — which since W3d is `session_posix.Terminal`'s job rather than every
    caller's. So this is now the same two calls `Runner.pump` makes, and the tests below
    exercise the real `Terminal` on the way to asking about the pty behind it.
    """
    out = b""
    end = time.time() + deadline
    while time.time() < end:
        chunk = terminal.read(0.2)
        out += chunk
        if not chunk and not terminal.alive():
            break
    return out


#: A fake `claude` that renders §9.3's trust dialog the way the real one does and insists on
#: being answered properly. Captured from the probe on 2026-09-13: words are separated by
#: `CSI <n> G` cursor jumps rather than spaces — which is why the stripped transcript has no
#: spaces in it at all, and why anything matching against this has to normalise whitespace
#: away. The default selection is `No, exit`, so a bare Enter ends the session: this exits with
#: no link, exactly as the real one would.
FAKE_CLAUDE = r"""
import os, select, sys, tty
tty.setraw(0)

def key():
    # os.read and not sys.stdin.buffer.read: select() asks the *kernel* what is waiting, and a
    # buffered reader takes all three bytes of an arrow key off it to return one — so the next
    # select() sees an empty fd, waits out IDLE, and the fake gives up on a terminal that has
    # already answered it. The same trap the runner's own loop avoids by reading the fd raw.
    ready, _, _ = select.select([0], [], [], IDLE)
    return os.read(0, 16) if ready else b""
LINK = "https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF"
OPTIONS = sys.argv[1] or "Yes, I trust this folder"
IDLE = float(sys.argv[2])     # nothing from the terminal for this long: give up and exit

def draw(selected):
    out = "\x1b[2GQuick\x1b[8Gsafety\x1b[15Gcheck:\x1b[22GIs\x1b[25Gthis\x1b[30Ga"
    out += "\x1b[32Gproject\x1b[40Gyou\x1b[44Gcreated\x1b[52Gor\x1b[55Gone\x1b[59Gyou"
    out += "\x1b[63Gtrust?\r\n\r\n"
    for i, label in enumerate(("No, exit", OPTIONS)):
        out += "\x1b[2G" + ("❯" if i == selected else " ")
        out += "\x1b[4G" + label.replace(" ", "\x1b[%dG" % (10 + i)) + "\r\n"
    out += "\x1b[2GEnter\x1b[8Gto\x1b[11Gconfirm\r\n"
    sys.stdout.write(out)
    sys.stdout.flush()

selected, seen = 0, b""
draw(selected)
while True:
    byte = key()
    if not byte:
        break                 # the real one would wait for a human; a test cannot
    seen += byte
    if seen.endswith(b"\x1b[B"):
        selected = 1
        draw(selected)
    elif seen.endswith(b"\x1b[A"):
        selected = 0
        draw(selected)
    elif seen.endswith(b"\r"):
        if selected == 1:
            sys.stdout.write("\r\nwelcome\x1b[10G" + LINK + "\r\n")
            sys.stdout.flush()
        else:
            sys.stdout.write("\r\nexiting\r\n")
            sys.stdout.flush()
        break
"""

class TestTheUrlInARealTranscript(unittest.TestCase):
    """The fixture is the contract. §14: when it stops matching, capture a fresh one."""

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, "rb") as fh:
            cls.raw = fh.read()

    def test_the_fixture_is_a_real_terminal_transcript(self):
        # Establishes that the tests below are not passing against sanitised text.
        self.assertGreater(len(self.raw), 1000)
        self.assertIn(b"\x1b[", self.raw, "no ANSI escapes — this is not a PTY capture")
        self.assertIn(b"\x1b[38;2;", self.raw, "no truecolor SGR — this is not Claude Code")

    def test_it_finds_the_link_in_the_whole_transcript(self):
        self.assertEqual(session.extract_url(self.raw), CAPTURED)

    def test_it_finds_the_link_however_the_pty_chops_it_up(self):
        """A pty delivers whatever happened to be in the buffer, not whatever is a unit.

        Every size from one byte upwards, because the interesting sizes are the ones that land
        inside the URL or inside an escape sequence, and which those are is a property of this
        transcript rather than something worth reasoning about in advance.
        """
        for size in (1, 2, 3, 7, 16, 64, 137, 512, 4096):
            scrape = session.Scrape()
            found = None
            for i in range(0, len(self.raw), size):
                found = found or scrape.feed(self.raw[i:i + size])
            self.assertEqual(found, CAPTURED, "lost the URL at chunk size %d" % size)

    def test_it_does_not_invent_a_link_before_one_arrives(self):
        # The listener waits up to 45s on this (§4.6). A premature answer is a dead link sent
        # to a phone, which is worse than the wait.
        cut = self.raw.find(b"claude.ai")
        scrape = session.Scrape()
        for i in range(0, cut, 64):
            self.assertIsNone(scrape.feed(self.raw[i:i + 64]))

    def test_the_first_link_wins_and_the_answer_never_changes(self):
        scrape = session.Scrape()
        scrape.feed(self.raw)
        self.assertEqual(scrape.url, CAPTURED)
        scrape.feed(b"https://claude.ai/code/session_somethingElseEntirely\n")
        self.assertEqual(scrape.url, CAPTURED)

    def test_the_pattern_is_not_pinned_to_todays_id_shape(self):
        # §9.5: ids are ULID-shaped and mixed case, so a UUID regex finds nothing. §14 adds the
        # other half — the shape is not a promise, and a length-pinned pattern would match today
        # and quietly stop after an upgrade, which is the hardest version of this to notice.
        for ident in ("01HJK2Lh42N7JbfMGExJkpTF", "01ARZ3NDEKTSV4RRFFQ69G5FAV", "aB9_x-Y"):
            url = "https://claude.ai/code/session_" + ident
            self.assertEqual(session.extract_url("at " + url + " ok"), url)


class TestThePlatformSeam(unittest.TestCase):
    """WINDOWS.md §2, W1a: the process and terminal mechanisms live behind `session.procs`.

    Everything under `Runner` that touches a pty or a process goes through one module chosen
    by `sys.platform`, and both modules export the same names. The names are the contract —
    `session_win.py` (W1c onwards) has to fill in every one of them, and a name missing on
    one side is an AttributeError at the worst moment, inside a runner that has already
    written `starting`.
    """

    SURFACE = ("spawn", "terminate", "detach", "alive", "started", "spawn_flags", "Lock",
               "request_stop", "stop_requested", "catch_signals", "restore_signals",
               # W3g: the child's environment is half policy and half platform. The hazard
               # filter is SPEC.md §6 and stays in session.py; what a child needs in order to
               # be a child at all is the platform's, and that half is this name.
               "child_env",
               # W3d: `spawn` hands back one of these and `pump` knows nothing else about a
               # terminal, so it is as much a part of the contract as the functions are.
               "Terminal")

    @needs_session_posix
    def test_the_posix_module_has_the_whole_surface(self):
        import session_posix
        for name in self.SURFACE:
            self.assertTrue(hasattr(session_posix, name), "session_posix.%s is missing" % name)

    def test_session_exposes_the_chosen_module_as_procs(self):
        for name in self.SURFACE:
            self.assertTrue(hasattr(session.procs, name), "session.procs.%s is missing" % name)

    @needs_session_posix
    def test_the_module_is_chosen_by_platform(self):
        # Every non-Windows platform gets the posix module; the win32 branch is W1c's.
        for platform in ("darwin", "linux", "freebsd13"):
            with mock.patch.object(sys, "platform", platform):
                self.assertEqual(session._platform().__name__, "session_posix", platform)

    def test_the_old_names_still_resolve_on_session(self):
        # bot.py and these tests call session.spawn / terminate / detach directly. detach and
        # _reaped are the platform's functions; spawn and terminate are wrappers because their
        # defaults are this module's constants.
        for name in ("detach", "_reaped"):
            self.assertIs(getattr(session, name), getattr(session.procs, name), name)
        for name in ("spawn", "terminate"):
            self.assertTrue(callable(getattr(session, name)), name)

    def test_spawn_and_terminate_forward_with_the_module_defaults(self):
        with mock.patch.object(session.procs, "spawn", return_value=(1, 2)) as spawn:
            self.assertEqual(session.spawn(["x"], "/tmp", {}), (1, 2))
            spawn.assert_called_once_with(["x"], "/tmp", {}, session.ROWS, session.COLS)
        with mock.patch.object(session.procs, "terminate", return_value=True) as terminate:
            self.assertTrue(session.terminate(4242))
            terminate.assert_called_once_with(4242, session.GRACE, session._stderr, None)

    def test_child_env_is_filtered_here_and_finished_by_the_platform(self):
        """WINDOWS.md W3g: which half of the child's environment belongs to which file.

        `session.child_env` keeps the part that is a *decision* — the hazards of SPEC.md §6,
        which are the same hazards on any platform, and `COLUMNS`/`LINES`, which are this
        module's `ROWS`/`COLS`. What it hands on is an environment with nothing dangerous left
        in it and nothing platform-shaped added yet; the module that knows what a child needs
        in order to start adds that. Asserted on both platforms because the division is the
        contract, not either side's answer.
        """
        with mock.patch.object(session.procs, "child_env",
                               side_effect=lambda env: dict(env, PLATFORM_RAN="1")) as half:
            env = session.child_env({"CLAUDE_PID": "1", "AI_AGENT": "1", "KEPT": "2"})
        handed = half.call_args[0][0]
        self.assertEqual([k for k in handed if k.startswith("CLAUDE")], [],
                         "the platform half was handed variables the filter should have taken")
        self.assertNotIn("AI_AGENT", handed)
        self.assertEqual(handed["KEPT"], "2")
        self.assertEqual((handed["COLUMNS"], handed["LINES"]), ("200", "50"))
        self.assertEqual(env["PLATFORM_RAN"], "1", "the platform half did not get the last word")

    def test_terminate_forwards_the_terminal_the_session_was_read_through(self):
        """WINDOWS.md W3e: on Windows a pid alone is not enough to end a session politely.

        The Mac's `terminate` needs nothing but the pid — `killpg` and `kill` are addressed by
        number — so the seam was shaped that way, and W3e found the shape too narrow. A Ctrl-C
        on Windows is not a signal, it is two bytes written to the terminal, and the Job Object
        that takes the rest of the tree was created at spawn and is held by the same `Terminal`
        object. Neither of them is reachable from a pid. So the terminal travels with it, and
        `session_posix.terminate` ignores the argument — which is what makes this test the
        portable one: it says the argument arrives, on both platforms, whatever is done with it.

        `bot.py` is the caller that has a pid and nothing else (it is ending a *runner*, not a
        session), and the default above is the shape it keeps.
        """
        term = FakeTerminal()
        with mock.patch.object(session.procs, "terminate", return_value=True) as terminate:
            self.assertTrue(session.terminate(4242, terminal=term))
            terminate.assert_called_once_with(4242, session.GRACE, session._stderr, term)

    @needs_session_posix
    def test_the_posix_no_ops_answer_as_the_mac_needs(self):
        import session_posix
        self.assertEqual(session_posix.spawn_flags(), {})
        self.assertTrue(session_posix.Lock(os.path.join(tempfile.gettempdir(), "x")).take())
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # The marker was W1a's no-op here and is W3f's mechanism on both platforms now: the
        # Mac keeps SIGTERM *as well*, so `bot.py` on this side has two ways to say the same
        # thing and the runner has one place that hears it. See TestTheStopMarker.
        self.assertFalse(session_posix.stop_requested(d))
        session_posix.request_stop(d)
        self.assertTrue(session_posix.stop_requested(d))


#: WINDOWS.md W0a: the same startup captured through ConPTY on the Windows box. ConPTY does
#: not pass the program's bytes through — it re-renders from its own screen buffer and emits
#: a diff as VT sequences of its own — so the Mac fixture proves nothing about what the
#: scraper sees there. Absent until W0a captures it; skipped rather than failed until then,
#: because the fixture is the output of that slice and not an input to it.
FIXTURE_WIN = os.path.join(ROOT, "tests", "fixtures", "rc_startup_win.log")


@unittest.skipUnless(os.path.exists(FIXTURE_WIN), "no Windows capture yet (WINDOWS.md W0a)")
class TestTheWindowsCapture(unittest.TestCase):
    """What ConPTY hands the runner, and whether the same Scrape reads it.

    The link is not pinned to a constant the way CAPTURED is, because it is whatever session
    the spike happened to start; the contract is that exactly one well-formed link is found,
    and that it is the *same* one at every chunk size — the two ways ConPTY's re-rendering
    could break the scraper are a URL interleaved with cursor moves (no match at all) and a
    URL emitted twice in two redraws with a boundary in one of them (a different, truncated
    match at some chunk size).
    """

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE_WIN, "rb") as fh:
            cls.raw = fh.read()
        cls.url = session.extract_url(cls.raw)

    def test_the_fixture_is_a_real_conpty_transcript(self):
        self.assertGreater(len(self.raw), 1000)
        self.assertIn(b"\x1b[", self.raw, "no ANSI escapes — this is not a ConPTY capture")

    def test_it_finds_exactly_one_link_in_the_whole_transcript(self):
        self.assertIsNotNone(self.url, "no link in the Windows capture")
        self.assertRegex(self.url, r"^https://claude\.ai/code/session_[A-Za-z0-9_-]+$")
        clean = session.ANSI_RE.sub("", self.raw.decode("utf-8", "replace"))
        self.assertEqual(len(set(session.URL_RE.findall(clean))), 1,
                         "more than one distinct link — a redraw changed it")

    def test_it_finds_the_same_link_however_conpty_chops_it_up(self):
        for size in (1, 2, 3, 7, 16, 64, 137, 512, 4096):
            scrape = session.Scrape()
            found = None
            for i in range(0, len(self.raw), size):
                found = found or scrape.feed(self.raw[i:i + size])
            self.assertEqual(found, self.url, "lost or changed the URL at chunk size %d" % size)

    def test_it_does_not_invent_a_link_before_one_arrives(self):
        cut = self.raw.find(b"claude.ai")
        scrape = session.Scrape()
        for i in range(0, cut, 64):
            self.assertIsNone(scrape.feed(self.raw[i:i + 64]))


#: W0a's first run, before the spike answered anything: the §9.3 dialog as ConPTY draws it,
#: marker on `No, exit`. A fresh clone is untrusted, so on a new box the dialog is the first
#: thing every session meets, not an edge case.
TRUST_FIXTURE_WIN = os.path.join(ROOT, "tests", "fixtures", "trust_dialog_win.log")


@unittest.skipUnless(os.path.exists(TRUST_FIXTURE_WIN) and os.path.exists(FIXTURE_WIN),
                     "no Windows captures yet (WINDOWS.md W0a)")
class TestTheWindowsTrustDialog(unittest.TestCase):
    """session.Trust against ConPTY's rendering of the dialog, both halves.

    `trust_dialog_win.log` stops at the panel: Trust must see it and press Down, and nothing
    else, because the marker never moves in that capture. `rc_startup_win.log` is the whole
    run — panel, the redraw after Down with the marker on `Yes`, then the link — so the same
    Trust fed that stream must press Down and then Enter, in that order, once each.
    """

    @classmethod
    def setUpClass(cls):
        with open(TRUST_FIXTURE_WIN, "rb") as fh:
            cls.panel = fh.read()
        with open(FIXTURE_WIN, "rb") as fh:
            full = fh.read()
        # The whole run began exactly as the panel capture did — same binary, same directory,
        # same dialog — so what follows the common prefix is what ConPTY drew *after* Down.
        assert full.startswith(cls.panel), "the two captures are not from the same startup"
        cls.after_down = full[len(cls.panel):]

    def drive(self, size, with_redraw):
        """The runner's rhythm: output arrives in chunks, and a keystroke is answered by
        whatever the terminal draws next. Feeding the whole run in one chunk would hand
        Trust the post-Down redraw *before* it pressed Down, which no terminal does."""
        trust = session.Trust()
        keys = []
        now = 0.0

        def feed(chunk):
            nonlocal now
            key = trust.feed(chunk, now)
            now += 1.0                       # every call is past SETTLE
            if key is not None:
                keys.append(key)

        for i in range(0, len(self.panel), size):
            feed(self.panel[i:i + size])
        feed(b"")                            # the settle tick that produces Down
        if with_redraw:
            for i in range(0, len(self.after_down), size):
                feed(self.after_down[i:i + size])
            feed(b"")                        # the settle tick that produces Enter
        return trust, keys

    def test_the_panel_alone_gets_down_and_nothing_else(self):
        trust, keys = self.drive(512, with_redraw=False)
        self.assertEqual(keys, [session.DOWN])
        self.assertEqual(trust.state, trust.ASKED)

    def test_the_redraw_after_down_gets_enter(self):
        trust, keys = self.drive(512, with_redraw=True)
        self.assertEqual(keys, [session.DOWN, session.ENTER])
        self.assertEqual(trust.state, trust.DONE)

    @unittest.expectedFailure
    def test_the_answer_does_not_depend_on_chunking(self):
        """Fails today at 64 bytes and below, on both platforms — WINDOWS.md W3h.

        Trust squeezes each chunk on its own, so an escape split across two chunks leaves
        its tail in the text and breaks the phrase it lands in. Scrape carries a partial
        escape to the next chunk for exactly this reason; Trust does not yet. The W0a spike
        answered the dialog because it matched against everything accumulated, not chunk by
        chunk. Expected to fail until W3h gives Trust the same carry; the decorator comes off
        in that slice.
        """
        for size in (1, 16, 64, 128, 512):
            _, keys = self.drive(size, with_redraw=True)
            self.assertEqual(keys, [session.DOWN, session.ENTER], "chunk size %d" % size)


class TestAChunkBoundaryInsideTheUrl(unittest.TestCase):
    """The failure this prevents is a *plausible* wrong answer, not an error."""

    def test_a_split_anywhere_in_the_url_still_resolves(self):
        stream = b"noise \x1b[32m" + CAPTURED.encode() + b"\r\n more noise"
        start = stream.find(b"https")
        for cut in range(start, start + len(CAPTURED) + 1):
            scrape = session.Scrape()
            first = scrape.feed(stream[:cut])
            second = scrape.feed(stream[cut:])
            self.assertEqual(first or second, CAPTURED, "split at %d" % cut)

    def test_a_half_url_at_the_end_of_a_chunk_is_not_answered_yet(self):
        """The reason the matcher waits for a terminator rather than taking what it has.

        A greedy match on `session_[A-Za-z0-9_-]+` is happy to stop at a chunk boundary, and
        the truncated link it returns is well-formed, tappable, and wrong. There is no error to
        notice: the phone gets a link that 404s. So a match that runs to the end of what has
        arrived is held until something that cannot be part of a URL follows it.
        """
        scrape = session.Scrape()
        self.assertIsNone(scrape.feed(b"at https://claude.ai/code/session_01HJK2Lh42N7"))
        self.assertEqual(scrape.feed(b"JbfMGExJkpTF\r\n"), CAPTURED)

    def test_one_byte_at_a_time_still_resolves(self):
        scrape = session.Scrape()
        found = None
        for byte in (b"see " + CAPTURED.encode() + b" \r\n"):
            found = found or scrape.feed(bytes([byte]))
        self.assertEqual(found, CAPTURED)


class TestAChunkBoundaryInsideAnEscape(unittest.TestCase):
    """§12: carry the tail, do not strip per-chunk."""

    def test_an_escape_split_across_chunks_does_not_leak_into_the_text(self):
        """Stripping each chunk on its own is the obvious implementation and it is wrong.

        `\\x1b[38;2;153` in one chunk and `;153;153m` in the next: neither half matches the
        escape pattern, so a per-chunk stripper passes both through and `;153;153m` lands in the
        cleaned text. Harmless-looking, until the split falls inside the escape that immediately
        precedes the link and the leaked tail is prepended to it.
        """
        whole = b"\x1b[38;2;153;153;153mhttps://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF\r\n"
        for cut in range(1, 20):
            scrape = session.Scrape()
            found = scrape.feed(whole[:cut]) or scrape.feed(whole[cut:])
            self.assertEqual(found, CAPTURED, "escape split at %d" % cut)

    def test_a_partial_escape_is_held_rather_than_emitted(self):
        scrape = session.Scrape()
        scrape.feed(b"before\x1b[3")
        self.assertNotIn("\x1b", scrape.tail)
        self.assertNotIn("[3", scrape.tail, "a partial escape leaked into the cleaned text")

    def test_a_stray_escape_does_not_stall_the_stream_forever(self):
        # If a lone ESC were held indefinitely, everything behind it — including the link —
        # would be held with it. A carry that stops looking like an escape is released.
        scrape = session.Scrape()
        scrape.feed(b"\x1b")
        found = scrape.feed(b"x" * 600 + b" " + CAPTURED.encode() + b"\r\n")
        self.assertEqual(found, CAPTURED)

    def test_the_osc_title_sequence_is_stripped(self):
        # The transcript opens with one: \x1b]0;<title>\x07. It is terminated by BEL, not by a
        # letter, so an SGR-only pattern leaves the whole title in the text.
        text = session.strip(b"\x1b]0;\xf0\x9f\xa4\x96 Claude Code\x07ready")
        self.assertEqual(text, "ready")


@posix_only
class TestTheTerminalSize(unittest.TestCase):
    """SPEC.md §6: the scrape depends on the URL landing on one line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_fresh_pty_is_zero_by_zero(self):
        """The verified claim §6 is built on, pinned without going through session.py.

        Not 80x24. Claude Code asks the kernel, gets nothing usable and falls back to its own
        80 columns, while $COLUMNS in the child says whatever it was told. That is why the
        probe's panel rules measured 80 despite COLUMNS=100, and why the URL fitting on one
        line was luck rather than design.
        """
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        rows, cols, _, _ = struct.unpack(
            "HHHH", fcntl.ioctl(master, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0)))
        self.assertEqual((rows, cols), (0, 0))

    def test_a_child_reports_the_size_the_spec_requires(self):
        """§6's ioctl, proved by asking a real child through the real setup.

        `stty size` reads it from the kernel exactly as Claude Code's renderer does. §14 makes
        this the test to re-run after a Claude Code upgrade.
        """
        pid, terminal = session.spawn(["/bin/sh", "-c", "stty size"], self.tmp,
                                      session.child_env())
        self.addCleanup(terminal.close)
        out = drain(terminal)
        os.waitpid(pid, 0)
        self.assertEqual(out.decode().strip(), "%d %d" % (session.ROWS, session.COLS))

    def test_the_size_is_the_one_section_six_names(self):
        self.assertEqual((session.ROWS, session.COLS), (50, 200))

    def test_the_child_has_a_controlling_terminal(self):
        # Without TIOCSCTTY the pty is just three file descriptors: `stty` fails, and
        # --remote-control refuses to start an interactive session at all (§9.1).
        pid, terminal = session.spawn(["/bin/sh", "-c", "tty"], self.tmp, session.child_env())
        self.addCleanup(terminal.close)
        out = drain(terminal).decode().strip()
        os.waitpid(pid, 0)
        self.assertTrue(out.startswith("/dev/"), out)
        self.assertNotIn("not a tty", out)

    def test_the_child_starts_in_the_directory_it_was_given(self):
        work = os.path.join(self.tmp, "somewhere")
        os.makedirs(work)
        pid, terminal = session.spawn(["/bin/sh", "-c", "pwd"], work, session.child_env())
        self.addCleanup(terminal.close)
        out = drain(terminal).decode().strip()
        os.waitpid(pid, 0)
        self.assertEqual(os.path.realpath(out), os.path.realpath(work))

    def test_closing_the_pty_hangs_up_the_child_once_it_owns_the_terminal(self):
        """§2's claim, with the qualifier this slice found. Verified on this box.

        Drop the master and the kernel SIGHUPs the child in about 100ms — which is why the
        runner sits in a read loop for the whole life of a session rather than spawning and
        returning: the read loop *is* what holds the pty open.

        The qualifier is the settle below, and it is not test scaffolding. The hangup reaches a
        process through its *controlling terminal*, and the child only has one once it has run
        setsid() and TIOCSCTTY. Close the master inside the window between fork and that ioctl
        and there is nothing to hang up from: the child survives, orphaned, writing to a pty
        nobody holds. That window is why `terminate()` signals the process group explicitly
        instead of trusting the hangup.
        """
        pid, terminal = session.spawn(["/bin/sh", "-c", "sleep 30"], self.tmp,
                                      session.child_env())
        time.sleep(0.3)
        terminal.close()
        for _ in range(60):
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                self.assertEqual(status & 0x7f, signal.SIGHUP)
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        self.fail("the child survived the pty being hung up")

    def test_a_child_orphaned_before_it_owns_the_terminal_is_still_killed(self):
        """The window above, closed. This is the one `stop all` depends on (§5).

        A stop that arrives moments after a spawn lands exactly here, and the session it fails
        to kill is one with permissions bypassed and nobody watching it.
        """
        pid, terminal = session.spawn(["/bin/sh", "-c", "sleep 30"], self.tmp,
                                      session.child_env())
        terminal.close()                      # no settle: the race, deliberately
        self.assertTrue(session.terminate(pid, grace=2.0, log=lambda m: None))

    def test_terminating_takes_the_whole_process_group(self):
        """§5: `stop` must not leave a dev server or a simulator behind.

        The child leads its own group after setsid(), so everything the session spawned is in
        the group with it — which is the point of signalling the group rather than the process.
        """
        pid, terminal = session.spawn(
            ["/bin/sh", "-c", "sleep 30 & echo $!; sleep 30"], self.tmp, session.child_env())
        self.addCleanup(terminal.close)
        grandchild = int(drain(terminal, 3.0).decode().strip().splitlines()[0])
        self.assertTrue(session.terminate(pid, grace=2.0, log=lambda m: None))
        for _ in range(40):
            try:
                os.kill(grandchild, 0)
            except OSError:
                return
            time.sleep(0.05)
        self.fail("a process the session spawned outlived the session")

    def test_terminating_something_already_gone_is_not_an_error(self):
        pid, terminal = session.spawn(["/bin/sh", "-c", "true"], self.tmp, session.child_env())
        self.addCleanup(terminal.close)
        drain(terminal, 3.0)
        self.assertTrue(session.terminate(pid, grace=1.0, log=lambda m: None))
        self.assertTrue(session.terminate(pid, grace=1.0, log=lambda m: None))

    def test_a_runner_that_is_no_longer_our_child_is_still_terminated(self):
        """Every `stop` from a phone will be this case, and §9.10's rule applies to it too.

        A runner is detached, so the moment the listener that forked it exits — a crash, a
        logout, a `launchctl kickstart` — it is reparented to launchd and stops being anybody's
        child. `waitpid()` then answers ECHILD, which means *not mine* and reads exactly like
        *already gone*: `terminate()` would return True having signalled nothing at all, and
        the session would live on with permissions bypassed and nobody watching it.

        Found by hand in slice 7, against a runner that had outlived the process that spawned
        it — which is to say against the ordinary case rather than an edge of it.
        """
        code = ("import os, sys, time\n"
                "pid = os.fork()\n"
                "if pid:\n"
                "    sys.stdout.write(str(pid)); sys.stdout.flush(); os._exit(0)\n"
                "os.setsid()\n"
                # The pipe is what subprocess.run waits on for EOF, and an orphan holding it
                # open for two minutes is a test that hangs rather than one that fails.
                "os.close(1)\n"
                "time.sleep(120)\n")
        done = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, timeout=30)
        orphan = int(done.stdout.strip())
        self.addCleanup(lambda: _silently_kill(orphan))

        self.assertFalse(session._reaped(orphan), "a live process read as a dead one")
        self.assertTrue(session.terminate(orphan, grace=2.0, log=lambda m: None))
        with self.assertRaises(OSError):
            os.kill(orphan, 0)


class TestTheChildEnvironment(unittest.TestCase):
    """SPEC.md §6. Every absence here is a Remote Control that starts and never connects."""

    def test_the_required_variables_are_set(self):
        """What both platforms owe the child. WINDOWS.md W3g split the rest of it in two.

        `COLUMNS`/`LINES` are `session.py`'s constants on either side — they are for the shell
        and the tools the session runs, not for the terminal Claude Code renders into — and a
        `PATH` the child can find a program on is the whole of what the two platforms agree
        about. Everything else that used to be asserted here is one platform's idea of an
        environment: see the two tests below.
        """
        env = session.child_env({})
        self.assertEqual(env["COLUMNS"], "200")
        self.assertEqual(env["LINES"], "50")
        self.assertTrue(env["PATH"], "a child with no PATH finds nothing that is not builtin")

    @posix_only
    def test_the_mac_shell_variables_are_set(self):
        # SPEC.md §6 on the Mac: the PATH is *replaced* with a known one, because the version
        # -pinned `claude` has to win, and `TERM` is what the pty tells the child it is.
        env = session.child_env({})
        self.assertEqual(env["TERM"], "xterm-256color")
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertIn("/.local/bin", env["PATH"])
        self.assertIn("/opt/homebrew/bin", env["PATH"])

    @unittest.skipUnless(not POSIX, "the Windows half of the child environment (WINDOWS.md W3g)")
    def test_child_env_win_keeps_windows_essentials(self):
        """WINDOWS.md §4 and W3g: on this platform the filter is the whole of it.

        The Mac replaces `PATH` with a list it chose. Doing that here is what W3b's run step
        hit — `cmd /c mode con` answered `'mode' is not recognized`, because the child had been
        handed `/usr/bin:/bin` and there is nothing at those paths on this box. So the
        inherited `PATH` stays, and with it every variable a Windows program quietly requires
        before it will start at all.

        `TERM` is the one thing the Mac sets that is not to be invented here: ConPTY does not
        read it, so setting it is a claim to a terminfo database this box has no reason to
        have. *Not invented* is the assertion and not *removed* — this is a filter and `TERM`
        is not a hazard, so one inherited from a Git Bash or a Claude Code session that started
        the listener travels on like any other variable.
        """
        real = dict(os.environ)
        env = session.child_env()
        self.assertEqual(env["PATH"], real["PATH"], "the inherited PATH was replaced")
        for name in ("SYSTEMROOT", "COMSPEC", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP"):
            self.assertEqual(env.get(name), real.get(name), "%s did not survive" % name)
        self.assertEqual("TERM" in env, "TERM" in real, "TERM was invented, or taken away")
        self.assertNotIn("TERM", session.child_env({}), "a TERM nobody asked for")

    @unittest.skipUnless(not POSIX, "the Windows half of the child environment (WINDOWS.md W3g)")
    def test_a_windows_essential_missing_from_the_base_is_filled_in(self):
        """The twin of the Mac's `HOME` line, which has always said `or expanduser("~")`.

        A base without `SYSTEMROOT` is not a child that runs with one fewer variable; it is a
        child that does not start — most of the Windows API reads it, and `cmd.exe` cannot be
        found without `COMSPEC` or a `PATH`. The runner's real base is `os.environ` and has all
        of them, so this is the same kind of insurance the Mac line is, aimed at the same
        failure: a session that starts and never connects (SPEC.md §6).
        """
        env = session.child_env({"CLAUDE_PID": "1"})
        for name in ("SYSTEMROOT", "COMSPEC", "PATH", "TEMP"):
            self.assertTrue(env.get(name), "%s was neither inherited nor filled in" % name)
        self.assertEqual([k for k in env if k.startswith("CLAUDE")], [])

    @unittest.skipUnless(not POSIX, "the Windows half of the child environment (WINDOWS.md W3g)")
    def test_a_backfilled_name_is_not_added_twice_in_another_case(self):
        """`os.environ` upper-cases its keys here; an arbitrary base need not.

        Windows looks variables up case-insensitively, but an environment *block* is a flat
        list of `name=value` and `session_win.environment_block` writes whatever is in the
        dict. `SystemRoot=C:\\Windows` and `SYSTEMROOT=C:\\Windows` both in it is two entries
        for one variable, and which of them a child sees is nobody's decision.
        """
        env = session.child_env({"SystemRoot": "C:\\Windows", "PATH": "C:\\nowhere"})
        self.assertEqual([k for k in env if k.upper() == "SYSTEMROOT"], ["SystemRoot"])
        self.assertEqual(env["PATH"], "C:\\nowhere", "a PATH that was given was overwritten")

    @unittest.skipUnless(POSIX, "the Mac's PATH; Windows keeps the inherited one (WINDOWS.md W3g)")
    def test_claude_is_first_on_the_path(self):
        # ~/.local/bin/claude is the version-pinned symlink (§9.8) and must win over anything
        # a brew install might drop in later.
        self.assertTrue(session.child_env({})["PATH"].split(":")[0].endswith("/.local/bin"))

    def test_the_feature_flag_killers_are_removed(self):
        # §6: each of these disables the evaluation Remote Control's availability depends on.
        # The session then starts perfectly and simply never connects — no error, anywhere.
        hostile = {"DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "DISABLE_GROWTHBOOK": "1",
                   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        env = session.child_env(hostile)
        for name in hostile:
            self.assertNotIn(name, env)

    def test_the_api_key_and_base_url_are_removed(self):
        # §6: RC requires the claude.ai subscription login, not an API key.
        env = session.child_env({"ANTHROPIC_API_KEY": "sk-ant-x",
                                 "ANTHROPIC_BASE_URL": "https://proxy.internal"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_every_claude_code_variable_is_stripped_by_prefix(self):
        """§6, and the list is stripped by prefix because it grows between versions.

        These leak in whenever the listener is started by hand from inside a Claude Code
        session, and they confuse the child about which session it is. Hit during the probe.
        """
        env = session.child_env({"CLAUDECODE": "1", "CLAUDE_CODE_SSE_PORT": "1",
                                 "CLAUDE_CODE_ENTRYPOINT": "cli",
                                 "CLAUDE_CODE_SOMETHING_INVENTED_NEXT_VERSION": "1"})
        self.assertEqual([k for k in env if k.startswith("CLAUDE")], [])

    def test_the_hazards_that_miss_the_claude_code_prefix_go_too(self):
        """§14, and the reason it stopped being a footnote the morning §8 was suspended.

        `CLAUDE_PID` and `CLAUDE_EFFORT` miss the `CLAUDE_CODE` prefix by one underscore, and
        `AI_AGENT` misses it altogether — so all three walked through a filter written when
        launchd, which has none of them, was the only way the listener started. Start `bot.sh`
        from a shell inside a Claude Code session and every session the bot spawns inherits
        that session's effort setting and a `CLAUDE_PID` naming somebody else's process, which
        is a launcher quietly downgrading what it launches (§6) by a second route.

        §8 coming back does not retire this. The hand-run path stays supported for debugging,
        and a shell inside a Claude Code session is exactly where debugging happens.
        """
        env = session.child_env({"CLAUDE_PID": "77313", "CLAUDE_EFFORT": "low",
                                 "AI_AGENT": "1", "CLAUDE_INVENTED_NEXT_VERSION": "1"})
        self.assertEqual([k for k in env if k.startswith("CLAUDE")], [])
        self.assertNotIn("AI_AGENT", env)

    def test_an_unrelated_variable_is_left_alone(self):
        # Not an allowlist: the session's own Bash tool wants the ordinary environment. Only
        # the named hazards go.
        env = session.child_env({"SSH_AUTH_SOCK": "/tmp/agent.sock", "TMPDIR": "/tmp/x"})
        self.assertEqual(env["SSH_AUTH_SOCK"], "/tmp/agent.sock")
        self.assertEqual(env["TMPDIR"], "/tmp/x")

    def test_the_real_environment_is_not_mutated(self):
        before = dict(os.environ)
        session.child_env()
        self.assertEqual(dict(os.environ), before)

    def test_no_model_or_effort_flag_is_passed(self):
        """§6 and §15: a launcher that quietly downgrades what it launches is a trap.

        Sessions inherit ~/.claude/settings.json, so one started from the phone is the one you
        would have started at the desk. The difference would show up only as worse answers,
        hours later.
        """
        argv = session.claude_argv("/bin/claude", "centrion-3f2a")
        self.assertNotIn("--model", argv)
        self.assertNotIn("--effort", argv)
        self.assertEqual(argv, ["/bin/claude", "--remote-control", "centrion-3f2a",
                                "--dangerously-skip-permissions"])


class TestMetaIsWrittenAtomically(unittest.TestCase):
    """SPEC.md §3: a listener reading mid-write must never see half a record."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_record_survives_the_round_trip(self):
        record = {"sid": "3f2a91", "state": "live", "url": CAPTURED, "runner_pid": 44213}
        session.write_meta(self.tmp, record)
        self.assertEqual(session.read_meta(self.tmp), record)

    def test_no_temporary_file_is_left_behind(self):
        session.write_meta(self.tmp, {"state": "starting"})
        self.assertEqual(os.listdir(self.tmp), ["meta.json"])

    def test_a_reader_never_sees_a_partial_record(self):
        """The test that would fail against a plain `open(path, "w")`.

        A big record makes the window wide enough to hit reliably: a non-atomic writer truncates
        the file and then fills it, so a reader in the gap gets either nothing or a prefix, and
        `json.loads` raises. §4 has the listener polling this file every 0.25s while a session
        starts, so the window is not theoretical.

        **The two failures are not the same failure, and W3a is where that stopped being a
        detail.** This ran posix-only since W1c because the reader recorded every exception
        alike. On Windows a reader holding the file open makes the *writer's* `os.replace`
        fail, and a writer replacing it makes the *reader's* `open` fail — both
        `PermissionError`, both transient, and neither one a torn record. `read_meta` already
        answers `None` to the first and `write_meta` retries the second. A partial record is a
        `ValueError` out of `json`, it is the only thing this test is about, and measured over
        every run of this spike it has never happened on either platform.

        The retry delay is patched to nothing because this reader is not the listener: it holds
        the file open nearly always, and W3a measured that no retry policy wins against it —
        5 x 20ms lost 147 of 150 writes and 10 x 50ms lost 95. What is asserted here is the
        reader's view. The retry's own timing is two tests up.

        **The millisecond between reads is what makes the Windows half assert anything** (W3b).
        Without it the reader holds the file so continuously that 0 to 8 of the 150 replaces
        land, and in 3 of 32 measured runs *none* did — so `written` was a coin toss, and the
        run it came up tails on was a full suite under the load of W3b's ConPTY tests, failing
        on the guard rather than on the property. With the pause 38 to 42 land, every run, busy
        or idle. It is still nothing like the listener's quarter-second poll, and the reader is
        still open across the whole of every `json.loads` — which is the part that matters.
        """
        path = os.path.join(self.tmp, "meta.json")
        session.write_meta(self.tmp, {"state": "starting", "pad": "x" * 100000})
        torn, denied, reads = [], [], []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    with open(path) as fh:
                        json.loads(fh.read())
                    reads.append(1)
                except ValueError as e:
                    torn.append(repr(e))
                    return
                except OSError as e:
                    denied.append(repr(e))
                time.sleep(0.001)          # see the docstring: the writer needs a way in

        watcher = threading.Thread(target=reader)
        watcher.start()
        written = lost = 0
        try:
            with mock.patch.object(session, "META_RETRY_DELAY", 0):
                for i in range(150):
                    try:
                        session.write_meta(self.tmp,
                                           {"state": "live", "pad": "y" * (100000 + i % 3)})
                        written += 1
                    except PermissionError:
                        lost += 1
        finally:
            stop.set()
            watcher.join()

        self.assertEqual(torn, [], "a reader saw a partial meta.json")
        self.assertTrue(reads, "the reader never read anything; the test asserted nothing")
        self.assertTrue(written, "every write was refused; the test asserted nothing")
        if POSIX:
            # Neither sharing failure exists here, and if one ever appears it is news.
            self.assertEqual(denied, [], "a reader was refused the file on a platform that "
                                         "has no sharing violations")
            self.assertEqual(lost, 0, "a write was refused on a platform where rename over an "
                                      "open file always works")

    def test_a_replace_the_listener_refuses_is_retried(self):
        """WINDOWS.md §4, W3a: `os.replace` onto a file another process has open.

        `read_meta` opens, parses and closes in microseconds, and the listener does it once a
        tick — but `os.replace` over a target held by any handle without `FILE_SHARE_DELETE`
        is a `PermissionError` on Windows, and the moment it matters is the one write that
        carries the URL. Measured on this box (W3a) a listener polling at `bot.SESSION_POLL`
        refused 22 of 150 writes outright; five tries 20ms apart lost none, three runs running.
        """
        calls = []
        real = os.replace

        def refused_twice(source, target):
            calls.append(target)
            if len(calls) <= 2:
                raise PermissionError(13, "Permission denied")
            real(source, target)

        slept = []
        with mock.patch.object(session.os, "replace", refused_twice), \
                mock.patch.object(session.time, "sleep", slept.append):
            session.write_meta(self.tmp, {"state": "live", "url": CAPTURED})

        self.assertEqual(len(calls), 3, "it did not retry")
        self.assertEqual(slept, [session.META_RETRY_DELAY] * 2, "it waited the wrong amount")
        self.assertEqual(session.read_meta(self.tmp)["url"], CAPTURED)
        self.assertEqual(os.listdir(self.tmp), ["meta.json"], "a temporary file was left behind")

    def test_a_replace_that_works_first_time_does_not_wait(self):
        slept = []
        with mock.patch.object(session.time, "sleep", slept.append):
            session.write_meta(self.tmp, {"state": "starting"})
        self.assertEqual(slept, [], "it slept on the happy path")

    def test_it_gives_up_rather_than_retrying_forever(self):
        """Five tries and 80ms of waiting, then the error is the caller's.

        The runner's read loop is the session's life (§2) and this is called from inside it, so
        the retry is bounded at something a terminal read can afford to miss. Giving up is not
        silent: `write_meta` raises what `os.replace` raised, `bot.py`'s caller already catches
        `OSError`, and the temporary file is cleaned up so the next write is not confused by it.
        """
        always = mock.Mock(side_effect=PermissionError(13, "Permission denied"))
        with mock.patch.object(session.os, "replace", always), \
                mock.patch.object(session.time, "sleep", lambda _: None):
            with self.assertRaises(PermissionError):
                session.write_meta(self.tmp, {"state": "live"})
        self.assertEqual(always.call_count, session.META_RETRY_TRIES)
        self.assertEqual(os.listdir(self.tmp), [],
                         "the abandoned temporary file was left in the session directory")

    def test_a_missing_record_reads_as_nothing(self):
        self.assertIsNone(session.read_meta(os.path.join(self.tmp, "nope")))

    def test_a_corrupt_record_reads_as_nothing_rather_than_raising(self):
        # The listener walks var/sessions/*/meta.json on every tick (§4). One unreadable
        # directory must not end the reconciliation pass, or one bad session hides all of them.
        with open(os.path.join(self.tmp, "meta.json"), "w") as fh:
            fh.write("{not json")
        self.assertIsNone(session.read_meta(self.tmp))


class TestTheRunner(unittest.TestCase):
    """The whole thing, against a fake `claude` that prints a link and waits."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions = os.path.join(self.tmp, "sessions")
        self.root = os.path.join(self.tmp, "Projects")
        self.work = os.path.join(self.root, "beacon")
        os.makedirs(self.sessions)
        os.makedirs(self.work)

    def runner(self, script, **kw):
        kw.setdefault("project", "beacon")
        kw.setdefault("chat_id", 1908330607)
        kw.setdefault("cwd", self.work)
        kw.setdefault("projects_root", self.root)
        return session.Runner("3f2a91", kw.pop("cwd"), "beacon-3f2a", root=self.sessions,
                              argv=["/bin/sh", "-c", script], log=lambda m: None, **kw)

    @posix_only
    def test_it_records_the_link_and_goes_live(self):
        r = self.runner("printf '\\033[32mhere: %s\\033[0m\\r\\n'; sleep 0.4" % CAPTURED)
        r.run()
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["url"], CAPTURED)
        self.assertEqual(meta["state"], session.ENDED)
        self.assertEqual(meta["project"], "beacon")
        self.assertEqual(meta["chat_id"], 1908330607)
        self.assertEqual(meta["cwd"], self.work)

    def test_the_record_says_starting_before_anything_is_spawned(self):
        # §4.6: the listener starts polling the moment it has spawned the runner, so the file
        # has to exist by then or the poll reads "no such session" and gives up.
        r = self.runner("sleep 0.2")
        r.begin()
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["state"], session.STARTING)
        self.assertEqual(meta["runner_pid"], os.getpid())
        self.assertIsNone(meta["url"])

    @posix_only
    def test_a_child_that_dies_without_a_link_is_failed_not_ended(self):
        """§4.6 sends the pty tail to the phone on `failed`, and §9.7 is why it matters.

        The likeliest failure after week one is an expired claude.ai login: the session comes up
        into a /login prompt and waits for a human who is not there. Nothing crashes. `failed`
        versus `ended` is what tells the listener to send the tail that makes that obvious.
        """
        r = self.runner("echo 'Invalid API key · Please run /login'; exit 1")
        r.run()
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["state"], session.FAILED)
        self.assertIsNone(meta["url"])

    @posix_only
    def test_the_transcript_is_kept_in_full(self):
        r = self.runner("printf 'line one\\r\\nline two\\r\\n'")
        r.run()
        with open(os.path.join(r.dir, "pty.log"), "rb") as fh:
            log = fh.read()
        self.assertIn(b"line one", log)
        self.assertIn(b"line two", log)

    @posix_only
    def test_the_transcript_keeps_its_escapes(self):
        # §3 calls pty.log the full ANSI transcript, and §12's own fixture came from one. A
        # stripped log could never be replayed into a test like this file's.
        r = self.runner("printf '\\033[32mgreen\\033[0m\\r\\n'")
        r.run()
        with open(os.path.join(r.dir, "pty.log"), "rb") as fh:
            self.assertIn(b"\x1b[32m", fh.read())

    def test_the_session_directory_is_made_if_it_is_not_there(self):
        r = self.runner("true")
        self.assertFalse(os.path.exists(r.dir))
        r.begin()
        self.assertTrue(os.path.isdir(r.dir))

    @posix_only
    def test_an_initial_prompt_is_typed_after_the_link_appears(self):
        """§4: type it only once the session is live, then Enter separately.

        Before `live` there is no input box to type into — the text would land in whatever the
        renderer was drawing and be lost. The separate Enter is the same care: sent with the
        text, it arrives while the box is still settling.
        """
        r = self.runner("printf '%s\\r\\n'; read line" % CAPTURED,
                        prompt="fix the probe test")
        r.run()
        with open(os.path.join(r.dir, "pty.log"), "rb") as fh:
            log = fh.read()
        self.assertIn(b"fix the probe test", log, "the prompt was never typed")

    @posix_only
    def test_no_prompt_means_nothing_is_typed(self):
        # Otherwise a bare `claude beacon` would put a stray newline into a fresh session.
        # Exact equality: the transcript is what the child printed and nothing else, so a
        # stray Enter would show up as the tty echoing it back.
        r = self.runner("printf '%s\\r\\n'; sleep 0.5" % CAPTURED)
        r.run()
        with open(os.path.join(r.dir, "pty.log"), "rb") as fh:
            body = fh.read()
        # Line endings are the tty's own doing — ONLCR turns the child's \n into \r\n. What
        # matters is that nothing else is on this terminal: a typed prompt or a stray Enter
        # would be echoed back by the line discipline and show up here.
        self.assertEqual(body.replace(b"\r", b"").replace(b"\n", b""), CAPTURED.encode())

    def test_a_directory_outside_the_root_is_refused_before_anything_is_spawned(self):
        """§3: the listener resolves, and the runner re-checks before chdir.

        Cheap, and it means a bug in command parsing — or anything that can reach the runner's
        argv — cannot become an arbitrary working directory for a session that has permissions
        bypassed. The check is `samefile` rather than `==` because §9.9: this volume is
        case-insensitive and realpath() is not, so `beacon` and `BEACON` name one directory
        through two strings that compare unequal.
        """
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside)
        r = self.runner("echo SHOULD NOT RUN", cwd=outside, project="elsewhere")
        r.run()
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["state"], session.FAILED)
        self.assertIsNone(meta["claude_pid"], "it spawned something anyway")

    def test_a_terminal_that_cannot_be_started_is_failed_not_a_traceback(self):
        """WINDOWS.md W3b: a spawn that raises leaves a record, not a stack trace.

        On the Mac `spawn` barely has a failure: the fork succeeds whatever the binary is, and
        the child reports the exec failure on the pty itself (session_posix.spawn writes it to
        fd 2 and exits 126), so it arrives as ordinary output and §4.6 sends it to the phone.
        Windows has no such child — `CreateProcess` fails before one exists and
        `session_win.spawn` raises `OSError` — and without this the runner dies with a
        traceback, leaving meta.json saying `starting` for a session that will never start:
        the listener waits out its full timeout and the phone is told nothing.
        """
        r = self.runner("true")
        with mock.patch.object(session, "spawn", side_effect=OSError("could not start it")):
            self.assertEqual(r.run(), session.FAILED)
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["state"], session.FAILED)
        self.assertIn("could not start it", meta["error"])
        self.assertIsNone(meta["claude_pid"], "it recorded a pid for a child that never was")

    def test_the_terminal_is_handed_to_terminate_and_closed_after_it(self):
        """WINDOWS.md §4 and W3e: the order in `run`'s `finally`, and what goes with it.

        Two things, and the second is why this test is not just the seam test above again.
        `terminate` gets the terminal because on Windows the polite stop is two bytes written
        to it and the job that takes the tree is held by it (W3e). And it is closed *after*
        that call and not before, on both platforms: closing hangs up a Mac child that may not
        yet have taken the pty as its controlling terminal (§9.10) and ends a Windows one
        outright within half a second (measured in W3b), either of which turns the graceful
        path into a kill that happens to look like one.

        `catch_signals` is stood aside from because it is not what this test is about, and on
        Windows it is still W3f's stub.
        """
        term = FakeTerminal([b"nothing useful\r\n"])
        order = []
        term.close = lambda: order.append("close")
        r = self.runner("true")
        with mock.patch.object(session, "spawn", return_value=(4242, term)), \
                mock.patch.object(session.procs, "catch_signals", return_value=None), \
                mock.patch.object(session.procs, "restore_signals"), \
                mock.patch.object(session, "terminate",
                                  side_effect=lambda *a, **kw: order.append(kw)) as terminate:
            r.run()
        terminate.assert_called_once_with(4242, log=r.log, terminal=term)
        self.assertEqual([type(step) for step in order], [dict, str],
                         "the terminal was closed before terminate could use it")

    @posix_only
    def test_a_miscased_project_still_passes_the_recheck(self):
        # §9.9 again, from the other side: the spelling that arrives is the spelling that was
        # sent, and the re-check must not reject a real directory over its case.
        r = self.runner("printf '%s\\r\\n'" % CAPTURED,
                        cwd=os.path.join(self.root, "BEACON"), project="BEACON")
        r.run()
        self.assertEqual(session.read_meta(r.dir)["state"], session.ENDED)


@posix_only
class TestKillingTheRunner(unittest.TestCase):
    """§12's manual check, automated: kill the runner and claude dies with it."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "Projects", "beacon")
        os.makedirs(self.work)

    def test_a_sigterm_to_the_runner_ends_the_session_and_says_so(self):
        """The whole point of the runner being a separate process is that it can be killed.

        What must not happen is the runner dying and leaving claude behind — a session with
        permissions bypassed, no longer reachable from the phone, and invisible to `ls` because
        its record still says `live`. So the runner catches the signal, ends the session, and
        writes a truthful record before it goes.
        """
        code = (
            "import os, sys, session\n"
            "r = session.Runner('3f2a91', %r, 'beacon-3f2a', root=%r,\n"
            "                   argv=['/bin/sh', '-c', 'printf \"%%s\\\\r\\\\n\"; sleep 60'],\n"
            "                   project='beacon', projects_root=%r, log=lambda m: None)\n"
            "r.run()\n" % (self.work, os.path.join(self.tmp, "sessions"),
                            os.path.join(self.tmp, "Projects"))
        ) % CAPTURED
        runner = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(runner.stdout.close)
        self.addCleanup(runner.stderr.close)
        directory = os.path.join(self.tmp, "sessions", "3f2a91")

        meta = None
        for _ in range(100):                       # wait for it to come up live
            meta = session.read_meta(directory)
            if meta and meta["state"] == session.LIVE:
                break
            time.sleep(0.05)
        self.assertIsNotNone(meta, "the runner never wrote a record")
        self.assertEqual(meta["state"], session.LIVE, meta)
        claude = meta["claude_pid"]

        runner.terminate()
        runner.wait(timeout=20)

        for _ in range(100):
            try:
                os.kill(claude, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            os.kill(claude, signal.SIGKILL)
            self.fail("claude outlived the runner that was killed")

        self.assertEqual(session.read_meta(directory)["state"], session.ENDED,
                         "the record must not still say `live` after the runner is gone")


@posix_only
class TestDetaching(unittest.TestCase):
    """SPEC.md §8. Tested in a subprocess, because a setsid() here would detach the runner."""

    def child(self, body):
        code = ("import os, sys\n"
                "sys.path.insert(0, %r)\n"
                "import session\n" % ROOT) + body
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def test_the_inherited_lock_fd_is_closed(self):
        """§8, verified on this box and the nastiest delayed failure in the design.

        `exec 9>>` in launchd/bot.sh sets no close-on-exec flag, so fd 9 survives into python
        and into every detached runner it forks. A runner that keeps it keeps the flock: the
        listener exits, its sessions look perfectly healthy, and launchd's restarted listener
        can never take the lock again — so the bot goes silent for good.
        """
        r = self.child(
            "fd = os.open(os.devnull, os.O_RDWR)\n"
            "os.dup2(fd, 9)\n"
            "session.detach()\n"
            "try:\n"
            "    os.fstat(9)\n"
            "    print('STILL OPEN')\n"
            "except OSError:\n"
            "    print('CLOSED')\n")
        self.assertIn(b"CLOSED", r.stdout, r.stderr)

    def test_it_becomes_its_own_session_leader(self):
        # §2: launchd restarts the listener on every crash and logout. If the runner stayed in
        # the listener's session, each restart would SIGHUP every live session and kill work
        # mid-turn. Sessions must outlive their launcher.
        r = self.child("session.detach()\n"
                       "print('LEADER', os.getsid(0) == os.getpid())\n")
        self.assertIn(b"LEADER True", r.stdout, r.stderr)

    def test_it_does_not_mind_when_there_is_no_lock_fd(self):
        # The hand-run case in §12's manual step: nobody opened fd 9, and detaching must not
        # be the thing that fails.
        r = self.child("session.detach()\nprint('OK')\n")
        self.assertIn(b"OK", r.stdout, r.stderr)


class TestTheTranscriptDoesNotGrowForever(unittest.TestCase):
    """Slice 10. §3 calls pty.log the full ANSI transcript, and it meant *full* literally.

    A session is a 200-column terminal that redraws itself, and the runner appends every byte
    of it for as long as the session lives — days, for a session left open on purpose. Nothing
    trimmed it and nothing was watching it: the only bound on var/ was how long somebody
    happened to leave a session running, which is the one variable this bot exists to make
    large.

    Two files, not a numbered series. One previous transcript is what the tail has to be able
    to reach back into (§4.6 sends it when there is no link), and any more is a debugging
    archive nobody on a phone is going to read.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "pty.log")

    def transcript(self, cap=1024):
        t = session.Transcript(self.path, cap=cap, log=lambda m: None)
        self.addCleanup(t.close)
        return t

    def read(self, name="pty.log"):
        with open(os.path.join(self.tmp, name), "rb") as fh:
            return fh.read()

    def test_a_quiet_session_stays_one_file(self):
        t = self.transcript()
        t.write(b"x" * 200)
        self.assertEqual(self.read(), b"x" * 200)
        self.assertFalse(os.path.exists(self.path + ".1"),
                         "nothing was rotated and there is nothing to keep")

    def test_it_rotates_once_it_passes_its_cap(self):
        t = self.transcript(cap=1024)
        t.write(b"a" * 1000)
        self.assertFalse(os.path.exists(self.path + ".1"))
        t.write(b"b" * 100)
        self.assertTrue(os.path.exists(self.path + ".1"), "the cap was passed and nothing moved")

    def test_the_rotated_file_holds_everything_written_up_to_the_rotation(self):
        # The rotation is a rename, so the bytes are not rewritten and not lost — which is what
        # makes the tail able to reach back through it.
        t = self.transcript(cap=1024)
        t.write(b"a" * 600)
        t.write(b"b" * 600)
        self.assertEqual(self.read("pty.log.1"), b"a" * 600 + b"b" * 600)

    def test_what_is_written_after_a_rotation_goes_to_the_live_file(self):
        t = self.transcript(cap=1024)
        t.write(b"a" * 1200)
        t.write(b"the newest line")
        self.assertEqual(self.read(), b"the newest line")

    def test_only_one_previous_transcript_is_ever_kept(self):
        t = self.transcript(cap=1024)
        for _ in range(10):
            t.write(b"c" * 1100)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["pty.log", "pty.log.1"])

    def test_the_two_files_together_stay_inside_twice_the_cap(self):
        """The bound that makes var/ predictable: two files, each of which is renamed away the
        moment it passes the cap. §10.7 multiplies this by `max_sessions` and gets a number."""
        t = self.transcript(cap=1024)
        for _ in range(50):
            t.write(b"d" * 300)
        total = sum(os.path.getsize(os.path.join(self.tmp, n)) for n in os.listdir(self.tmp))
        self.assertLessEqual(total, 2 * 1024 + 300,
                             "a chunk may straddle the cap; fifty of them may not")

    def test_a_single_write_larger_than_the_cap_still_rotates(self):
        # 64 KB is one read() off the master and the cap is measured after the write, not
        # before it, so the file is briefly over — rotating on the next write is what keeps a
        # noisy session from being the case the cap does not cover.
        t = self.transcript(cap=1024)
        t.write(b"e" * 4096)
        t.write(b"f")
        self.assertEqual(self.read(), b"f")
        self.assertEqual(len(self.read("pty.log.1")), 4096)

    def test_it_picks_up_the_size_of_a_transcript_that_is_already_there(self):
        """A runner re-opening a session directory — or the `--foreground` hand-run of §12
        against one that has already been used — must not start counting from zero, or the cap
        is one cap per open rather than one cap."""
        with open(self.path, "wb") as fh:
            fh.write(b"g" * 1000)
        t = self.transcript(cap=1024)
        t.write(b"h" * 100)
        self.assertTrue(os.path.exists(self.path + ".1"))

    def test_the_transcript_is_closed_before_it_is_renamed(self):
        """Tidy on the Mac, load-bearing here. WINDOWS.md §4.

        POSIX will rename a file that is still open and the handle follows it, so the order of
        these two lines has never mattered on the Mac. On Windows a rename of a file open
        without `FILE_SHARE_DELETE` — which is every handle Python's `open` hands out — fails
        with `PermissionError`, and `rotate` catches that by switching the cap off for the rest
        of the session. So the wrong order here is not a crash; it is a transcript that quietly
        stops being bounded, which is the whole of what slice 10 exists to prevent.
        """
        t = self.transcript(cap=1024)
        saw = {}

        def recording(source, target):
            saw["closed"] = t.fh.closed
            os.replace(source, target)

        t.replace = recording
        t.write(b"x" * 1100)
        self.assertTrue(saw, "the transcript never rotated; the test asserted nothing")
        self.assertTrue(saw["closed"],
                        "rotate() renamed pty.log while it was still open — on Windows that is "
                        "a PermissionError and the cap switches itself off")

    def test_a_rotation_that_cannot_be_done_does_not_stop_the_session(self):
        """The runner's read loop is the session's life: it holds the pty open (§2), and a
        rotation that raised — or that failed and was retried on every write — would take the
        session down with it, or peg a core and stop reading the terminal."""
        t = self.transcript(cap=1024)
        t.replace = _raises
        t.write(b"i" * 1100)
        t.write(b"j" * 1100)
        t.write(b"k")
        self.assertIn(b"k", self.read(), "the transcript stopped being written")

    @posix_only
    def test_the_runner_caps_a_noisy_session(self):
        """End to end, through a real pty: the child prints more than the cap and the two
        files on disk are still bounded when it is over."""
        sessions = os.path.join(self.tmp, "sessions")
        root = os.path.join(self.tmp, "Projects")
        work = os.path.join(root, "beacon")
        os.makedirs(work)
        r = session.Runner("3f2a91", work, "beacon-3f2a", root=sessions, project="beacon",
                           projects_root=root, log=lambda m: None,
                           argv=["/bin/sh", "-c", "for i in 1 2 3 4 5 6 7 8; do "
                                                  "dd if=/dev/zero bs=1024 count=8 2>/dev/null "
                                                  "| tr '\\0' 'z'; done"])
        cap, session.TRANSCRIPT_CAP = session.TRANSCRIPT_CAP, 8192
        try:
            r.run()
        finally:
            session.TRANSCRIPT_CAP = cap
        sizes = {n: os.path.getsize(os.path.join(r.dir, n))
                 for n in os.listdir(r.dir) if n.startswith("pty.log")}
        self.assertEqual(sorted(sizes), ["pty.log", "pty.log.1"], sizes)
        import session_posix
        self.assertLessEqual(sum(sizes.values()), 2 * 8192 + session_posix.READ_SIZE, sizes)


def _raises(*_args):
    raise OSError(errno.EACCES, "read-only file system")



class TestTheTrustDialog(unittest.TestCase):
    """§9.3, and slice 11 is what stops it being true.

    A directory created in `~/Projects` after Claude Code last saw it comes up to a *Quick
    safety check* — verified on this box at last, under `--dangerously-skip-permissions`, where
    the spec had only inferred it from a flag set on all 46 project entries. The runner sits
    there until the 45s deadline and the phone gets a panel instead of a link.

    **The default selection is `No, exit`.** That is the finding that decides the shape of the
    answer: pressing Enter — the obvious "just confirm it" — ends the session. The answer is
    Down, then Enter, and in between the one thing that makes it safe to send at all, which is
    checking that the marker actually moved onto the option this code means to choose. A UI
    that reorders these two must leave the session hanging (which is today's behaviour, and is
    honest) rather than confirm whatever is now second.
    """

    def trust(self, **kw):
        t = session.Trust(**kw)
        self.addCleanup(lambda: None)
        return t

    def dialog(self, marked=0, second="Yes, I trust this folder"):
        return trust_panel(marked, second)

    def test_nothing_is_sent_before_the_dialog_is_there(self):
        t = self.trust()
        self.assertIsNone(t.feed(b"\x1b[2J starting up\r\n", 0.0))
        self.assertIsNone(t.feed(b"", 10.0))

    def test_it_presses_down_once_the_panel_has_settled(self):
        t = self.trust()
        self.assertIsNone(t.feed(self.dialog(), 0.0), "it answered a half-drawn panel")
        self.assertEqual(t.feed(b"", session.SETTLE), session.DOWN)

    def test_it_confirms_only_after_the_marker_has_moved(self):
        t = self.trust()
        t.feed(self.dialog(), 0.0)
        self.assertEqual(t.feed(b"", session.SETTLE), session.DOWN)
        # The redraw the real one sends back within milliseconds.
        self.assertIsNone(t.feed(self.dialog(marked=1), session.SETTLE))
        self.assertEqual(t.feed(b"", 2 * session.SETTLE), session.ENTER)

    def test_a_marker_that_never_moves_is_never_confirmed(self):
        """If Down did not select what this code thinks it selected, the session hangs and the
        phone gets the panel at 45s. That is the failure this is *choosing* — the alternative
        is confirming an option nobody has read."""
        t = self.trust()
        t.feed(self.dialog(), 0.0)
        self.assertEqual(t.feed(b"", session.SETTLE), session.DOWN)
        for tick in range(2, 60):
            self.assertIsNone(t.feed(self.dialog(marked=0), tick * session.SETTLE))

    def test_a_second_option_that_is_not_the_yes_is_not_even_reached(self):
        # A dialog that has been reworded is a dialog this code does not understand, and the
        # safe thing to do with one is nothing at all.
        t = self.trust()
        self.assertIsNone(t.feed(self.dialog(second="Yes, and do not ask again"), 0.0))
        self.assertIsNone(t.feed(b"", 10 * session.SETTLE))

    def test_the_words_are_matched_without_their_spacing(self):
        """The renderer writes `CSI <n> G` between words instead of spaces, so the stripped
        transcript is one run of letters. A matcher written against what a human sees on the
        screen matches nothing at all here — which is the §9.5 mistake in another costume."""
        t = self.trust()
        spaced = ("Is this a project you created or one you trust?\r\n"
                  "\u276f No, exit\r\nYes, I trust this folder\r\n").encode()
        t.feed(spaced, 0.0)
        self.assertEqual(t.feed(b"", session.SETTLE), session.DOWN,
                         "it only recognises one of the two spellings")

    def test_it_answers_once_and_then_stays_quiet(self):
        # The panel redraws constantly. A second Enter goes into whatever replaced it.
        t = self.trust()
        t.feed(self.dialog(), 0.0)
        t.feed(b"", session.SETTLE)
        t.feed(self.dialog(marked=1), session.SETTLE)
        self.assertEqual(t.feed(b"", 2 * session.SETTLE), session.ENTER)
        for tick in range(3, 20):
            self.assertIsNone(t.feed(self.dialog(marked=1), tick * session.SETTLE))

    def test_it_does_not_hold_the_whole_session_in_memory(self):
        t = self.trust()
        for _ in range(200):
            t.feed(b"x" * 4096, 0.0)
        self.assertLessEqual(len(t.text), session.TRUST_KEEP * 2)


@posix_only
class TestAFreshDirectoryComesUpToALink(unittest.TestCase):
    """The other half of §9.3, through a real pty against a fake that insists on a real answer.

    The fake exits when it is sent a bare Enter, because the real one does: `No, exit` is the
    default selection. So a link coming back is proof of the whole sequence — the panel was
    recognised, Down moved the marker, the marker was checked, and only then was it confirmed.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions = os.path.join(self.tmp, "sessions")
        self.root = os.path.join(self.tmp, "Projects")
        self.work = os.path.join(self.root, "scratchpad")
        os.makedirs(self.sessions)
        os.makedirs(self.work)

    #: How long the fake waits for a keystroke before giving up. The answer takes two SETTLEs
    #: — recognise, Down, check the marker, Enter — so a second is well over twice as long as
    #: an answer that is coming needs, and it is what the three tests below spend proving that
    #: none is. The suite is 7 seconds and slice 7 worked hard for that; a test that waits out
    #: a timeout it could have bounded is how it goes back to 40.
    QUIET = "1.0"

    def runner(self, trust=True, options="", idle=QUIET):
        argv = [sys.executable, "-c", FAKE_CLAUDE, options, idle]
        return session.Runner("3f2a91", self.work, "scratchpad-3f2a", root=self.sessions,
                              argv=argv, log=lambda m: None, project="scratchpad",
                              projects_root=self.root, trust=trust)

    def test_the_directory_the_bot_just_made_comes_up_to_a_link(self):
        r = self.runner(idle="10.0")
        r.run()
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["url"], CAPTURED, "no link: the dialog was never answered")
        with open(os.path.join(r.dir, session.TRANSCRIPT), "rb") as fh:
            self.assertNotIn(b"exiting", fh.read(), "it pressed Enter on `No, exit`")

    def test_without_the_flag_the_dialog_is_left_alone(self):
        """`claude beacon` must never answer it. §9.3's argument for answering at all is that
        the directory was created empty by this bot a second earlier — a directory somebody
        else made, or a repository cloned into the root, is exactly what the dialog is for."""
        r = self.runner(trust=False)
        r.run()
        meta = session.read_meta(r.dir)
        self.assertIsNone(meta["url"])
        self.assertEqual(meta["state"], session.FAILED)
        with open(os.path.join(r.dir, session.TRANSCRIPT), "rb") as fh:
            self.assertIn(b"trust", fh.read(), "the panel should still be in the transcript")

    def test_a_directory_with_anything_in_it_is_never_trusted(self):
        """The listener says `new`, and the runner still checks. `new beacon` on a directory
        that is already a repository is the case: the verb says create, the directory says
        otherwise, and what is in it is not this bot's to trust away."""
        open(os.path.join(self.work, "README.md"), "w").close()
        r = self.runner(trust=True)
        r.run()
        self.assertIsNone(session.read_meta(r.dir)["url"])

    def test_a_reworded_dialog_is_left_hanging_rather_than_confirmed(self):
        r = self.runner(options="Yes, and remember this")
        r.run()
        meta = session.read_meta(r.dir)
        self.assertIsNone(meta["url"])
        with open(os.path.join(r.dir, session.TRANSCRIPT), "rb") as fh:
            self.assertNotIn(b"exiting", fh.read(), "it confirmed an option it did not read")



if __name__ == "__main__":
    unittest.main()


#: WINDOWS.md W3d. A `Terminal` made of a script, so `pump` can be driven without a process.
class FakeTerminal:
    """The four calls `pump` makes, over a list of chunks instead of a pty.

    W3d's whole change is that `pump` names `read`, `write`, `alive` and `close` and nothing
    else — no fd, no `select`, no `os.read` — so the loop that was previously reachable only
    on the Mac behind a fork is reachable everywhere. This class is what that buys.

    What it deliberately does not stand in for is how a terminal *ends*. A pty master says
    EIO and a pseudoconsole raises `WinptyError`, and both of those are facts about the real
    mechanisms with their own tests against real processes (`TestTheTerminalSize` here,
    `tests/test_session_win.py` there). `gap` is the one piece of that shape this does carry,
    because `pump` has a branch for it: on Windows the last of a child's output is still
    readable after `alive()` has gone false (§4.6, measured in W3b), so an empty read against
    a dead terminal is not the end of the session until one more read has come back empty too.
    """

    def __init__(self, chunks=(), tail=(), gap=0, forever=False):
        self.chunks = list(chunks)
        self.tail = list(tail)
        self.gap = gap
        # W3f: a session that is up and waiting for its human, which is every session the
        # phone ever stops. Running out of chunks is how the others end, and a terminal that
        # ends on its own can say nothing about the mechanism that ends the ones that do not.
        #
        # Not actually forever: after PATIENCE it gives up and reports itself finished, so a
        # `pump` that never notices the marker *fails* the test rather than wedging the suite.
        # A hanging test says nothing about what is wrong; this one says which assertion.
        self.forever = forever
        self.deadline = None
        self.written = bytearray()
        self.reads = 0
        self.closed = False

    def read(self, timeout=0.0):
        self.reads += 1
        if self.chunks:
            return self.chunks.pop(0)
        if self.gap:
            self.gap -= 1
            return b""
        if self.tail:
            return self.tail.pop(0)
        if self.forever:
            time.sleep(timeout)     # a real Terminal.read waits out its deadline before b""
        return b""

    def alive(self):
        if self.chunks:
            return True
        if not self.forever:
            return False
        if self.deadline is None:
            self.deadline = time.monotonic() + PATIENCE
        return time.monotonic() < self.deadline

    def write(self, data):
        self.written += data

    def close(self):
        self.closed = True


class TestPumpOverATerminal(unittest.TestCase):
    """WINDOWS.md W3d: `pump` reads a `Terminal`, and the loop is one implementation.

    Until this slice `pump` held a pty master fd and called `select` and `os.read` on it, so
    every test of it had to fork — which is why everything that reached it was `@posix_only`
    and why the Windows half of the runner had no test of its own loop at all. The waiting
    now lives behind `Terminal.read(timeout)` on both platforms, and what is left here is the
    part that was always portable: what to do with a chunk, when to answer §9.3's dialog, and
    when to type the prompt.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions = os.path.join(self.tmp, "sessions")

    def runner(self, **kw):
        kw.setdefault("project", "beacon")
        shutil.rmtree(os.path.join(self.sessions, "3f2a91"), ignore_errors=True)
        r = session.Runner("3f2a91", self.tmp, "beacon-3f2a", root=self.sessions,
                           argv=["/bin/true"], log=lambda m: None, **kw)
        r.begin()
        return r

    def transcript(self, r):
        t = session.Transcript(os.path.join(r.dir, session.TRANSCRIPT), log=lambda m: None)
        self.addCleanup(t.close)
        return t

    def written(self, r):
        with open(os.path.join(r.dir, session.TRANSCRIPT), "rb") as fh:
            return fh.read()

    def drive(self, terminal, **kw):
        r = self.runner(**kw)
        t = self.transcript(r)
        r.pump(terminal, t)
        t.close()
        return r

    def test_pump_with_fake_terminal(self):
        """The slice's named test: a real capture, in chunks, and a live session out of it.

        Both captures, because the two renderers are what W0a existed to tell apart — the Mac
        fixture is the program's own bytes and the Windows one is ConPTY's redraw of them —
        and `pump` is now the same code over either.
        """
        for name, path in (("mac", FIXTURE), ("win", FIXTURE_WIN)):
            if not os.path.exists(path):
                continue                    # the Windows capture is W0a's, on that box only
            with self.subTest(capture=name):
                with open(path, "rb") as fh:
                    capture = fh.read()
                url = session.extract_url(capture)
                r = self.drive(FakeTerminal(
                    [capture[i:i + 64] for i in range(0, len(capture), 64)]))
                self.assertEqual(r.scrape.url, url)
                meta = session.read_meta(r.dir)
                self.assertEqual(meta["state"], session.LIVE)
                self.assertEqual(meta["url"], url)
                self.assertEqual(self.written(r), capture,
                                 "the transcript is not what the terminal produced")

    def test_pump_returns_when_the_terminal_is_finished(self):
        """An empty read is not the end; an empty read against a dead terminal is — after one
        more read, which is what the count here pins."""
        term = FakeTerminal([b"one ", b"two"])
        r = self.drive(term)
        self.assertEqual(self.written(r), b"one two")
        self.assertEqual(term.reads, 4, "two chunks, the empty read, and the drain")

    def test_the_last_of_a_dead_childs_output_is_not_lost(self):
        """§4.6, and the reason a `failed` session has anything to send the phone.

        On Windows the child is gone before the pseudoconsole has handed over the last of
        what it wrote (measured in W3b), and that last part is precisely the error text — a
        bad flag, an expired login — the tail is made of. So a read that comes back empty
        against a dead terminal costs one more read before the loop gives up.
        """
        term = FakeTerminal([b"starting\r\n"], gap=1, tail=[b"error: not logged in\r\n"])
        r = self.drive(term)
        self.assertEqual(self.written(r), b"starting\r\nerror: not logged in\r\n")

    def test_a_link_in_the_tail_still_goes_live(self):
        # The drain is a whole pass of the loop body and not just a write to the transcript.
        term = FakeTerminal([b"..."], gap=1, tail=[("here %s\r\n" % CAPTURED).encode()])
        r = self.drive(term)
        self.assertEqual(r.scrape.url, CAPTURED)
        self.assertEqual(session.read_meta(r.dir)["state"], session.LIVE)

    def test_a_session_with_no_link_leaves_the_record_alone(self):
        r = self.drive(FakeTerminal([b"no link here\r\n"]))
        self.assertIsNone(r.scrape.url)
        self.assertEqual(session.read_meta(r.dir)["state"], session.STARTING)

    def test_stopping_ends_the_loop_without_reading_the_rest(self):
        """What a signal (the Mac) or the stop marker (W3f, Windows) does to a live session."""
        r = self.runner()
        t = self.transcript(r)
        term = FakeTerminal([b"a"] * 50)
        original = term.read

        def read(timeout=0.0):
            r.stopping = True
            return original(timeout)

        term.read = read
        r.pump(term, t)
        t.close()
        self.assertEqual(self.written(r), b"a", term.reads)

    def test_the_prompt_is_typed_only_once_there_is_a_link(self):
        """§4: before the session is live there is no input box, and the text lands in
        whatever the renderer was drawing."""
        settle, session.SETTLE = session.SETTLE, 0.0
        self.addCleanup(setattr, session, "SETTLE", settle)
        term = FakeTerminal([b"warming up\r\n"] + [b""] * 4
                            + [("here %s\r\n" % CAPTURED).encode()] + [b""] * 4)
        self.drive(term, prompt="fix the probe test")
        self.assertEqual(bytes(term.written), b"fix the probe test\r")

    def test_nothing_is_typed_into_a_session_that_never_went_live(self):
        term = FakeTerminal([b"no link here\r\n"] + [b""] * 4)
        self.drive(term, prompt="fix the probe test")
        self.assertEqual(bytes(term.written), b"")

    def test_enter_waits_for_the_box_to_settle(self):
        """Separately, and after SETTLE, for the same reason the prompt waits for the link."""
        settle, session.SETTLE = session.SETTLE, 30.0
        self.addCleanup(setattr, session, "SETTLE", settle)
        term = FakeTerminal([("here %s\r\n" % CAPTURED).encode()] + [b""] * 6)
        self.drive(term, prompt="hello")
        self.assertEqual(bytes(term.written), b"hello", "Enter went before the box settled")

    def test_the_trust_dialog_is_answered_through_the_terminal(self):
        """§9.3 over the seam: the keys go to `Terminal.write` and not to a file descriptor.

        The same panel `TestTheTrustDialog` feeds the matcher directly, arriving the way a
        session really delivers it — through the pump, in chunks, with the redraw after the
        Down in a later one.
        """
        settle, session.SETTLE = session.SETTLE, 0.0
        self.addCleanup(setattr, session, "SETTLE", settle)
        r = self.runner()
        r.trust = session.Trust()
        t = self.transcript(r)
        term = FakeTerminal([trust_panel(), b"", trust_panel(marked=1), b"", b""])
        r.pump(term, t)
        t.close()
        self.assertEqual(bytes(term.written), session.DOWN + session.ENTER)

    def test_pump_does_not_close_the_terminal(self):
        """`run()`'s `finally` does, after `terminate`, and that order is §4's point."""
        term = FakeTerminal([b"x"])
        self.drive(term)
        self.assertFalse(term.closed)


class TestTheStopMarker(unittest.TestCase):
    """WINDOWS.md W3f: how a listener asks a runner to stop where it cannot signal it.

    On the Mac `stop` is a SIGTERM and the runner catches it (§9.10). Windows has no
    catchable signal one process can send another: `TerminateProcess` runs no handler, and a
    console control event needs a console the detached runner does not have. SPEC.md §2
    already has these two processes talking only through files, so the stop is one more file
    — the listener creates it in the session directory, the runner sees it on its next tick.

    Both platforms honour it, and that is a decision rather than a side effect. The Mac keeps
    SIGTERM as well, so nothing about `bot.py` on that side has to change; what the marker
    buys there is that the runner has **one** place that hears a stop and this file has one
    set of tests over it, on the box where the mechanism is real and on the box where it is
    not. A marker-shaped stop that only ran on Windows would be tested only on Windows.

    The case these tests exist for is the ordinary one and it is easy to miss: a live session
    is a terminal that is *not* finishing. Every other test in this file drives a terminal
    that runs out — the chunks end, `alive()` goes false, `pump` returns because the child
    died. A session the phone stops is one that would have sat there all day, so `forever` is
    the shape of terminal that matters here.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions = os.path.join(self.tmp, "sessions")
        self.root = os.path.join(self.tmp, "Projects")
        self.work = os.path.join(self.root, "beacon")
        os.makedirs(self.sessions)
        os.makedirs(self.work)

    def runner(self, **kw):
        r = session.Runner("3f2a91", self.work, "beacon-3f2a", root=self.sessions,
                           project="beacon", projects_root=self.root,
                           argv=["/bin/true"], log=lambda m: None, **kw)
        r.begin()
        return r

    def transcript(self, r):
        t = session.Transcript(os.path.join(r.dir, session.TRANSCRIPT), log=lambda m: None)
        self.addCleanup(t.close)
        return t

    def test_the_marker_is_seen_by_the_call_that_asks_for_it(self):
        """The whole protocol, on whichever platform this is running on."""
        d = tempfile.mkdtemp(dir=self.tmp)
        self.assertFalse(session.procs.stop_requested(d))
        session.procs.request_stop(d)
        self.assertTrue(session.procs.stop_requested(d))

    def test_asking_twice_is_asking_once(self):
        """§5's `stop` is retried by a human with a phone in their hand."""
        d = tempfile.mkdtemp(dir=self.tmp)
        session.procs.request_stop(d)
        session.procs.request_stop(d)
        self.assertTrue(session.procs.stop_requested(d))

    def test_one_session_is_not_stopped_by_another_sessions_marker(self):
        # The marker is in the session directory, not in `var/`, because `stop` names a
        # session and two can be live at once.
        one, two = tempfile.mkdtemp(dir=self.tmp), tempfile.mkdtemp(dir=self.tmp)
        session.procs.request_stop(one)
        self.assertFalse(session.procs.stop_requested(two))

    def test_stop_marker_ends_pump(self):
        """The slice's named test: a session that would never end, ended, and `ended` on disk.

        End to end through `run()` rather than `pump` alone, because the state the phone is
        told is the point — a stopped session is `ended`, the same as one that finished on
        its own, and not `failed`.

        `assertTrue(term.alive())` is the assertion that makes this test about the marker,
        and it is here because the first version of it was not: with the terminal's patience
        as the only bound, a `pump` that ignored the marker outright still reached `ended`,
        five seconds later, by way of the fake giving up. Every other claim below was true of
        that run too. What says the stop did it is that the terminal was still live when
        `run` came back.
        """
        r = self.runner()
        term = FakeTerminal([("here %s\r\n" % CAPTURED).encode()], forever=True)
        asked = threading.Timer(0.3, session.procs.request_stop, [r.dir])
        self.addCleanup(asked.cancel)
        asked.start()
        with mock.patch.object(session, "spawn", return_value=(4242, term)), \
                mock.patch.object(session, "terminate", return_value=True):
            state = r.run()
        self.assertTrue(term.alive(), "the session ended on its own, not because of the stop")
        self.assertEqual(state, session.ENDED)
        meta = session.read_meta(r.dir)
        self.assertEqual(meta["state"], session.ENDED)
        self.assertEqual(meta["url"], CAPTURED)

    def test_a_marker_that_is_already_there_is_honoured_before_the_first_read(self):
        """A stop that arrives while the runner is still starting must not cost a tick of
        reading — and must not be missed because the check is at the bottom of the loop."""
        r = self.runner()
        session.procs.request_stop(r.dir)
        term = FakeTerminal([b"a"] * 10, forever=True)
        r.pump(term, self.transcript(r))
        self.assertEqual(term.reads, 0, "it read the terminal after being told to stop")

    def test_a_session_that_never_goes_quiet_is_still_stoppable(self):
        """The check is per tick and not per idle tick, which is not the same thing.

        A build running under a live session produces a chunk every time it is asked, so an
        implementation that only looks for the marker when a read came back empty would stop
        every session except the busy ones — and the busy one is what somebody reaches for
        `stop` about. The terminal here never once answers empty.
        """
        r = self.runner()
        term = FakeTerminal([b"noise\r\n"] * 10000, forever=True)
        original = term.read

        def read(timeout=0.0):
            if term.reads == 5:
                session.procs.request_stop(r.dir)
            return original(timeout)

        term.read = read
        r.pump(term, self.transcript(r))
        self.assertLess(term.reads, 20, "it never looked for the marker while output lasted")

    def test_being_stopped_is_not_a_failure_to_scrape(self):
        """A session stopped before it ever produced a link is still `failed` — the marker
        does not change what §4.6 means, it only ends the loop."""
        r = self.runner()
        session.procs.request_stop(r.dir)
        term = FakeTerminal(forever=True)
        with mock.patch.object(session, "spawn", return_value=(4242, term)), \
                mock.patch.object(session, "terminate", return_value=True):
            self.assertEqual(r.run(), session.FAILED)
        self.assertTrue(term.alive(), "the session ended on its own, not because of the stop")


@posix_only
class TestThePosixTerminal(unittest.TestCase):
    """WINDOWS.md W3d: the Mac's `Terminal`, which is the pty master with `select` inside it.

    The `select` that used to be the first statement of `pump` is now the first statement of
    `read`, and that is the whole of the Mac-side change — so these tests ask the one question
    the move could have got wrong, which is when a read is *empty* and when it is *the end*.
    Those were one answer before (a `break` either way) and are two now: `read` says what
    arrived and `alive()` says whether more is coming, because Windows needs them separated
    and the loop is shared.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def start(self, script):
        pid, terminal = session.spawn(["/bin/sh", "-c", script], self.tmp, session.child_env())
        self.addCleanup(terminal.close)
        self.addCleanup(_silently_kill, pid)
        return pid, terminal

    def test_it_reads_what_the_child_wrote_as_bytes(self):
        _, terminal = self.start("printf 'hello\\r\\n'")
        got = b""
        deadline = time.time() + 5.0
        while time.time() < deadline and b"hello" not in got:
            chunk = terminal.read(0.2)
            self.assertIsInstance(chunk, bytes)
            got += chunk
            if not chunk and not terminal.alive():
                break
        self.assertIn(b"hello", got)

    def test_a_read_that_times_out_is_empty_and_not_the_end(self):
        """The distinction the shared loop is built on: nothing to read is not a hangup."""
        _, terminal = self.start("sleep 5")
        began = time.time()
        self.assertEqual(terminal.read(0.2), b"")
        self.assertGreaterEqual(time.time() - began, 0.15)
        self.assertTrue(terminal.alive(), "an idle terminal was reported finished")

    def test_it_is_finished_once_the_child_has_hung_up(self):
        """EIO off a pty master is the ordinary end of a session, not an error (§2)."""
        _, terminal = self.start("printf 'bye\\r\\n'")
        deadline = time.time() + 5.0
        while time.time() < deadline and terminal.alive():
            terminal.read(0.1)
        self.assertFalse(terminal.alive(), "the pty never reported the hangup")
        self.assertEqual(terminal.read(0.05), b"", "a finished terminal kept answering")

    def test_writing_reaches_the_child(self):
        _, terminal = self.start("read line; printf 'got:%s\\r\\n' \"$line\"")
        terminal.write(b"ping\r")
        got = b""
        deadline = time.time() + 5.0
        while time.time() < deadline and b"got:ping" not in got:
            chunk = terminal.read(0.2)
            got += chunk
            if not chunk and not terminal.alive():
                break
        self.assertIn(b"got:ping", got)

    def test_closing_it_hangs_up_the_child(self):
        """The same claim `test_closing_the_pty_hangs_up_the_child_once_it_owns_the_terminal`
        makes about the raw fd, now that `close()` is what `run()`'s `finally` calls."""
        pid, terminal = self.start("sleep 30")
        time.sleep(0.3)
        terminal.close()
        for _ in range(60):
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                self.assertEqual(status & 0x7f, signal.SIGHUP)
                return
            time.sleep(0.05)
        self.fail("the child survived the terminal being closed")

    def test_closing_twice_is_not_an_error(self):
        """It is called from a `finally` on both platforms, so it has to be idempotent."""
        _, terminal = self.start("sleep 5")
        terminal.close()
        terminal.close()
        self.assertFalse(terminal.alive())
        self.assertEqual(terminal.read(0.01), b"")
