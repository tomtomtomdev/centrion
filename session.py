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
import errno
import fcntl
import json
import os
import re
import select
import signal
import struct
import sys
import termios
import time

import config

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "var", "sessions")

#: §6, verified: with this the child's `stty size` reports `50 200`. Without it, `0 0`.
ROWS, COLS = 50, 200

#: launchd/bot.sh's flock (§8). Closed before setsid, guarded for the hand-run case.
LOCK_FD = 9

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

#: §4: type the prompt, let the input box settle, then send Enter separately.
SETTLE = 0.4

READ_SIZE = 65536
TICK = 0.2            # select timeout; also how often the settle timer is checked

#: How long a terminating session gets between SIGTERM and SIGKILL.
GRACE = 5.0


def _stderr(message):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


def strip(data):
    """Terminal output → the text a human would have seen. Bytes or str."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    return ANSI_RE.sub("", data)


def extract_url(data):
    """The session link in `data`, or None. One-shot; `Scrape` is the streaming version."""
    found = URL_RE.search(strip(data))
    return found.group(0) if found else None


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
    named hazards — and `CLAUDE_CODE_*` goes by prefix rather than by name, because that list
    grows between versions and these leak in whenever the listener was started by hand from
    inside a Claude Code session.

    No `--model` and no `--effort` either (see `claude_argv`): a launcher that quietly
    downgrades what it launches is a trap, because the difference shows up only as worse answers
    hours later.
    """
    env = dict(os.environ if base is None else base)

    for name in list(env):
        if name.startswith("CLAUDE_CODE") or name == "CLAUDECODE":
            del env[name]
    for name in ("DISABLE_TELEMETRY", "DO_NOT_TRACK", "DISABLE_GROWTHBOOK",
                 "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        env.pop(name, None)

    home = env.get("HOME") or os.path.expanduser("~")
    env.update({
        # ~/.local/bin first: that is the version-pinned `claude` symlink (§9.8), and brew is
        # on the path after it because the session's own Bash tool needs it to be useful.
        "PATH": "%s/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" % home,
        "HOME": home,
        "USER": env.get("USER") or os.environ.get("USER", ""),
        "SHELL": env.get("SHELL") or "/bin/zsh",
        "LANG": "en_US.UTF-8",
        "TERM": "xterm-256color",
        # For the shell and for tools the session runs. These do NOT size the terminal Claude
        # Code renders into — that is the ioctl in spawn(). §6.
        "COLUMNS": str(COLS),
        "LINES": str(ROWS),
    })
    return env


def claude_argv(binary, name):
    """§1: the flag form is what "a session with /rc enabled" means. No model, no effort."""
    return [binary, "--remote-control", name, "--dangerously-skip-permissions"]


def spawn(argv, cwd, env, rows=ROWS, cols=COLS):
    """Run `argv` on a real terminal of the size §6 requires. Returns (pid, master_fd).

    `os.openpty()` and an explicit fork rather than `pty.fork()`, because TIOCSWINSZ has to be
    set on the slave *before* the exec. Setting it on the master after pty.fork() returns does
    work and races the child's first render, and what that produces is an occasional missed URL
    — the worst kind of bug to go looking for.
    """
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    pid = os.fork()
    if pid == 0:
        try:
            os.close(master)
            # Its own session, with the pty as its controlling terminal — without which
            # --remote-control will not start an interactive session at all (§9.1).
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            if slave > 2:
                os.close(slave)
            os.chdir(cwd)
            os.execve(argv[0], argv, env)
        except BaseException as e:                      # pragma: no cover - the child never returns
            # On the pty, so it lands in pty.log and reaches the phone as the error tail (§4.6).
            # 126 is the exit code TCC produces for the same shape of failure (§9.2).
            try:
                os.write(2, ("centrion: could not start %s: %r\r\n" % (argv[0], e)).encode())
            except OSError:
                pass
            os._exit(126)
    os.close(slave)
    return pid, master


def _reaped(pid):
    """True once `pid` is gone. False only while it is genuinely still running."""
    try:
        return bool(os.waitpid(pid, os.WNOHANG)[0])
    except OSError:
        return True          # already reaped, or never ours to wait on


def _signal(pid, sig, log):
    """Signal the session's process group *and* the process itself.

    Both, because which one exists depends on how far the spawn got. Once the child has run
    setsid() it leads its own group and the group is what matters — a session that started a
    dev server has it in there too, and §5's `stop` must not leave that behind. Before setsid()
    there is no such group: `killpg(pid)` fails with ESRCH and the only thing that reaches the
    child is a plain kill. Getting this wrong is silent — the call "succeeds" at signalling
    nothing and the session lives on.

    `killpg(pid)` is safe to attempt either way: pid is a fresh child, so it can never be this
    process's own group id, and there is no way for this to signal the runner itself.
    """
    reached = False
    for send, target in ((os.killpg, "group"), (os.kill, "process")):
        try:
            send(pid, sig)
            reached = True
        except OSError as e:
            if e.errno not in (errno.ESRCH, errno.EPERM):
                log("could not signal %s %d: %s" % (target, pid, e))
    return reached


def terminate(pid, grace=GRACE, log=_stderr):
    """End the session at `pid` and everything it spawned. Returns True once it is gone.

    Signalled explicitly rather than by hanging up the pty, and that is not belt-and-braces.
    *Verified on this box:* closing the master does SIGHUP the child, in about 100ms — but only
    once the child has actually taken the pty as its controlling terminal. Close it inside the
    window between the fork and TIOCSCTTY and the child has no controlling terminal to be hung
    up from: it survives, orphaned, writing to a pty nobody holds. A `stop` arriving moments
    after a spawn lands exactly in that window, and the session it fails to kill is one with
    permissions bypassed and nobody watching it (§5, §12 slice 8).
    """
    if _reaped(pid):
        return True
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 1.0)):
        _signal(pid, sig, log)
        deadline = time.time() + wait
        while time.time() < deadline:
            if _reaped(pid):
                return True
            time.sleep(0.05)
    return _reaped(pid)


