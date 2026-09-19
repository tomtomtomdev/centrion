#!/usr/bin/env python3
"""The PTY runner: one detached process per Claude session, holding its terminal open.

Started by the listener, and then on its own — `os.setsid()`, so it belongs to no session but
its own. SPEC.md §2 is the reason: launchd restarts the listener on every crash, every logout
and every `launchctl kickstart`, and if the listener held the pty master, each restart would
SIGHUP every live session and kill work mid-turn. Sessions must outlive their launcher.

**It cannot exit, and that is the design.** Closing the pty master hangs up the child — the
kernel SIGHUPs the foreground process group the moment the last master fd goes away. So the
runner sits in a read loop for the whole life of the session, appending to a transcript. Holding
the terminal open *is* the job; the reading is how it passes the time.
`test_session.TestTheTerminalSize.test_closing_the_pty_hangs_up_the_child` pins it.

**It talks to the listener through files.** §2: a restarted listener re-reads var/sessions/*/
and knows exactly what is live — no reconnection protocol, no port, and the state is
inspectable with `cat` when something goes wrong. meta.json is written atomically (§3) because
the listener polls it every 0.25s while a session starts, so a reader in the middle of a write
is not a theoretical concern.

Three things here look like details and are not:

- **The window size is an ioctl, not `COLUMNS`** (§6). A fresh pty is 0x0, Claude Code asks the
  kernel, sees nothing usable and falls back to 80 columns. The URL fitting on one line at 80
  was luck. `TIOCSWINSZ` goes on the *slave* before the exec, which is why this uses
  `os.openpty()` and an explicit fork rather than `pty.fork()` — setting it on the master
  afterwards races the child's first render, and the failure that produces is an occasional
  missed URL.
- **The environment is built, not inherited** (§6). Four variables switch off the feature-flag
  evaluation Remote Control's availability depends on, and the session then starts perfectly and
  never connects.
- **fd 9 is closed before `setsid()`** (§8). It is launchd/bot.sh's flock, it has no
  close-on-exec flag, and a runner that keeps it keeps the lock — which silently bricks the
  listener's next restart while the sessions look fine.

Runnable by hand for debugging, which is how the §12 fixture was captured:

    python3 session.py --cwd ~/Projects/beacon --name probe --foreground
"""
import argparse
import codecs
import json
import os
import re
import sys
import time

import config


def _platform():
    """The module that owns the process and terminal mechanisms. WINDOWS.md §2.

    Everything under `Runner` that forks, signals, opens a pty or takes a lock goes through
    this one seam, so the Mac code in session_posix.py stays exactly as SPEC.md verified it
    and the Windows code in session_win.py stands beside it. Both export the same names —
    tests/test_session.py::TestThePlatformSeam is the list.
    """
    if sys.platform == "win32":
        import session_win as mod
    else:
        import session_posix as mod
    return mod


procs = _platform()

#: Two of the names bot.py and the tests have always reached through `session.`, pointing at
#: the chosen module's functions. `spawn` and `terminate` are wrappers further down, because
#: their defaults (`ROWS`/`COLS`, `GRACE`) are this module's constants — the platform modules
#: take every argument explicitly and own no policy.
detach = procs.detach
_reaped = procs._reaped

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "var", "sessions")

#: §6, verified: with this the child's `stty size` reports `50 200`. Without it, `0 0`.
ROWS, COLS = 50, 200

#: §3's state machine. `starting` → `live` → `ended`, or `starting` → `failed`.
STARTING, LIVE, FAILED, ENDED = "starting", "live", "failed", "ended"

#: §4. Not a UUID (§9.5): ids are ULID-shaped and mixed case. The character class is kept wide
#: on purpose — §14 expects this to outlive a UI change, and a pattern pinned to today's exact
#: id length would match today and quietly stop matching after an upgrade.
URL_RE = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9_-]+")

#: Order matters: CSI and OSC first, so the catch-all never eats the two bytes that start one.
#: The catch-all excludes `[` and `]` for the same reason — otherwise an *incomplete* CSI at a
#: chunk boundary would be consumed as a complete single-character escape, and the carry below
#: would never see it.
ANSI_RE = re.compile(
    r"\x1b\[[0-9;:?<>=!]*[ -/]*[@-~]"          # CSI — colour, cursor movement, erase
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"      # OSC — the window title the header sets
    r"|\x1b[^\x1b\[\]]")                       # Fp/Fe/Fs — ESC 7, ESC 8, ESC =, ESC >