def write_meta(directory, record):
    """Atomically, per §3 — the listener polls this every 0.25s while a session starts."""
    tmp = os.path.join(directory, "meta.json.%d.tmp" % os.getpid())
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, os.path.join(directory, "meta.json"))


def read_meta(directory):
    """The record, or None if it is absent or unreadable.

    None rather than an exception because §4 has the listener walking every session directory on
    every tick: one unreadable record must not end the reconciliation pass, or a single bad
    session hides all the good ones.
    """
    try:
        with open(os.path.join(directory, "meta.json")) as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def detach():
    """Leave the listener behind. SPEC.md §8, and the order matters.

    fd 9 is launchd/bot.sh's flock. `exec 9>>` sets no close-on-exec flag, so it survived into
    python and into this fork, and a runner that keeps it keeps the lock — verified on this box.
    The consequence is delayed and nasty: the listener exits, its detached runners hold the lock
    open, launchd's restarted listener can never acquire it, and the bot goes permanently silent
    while its sessions look perfectly healthy.
    """
    try:
        os.close(LOCK_FD)
    except OSError:
        pass          # hand-run: fd 9 was never opened
    os.setsid()


class Runner:
    """One session, from `starting` to `ended`, and the pty held open in between."""

    def __init__(self, sid, cwd, name, root=SESSIONS, binary=None, argv=None, project=None,
                 chat_id=None, prompt=None, projects_root=None, log=_stderr):
        self.sid = sid
        self.cwd = cwd
        self.name = name
        self.dir = os.path.join(root, sid)
        self.project = project or os.path.basename(cwd.rstrip(os.sep))
        self.chat_id = chat_id
        self.prompt = prompt
        self.projects_root = projects_root
        self.argv = argv or claude_argv(binary, name)
        self.log = log
        self.scrape = Scrape()
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

        pid, master = spawn(self.argv, self.cwd, child_env())
        self.update(claude_pid=pid)
        self.log("spawned pid %d in %s" % (pid, self.cwd))

        # §12: kill the runner and claude dies with it. Not left to the pty hangup — see
        # terminate() for the window in which that does not happen.
        previous = self._catch_signals()
        transcript = open(os.path.join(self.dir, "pty.log"), "ab", 0)
        try:
            self.pump(master, transcript)
        finally:
            transcript.close()
            terminate(pid, log=self.log)
            try:
                os.close(master)
            except OSError:
                pass
            self._restore_signals(previous)

        # §4.6: on `failed` the listener sends the tail of pty.log, which is almost always the
        # actual error — an expired login sitting at a /login prompt, most likely of all (§9.7).
        state = ENDED if self.scrape.url else FAILED
        self.update(state=state)
        self.log("session %s: %s" % (self.sid, state))
        return state

    def _catch_signals(self):
        """SIGTERM ends the session rather than the runner, so meta.json is left truthful."""
        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                previous[sig] = signal.signal(sig, self._signalled)
            except (ValueError, OSError):
                pass          # not the main thread; the finally clause still cleans up
        return previous

    def _restore_signals(self, previous):
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    def _signalled(self, signum, _frame):
        self.stopping = True

    def pump(self, master, transcript):
        """Read until the child hangs up. This loop is what holds the pty open (§2)."""
        typed_at = None
        entered = False

        while not self.stopping:
            try:
                ready, _, _ = select.select([master], [], [], TICK)
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                break

            if ready:
                try:
                    chunk = os.read(master, READ_SIZE)
                except OSError as e:
                    # EIO is how a pty master reports the last slave closing. That is the
                    # ordinary end of a session, not a failure.
                    if e.errno not in (errno.EIO, errno.EBADF):
                        raise
                    break
                if not chunk:
                    break

                transcript.write(chunk)
                if not self.scrape.url and self.scrape.feed(chunk):
                    self.update(state=LIVE, url=self.scrape.url)
                    self.log("live: %s" % self.scrape.url)

            if not self.prompt or not self.scrape.url:
                continue
            # §4: type it only once the session is live — before that there is no input box and
            # the text lands in whatever the renderer was drawing. Enter goes separately, after
            # the box has settled, for the same reason.
            if typed_at is None:
                os.write(master, self.prompt.encode())
                typed_at = time.time()
            elif not entered and time.time() - typed_at >= SETTLE:
                os.write(master, b"\r")
                entered = True


def main():
    ap = argparse.ArgumentParser(description="centrion — the PTY runner for one session.")
    ap.add_argument("--cwd", required=True, help="the project directory to start in")
    ap.add_argument("--name", required=True, help="the --remote-control session name")
    ap.add_argument("--sid", default=None, help="6 hex; generated when absent")
    ap.add_argument("--project", default=None, help="defaults to the basename of --cwd")
    ap.add_argument("--chat-id", type=int, default=None, help="who to tell when it ends")
    ap.add_argument("--prompt", default=None, help="typed into the session once it is live")
    ap.add_argument("--root", default=SESSIONS, help="where session directories live")
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
                    chat_id=a.chat_id, prompt=a.prompt)
    if a.foreground:
        print("session %s · %s" % (runner.sid, runner.dir), file=sys.stderr)
    state = runner.run()
    return 0 if state == ENDED else 1


if __name__ == "__main__":
    sys.exit(main())