#: How much stripped text is kept in case the URL straddles a chunk boundary. A link is ~55
#: characters; this is room to spare without letting the buffer grow for the life of a session.
KEEP = 256

#: A held escape longer than this was never an escape. Release it rather than hold the stream —
#: and the link — behind a stray ESC forever.
CARRY_LIMIT = 512

#: §4: type the prompt, let the input box settle, then send Enter separately. §9.3's dialog is
#: answered on the same rhythm and for the same reason — a panel still drawing itself is a panel
#: whose keystrokes land somewhere unpredictable.
SETTLE = 0.4

#: §9.3's trust dialog, normalised. *Verified on this box, 2026-09-13*, and the verification is
#: the point: the spec had inferred the hang from a flag set on 46 project entries and had never
#: watched a fresh directory come up under `--dangerously-skip-permissions`. It hangs.
#:
#: Matched against text with **all whitespace removed**, because the renderer writes words
#: separated by `CSI <n> G` cursor jumps rather than spaces — strip() takes the escapes out and
#: what is left has no gaps in it at all. A matcher written against what a human sees on the
#: screen matches nothing here.
TRUST_QUESTION = "isthisaprojectyoucreatedoroneyoutrust"
TRUST_YES = "yes,itrustthisfolder"

#: The selected option carries this marker. **The default selection is `No, exit`** — which is
#: why the answer is not a bare Enter, and why the marker having moved is checked before one is
#: sent at all.
TRUST_MARKER = "\u276f"

#: Arrow keys as the terminal sends them, and the confirm.
DOWN, ENTER = b"\x1b[B", b"\r"

#: How much normalised text is kept while looking for the dialog. The panel is ~350 characters
#: with its spacing removed and the question and the option have to be in the buffer together.
TRUST_KEEP = 4096

#: How long `pump` waits on a terminal before going round again, and so how often the settle
#: timers are checked. The read size that goes with it is the platform's — `session_posix`
#: names one because `os.read` wants a length and ConPTY never asks (W3d).
TICK = 0.2

#: How long a terminating session gets between the polite signal and the hard kill — SIGTERM
#: then SIGKILL on the Mac, Ctrl-C then a job kill on Windows (WINDOWS.md §4).
GRACE = 5.0

#: The files in a session directory that both processes name. Spelled once here because the
#: listener reads all three from the other side of §2's file boundary, and a second spelling
#: of any of them is a silent failure — a tail that finds nothing, a record never reaped.
META = "meta.json"
TRANSCRIPT = "pty.log"
#: What a rotated transcript is called (§10.7). One suffix, applied in one place, so the name
#: the runner renames to and the name the listener reads back cannot drift apart.
ROTATED = ".1"
PREVIOUS = TRANSCRIPT + ROTATED

#: §10.7: how much transcript is kept before it is rotated, and one predecessor is kept, so a
#: session costs at most twice this. Four megabytes is minutes of a 200-column terminal
#: redrawing itself under `dd`, and days of an ordinary session — measured here, a real one
#: reached 56 KB in an evening. The bound that matters is the product: `max_sessions` × 2 ×
#: this, plus whatever a day of finished sessions left behind (§4).
TRANSCRIPT_CAP = 4 * 1024 * 1024


def _stderr(message):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


def spawn(argv, cwd, env, rows=ROWS, cols=COLS):
    """Run `argv` on a real terminal of the size §6 requires. Returns (pid, Terminal).

    The mechanism is the platform's (session_posix.spawn: openpty + fork + TIOCSWINSZ on the
    slave before the exec; session_win.spawn: a ConPTY); the size is this module's. Kept as a
    function here so a test that patches `session.spawn` reaches the one `Runner.run` calls.

    The second value is a `Terminal` since W3d, not a descriptor: `pump` reads it through
    `read`/`write`/`alive`/`close` and knows nothing else about it, which is what lets one
    loop serve a pty and a pseudoconsole.
    """
    return procs.spawn(argv, cwd, env, rows, cols)


def terminate(pid, grace=GRACE, log=_stderr, terminal=None):
    """End the session at `pid` and everything it spawned. Returns True once it is gone.

    **The grace periods nest, and a caller ending a *runner* has to allow for it** (§9.11):
    the runner catches the stop, then spends up to its own `GRACE` ending claude, so a caller
    that allows a runner only `GRACE` hard-kills it mid-way and leaves meta.json saying `live`
    for something on its way out. The signalling itself — process group and process, both,
    and why — is session_posix.terminate's docstring.

    `terminal` is the session's terminal, when the caller has one, and it is there for
    Windows (WINDOWS.md §4, W3e): the polite stop is a Ctrl-C *written to the terminal* rather
    than a signal, and the Job Object that takes the rest of the tree has been held by that
    same object since before `spawn` returned. Neither is reachable from a pid, which is the
    one place the Mac's shape did not survive the port. `session_posix.terminate` ignores it —
    `killpg` needs a number and nothing else — so the two modules keep one signature.
    """
    return procs.terminate(pid, grace, log, terminal)


def strip(data):
    """Terminal output → the text a human would have seen. Bytes or str."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    return ANSI_RE.sub("", data)


def extract_url(data):
    """The session link in `data`, or None. One-shot; `Scrape` is the streaming version."""
    found = URL_RE.search(strip(data))
    return found.group(0) if found else None


def squeeze(text):
    """Terminal text with its spacing and case removed. See TRUST_QUESTION for why."""
    return "".join(strip(text).split()).lower()


class Trust:
    """§9.3's trust dialog, answered on the terminal. Slice 11.

    A directory created in `~/Projects` after Claude Code last saw it comes up to a *Quick
    safety check* and waits for a human who is not at the keyboard — under
    `--dangerously-skip-permissions` too, which is the only way this bot ever starts one. The
    runner sits there until the listener's 45s deadline and the phone gets a panel where a link
    should be. That is the whole of what stands between `new scratchpad` and a session.

    **Why answering it here is legitimate and not merely convenient.** §9.3 weighed the
    alternative — pre-seeding `projects[<path>].hasTrustDialogAccepted` in `~/.claude.json` —
    and rejected it: every live Claude Code process rewrites that file continuously, so the
    daemon would be racing all of them over 46 projects' configuration to save one key. The
    argument that makes the keystroke honest is narrower than "the bot needs it": the directory
    was created by this bot, empty, a second earlier, so there is nothing in it to trust. The
    runner enforces the empty half (see Runner.run) and the listener the created half; neither
    is enough on its own, and `claude <project>` never gets here at all.

    **Down, then check, then Enter.** The default selection is `No, exit`, so the obvious
    "press Enter to confirm" ends the session — and a blind Down+Enter would confirm whatever
    the second option happens to be after the next UI change. So the marker is checked onto the
    option this code means to choose before it is confirmed, and only the redraw that arrives
    *after* the Down counts. A dialog that does not answer to this is left alone: the session
    hangs, the phone gets the panel at 45s, and that is the failure being chosen deliberately
    over confirming something nobody read. §14 has the standing check.
    """

    WAITING, SEEN, ASKED, MOVED, DONE = "waiting", "seen", "asked", "moved", "done"

    def __init__(self):
        self.state = self.WAITING
        self.text = ""
        self.at = None

    def feed(self, chunk, now):
        """Terminal output in, a keystroke out — or None, which is the usual answer."""
        if chunk:
            self.text = (self.text + squeeze(chunk))[-TRUST_KEEP:]

        if self.state == self.WAITING:
            if TRUST_QUESTION in self.text and TRUST_YES in self.text:
                self.state, self.at = self.SEEN, now
        elif self.state == self.SEEN and now - self.at >= SETTLE:
            # The buffer is cleared with the keystroke: what proves the marker moved is the
            # redraw that comes *after* this, and the panel already on screen has the marker
            # sitting on `No, exit`.
            self.state, self.text = self.ASKED, ""
            return DOWN
        elif self.state == self.ASKED and TRUST_MARKER + TRUST_YES in self.text:
            self.state, self.at = self.MOVED, now
        elif self.state == self.MOVED and now - self.at >= SETTLE:
            self.state = self.DONE
            return ENTER
        return None


class Scrape:
    """Feed it pty chunks; it answers with the link once, and then keeps answering the same one.

    Two boundary problems, both of which produce a *wrong answer* rather than an error, which is
    why each has its own test:

    **A chunk can end inside an escape sequence.** Stripping each chunk on its own is the
    obvious implementation: `\\x1b[38;2;153` arrives, then `;153;153m`, neither half matches,
    and `;153;153m` lands in the cleaned text. Harmless until the split falls in the escape
    immediately before the link. So a trailing partial escape is carried to the next chunk.

    **A chunk can end inside the URL.** `session_[A-Za-z0-9_-]+` is perfectly happy to stop at
    a chunk boundary, and the half-link it returns is well-formed and tappable and wrong — the
    phone gets a 404 and there is no error anywhere to notice. So a match that runs to the end
    of what has arrived is not accepted until something that cannot be part of a URL follows it.
    """

    def __init__(self):
        self.url = None
        self.tail = ""      # cleaned text not yet ruled out as the start of a link
        self.carry = ""     # a partial escape held back from the end of the last chunk
        # Incremental, so a multi-byte character split across two reads is decoded rather than
        # turned into replacement characters. Harmless for the ASCII URL, but pty.log's own
        # fixture came through here and the box drawing in it should survive the trip.
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed(self, chunk):
        """One chunk in, the URL out the first time it is complete, otherwise None."""
        if self.url:
            return self.url

        text = chunk if isinstance(chunk, str) else self._decoder.decode(chunk)
        if not text and not self.carry:
            return None

        clean = ANSI_RE.sub("", self.carry + text)
        held = clean.rfind("\x1b")
        if held == -1:
            self.carry = ""
        elif len(clean) - held > CARRY_LIMIT:
            self.carry = ""           # not an escape after all
        else:
            self.carry = clean[held:]
            clean = clean[:held]

        self.tail += clean
        found = URL_RE.search(self.tail)
        if found and found.end() < len(self.tail):
            self.url = found.group(0)
            self.tail = ""
            return self.url
        if len(self.tail) > KEEP:
            self.tail = self.tail[-KEEP:]
        return None


def child_env(base=None):
    """The environment the session runs in. SPEC.md §6.

    A filter and not an allowlist: the session's own Bash tool wants an ordinary environment,
    including things like SSH_AUTH_SOCK that no list here would think to name. What goes is the
    named hazards — and everything Claude Code sets about *itself* goes by prefix rather than by
    name, because that list grows between versions.

    The prefix is `CLAUDE`, not `CLAUDE_CODE`, and that is §14 paid off. `CLAUDE_PID` and
    `CLAUDE_EFFORT` miss the longer prefix by one underscore and `AI_AGENT` misses it
    altogether, so all three survived a filter written when launchd — which sets none of them —
    was the only supported way to start the listener. Started from a shell inside a Claude Code
    session, the bot was handing that session's effort setting, and a pid naming somebody
    else's process, to every session it spawned.

    Nothing here is load-bearing for the child: its login is in the keychain, not in the
    environment (§9.13). `CLAUDE_CONFIG_DIR` would be the one to think twice about, because it
    moves where the child reads its config from — it is not set on this box, and if it ever is,
    it should be set deliberately by the runner rather than inherited from whoever started it.

    No `--model` and no `--effort` either (see `claude_argv`): a launcher that quietly
    downgrades what it launches is a trap, because the difference shows up only as worse answers
    hours later.

    **Two halves, since W3g.** What is written here is the part that is a decision: which
    variables are hazards, and the two that carry this module's `ROWS`/`COLS`. A hazard is a
    hazard on any platform — `ANTHROPIC_API_KEY` is the wrong login on Windows too — so the
    filter is portable and is the reason this function did not simply move behind the seam.
    What a child needs in order to *be* a child is the platform's, and `procs.child_env` adds
    it: a chosen `PATH`, `HOME`, `SHELL` and `TERM` on the Mac; the inherited `PATH` and the
    `SYSTEMROOT`/`COMSPEC` family on Windows, where a replaced `PATH` means a session that can
    run nothing that is not a shell builtin (WINDOWS.md §4, and W3b's run step measured it).
    """
    env = dict(os.environ if base is None else base)

    for name in list(env):
        if name.startswith("CLAUDE"):
            del env[name]
    for name in ("AI_AGENT", "DISABLE_TELEMETRY", "DO_NOT_TRACK", "DISABLE_GROWTHBOOK",
                 "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        env.pop(name, None)

    env.update({
        # For the shell and for tools the session runs. These do NOT size the terminal Claude
        # Code renders into — that is the ioctl in spawn() on the Mac and the ConPTY's own
        # constructor on Windows. §6, and WINDOWS.md §4 keeps them for the same reason.
        "COLUMNS": str(COLS),
        "LINES": str(ROWS),
    })
    return procs.child_env(env)


def claude_argv(binary, name):
    """§1: the flag form is what "a session with /rc enabled" means. No model, no effort."""
    return [binary, "--remote-control", name, "--dangerously-skip-permissions"]


# spawn(), _reaped(), _signal() and terminate() live in session_posix.py / session_win.py
# (WINDOWS.md §2, W1a) and are reached as `spawn` / `terminate` / `_reaped` above.


#: How hard `write_meta` tries to land its `os.replace`. WINDOWS.md §4, W3a.
#:
#: POSIX renames over an open file without noticing. Windows refuses, with `PermissionError`,
#: whenever any handle on the target was opened without `FILE_SHARE_DELETE` — which is every
#: handle `open()` hands out, including the listener's own `read_meta`. So on that platform the
#: atomic write has a second failure the Mac has never had, and the write that matters most is
#: the one carrying the URL.
#:
#: Measured (W3a, this box): against a listener polling at `bot.SESSION_POLL`, 22 of 150 writes
#: were refused outright and five tries 20ms apart lost none at all, three runs running, for
#: about 0.2s of waiting across the whole run. Against a reader that never pauses, *no* policy wins — 5 x 20ms lost 147 of 150 and
#: 10 x 50ms lost 95 while costing half a second a write. So this is sized for the real reader
#: and deliberately not widened for the pathological one: it is called from inside the runner's
#: read loop, which is the session's life (§2), and 80ms is what that loop can afford to miss.
META_RETRY_TRIES = 5
META_RETRY_DELAY = 0.020


def write_meta(directory, record):
    """Atomically, per §3 — the listener polls this every 0.25s while a session starts."""
    tmp = os.path.join(directory, META + ".%d.tmp" % os.getpid())
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())

    target = os.path.join(directory, META)
    for attempt in range(META_RETRY_TRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            # A reader has the target open; it will not have it open for long. See the
            # constants above for why the bound is five and not fifty.
            if attempt == META_RETRY_TRIES - 1:
                # Give up the way the Mac always has — by raising what `os.replace` raised,
                # which `bot.py`'s caller already catches. But take the temporary file with
                # us: its name is fixed by the pid, so leaving it behind puts a stale record
                # in the session directory for the next write to land on top of.
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            time.sleep(META_RETRY_DELAY)


def read_meta(directory):
    """The record, or None if it is absent or unreadable.

    None rather than an exception because §4 has the listener walking every session directory on
    every tick: one unreadable record must not end the reconciliation pass, or a single bad
    session hides all the good ones.
    """
    try:
        with open(os.path.join(directory, META)) as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


class Transcript:
    """pty.log, and the cap that keeps a session off the disk. Slice 10, §10.7.

    §3 called this the full ANSI transcript and meant *full* literally: the runner appended
    every byte the terminal produced for as long as the session lived, and a session left open
    on purpose for a week is the thing this bot is for. Nothing trimmed it and nothing watched
    it, so the only bound on var/ was how long somebody happened to leave a session running.

    **Two files, and the second one is not an archive.** The live transcript is renamed to
    `pty.log.1` when it passes the cap and a fresh one takes its place, so what is kept is
    always the most recent `cap` to `2 × cap` bytes. One predecessor rather than none because
    §4.6's tail is read from the *end* of this file by the other process — and a session that
    fails a moment after a rotation would otherwise hand the phone the last half-second of a
    redraw instead of the error that killed it. One rather than five because nobody on a phone
    is going to read a debugging archive, and the point here is a number var/ cannot exceed.

    The rename is `os.replace`, which is atomic: a listener reading the tail across a rotation
    sees one file or the other and never a gap.
    """

    def __init__(self, path, cap=None, log=_stderr):
        self.path = path
        self.previous = path + ROTATED
        self.cap = TRANSCRIPT_CAP if cap is None else cap
        self.log = log
        self.fh = None
        self.open()

    def open(self):
        # Append, unbuffered — the same handle the runner has always used. The size comes off
        # the fd rather than from zero, or a runner re-opening a session directory (§12's
        # hand-run `--foreground` is the ordinary way that happens) gets a fresh cap each time
        # and the bound is one cap per open rather than one cap.
        self.fh = open(self.path, "ab", 0)
        self.size = os.fstat(self.fh.fileno()).st_size

    def write(self, chunk):
        self.fh.write(chunk)
        self.size += len(chunk)
        # Checked after the write and not before it: a chunk is one read() off the pty master
        # and may be 64 KB, so the file is briefly over the cap either way. Rotating on the
        # next write is what keeps a single enormous chunk from being the case that escapes.
        if self.cap and self.size >= self.cap:
            self.rotate()

    def rotate(self):
        """Rename the transcript out of the way and start another one."""
        try:
            self.fh.close()
            self.replace(self.path, self.previous)
        except OSError as e:
            # The read loop this sits inside is the session's life (§2), so a rotation that
            # cannot be done gives up on rotating rather than on the session. Switching the
            # cap off is the important half: without it every subsequent write would retry a
            # failing rename, which is a spin in the one loop that has to keep reading the
            # terminal.
            self.log("could not rotate %s (%s) — the transcript will grow from here"
                     % (self.path, e.strerror or e))
            self.cap = None
        finally:
            self.open()

    def replace(self, source, target):
        """`os.replace`, as a method so a test can take it away. See rotate()."""
        os.replace(source, target)

    def close(self):
        try:
            self.fh.close()
        except OSError:
            pass


class Runner:
    """One session, from `starting` to `ended`, and the pty held open in between."""

    def __init__(self, sid, cwd, name, root=SESSIONS, binary=None, argv=None, project=None,
                 chat_id=None, prompt=None, projects_root=None, trust=False, log=_stderr):
        self.sid = sid
        self.cwd = cwd
        self.name = name
        self.dir = os.path.join(root, sid)
        self.project = project or os.path.basename(cwd.rstrip(os.sep))
        self.chat_id = chat_id
        self.prompt = prompt
        self.projects_root = projects_root
        self.argv = argv or claude_argv(binary, name)
        # §9.3: the listener sets this for `new`, because the created-by-this-bot half of the
        # argument is the half only the listener knows. The empty half is checked in run(),
        # where the directory is — see there.
        self.trusting = trust
        self.log = log
        self.scrape = Scrape()
        self.trust = None
        self.record = {}
        self.stopping = False

    def begin(self):
        """Write `starting` before anything is spawned.

        §4.6 has the listener polling this file the moment it has forked the runner, so the
        record has to exist by then — a poll that finds nothing cannot tell a session that is
        starting from one that never started.
        """
        os.makedirs(self.dir, exist_ok=True)
        self.record = {
            "sid": self.sid,
            "state": STARTING,
            "project": self.project,
            "cwd": self.cwd,
            "name": self.name,
            "runner_pid": os.getpid(),
            "claude_pid": None,
            "url": None,
            "started": int(time.time()),
            "chat_id": self.chat_id,
        }
        write_meta(self.dir, self.record)

    def update(self, **changes):
        self.record.update(changes)
        write_meta(self.dir, self.record)

    def recheck(self):
        """§3: the listener resolves, and the runner re-checks before chdir.

        Cheap, and it means that nothing which can reach this process's argv — a bug in command
        parsing, a hand-run with a typo — becomes an arbitrary working directory for a session
        that has permissions bypassed.

        `samefile` and not `==`, per §9.9: this volume is case-insensitive while realpath() is
        not, so `beacon` and `BEACON` are one directory reached through two strings that compare
        unequal, and a string comparison here would refuse a session the listener had already
        allowed.
        """
        root = self.projects_root
        if root is None:
            root = config.load(check_claude=False).projects_root
        resolved = config.resolve(self.project, root)
        if not os.path.samefile(resolved, self.cwd):
            raise config.ProjectError("the runner was given a directory that is not the one "
                                      "its project name resolves to")
        return resolved

    def run(self):
        """Spawn, then hold the terminal open until the session ends."""
        self.begin()
        try:
            self.recheck()
        except (config.ProjectError, config.ConfigError, OSError) as e:
            self.log("refusing to start: %s" % e)
            self.update(state=FAILED, error=str(e))
            return FAILED

        # §9.3, and the second half of the argument that makes answering the trust dialog
        # honest: the listener says this session was a `new`, and the directory says whether
        # there is anything in it to be trusted away. Both, or the dialog is left alone —
        # `new beacon` on a repository somebody else made is `claude beacon` in that respect,
        # and the check is here because the emptiness is a fact about a directory, on the
        # machine that has it, at the moment before anything is started in it.
        if self.trusting:
            try:
                empty = not os.listdir(self.cwd)
            except OSError:
                empty = False
            if empty:
                self.trust = Trust()
            else:
                self.log("session %s: %s is not empty — leaving §9.3's dialog alone"
                         % (self.sid, self.project))

        # §4.6, and WINDOWS.md W3b: a terminal that cannot be started is a `failed` session
        # with a reason in it, not a traceback. On the Mac this branch is close to unreachable
        # — the fork succeeds whatever the binary is, and the child reports the exec failure on
        # the pty, where it becomes ordinary output and the tail the phone gets. Windows has no
        # such child: CreateProcess fails before one exists, so without this the runner dies
        # here leaving meta.json saying `starting` for a session that will never start, which
        # the listener can only wait out.
        try:
            pid, terminal = spawn(self.argv, self.cwd, child_env())
        except OSError as e:
            self.log("session %s: could not start the terminal: %s" % (self.sid, e))
            self.update(state=FAILED, error=str(e))
            return FAILED
        self.update(claude_pid=pid)
        self.log("spawned pid %d in %s" % (pid, self.cwd))

        # §12: kill the runner and claude dies with it. Not left to the pty hangup — see
        # terminate() for the window in which that does not happen.
        previous = self._catch_signals()
        transcript = Transcript(os.path.join(self.dir, TRANSCRIPT), log=self.log)
        try:
            self.pump(terminal, transcript)
        finally:
            transcript.close()
            terminate(pid, log=self.log, terminal=terminal)
            # Last, and that order is the point on both platforms (§9.10, WINDOWS.md §4):
            # closing this hangs up a Mac child and ends a Windows one outright, and a session
            # that has not yet attached to its terminal survives being hung up from it.
            terminal.close()
            self._restore_signals(previous)

        # §4.6: on `failed` the listener sends the tail of pty.log, which is almost always the
        # actual error — an expired login sitting at a /login prompt, most likely of all (§9.7).
        state = ENDED if self.scrape.url else FAILED
        self.update(state=state)
        self.log("session %s: %s" % (self.sid, state))
        return state

    def _catch_signals(self):
        """SIGTERM ends the session rather than the runner, so meta.json is left truthful.

        Which signals exist, and whether a stop arrives as a signal at all, is the platform's
        business (WINDOWS.md §4): the runner only says what to do when one does.
        """
        return procs.catch_signals(self._signalled)

    def _restore_signals(self, previous):
        procs.restore_signals(previous)

    def _signalled(self, signum, _frame):
        self.stopping = True

    def pump(self, terminal, transcript):
        """Read until the terminal is finished. This loop is what holds the session open (§2).

        One implementation on both platforms since W3d. It used to hold a pty master fd and
        call `select` and `os.read` on it, neither of which a pseudoconsole has — so the
        waiting moved behind `Terminal.read(timeout)`, which each platform does its own way,
        and what is left here is what was always portable: what to do with a chunk, when to
        answer §9.3's dialog, and when to type the prompt.

        The loop now names four calls and nothing else — `read`, `write`, `alive`, `close` —
        which is also why it is testable without a process at all (`TestPumpOverATerminal`).
        """
        typed_at = None
        entered = False

        while not self.stopping:
            # §5's `stop`, the other half of it. `stopping` above is a signal that has already
            # arrived; this is the listener asking in the one way that works on a platform
            # where nothing can signal a detached process at all (WINDOWS.md §4, W3f) — a file
            # it creates in this directory, which the runner notices within a tick. Both
            # platforms honour the marker, so a stop is heard in one place here rather than
            # two, and the Mac keeps SIGTERM as well.
            #
            # Before the read rather than after it, and not folded into the `while` above: a
            # stop that arrived while the terminal was busy must not buy the session one more
            # tick of output, and a stop that arrived before `pump` was entered at all must
            # not buy it one either. Checked every pass and not only on an idle one — a
            # session running a build answers every read with a chunk, and that is exactly the
            # session somebody reaches for `stop` about.
            if procs.stop_requested(self.dir):
                self.log("session %s: stop requested" % self.sid)
                self.stopping = True
                break

            chunk = terminal.read(TICK)
            if chunk:
                self.absorb(chunk, transcript)
            elif not terminal.alive():
                # **The child's death is not the end of its output**, and on Windows that is
                # not a nicety: W3b measured ConPTY still holding the last of what a child
                # wrote after `alive()` had gone false, and that last part is exactly the
                # error text §4.6 sends the phone when a session is `failed`. So an empty read
                # against a finished terminal costs one more read before this gives up. On the
                # Mac the extra read is always empty — EIO is both answers at once there.
                self.absorb(terminal.read(TICK), transcript)
                break

            # §9.3. Outside the `if chunk` block because two of its four steps are timers, and
            # a panel that has finished drawing sends nothing more to wait for. It stops
            # mattering the moment there is a link.
            if self.trust is not None and not self.scrape.url:
                key = self.trust.feed(chunk, time.time())
                if key is not None:
                    terminal.write(key)
                    if key is ENTER:
                        self.log("session %s: answered §9.3's trust dialog" % self.sid)

            if not self.prompt or not self.scrape.url:
                continue
            # §4: type it only once the session is live — before that there is no input box and
            # the text lands in whatever the renderer was drawing. Enter goes separately, after
            # the box has settled, for the same reason.
            if typed_at is None:
                terminal.write(self.prompt.encode())
                typed_at = time.time()
            elif not entered and time.time() - typed_at >= SETTLE:
                terminal.write(b"\r")
                entered = True

    def absorb(self, chunk, transcript):
        """One chunk onto the disk and past the scraper. Empty is nothing to do.

        Its own method because `pump` does this from two places since W3d — the ordinary read
        and the drain after the terminal has finished — and the drain being only *half* of it
        is the bug it would have: the transcript would get the last of a failure's output and
        a link arriving in that same chunk would never be recorded.
        """
        if not chunk:
            return
        transcript.write(chunk)
        if not self.scrape.url and self.scrape.feed(chunk):
            self.update(state=LIVE, url=self.scrape.url)
            self.log("live: %s" % self.scrape.url)


def main():
    ap = argparse.ArgumentParser(description="centrion — the PTY runner for one session.")
    ap.add_argument("--cwd", required=True, help="the project directory to start in")
    ap.add_argument("--name", required=True, help="the --remote-control session name")
    ap.add_argument("--sid", default=None, help="6 hex; generated when absent")
    ap.add_argument("--project", default=None, help="defaults to the basename of --cwd")
    ap.add_argument("--chat-id", type=int, default=None, help="who to tell when it ends")
    ap.add_argument("--prompt", default=None, help="typed into the session once it is live")
    ap.add_argument("--root", default=SESSIONS, help="where session directories live")
    ap.add_argument("--trust", action="store_true",
                    help="answer §9.3's trust dialog if the directory is empty (`new` only)")
    ap.add_argument("--foreground", action="store_true",
                    help="do not detach; for debugging by hand (SPEC.md §12)")
    a = ap.parse_args()

    try:
        cfg = config.load()
    except config.ConfigError as e:
        sys.exit("config: %s" % e)

    # Not in --foreground: a shell with job control puts its child in a new process group as
    # the leader, and setsid() then fails with EPERM. Detaching is for the listener's spawn,
    # where the runner is not a group leader; by hand, the terminal is the point.
    if not a.foreground:
        detach()

    runner = Runner(a.sid or os.urandom(3).hex(), os.path.abspath(os.path.expanduser(a.cwd)),
                    a.name, root=a.root, binary=cfg.claude_bin, project=a.project,
                    chat_id=a.chat_id, prompt=a.prompt, trust=a.trust)
    if a.foreground:
        print("session %s · %s" % (runner.sid, runner.dir), file=sys.stderr)
    state = runner.run()
    return 0 if state == ENDED else 1


if __name__ == "__main__":
    sys.exit(main())
