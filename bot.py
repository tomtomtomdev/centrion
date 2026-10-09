#!/usr/bin/env python3
"""The listener: long-polls Telegram, decides who is allowed, and answers.

One process, started by launchd and restarted by it forever (SPEC.md §8). It owns four things
and nothing else: the allowlist, the poll offset, the reply, and the decision to start a
session. The session itself belongs to the runner, which the listener spawns detached and then
keeps no handle on (§2).

**This is the slice where a message becomes a shell.** Through slice 6 every verb was
understood and none of them acted; here `claude beacon` forks a runner that starts Claude Code
in that directory with `--dangerously-skip-permissions`. Everything in this file is what stands
between a Telegram message and that: the allowlist, the date guard, and `config.resolve()`.

Four properties matter more than what it says back.

**It answers exactly one chat.** §10 is blunt: this is a remote code execution endpoint for
this Mac with permissions bypassed, and `allowed_chat_ids` is the whole of the door. Both
`chat.id` and `from.id` are checked, the chat must be private, and every refusal is *silent* —
logged here, nothing sent — because a reply tells a stranger the bot exists and is worth
attacking (§10.3). The consequence is worth stating out loud: a mistyped id in .telegram.json
looks exactly like a broken bot, and var/bot.log is the only place that says which it is (§14).

**It forgets nothing across a restart.** The offset goes to var/offset the moment a batch is in
hand, before a single update is acted on. At-most-once, deliberately: a message lost to a crash
costs one tap on a phone, and a message replayed after one costs a second bypass-permissions
session nobody asked for (§7). The message-date guard is the second half of the same worry —
Telegram holds updates for 24 hours, so a weekend of downtime must not land as a weekend of
sessions on Monday morning.

**It never blocks on a session.** §4.6 has the listener polling meta.json for up to
forty-five seconds while a session comes up; §4.2 has it back in getUpdates the moment the
runner is forked. Both are true because the waiting happens on a thread of its own — one per
pending session, ending when the record says `live` or `failed` or when the deadline passes.
So the answer to `claude beacon` arrives after the answer to whatever was sent behind it, and
that ordering is the design rather than a wrinkle in it.

**It does not die.** §7: launchd would restart a crash, but a crash-loop against
ThrottleInterval is a worse failure mode than a patient retry. telegram.py already never raises
at the poll; everything above it here is wrapped too, per update, per tick and per waiter, so
neither a malformed message nor a session that goes strange can take the loop down with it.

**And it reconciles.** Slice 8 closed the two gaps slice 7 left — nothing enforced
`max_sessions`, and a listener killed between a spawn and its reply left a session running
that nobody had been told about. Both are the same shape: the listener only ever acted on
messages, so anything that happened to a session while nobody was messaging happened
unobserved. §4's pass runs on every *return* from getUpdates instead, message or not, and it
is the only thing here that sends without being asked — a session whose runner is gone, and
(§9.12) a session whose link turned up an hour after anyone was still waiting for it.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import commands
import config
import local
import session
import telegram

#: The platform's process mechanisms — WINDOWS.md §2, §6. Every question this file asks about
#: a process (is that pid alive, when did it start, how is a runner started so that it
#: outlives me, am I the only listener) goes through here, and session.py chose the module.
procs = session.procs

HERE = os.path.dirname(os.path.abspath(__file__))
VAR = os.path.join(HERE, "var")
OFFSET = os.path.join(VAR, "offset")
#: The single-instance lock (§8). On the Mac launchd/bot.sh holds a `lockf` on this file
#: before python starts and `procs.Lock` has nothing to do; on Windows the mutex is named
#: after it and taken in serve(). One path, spelled once, so the two never guard different
#: things.
LOCK = os.path.join(VAR, ".bot.lock")

#: §7: drop any message whose `date` predates daemon start by more than this.
STALE_AFTER = 120

#: A ceiling on what var/offset may contain, and it guards the asymmetry rather than the type.
#: An offset that reads back too *small* costs a replayed batch, which §7's date guard then
#: filters. One that reads back too large acknowledges updates that have not arrived yet, and
#: the bot goes deaf — permanently, silently, and through a restart, because the bad number is
#: on disk. So the bound is deliberately loose: far above anything Telegram emits (update_ids
#: run to ten digits), far below what a truncated or garbage-filled file produces.
MAX_OFFSET = 2 ** 53

#: How long a tick that raised waits before the next one. A bug that fires every time would
#: otherwise be a hot loop against Telegram, and launchd's ThrottleInterval cannot see it
#: because the process never exits.
ERROR_PAUSE = 5

#: §4.6: how long the listener waits for a session to come up, and how often it looks. 45
#: rather than the ten to twenty seconds §4 budgets, because a cold start after a Claude Code
#: update is slower than a warm one; 0.25 so the link reaches the phone at about the speed it
#: reaches the terminal.
SESSION_TIMEOUT = 45
SESSION_POLL = 0.25

#: §5's `power`: pmset's daily shutdown and power-on, read or changed without a session. The
#: script is the one install.sh runs, so the phone and the terminal set the same schedule.
POWER_SH = os.path.join(HERE, "launchd", "power.sh")

#: `power` blocks the listener, like `stop`, and for the same reason: somebody is holding a
#: phone waiting to hear whether the Mac will switch itself off tonight. power.sh runs sudo -n
#: without a terminal, so a missing sudoers rule fails at once; this is for a pmset that hangs.
POWER_TIMEOUT = 20

#: The script's flag for each `power` target. Bare `power` only reads.
POWER_ARGS = {None: "--check", commands.CANCEL: "--cancel", commands.SET: None}

#: §4.6: how much of pty.log goes back when there is no link to send instead. It is almost
#: always the actual error — §9.7's expired login most of all, which produces no other
#: evidence anywhere.
TAIL_LINES = 15

#: How much of the transcript is read to find those lines. A 200-column terminal redrawing
#: itself fills this within a few seconds of startup, which is the whole of what a tail is for.
TAIL_BYTES = 65536

#: §12 slice 13: the most project buttons a keyboard carries. A phone shows about this many
#: without scrolling, and the list in the text of the reply is never capped — §5's answer to
#: bare `claude` is the list, and the keyboard is a convenience laid over it.
MENU_MAX = 12

#: What a project button says. The verb is not decoration: a button reading `beacon` would send
#: `beacon`, which §5 answers with `help`, and the phone would look broken.
BUTTON = commands.START + " %s"

#: §12 slice 14: what a claim button says. An index, unlike BUTTON, because a terminal session
#: has no name of its own that the grammar could carry.
CLAIM_BUTTON = commands.RC + " %d"

#: What the phone hears about the terminal session a claim took over. §12 slice 14, §9.14.
HANDED = {
    local.ENDED: "■ the terminal session (pid %d) ended; it carries on here",
    local.GONE: "the terminal session (pid %d) had already gone",
    local.BUSY: "⚠ the terminal session (pid %d) was busy and is still running — end it there "
                "when its turn is done, or two copies will write one conversation",
    local.ALIVE: "⚠ the terminal session (pid %d) would not end — two copies are running",
}

#: §5: the phone's `/` menu, registered with setMyCommands at every startup so it can never drift
#: from what parse() understands the way a hand-typed BotFather /setcommands list would. Keyed by
#: commands.VERBS, which is the list of what belongs in it; this is only what each one says.
MENU_TEXT = {
    commands.START: "claude <project> [text] — start a session (bare: list the projects)",
    commands.NEW: "new <name> — make a project directory and start a session in it",
    commands.LIST: "the live sessions",
    commands.STOP: "stop <n> · stop all — end one session, or all of them",
    commands.RC: "rc · rc <n> — claim a terminal session: carry it on from here",
    commands.POWER: "power · power cancel · power set — the nightly shutdown schedule",
    commands.HELP: "what this bot understands, and the projects",
}
COMMAND_MENU = [(verb, MENU_TEXT[verb]) for verb in commands.VERBS]

#: §10.7: how long a finished session's directory is kept. Nothing ever removed one, and the
#: directory is not the part that grows — the transcript inside it is capped (§10.7) — but the
#: *number* of them is unbounded, and every `claude beacon` from a phone makes another. A day
#: because the transcript is only ever read for a session that has just gone wrong: §14 has you
#: reading it off disk when the phone got something unhelpful, and that is the same afternoon.
RETAIN = 86400

#: §4.1: six hex, naming a directory under var/sessions and nothing else. Not a credential
#: (§10 — even the session link is not one), but random rather than sequential so that a
#: counter cannot collide with whatever the last boot left on disk.
SID_BYTES = 3

HOME = os.path.expanduser("~")

#: §7's scrub, applied to free text rather than to a path. The lookahead is what keeps a
#: sibling directory — /Users/tomtomtomtom-old — from being mangled into `~-old`.
HOME_RE = re.compile(re.escape(HOME) + r"(?![A-Za-z0-9_-])")

#: §7 says *token-shaped*, not "our token", and means it: a session's transcript is full of
#: other people's credentials, and the one that leaks will be the one nobody thought to name.
#: Two shapes, both of which exist on this box — a Telegram bot token and an `sk-` API key.
#:
#: Deliberately not a general "long opaque run" rule. A session id is twenty-six characters of
#: mixed-case base62, which is exactly what such a rule goes looking for, and a scrub that ate
#: the link would break the one reply that matters and would do it silently.
TOKENISH = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{12,}")

#: What replaces the middle of a reply too long to send. See fit().
ELISION = "\n… %d characters elided …\n"

#: §9.11: **the two grace periods nest.** Ending a runner is two acts in sequence, not one —
#: the runner hears the stop and then spends up to its own `session.GRACE` ending claude,
#: which was measured at over five seconds for a real session on this box. A caller that
#: allowed it the same budget would kill it in the middle of that, orphaning the session
#: and leaving meta.json saying `live` for something already gone. Three times over, so the
#: multiple is visible rather than a number that looks arbitrary a year from now.
STOP_GRACE = session.GRACE * 3

#: How often `stop` asks whether the runner has gone yet, while it waits out the above. A
#: fifth of `session.TICK`, so the answer is never more than a poll behind the runner noticing
#: the marker; the wait it divides is measured in seconds, so nothing here is hot.
STOP_POLL = 0.05

#: §4: `pid reuse is theoretically possible between reboots; started is in the record, so
#: compare it against the process start time.` The runner writes its record within a moment of
#: starting, so a genuine runner's process is always *older* than its record; this slack is
#: for clock skew and a slow spawn, not for the reuse it is guarding against — which after a
#: reboot is hours out, not minutes.
PID_REUSE_SLACK = 120

#: The two things the bot ever says about a session unprompted (§4, §9.12), and the two
#: separate claims that keep each to exactly once. A session announces its link when it
#: arrives and its ending when it ends; one must never spend the other's claim.
LINK_SENT, END_SENT = "link", "end"

#: One marker file per claim, written by the listener and by nobody else. The record beside it
#: belongs to the runner (§3), and there is no lock between the two processes.
ANNOUNCED = "announced-%s"

#: §9.4: killing the PTY leaves the remote session registered but offline — it stays in the
#: claude.ai/code list without the green dot, and `claude --continue` in that directory
#: reattaches within roughly four hours. §9.4 asks `stop` to say so, because the alternative
#: is assuming the work went with it.
REATTACH = ("It stays in claude.ai/code, offline. `claude --continue` in that directory "
            "picks it up for about four hours.")


def log(message):
    """One line to stderr, which launchd routes to var/bot.log (§8). §14 starts here."""
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


def tilde(path):
    """`/Users/tomtomtomtom/Projects` → `~/Projects`. §7's scrub, and §10 is why.

    Telegram is not end-to-end encrypted and sees every reply. The home path is not a secret on
    its own, but it names the account whose keychain, OAuth credentials and tokens this bot can
    reach, and it appears in every path the bot would otherwise quote. The separator check keeps
    a sibling directory — `/Users/tomtomtomtom-old` — from being mangled into `~-old`.
    """
    if path == HOME:
        return "~"
    if path.startswith(HOME + os.sep):
        return "~" + path[len(HOME):]
    return path


def loggable(text, limit=60):
    """User text on its way into var/bot.log, which is read on a terminal with `tail -f`.

    `repr` rather than the string itself: this arrives from the wire, and a project name is
    free to contain ANSI escapes, which would otherwise rewrite the display of the very log
    they appear in. Clipped because a 4096-character message is a legal message and a
    4096-character log line is not a useful one.
    """
    return repr(text if len(text) <= limit else text[:limit - 1] + "…")


def fit(text, limit=telegram.LIMIT):
    """`text`, short enough for one sendMessage. SPEC.md §7.

    Head and tail with the middle marked, rather than a plain truncation: the first lines of an
    error say what was being attempted and the last say how it went, and which of the two
    matters is not knowable from here. Over the cap Telegram answers 400 rather than truncating
    — a silent non-reply at exactly the moment the phone is waiting for one.

    Sized against the longest the mark could be and then written with the true count, which is
    a smaller number and therefore never a longer string.
    """
    if len(text) <= limit:
        return text
    room = limit - len(ELISION % len(text))
    if room < 2:
        return text[:limit]
    head = room // 2
    return text[:head] + (ELISION % (len(text) - room)) + text[len(text) - (room - head):]


def tappable(name):
    """True if a button saying `claude <name>` is read back as this project and nothing else.

    §3 permits a space in a directory name — `My Project` is an ordinary directory — and
    `commands.parse` splits the verb from its argument on whitespace, so that button would
    arrive as `claude My` carrying the prompt `Project`: a refusal if nothing is called `My`,
    and a session in the *wrong* project if something is.

    The check is a round trip through the parser rather than a character rule of its own,
    which is the same argument §3 makes about `new` and `claude` sharing one set of checks: a
    second copy of the grammar's rules here is how the two quietly drift apart. Whatever the
    parser can read back is what a button may say.
    """
    intent = commands.parse(BUTTON % name)
    return (intent.verb == commands.START and intent.project == name
            and intent.prompt is None)


def menu_names(names):
    """The projects that get a button: the ones the grammar can carry, capped. §12 slice 13."""
    return [name for name in names if tappable(name)][:MENU_MAX]


def keyboard(names):
    """§5's project list as a reply keyboard, or None when there is nothing to tap.

    None rather than an empty keyboard, because Telegram reads an empty one as *remove the
    keyboard this chat already has* — a root that has briefly gone unreadable (§9.2) would
    otherwise take the menu away with it.

    `stop` is deliberately not on here, in any form. §4 already refuses to read a bare `stop`
    as `stop all` because it is the one misreading in this grammar that cannot be taken back,
    and a button for it is that message one thumb from every live session on the box — tapped
    by somebody half-attending, which is precisely the state §5 designs the bare `claude` reply
    around. The menu carries the verbs whose worst misreading is a wasted second.
    """
    rows = [[BUTTON % name] for name in menu_names(names)]
    if not rows:
        return None
    rows.append([commands.LIST, commands.HELP])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def _last(path, count):
    """The last `count` bytes of that file, or b"" if there is no such file. §4.6's tail."""
    if count <= 0:
        return b""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - count))
            return fh.read()
    except OSError:
        return b""


def scrub(data, redact=None):
    """Terminal output on its way to Telegram. §7, and §10 is why it is not optional.

    Four passes, most specific first: the escapes go first, because everything after them is
    matching against text a human would have seen; then this bot's own token, which only the
    client can recognise and so arrives as its `redact`; then anything else token-shaped; then
    this machine's home path.
    """
    text = session.strip(data)
    if redact is not None:
        text = redact(text)
    text = TOKENISH.sub("<redacted>", text)
    return HOME_RE.sub("~", text)


def uptime(started, now=None):
    """How long ago `started` was, short enough for a phone. §5's `ls` column.

    Total, like commands.parse(): §4 walks every directory under var/sessions and reads
    whatever is in it, so a record half-written by a kill at logout, or left by an older build
    of this bot, has to render as a line in a listing rather than as a traceback that hides
    every session behind it.

    Clamped at zero because `started` is a *wall clock* timestamp and this one is not
    monotonic — §4.6 uses a monotonic clock for the session deadline for exactly this reason.
    A laptop that has been asleep, or an NTP correction, routinely leaves a record dated a few
    seconds into the future, and "-3s" in a listing reads as a bug in the bot.
    """
    now = time.time() if now is None else now
    try:
        seconds = int(now - started)
    except (TypeError, ValueError, OverflowError):
        return "?"
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        hours, minutes = divmod(seconds // 60, 60)
        return "%dh %dm" % (hours, minutes) if minutes else "%dh" % hours
    days, hours = divmod(seconds // 3600, 24)
    return "%dd %dh" % (days, hours) if hours else "%dd" % days


def _seconds(value):
    """A record's `started` as a number to sort on. Anything unusable sorts first."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else 0.0


def ordinal(n):
    """2 → `2nd`. §5 spells the same-directory warning `⚠ 2nd session in beacon`."""
    if 10 <= n % 100 <= 20:
        return "%dth" % n
    return "%d%s" % (n, {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


#: When that pid's process started, in epoch seconds, or None if there is no such process.
#: §4 asks for this by name: `pid reuse is theoretically possible between reboots; started is
#: in the record, so compare it against the process start time before trusting a pid that is
#: alive.` The mechanism — `/bin/ps lstart` on the Mac, psutil on Windows — is the platform's
#: (session_posix.started); the comparison against the record is `Sessions.alive`'s.
process_started = procs.started


class Sessions:
    """The listener's one door to §2's second process: it starts runners and reads them back.

    A class rather than three functions because it is also the seam the tests replace. On this
    side of it is everything a unit test may do — name a session directory, read a record, reap
    a corpse. On the other side is the one thing it may not, which is start a Claude Code
    session with permissions bypassed.

    **The runner is not put into a session of its own here**, deliberately.
    `start_new_session=True` is the obvious way to spawn a detached child and it is the wrong
    one for this child: it calls `setsid()` before the exec, so `session.detach()`'s own
    `setsid()` then fails with EPERM — before the pty is allocated, before meta.json says
    anything, and the phone waits out all forty-five seconds for a session that died instantly.
    The runner detaches itself (§8), which is also where it drops fd 9.
    """

    def __init__(self, root=None, python=None, script=None, log=log):
        self.root = root or session.SESSIONS
        # §3 pins /usr/bin/python3 for the daemon, and the runner is the same interpreter for
        # the same reason: one set of 3.9 behaviours to be surprised by rather than two.
        self.python = python or sys.executable
        self.script = script or os.path.join(HERE, "session.py")
        self.log = log
        # Runners forked and not yet reaped. Not a handle on a session — §2 is explicit that
        # the listener holds none — only the corpse-collecting duty that comes with having
        # been the process that forked it. See reap().
        self.children = []

    def directory(self, sid):
        return os.path.join(self.root, sid)

    def read(self, sid):
        """That session's record, or None. §2: these two processes talk through files."""
        return session.read_meta(self.directory(sid))

    def start(self, sid, name, cwd, project, chat_id, prompt=None, trust=False, resume=None):
        """Fork a runner for this session and return its pid. Never waits for it (§4.2).

        `cwd` has already been through `config.resolve()` and goes through it again inside the
        runner (§3): the listener resolves, the runner re-checks, and the string that came off
        the wire is never a working directory.

        `trust` is §9.3's, and it is one half of a condition: this process knows the directory
        was created by this bot a moment ago, and the runner knows whether there is anything in
        it. Neither half is enough on its own — see session.Trust.
        """
        argv = [self.python, self.script,
                "--sid", sid, "--name", name, "--cwd", cwd, "--project", project,
                "--chat-id", str(chat_id), "--root", self.root]
        if prompt:
            argv.extend(["--prompt", prompt])
        if trust:
            argv.append("--trust")
        if resume:
            # §12 slice 14. Checked again by the runner where it becomes claude's argv.
            argv.extend(["--resume", resume])

        os.makedirs(self.root, exist_ok=True)
        # A list and no shell, which is the whole of the defence here: `project` and `prompt`
        # arrived from a phone (§10). On the Mac that is also the end of it — execve is handed
        # the list and the child is handed the same list, with no string in between for the
        # quoting to be got wrong in. On Windows there is no execve: Popen joins the list with
        # `subprocess.list2cmdline` and the runner's own C runtime splits it apart again before
        # argparse sees anything, so the defence there is one round trip rather than none. It
        # still holds — no shell is in the chain, so nothing expands a `%VAR%` or acts on a
        # `>` — but "nothing to quote for" was a Mac sentence, and the round trip is asserted
        # against a real runner in TestSpawningForRealOnWindows rather than assumed: a prompt
        # ending in a backslash is how the argument *after* it disappears (WINDOWS.md W3c).
        #
        # stdin is /dev/null because the runner has a terminal of its own for the session and no
        # use for launchd's.
        #
        # Where its stdout and stderr go is the platform's answer, and it stopped being the
        # same answer in W5e. On the Mac they are still inherited — `runner_output` yields
        # nothing — so the runner's log lines land in var/bot.log beside the listener's (§14),
        # which costs nothing because an append fd a child inherits denies no one. On Windows
        # `bot.cmd` opened that log with a `cmd` redirection, which denies other writers, and
        # a runner holding a duplicate of it for the life of a session meant no second
        # `bot.cmd` could open the log at all — so the KeepAlive loop could not restart a
        # listener that died while a session was running (measured: 5m10s down). There the
        # runner gets `var\sessions\<sid>\runner.log` of its own, and §14 is two files.
        # The `with` matters: the handle is the runner's, and a listener that kept its copy
        # would have moved the lock rather than removed it.
        # `spawn_flags()` is empty on the Mac — the runner detaches itself with setsid() — and
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP on Windows (WINDOWS.md §6).
        with open(os.devnull, "rb") as devnull, procs.runner_output(self.directory(sid)) as out:
            child = subprocess.Popen(argv, stdin=devnull, cwd=HERE, close_fds=True,
                                     **out, **procs.spawn_flags())
        self.children.append(child)
        return child.pid

    def reap(self):
        """Collect the runners that have exited. Every tick (§4).

        Not housekeeping. An unreaped child stays in the process table as a zombie, and a
        zombie answers `os.kill(pid, 0)` exactly as a living process does — which is the call
        §4's reconciliation uses to decide whether a session is still there. Every ended
        session would go on counting against `max_sessions` until the listener restarted.
        """
        for child in list(self.children):
            if child.poll() is not None:
                self.children.remove(child)

    # -- the fleet, as it is on disk (§4, §5) --------------------------------------------

    def records(self):
        """Every session's record, oldest first. The walk §4's reconciliation pass is.

        Ordered by `started` because §5 numbers the listing and `stop 2` takes that number:
        readdir order is arbitrary and changes underneath you, so an index built on it would
        mean a different session a minute after the `ls` that printed it. The sid breaks ties,
        so two sessions started in the same second still order the same way twice running.

        The directory name wins over the record's own `sid` field — it is what names the
        directory `claim()` and `finish()` are about to write into, and a record that
        disagrees with the directory it is in is a record that has been tampered with or
        truncated.
        """
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return []                # no var/sessions yet: no sessions, not an error
        found = []
        for sid in names:
            # read_meta and not self.read(): that one is the waiter's per-session poll (§4.6),
            # and this is the walk that has to see every session there is. Keeping them
            # separate is what stops one session being slow to answer from stalling the pass
            # whose whole job is to notice the others.
            record = session.read_meta(self.directory(sid))
            if record is None:
                # §4 is explicit that read_meta returns None rather than raising, and this is
                # the consequence it protects: one truncated record must not be able to hide a
                # live bypass-permissions session from the only listing anybody has.
                continue
            record["sid"] = sid
            found.append(record)
        found.sort(key=lambda r: (_seconds(r.get("started")), r.get("sid") or ""))
        return found

    def alive(self, record):
        """Is that session's runner still there? §4's check, and then the guard on it.

        Two questions, and the second is the one that catches a reboot. `kill(pid, 0)` says
        whether *something* answers to the pid; it cannot say whether that something is the
        runner this record was written about. After a reboot every pid in every record on
        disk is either free or somebody else's, and four-digit pids are handed out within a
        minute of login — so a bot that trusted the first question alone would come back from
        a restart reporting phantom sessions it could neither stop nor replace.

        The type check is not defensive padding. `kill(0, sig)` signals this process's entire
        group and `kill(-1, sig)` signals every process this user owns, so a record corrupted
        into either would turn this pass — and then `stop all` — into something that takes the
        daemon and the desktop with it. `True` is an int in Python and would be pid 1.
        """
        pid = record.get("runner_pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
            return False
        # `kill(pid, 0)` on the Mac, with EPERM counted as alive — a process that belongs to
        # someone else is there (§9.11); psutil on Windows. The platform answers "is there
        # something at this pid"; the start-time check below answers "is it ours".
        if not procs.alive(pid):
            return False

        started = record.get("started")
        if isinstance(started, bool) or not isinstance(started, (int, float)):
            # A corrupt record, since §3 always writes `started`. Of the two ways to be wrong
            # about it, showing a session that may not exist is recoverable and hiding one
            # that does is not: `ls` is the only view of a bypass-permissions session this
            # machine offers, and `stop` can only reach what `ls` can see.
            return True
        began = procs.started(pid)
        if began is None:
            return True              # it answered kill(0) a moment ago; ps losing a race is
        return began <= started + PID_REUSE_SLACK        # not evidence of anything

    def age(self, sid, now=None):
        """Seconds since that session's record was last written, or None if there is none.

        The record's mtime, deliberately, and not its `started` field: a session left open for
        a week is not an old *record*, and §10.7 is counting from the moment a session became
        terminal — which is the last time anything wrote meta.json, whether that was the runner
        on its way out or the reconciliation pass noticing its runner was gone.
        """
        try:
            mtime = os.path.getmtime(os.path.join(self.directory(sid), session.META))
        except OSError:
            return None
        return (time.time() if now is None else now) - mtime

    def discard(self, sid):
        """Remove that session's directory, and everything in it. §10.7.

        **Only ever called for a record this process has established is terminal and old.**
        The directory holds the record `ls` and `stop` are read from and the transcript §4.6
        sends to the phone; removing one belonging to a live session would leave a
        bypass-permissions session running with nothing on this machine naming it.
        """
        try:
            shutil.rmtree(self.directory(sid))
        except OSError as e:
            # Runs on every tick, so this must cost one line and not the pass. A full disk or a
            # directory this process cannot write is not a reason to stop answering the phone.
            self.log("could not remove the directory for session %s: %s" % (sid, e))
            return False
        return True

    def claim(self, sid, kind):
        """True for whoever gets here first, and False for everybody after. §4, §9.12.

        The arbitration between §4.6's waiter thread and §4's tick, both of which can be
        looking at one newly-live record at the same moment — the 45s deadline falls inside
        the ≤50s tick interval, so that window is not a narrow one. It has to be the
        filesystem's decision rather than a set in memory: the two claimants are two threads
        today and two *processes* after a `launchctl kickstart`, and a marker that did not
        survive a restart would mean every session that ever ended announcing itself again at
        every login.

        O_CREAT|O_EXCL is the whole mechanism — one atomic syscall, no read-then-write window.
        """
        path = os.path.join(self.directory(sid), ANNOUNCED % kind)
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            return False
        except OSError as e:
            # Claiming is what stops a message being sent twice, so a claim that cannot be
            # written has to read as "somebody else has it". The alternative is a full disk
            # turning into the same message every fifty seconds until it is noticed.
            self.log("could not claim the %s announcement for session %s: %s" % (kind, sid, e))
            return False
        return True

    def finish(self, record):
        """Write that record as `ended`, and hand back what is now on disk. §4.

        **Only ever called for a runner this process has established is gone.** meta.json
        belongs to the runner — §3 has it written atomically so that a listener reading
        mid-write never sees half a record — and there is no lock between the two processes,
        so the listener writing a record whose runner is still running would be a
        read-modify-write race that silently drops whatever the runner wrote in between.
        """
        sid = record.get("sid")
        ended = dict(record, state=session.ENDED)
        try:
            session.write_meta(self.directory(sid), ended)
        except OSError as e:
            self.log("could not mark session %s ended: %s" % (sid, e))
            return record
        return ended

    def stop(self, record, grace=STOP_GRACE):
        """Ask the runner to end its session; kill it if it will not. §5's `stop`, §9.10.

        **Ask, wait, force** — and W4b moved the first of those three from a signal to a file
        (WINDOWS.md §6). The ask is `procs.request_stop`, the marker `Runner.pump` looks for
        every tick, because Windows has no catchable signal to send a detached process: its
        only way of reaching another process is `TerminateProcess`, which the runner cannot
        hear and cannot act on, and a session ended that way leaves claude's tree to the
        pseudoconsole and the job object with nothing written down about any of it.

        On the Mac the marker is a second way of saying what SIGTERM says, and SIGTERM is
        still said — it is the first half of `session.terminate` below, which is now the
        *force* rather than the ask. That is a real change to this platform's timing and it is
        one way round rather than the other on purpose: a runner anywhere but `pump` hears
        only the signal, so the Mac pays `grace` before being reached at all, where it used to
        be reached at once. What it buys is that the stop a session actually gets is the same
        stop on both platforms, and that the process which knows what the session was doing is
        the one that ends it — `terminate` here is for the runner that did not answer.

        Thin on purpose past that: session.terminate() is where the hard-won details live —
        the process *group* as well as the process on the Mac, the Ctrl-C and the job on
        Windows — and slice 7 shipped bugs against two of them.
        """
        if not self.alive(record):
            return True              # already gone is the outcome `stop` was asking for

        sid = record.get("sid")
        # A record with no usable sid has no directory, and the marker is a file in one. §3
        # always writes it, so this is a corrupt record — the same case `alive`'s type guard
        # is about, and the same answer: it is still a session with permissions bypassed and
        # `stop` is still the only thing that can reach it, so skip the ask and take the pid.
        asked = isinstance(sid, str) and bool(sid)
        if not asked:
            self.log("session record %r has no sid: stopping pid %r without asking first"
                     % (sid, record.get("runner_pid")))
        else:
            try:
                procs.request_stop(self.directory(sid))
            except OSError as e:
                # A full disk, or a session directory deleted under us. The ask was not made,
                # so there is nothing to wait for — `claim()` draws the same line one file
                # away, for the same reason: what cannot be written did not happen.
                self.log("could not ask session %s to stop: %s" % (sid, e))
                asked = False

        if asked:
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                # Slept first: the runner needs a tick to notice the marker, and `alive` was
                # asked a moment ago at the top of this function.
                time.sleep(STOP_POLL)
                if not self.alive(record):
                    return True
        return session.terminate(record.get("runner_pid"), grace=grace, log=self.log)


def read_offset(path=OFFSET):
    """The last acknowledged update_id + 1, or None if there is not a usable one.

    None is an ordinary answer, not an error: it is the first run ever, and it is also every
    way the file can go wrong — truncated by a full disk, half-written by a kill at logout,
    deleted by hand. §7's message-date guard is what makes that survivable, so this hands back
    None and lets the guard do its job rather than refusing to start.
    """
    try:
        with open(path) as fh:
            value = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    return value if 0 < value < MAX_OFFSET else None


def write_offset(path, offset):
    """Atomically. A listener killed mid-write must not leave a plausible-looking number.

    Same temp-file-and-replace discipline meta.json gets in slice 6, and for the same reason:
    the file is read by a process that did not write it (this one, after a restart), so a
    partial write is a silent wrong answer rather than a visible failure.
    """
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as fh:
        fh.write("%d\n" % offset)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Listener:
    """The poll loop. One per process; launchd and lock.sh together guarantee that (§8)."""

    def __init__(self, cfg, tg, started=None, offset_path=OFFSET, log=log, sleep=time.sleep,
                 sessions=None, newsid=None, clock=time.monotonic,
                 timeout=SESSION_TIMEOUT, poll_every=SESSION_POLL, power_sh=None,
                 claimable=None, hand_over=None):
        self.cfg = cfg
        self.tg = tg
        self.offset_path = offset_path
        self.log = log
        self.sleep = sleep
        self.started = int(time.time()) if started is None else started
        self.sessions = Sessions(log=log) if sessions is None else sessions
        self.newsid = newsid or (lambda: os.urandom(SID_BYTES).hex())
        # monotonic, not wall clock: a deadline that moves when the system clock is set back —
        # which is every NTP correction and every wake from sleep — is a wait that never ends.
        self.clock = clock
        self.timeout = timeout
        self.poll_every = poll_every
        # How `power` runs launchd/power.sh. A seam for the tests, which must never touch pmset.
        self.power_sh = power_sh or (lambda argv: subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=POWER_TIMEOUT, cwd=HERE))
        # §12 slice 14's two seams onto ~/.claude/sessions: what `rc` lists, and ending the
        # terminal session a claim took over. Tests must never signal a real process.
        self.claimable = claimable or (lambda: local.claimable(self.cfg.projects_root))
        self.hand_over = hand_over or local.end
        # One thread per session still coming up. Pruned as they finish, in watch().
        self.waiters = []
        # Sids forked and not yet on disk. §4.2 has this process returning to the poll the
        # instant it has forked, so for the first fraction of a second a session exists as a
        # process and as nothing else — and §10.6's held-down `claude` is precisely a second
        # message arriving inside that window. A count taken off the filesystem alone would
        # wave every one of them through.
        self.pending = set()

        # Pick up where the last listener left off. Doing it here rather than in serve() means
        # a test can restart a listener by building a second one, which is exactly what a
        # launchctl kickstart does.
        self.saved = read_offset(offset_path)
        if self.saved is not None:
            self.tg.offset = self.saved

    # -- who gets an answer ------------------------------------------------------------------

    def permitted(self, message):
        """The chat id to reply to, or None. SPEC.md §10.1-§10.3.

        Silent on every refusal, and logged on every refusal. Both halves are the rule: §10.3
        wants a stranger to learn nothing, and §14 wants the owner to be able to find out why
        the phone got nothing. The log is the only place those two can both be served.
        """
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            self.log("refusing an update with no chat or no sender")
            return None

        cid, uid = chat.get("id"), sender.get("id")
        # `type(...) is not int` and not isinstance: bool subclasses int, and `True` is in
        # frozenset({1}). JSON's `true` parses to a bool, so an update carrying `"id": true`
        # would otherwise walk into an allowlist that happens to contain 1. config.py refuses
        # to build such an allowlist for the same reason; this is that check from the wire side.
        if type(cid) is not int or type(uid) is not int:
            self.log("refusing an update whose ids are not integers (chat %r, from %r)"
                     % (cid, uid))
            return None

        if cid not in self.cfg.allowed_chat_ids or uid not in self.cfg.allowed_chat_ids:
            # §10.3: log the id, send nothing. A "not authorised" reply confirms there is
            # something here worth attacking.
            self.log("refusing chat %d (from %d): not in allowed_chat_ids" % (cid, uid))
            return None

        if chat.get("type") != "private":
            # §10.2 stands on its own even when the id is allowlisted, which is the only case
            # that reaches this line: anyone can add a bot to a group, so a group id is not an
            # identity, and --whoami prints a warning beside one for exactly this reason.
            self.log("refusing chat %d: type is %r, not private" % (cid, chat.get("type")))
            return None
        return cid

    def fresh(self, message):
        """False for a message from before this daemon was started. §7's braces.

        One-sided on purpose. A phone's clock can run ahead of this Mac's, and a message from
        the future is a clock, not a backlog; only the past is suspicious. A message with no
        usable date fails closed, because the whole point here is not to guess.
        """
        date = message.get("date")
        if type(date) is not int:
            return False
        return date >= self.started - STALE_AFTER

    # -- what it says back -------------------------------------------------------------------

    def project_list(self):
        names = config.projects(self.cfg.projects_root)
        root = tilde(self.cfg.projects_root)
        if not names:
            return ("Nothing in %s. A directory has to exist there to be reachable, and "
                    "nothing outside it ever is." % root)
        text = "Projects in %s:\n%s" % (root, "\n".join("  " + n for n in names))
        # The one place the keyboard is allowed to affect the words. A button missing beside a
        # name that is plainly in the list reads as a bug from a phone, and both reasons for it
        # are things the owner can act on: too many projects, or a name this grammar cannot
        # carry (§12 slice 13).
        shown = menu_names(names)
        if len(shown) < len(names):
            text += "\n\n"
            if len(shown) == MENU_MAX:
                text += "The keyboard holds the first %d. " % MENU_MAX
            else:
                text += "A name with a space in it gets no button. "
            text += "`claude <project>` still reaches any of them."
        return text

    def buttons(self):
        """The keyboard for a reply that lists the projects. §12 slice 13.

        A second read of the root a moment after `project_list()` read it, and deliberately not
        a cached one: the two disagree only if a directory appeared or went between them, and
        the worse half of that — a button for a directory that has gone — is a `claude <gone>`,
        which is §3's ordinary refusal and already tested. Holding a listing across a reply to
        avoid it would be state, and state about the filesystem is the thing §4 spends a whole
        reconciliation pass not keeping.
        """
        return keyboard(config.projects(self.cfg.projects_root))

    def menu(self, intent):
        """The keyboard this intent's reply carries, if any. §12 slice 13.

        The two replies that list the projects draw the menu, and so does `ls`: the question
        after "what is running" is usually "start one where", and the answer is then one tap
        away. Everything else — a `stop`, a session's own reply — sends no markup at all, which
        leaves the keyboard the phone already has exactly where it was.
        """
        if intent.verb in (commands.HELP, commands.LIST):
            return self.buttons()
        if intent.verb == commands.START and intent.project is None:
            return self.buttons()
        if intent.verb == commands.RC and intent.target is None:
            return self.claim_buttons()
        return None

    def help(self):
        """§5's two tables, and the directories currently in the root."""
        return (
            "centrion — Claude Code sessions on this Mac, from here.\n"
            "\n"
            "claude                   the projects below, and nothing else\n"
            "claude <project>         a session there\n"
            "claude <project> <text>  a session there, then type that\n"
            "new <name>               a new project directory, and a session in it\n"
            "ls                       the live sessions\n"
            "stop <n> · stop all      end one, or all of them\n"
            "rc · rc <n>              terminal sessions here: list them, or carry one on\n"
            "power                    the nightly shutdown and morning power-on\n"
            "power cancel · power set clear that schedule, or put it back\n"
            "help                     this\n"
            "\n" + self.project_list())

    def answer(self, intent):
        """An intent → the text to send now.

        `claude <project>` is not here: it is answered when the session is, which may be three
        seconds later or forty-five (§4.6). See begin() and outcome().
        """
        if intent.verb == commands.START:
            # Only bare `claude` reaches this line. §5: it starts nothing, and the one extra
            # tap buys that no session can begin in a repository nobody named. §15 records the
            # decision and what would reopen it.
            return self.project_list() + "\n\nSend `claude <project>` to start one there."

        if intent.verb == commands.LIST:
            return self.listing()

        if intent.verb == commands.STOP:
            return self.halt(intent.target)

        if intent.verb == commands.RC:
            # Only bare `rc` reaches this line; `rc <n>` is answered when its session is.
            return self.terminal_listing()

        if intent.verb == commands.POWER:
            return self.power(intent.target)

        return self.help()

    def power(self, target):
        """§5's `power`: launchd/power.sh, run here rather than by a session.

        Changing the schedule is one pmset command, and spawning a Claude Code session with
        permissions bypassed to type it would be the most expensive way to run it and the least
        predictable. The script's own words are the reply — they already say what it did, and
        what to do when it could not (no sudoers rule yet: run it once in a terminal).
        """
        if sys.platform != "darwin":
            return "No power schedule here: it is pmset's, and this is not a Mac."
        argv = ["/bin/sh", POWER_SH]
        flag = POWER_ARGS[target]
        if flag:
            argv.append(flag)
        try:
            done = self.power_sh(argv)
        except subprocess.TimeoutExpired:
            self.log("power %s: power.sh did not finish in %ds" % (target or "", POWER_TIMEOUT))
            return "✗ power.sh did not finish in %ds. Nothing is known to have changed." \
                % POWER_TIMEOUT
        except OSError as e:
            self.log("power %s: could not run power.sh (%s)" % (target or "", e))
            return "✗ Could not run power.sh."
        text = HOME_RE.sub("~", (done.stdout or "").strip()) or "(power.sh said nothing)"
        self.log("power %s: power.sh exited %d" % (target or "", done.returncode))
        if target and done.returncode != 0:
            return "✗ " + text
        return text

    # -- the fleet -------------------------------------------------------------------------

    def fleet(self):
        """The sessions that still exist, in the order §5 numbers them.

        `starting` counts as existing. It has already paid for a Claude Code process, `stop`
        has to be able to reach it — §9.10's orphan is exactly a session nobody could see —
        and §4.1 checks the count before it mints rather than after a link comes back.
        """
        return [r for r in self.sessions.records()
                if r.get("state") not in (session.ENDED, session.FAILED)]

    def listing(self, fleet=None):
        """§5's `ls`: index, project, name, uptime, link.

        The only view of this machine a phone gets, and the numbers in it are the ones `stop`
        is about to be handed — so this and halt() read the same fleet in the same order, and
        neither builds its own.
        """
        fleet = self.fleet() if fleet is None else fleet
        if not fleet:
            return "No live sessions."
        lines = []
        for index, record in enumerate(fleet, 1):
            line = "%d. %s · %s · %s" % (index, record.get("project") or "?",
                                         record.get("name") or "?",
                                         uptime(record.get("started")))
            url = record.get("url")
            if record.get("state") == session.LIVE and isinstance(url, str) and url:
                lines.append(line)
                # §5 again: on its own line, or Telegram will not make it tappable.
                lines.append(url)
            else:
                lines.append(line + " · starting…")
        return "\n".join(lines)

    def halt(self, target):
        """§5's `stop <n>` and `stop all`.

        The one place the listener deliberately blocks. §4.2's rule is about *starting* a
        session — forty-five seconds of polling meta.json would be forty-five seconds of a
        deaf bot — and this is the opposite case: somebody is holding a phone waiting to hear
        that a session with permissions bypassed is gone, and the wait is bounded by
        STOP_GRACE rather than by a session that may never come up. A runner that dies when
        asked, which is all of them, returns this in well under a second.
        """
        fleet = self.fleet()
        if not fleet:
            return "No live sessions to stop."

        if target == commands.ALL:
            chosen = fleet
        elif isinstance(target, int) and 1 <= target <= len(fleet):
            chosen = [fleet[target - 1]]
        else:
            return "There is no session %s. %d running:\n\n%s" % (
                target, len(fleet), self.listing(fleet))

        stopped, left = [], []
        for record in chosen:
            if self.sessions.stop(record, STOP_GRACE):
                # Claimed before the record is written, so that §4's pass — which is about to
                # see a newly-ended record — does not announce an ending the reply below has
                # already reported. Only on success: a session that would not die is one this
                # bot still owes an announcement for, whenever it does.
                self.sessions.claim(record.get("sid"), END_SENT)
                self.sessions.finish(record)
                stopped.append(record)
            else:
                left.append(record)
            self.log("stop %s: session %s" % (record.get("sid"),
                                              "stopped" if record in stopped else "WOULD NOT DIE"))

        lines = ["■ %s · %s stopped." % (r.get("project"), r.get("name")) for r in stopped]
        lines += ["✗ %s · %s did not stop and is still running." % (r.get("project"),
                                                                   r.get("name"))
                  for r in left]
        if stopped:
            lines.append("")
            lines.append(REATTACH)
        return "\n".join(lines)

    def crowded(self, record, sid):
        """§5's `⚠ 2nd session in beacon`, or None. The one comparison in the whole spec.

        `samefile` and not `==`, per §9.9: this volume is case-insensitive and realpath() is
        lexical, so `claude beacon` and `claude BEACON` start two sessions in one directory
        under two path strings that compare unequal. A string comparison here would leave the
        warning silently never firing in the one case it exists for — two sessions editing the
        same files underneath each other, which is the entire hazard it is about.
        """
        cwd = record.get("cwd") if record else None
        if not isinstance(cwd, str) or not cwd:
            return None
        others = 0
        for other in self.fleet():
            if other.get("sid") == sid:
                continue
            try:
                if os.path.samefile(cwd, other.get("cwd")):
                    others += 1
            except (OSError, TypeError, ValueError):
                # A record naming a directory since renamed or deleted, which is an ordinary
                # thing to find in var/sessions after a week. samefile raises rather than
                # answering False, and a comparison that cannot be made is not a match.
                continue
        if not others:
            return None
        return "⚠ %s session in %s" % (ordinal(others + 1), record.get("project") or "one directory")

    # -- starting one ----------------------------------------------------------------------

    def full(self, chat_id):
        """§10.6's cap, said to the phone when it is reached. True means refuse.

        The union is the point — see self.pending.
        """
        running = set(r.get("sid") for r in self.fleet()) | self.pending
        if len(running) < self.cfg.max_sessions:
            return False
        self.say(chat_id, "%d of %d sessions already running. `ls` to see them, "
                          "`stop <n>` or `stop all` to make room."
                 % (len(running), self.cfg.max_sessions))
        return True

    # -- claiming a terminal session (§12 slice 14) -----------------------------------------

    def terminal_listing(self, found=None):
        """Bare `rc`: index, project, name, busy/idle, uptime — and nothing from the transcript."""
        found = self.claimable() if found is None else found
        if not found:
            return ("No terminal sessions to claim: every Claude Code session here already has "
                    "Remote Control, or none is running in a project.")
        lines = ["%d. %s · %s · %s · %s" % (index, entry.project, entry.name, entry.status,
                                            uptime(entry.started))
                 for index, entry in enumerate(found, 1)]
        lines.append("")
        lines.append("`rc <n>` carries one on from here and ends it in the terminal.")
        return "\n".join(lines)

    def claim_buttons(self, found=None):
        found = self.claimable() if found is None else found
        if not found:
            return None
        rows = [[CLAIM_BUTTON % index] for index in range(1, min(len(found), MENU_MAX) + 1)]
        rows.append([commands.LIST, commands.HELP])
        return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}

    def claim(self, chat_id, index):
        """`rc <n>`: resume that conversation under a runner, then end the original. §9.14.

        The registry is read again here rather than trusted from the `rc` that printed the
        number, the way halt() reads the fleet again for `stop <n>`. A busy session is refused
        before anything starts: its turn would be cut off at the hand-over, or left writing the
        same conversation as the claim.
        """
        if self.full(chat_id):
            return "refused, at the cap"
        found = self.claimable()
        if not 1 <= index <= len(found):
            self.say(chat_id, "There is no terminal session %d.\n\n%s"
                     % (index, self.terminal_listing(found)), self.claim_buttons(found))
            return "refused, no such terminal session"
        entry = found[index - 1]
        if entry.status != "idle":
            self.say(chat_id, "%s · %s is %s. Claim it when its turn is done — taking it now "
                              "would cut the turn off." % (entry.project, entry.name,
                                                           entry.status))
            return "refused, terminal session is %s" % entry.status

        sid = self.mint()
        name = "%s-%s" % (entry.project, sid[:4])
        self.pending.add(sid)
        try:
            pid = self.sessions.start(sid, name, entry.cwd, entry.project, chat_id,
                                      resume=entry.session_id)
        except Exception as e:
            self.pending.discard(sid)
            self.log("could not start a runner to claim pid %d: %s"
                     % (entry.pid, self.tg.redact(e)))
            self.say(chat_id, "Could not start a session: %s" % scrub(str(e), self.tg.redact))
            return "spawn failed"
        self.watch(chat_id, sid, entry.project, name, handover=entry)
        return "claiming pid %d as session %s, runner pid %d" % (entry.pid, sid, pid)

    def mint(self):
        """A session id that does not already name a directory. §4.1.

        Six hex is 16.7 million, so a collision is vanishingly unlikely — and its consequence
        is not, which is why the check is here: two runners writing one meta.json, and a link
        that belongs to the other session.
        """
        for _ in range(16):
            sid = self.newsid()
            if not os.path.exists(self.sessions.directory(sid)):
                return sid
        raise RuntimeError("could not mint an unused session id in 16 attempts")

    def begin(self, chat_id, intent):
        """Resolve, mint, fork, and hand the waiting to a thread. §4.1-§4.2.

        Returns a phrase for the log line, not a reply: the answer to this message is composed
        on another thread, by outcome(), once meta.json says something. §4.2 is the rule being
        kept — *the listener never blocks on a session* — and the visible consequence is that
        `claude beacon` is answered after anything sent behind it.
        """
        # §10.6: without a cap at all, a held-down `claude` fills RAM with Claude Code
        # processes. The union is the point — see self.pending.
        #
        # **Before the project is resolved, and before `new` creates anything.** A session that
        # cannot start is not a reason to leave an empty directory on the disk — and one this
        # bot made but never used is exactly the accident §5 gives `new` its own verb to
        # prevent, with the added insult that nothing here can delete it afterwards.
        if self.full(chat_id):
            return "refused, at the cap"

        making = intent.verb == commands.NEW
        try:
            if making:
                # §5: creating a project is its own verb, and the checks it runs are
                # `resolve()`'s own — 1 to 3 unchanged, 4 inverted (§12 slice 11). Nothing is
                # created unless all of them pass, so a refused `new` leaves nothing behind.
                cwd, created = config.create(intent.project, self.cfg.projects_root)
            else:
                cwd, created = config.resolve(intent.project, self.cfg.projects_root), None
        except config.ProjectError as e:
            # §3: any resolution failure is a help reply, and it names the rule that was broken
            # rather than the path that broke it.
            # With the keyboard, because this reply carries the project list and §5's answer
            # to a mistyped name is the list — `claude beacn` is the moment a button is most
            # use (§12 slice 13).
            self.say(chat_id, "%s\n\n%s" % (e, self.help()), self.buttons())
            return "refused, not a project"

        sid = self.mint()
        # §3's meta.json: sid 3f2a91 gives the name centrion-3f2a. The spelling is the one that
        # was sent, not the one on disk (§9.9) — it is a label in the Claude app, not a path.
        name = "%s-%s" % (intent.project, sid[:4])
        self.pending.add(sid)
        try:
            pid = self.sessions.start(sid, name, cwd, intent.project, chat_id, intent.prompt,
                                      trust=making)
        except Exception as e:
            self.pending.discard(sid)
            # The one failure with no transcript behind it: nothing was spawned, so nothing
            # will ever write a record, and if the reply is not composed here the phone simply
            # never hears back.
            self.log("could not start a runner for %s: %s"
                     % (loggable(intent.project), self.tg.redact(e)))
            self.say(chat_id, "Could not start a session: %s"
                     % scrub(str(e), self.tg.redact))
            return "spawn failed"

        self.watch(chat_id, sid, intent.project, name, created=created)
        return "session %s, runner pid %d" % (sid, pid)

    def watch(self, chat_id, sid, project, name, created=None, handover=None):
        """Wait for this session somewhere other than the poll loop. §4.2."""
        waiter = threading.Thread(target=self.waited, name="session-" + sid, daemon=True,
                                  args=(chat_id, sid, project, name, created, handover))
        # A thread per pending session is the design; a thread per message ever received would
        # be a leak that takes a week to show itself on a process that never exits.
        self.waiters = [w for w in self.waiters if w.is_alive()]
        self.waiters.append(waiter)
        waiter.start()

    def waited(self, chat_id, sid, project, name, created=None, handover=None):
        """One waiter, start to finish. Runs on its own thread and swallows everything."""
        try:
            record, state = self.wait(sid)
            # Claimed before the send, not after: §4's tick can be looking at this same
            # record right now, and at-most-once is the side to err on — the same side the
            # offset errs on, for the same reason. A deadline that expired claims *nothing*,
            # which is what leaves §9.12's late link for the tick to announce an hour later.
            #
            # **And the answer is acted on.** claim() is the arbitration between this thread
            # and the tick, so losing it means the tick has already told this chat about this
            # session and there is exactly one reply owed (§4.6). Until W4e this line claimed
            # and then sent regardless, which made the marker a record of who got there first
            # rather than a decision — and the phone got the same link twice, seconds apart,
            # whenever a getUpdates happened to return inside the 0.25s between two polls of
            # meta.json below.
            claimed = True
            if state == session.LIVE:
                claimed = self.sessions.claim(sid, LINK_SENT)
            elif state in (session.FAILED, session.ENDED):
                claimed = self.sessions.claim(sid, END_SENT)
            if not claimed:
                self.log("session %s: %s — the tick announced it first, so this waiter says "
                         "nothing (§4.6: one reply per session)" % (sid, state))
                return
            # §12 slice 14: the original ends only once the claim has a link — a claim that
            # never comes up must leave the terminal session as it found it.
            handed = None
            if state == session.LIVE and handover is not None:
                result = self.hand_over(handover)
                handed = HANDED.get(result, "%s") % handover.pid
                self.log("session %s: terminal session pid %d %s"
                         % (sid, handover.pid, result))
            sent = self.say(chat_id,
                            self.outcome(record, state, sid, project, name, created, handed))
            self.log("session %s: %s — %s" % (sid, state or "no link in %ds" % self.timeout,
                                              "replied" if sent else "reply NOT delivered"))
        except Exception as e:
            # A thread that dies takes its reply with it and nothing else, which is worse than
            # a crash rather than better: the phone hears nothing, the session is running, and
            # §14's log is the only place that could ever say so.
            self.log("session %s: waiting failed (%s: %s)"
                     % (sid, type(e).__name__, self.tg.redact(e)))
        finally:
            # Its record is on disk by now, or it never will be; either way the fleet is a
            # better count of this session than this set is.
            self.pending.discard(sid)

    def wait(self, sid):
        """Poll meta.json until it says something, or the deadline passes. §4.6.

        Returns `(record, state)`, where a state of None means the deadline won. That is not
        the same as failure: the listener stopped waiting, the session did not stop starting,
        and the reply has to be careful about the difference.
        """
        deadline = self.clock() + self.timeout
        while True:
            record = self.sessions.read(sid)
            state = record.get("state") if record else None
            if state == session.LIVE and record.get("url"):
                return record, session.LIVE
            if state in (session.FAILED, session.ENDED):
                return record, state
            if self.clock() >= deadline:
                return record, None
            self.sleep(self.poll_every)

    def outcome(self, record, state, sid, project, name, created=None, handed=None):
        """What the phone gets when a session has resolved one way or the other. §4.6, §5.

        `created` is None for `claude` and a bool for `new`. It is a line in the reply rather
        than a message of its own because §4.6 promises exactly one reply per session — and it
        is in *both* branches because a `new` whose session then failed has still left a
        directory behind, and the phone is the only place that will ever say so.
        """
        if state == session.LIVE:
            # §5: the link on a line of its own, because that is what Telegram will make
            # tappable. The third line is not decoration either — what has just started is a
            # session on this Mac that will not ask before it acts, and this is the only moment
            # that fact is in front of the person who caused it.
            fleet = self.fleet()
            lines = ["▶ %s · %s" % (project, name)]
            if created is not None:
                lines.append(self.made(record, project, created))
            if handed:
                lines.append(handed)
            # §5: allowed, and flagged. Refusing would be wrong — two sessions on one repo is
            # a normal way to work — and one line is the whole mitigation; anything more
            # belongs to git rather than to this bot.
            warning = self.crowded(record, sid)
            if warning:
                lines.append(warning)
            lines.append(record["url"])
            lines.append("bypass permissions on · %d of %d sessions"
                         % (len(fleet), self.cfg.max_sessions))
            return "\n".join(lines)

        if state == session.FAILED:
            head = "✗ %s · the session did not start." % project
        elif state == session.ENDED:
            head = "✗ %s · the session ended before a link appeared." % project
        else:
            # §9.3 and §9.7, the two that produce no error at all: a directory nobody has
            # opened by hand sits at the trust prompt, and an expired login sits at /login.
            head = ("… %s · no link after %ds (session %s). It may still come up; a new "
                    "directory's trust prompt and an expired /login both look like this."
                    % (project, self.timeout, sid))

        if created is not None:
            head = "%s\n%s" % (head, self.made(record, project, created))
        tail = self.tail(sid)
        return "%s\n\n%s" % (head, tail) if tail else \
               "%s\n\nThe transcript is empty." % head

    def made(self, record, project, created):
        """§5's one extra line for `new`: what it made, or what was already there.

        The path is tilde-collapsed like every other path that leaves this machine (§7), and it
        is here at all because `new` is the only verb that changes the filesystem — a reply
        that did not say so would leave the phone unable to tell a fresh directory from a
        session started in a repository full of somebody's work.
        """
        cwd = record.get("cwd") if isinstance(record, dict) else None
        where = tilde(cwd) if isinstance(cwd, str) and cwd else project
        return "📁 %s created" % where if created else "📁 %s was already there" % where

    def tail(self, sid, lines=TAIL_LINES):
        """The last few lines of that session's terminal, fit to leave this machine. §4.6.

        Split on `\r` as well as `\n`: a TUI redraws a line in place, so what the renderer
        wrote last is often separated from what came before it by a bare carriage return, and
        splitting on newlines alone returns one enormous line of overwritten text. Blank lines
        go for the same reason — fifteen lines of a cleared panel say nothing, and §4.6 is
        promising the error.
        """
        directory = self.sessions.directory(sid)
        raw = _last(os.path.join(directory, session.TRANSCRIPT), TAIL_BYTES)
        if len(raw) < TAIL_BYTES:
            # §10.7 rotates the transcript, and a session that rotated a moment before it died
            # leaves a live file holding the last half-second of a redraw. The error that
            # killed it is in the file that was renamed out of the way, and the tail is the
            # only place anyone will ever see it.
            raw = _last(os.path.join(directory, session.PREVIOUS), TAIL_BYTES - len(raw)) + raw
        kept = [line.rstrip() for line in re.split(r"[\r\n]+", scrub(raw, self.tg.redact))]
        return "\n".join([line for line in kept if line.strip()][-lines:])

    # -- the loop ----------------------------------------------------------------------------

    def handle(self, update):
        """One update, from the wire to a reply. Refusals return without sending anything."""
        message = update.get("message")
        if not isinstance(message, dict):
            # allowed_updates is a request and not a guarantee — telegram.py says so where it
            # advances the offset past everything in the batch, message or not.
            return

        chat_id = self.permitted(message)
        if chat_id is None:
            return
        if not self.fresh(message):
            self.log("dropping a message from chat %d dated %.40r: older than startup by "
                     "more than %ds (§7)" % (chat_id, message.get("date"), STALE_AFTER))
            return

        intent = commands.parse(message.get("text"))
        if intent.verb in (commands.START, commands.NEW) and intent.project is not None:
            done = self.begin(chat_id, intent)
        elif intent.verb == commands.RC and intent.target is not None:
            done = self.claim(chat_id, intent.target)
        else:
            done = "replied" if self.say(chat_id, self.answer(intent), self.menu(intent)) \
                   else "reply NOT delivered"
        # One line per handled message, and it is what makes the refusal lines above legible:
        # §14 sends you here when the phone gets nothing, and an empty log has to mean "nothing
        # arrived" rather than "answered, and the reply went missing". The verb and the project
        # are also the audit trail for which repository a session was started in — it is the
        # only local record of that. The prompt is deliberately not here: it is content, it is
        # unbounded, and Telegram already has it (§10).
        self.log("chat %d: %s%s — %s" % (chat_id, intent.verb,
                                         " " + loggable(intent.project) if intent.project else "",
                                         done))

    def say(self, chat_id, text, markup=None):
        """Every reply leaves through here, so §7's 4096 is enforced in exactly one place.

        The cap is on the text alone: the keyboard is a field of its own on the wire and a
        truncated project list must not arrive with the buttons missing (§12 slice 13).
        """
        return self.tg.send_message(chat_id, fit(text), markup)

    def remember(self):
        """Persist the offset. Losing it is survivable; exiting over it is not."""
        if self.tg.offset is None or self.tg.offset == self.saved:
            # An idle bot returns from getUpdates every 50 seconds forever, and rewriting an
            # unchanged number 1,700 times a day earns nothing.
            return
        try:
            write_offset(self.offset_path, self.tg.offset)
        except OSError as e:
            self.log("could not write the offset to %s (%s) — a restart would replay this batch"
                     % (self.offset_path, e.strerror or e))
            return
        self.saved = self.tg.offset

    # -- the sessions nobody is waiting for any more (§4) ------------------------------------

    def reconcile(self):
        """§4's reconciliation pass. Every return from getUpdates, message or not.

        The listener is otherwise purely reactive — it blocks in getUpdates and acts only on
        messages — so without this it never notices a session ending, a runner dying, or a
        link arriving after its waiter gave up. This box has already produced all three.

        It announces *arrivals* as well as departures, which §4 did not originally ask for and
        §9.12 does: two sessions reached `live` sixty-eight minutes after their waiters timed
        out, with working links in meta.json that nobody was ever told about. The delay is not
        the defect and no deadline covers it; the silence is, and this is the same walk.
        """
        try:
            records = self.sessions.records()
        except Exception as e:
            # Runs on every tick, so a bug in here is a bot that stops answering the phone
            # altogether. §7: the loop does not die.
            self.log("could not read the session records: %s" % self.tg.redact(e))
            return

        for record in records:
            try:
                self.settled(record)
            except Exception as e:
                # One strange session must not hide the rest, which is the same rule read_meta
                # follows one level down.
                self.log("could not reconcile session %s: %s"
                         % (record.get("sid"), self.tg.redact(e)))

        # After the loop above and not inside it, and that ordering is the whole of §10.7's
        # correctness: the marker that keeps an ending to exactly one announcement lives
        # *inside* the directory this is about to remove. Two days of downtime leaves records
        # that are terminal, old, and never announced — and sweeping first would delete the
        # evidence that the phone was owed a message before anything sent one.
        self.forget(records)

    def forget(self, records):
        """Remove the directories of sessions that finished a day ago. §10.7.

        The transcript inside each one is capped, so no single session is unbounded; the
        number of them is, and a phone makes another one per `claude`. Terminal, old, and
        demonstrably not running — all three, because the cost of being wrong here is not disk.
        """
        for record in records:
            sid = record.get("sid")
            try:
                if record.get("state") not in (session.ENDED, session.FAILED):
                    # `live` and `starting` are never removed, however old the record is. A
                    # session left open for a week is what this bot is for, and a runner that
                    # has not written since it came up is not an old session.
                    continue
                age = self.sessions.age(sid)
                if age is None or age < RETAIN:
                    continue
                # Belt and braces, and one kill(0) on a record that is about to be deleted
                # anyway: the runner writes its terminal state on its way out, so `ended` with
                # a pid that still answers should be impossible — and the directory being
                # written into is the last one to take away.
                if self.sessions.alive(record):
                    continue
                if self.sessions.discard(sid):
                    self.log("session %s: %s old, directory removed"
                             % (sid, uptime(time.time() - age)))
            except Exception as e:
                self.log("could not sweep session %s: %s" % (sid, self.tg.redact(e)))

    def settled(self, record):
        """One record, and whatever has become true about it since anyone last looked."""
        state = record.get("state")
        if state in (session.ENDED, session.FAILED):
            # Terminal already — the runner wrote it on its way out, or a previous listener
            # did. Nothing to mark; the announcement may still be owed.
            self.ended(record)
            return

        if self.sessions.alive(record):
            url = record.get("url")
            if state == session.LIVE and isinstance(url, str) and url:
                self.arrived(record)
            return

        # §4: `A live record whose process is gone — reboot, force quit, OOM — is stale.`
        # Without this the record accumulates against max_sessions until the bot refuses to
        # start anything and `ls` lists sessions that do not exist. A reboot alone does it,
        # and did: two of them were sitting in var/ this morning.
        self.ended(self.sessions.finish(record))

    def arrived(self, record):
        """§9.12: a session that reached `live` after its waiter had given up."""
        chat_id = record.get("chat_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        sid = record.get("sid")
        if not self.sessions.claim(sid, LINK_SENT):
            return
        age = uptime(record.get("started"))
        # It says how long it took because the phone is holding a message from forty-five
        # seconds in that says this session failed, and nothing else distinguishes the two.
        sent = self.say(chat_id, "▶ %s · %s came up after %s.\n%s\nbypass permissions on"
                        % (record.get("project"), record.get("name"), age, record["url"]))
        self.log("session %s: link arrived late after %s — %s"
                 % (sid, age, "announced" if sent else "announcement NOT delivered"))

    def ended(self, record):
        """§4's one unprompted message: this session is over.

        To the chat that started it, which `chat_id` is in the record for and which is not
        necessarily the chat that is messaging now. Sent at most once, and not retried if it
        does not land — the same side of that trade bot.py takes for the offset, because the
        alternative is an afternoon of network trouble arriving later as forty copies.
        """
        chat_id = record.get("chat_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            return
        sid = record.get("sid")
        if not self.sessions.claim(sid, END_SENT):
            return
        # `started ... ago` and not `ran for ...`: this pass is the only thing that notices a
        # session whose runner a reboot took, and it cannot know *when* it went — only that it
        # is gone now. Against the two records this box had on disk the difference was three
        # hours of runtime the bot would otherwise have claimed to have watched.
        sent = self.say(chat_id, "■ %s · %s ended (started %s ago).\n%s"
                        % (record.get("project"), record.get("name"),
                           uptime(record.get("started")), REATTACH))
        self.log("session %s: ended — %s"
                 % (sid, "announced" if sent else "announcement NOT delivered"))

    def tick(self):
        """One return from getUpdates, and everything that follows from it.

        §4 calls this the tick and means it literally: message or not, this is where the
        listener gets to act at a ≤50s cadence, and slice 8 hangs the reconciliation pass off
        the same return. The offset is written before the batch is dispatched — see the module
        docstring for why at-most-once is the side to err on.
        """
        updates = self.tg.poll()
        self.remember()
        # Every return from getUpdates is the tick, message or not — which makes this the
        # place for anything that has to happen at a ≤50s cadence whether or not the phone is
        # doing anything. Slice 8 hangs §4's reconciliation pass off the same line.
        self.sessions.reap()
        # Before the batch, not after. The first `claude` following a reboot is answered
        # against a fleet that reboot emptied, and reconciling afterwards would refuse it at a
        # cap held entirely by sessions that no longer exist.
        self.reconcile()
        for update in updates:
            try:
                self.handle(update)
            except Exception as e:
                # One malformed update must not cost the rest of the batch. Redacted because
                # anything reaching here was built from the wire (§7).
                self.log("could not handle update %r: %s"
                         % (update.get("update_id") if isinstance(update, dict) else None,
                            self.tg.redact(e)))

    def run(self):
        """Forever. §7: never exit."""
        self.tg.delete_webhook()
        self.tg.set_commands(COMMAND_MENU)
        while True:
            try:
                self.tick()
            except Exception as e:
                # telegram.py's poll() does not raise, so anything here is a bug in this file.
                # A bug that fires every tick would be a hot loop, hence the pause.
                self.log("tick failed (%s: %s) — continuing in %ds"
                         % (type(e).__name__, self.tg.redact(e), ERROR_PAUSE))
                self.sleep(ERROR_PAUSE)


def serve(cfg=None, tg=None):
    """Load, announce, and poll until launchd stops us.

    The lock that keeps this to one process lives in launchd/bot.sh, not here — §8 puts it
    there so the `lockf` holder is the shell that outlives an `exec`, and so a second copy is
    refused before it has spent a single getUpdates knocking the first one off the air (§7).
    """
    cfg = cfg or config.load()
    os.makedirs(VAR, exist_ok=True)
    # Before the first getUpdates, because a 409 is mutual (§7): the copy that loses the race
    # takes the working one down with it. On the Mac this is always True — bot.sh already
    # holds the lockf — and on Windows it is the mutex (WINDOWS.md §6).
    if not procs.Lock(LOCK).take():
        log("another centrion already holds %s — this copy exits rather than 409ing the one "
            "that is working (SPEC.md §7)" % tilde(LOCK))
        return 0
    tg = tg or telegram.Telegram(cfg.bot_token, log=log)
    listener = Listener(cfg, tg)
    log("centrion listening · %d allowed chat(s) · root %s · max_sessions %d · offset %s"
        % (len(cfg.allowed_chat_ids), tilde(cfg.projects_root), cfg.max_sessions,
           listener.saved))
    listener.run()
    return 0


def whoami(cfg=None, client=None):
    """Print the chat ids that have messaged the bot, and whether they are allowed in.

    The way `allowed_chat_ids` gets filled in, and §14 keeps it that way. Two things it checks
    that a bare id list would not:

    - **private vs group.** §10.2 requires `chat.type == "private"`, because anyone can add a
      bot to a group and a group id is not an identity. A group id pasted into the allowlist
      would be refused at runtime and look like the bot ignoring you.
    - **what is already configured.** A hand-typed id that is one digit out fails closed — the
      bot answers nobody, silently, exactly as §10.3 requires for a stranger. That is correct
      behaviour and indistinguishable from a broken bot, so it is worth saying out loud here.

    Non-destructive: getUpdates is called once without an offset and nothing is acknowledged,
    so a pending message is still there afterwards.
    """
    cfg = cfg or config.load()
    tg = client or telegram.Telegram(cfg.bot_token)

    # A webhook left set from any point in this bot's past makes getUpdates 409 forever (§7),
    # and that is exactly the failure this command would otherwise report as "no updates".
    tg.delete_webhook()

    seen = {}
    for u in tg.poll(timeout=0):
        chat = (u.get("message") or {}).get("chat") or {}
        if isinstance(chat.get("id"), int):
            who = " ".join(x for x in (chat.get("title"), chat.get("first_name"),
                                       chat.get("username") and "@" + chat["username"]) if x)
            seen[chat["id"]] = (chat.get("type") or "?", who or "?")

    if not seen:
        print("No updates. Message the bot once from the phone, then run this again.\n"
              "If it stays empty, the daemon may already be polling this token — stop it "
              "first, or the two will 409 each other (SPEC.md §7).", file=sys.stderr)

    for cid, (kind, who) in sorted(seen.items()):
        flags = []
        if cid in cfg.allowed_chat_ids:
            flags.append("already allowed")
        if kind != "private":
            flags.append("NOT private — §10.2 refuses this, do not add it")
        print("%-14d %-10s %-28s %s" % (cid, kind, who, "· ".join(flags)))

    extra = sorted(set(cfg.allowed_chat_ids) - set(seen))
    if extra:
        print("\nAllowlisted but not seen here: %s\nThat is fine if you have not messaged the "
              "bot from them today — but a mistyped id looks exactly like this, and the bot "
              "answers a mistyped id with silence (§10.3)."
              % ", ".join(str(i) for i in extra), file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(description="centrion — Claude Code sessions from Telegram.")
    ap.add_argument("--serve", action="store_true",
                    help="poll Telegram forever; what launchd/bot.sh runs")
    ap.add_argument("--whoami", action="store_true",
                    help="print the chat ids that have messaged the bot")
    a = ap.parse_args()
    try:
        if a.whoami:
            return whoami()
        if a.serve:
            return serve()
    except config.ConfigError as e:
        sys.exit("config: %s" % e)
    except telegram.TelegramError as e:
        sys.exit("telegram: %s" % e)
    # Deliberately not the default. A bare `python3 bot.py` that started polling would be a
    # second consumer on the token — a 409 for both this and the launchd copy (§7) — and the
    # lock that prevents it lives in launchd/bot.sh. Run that instead, by hand if you like: it
    # takes the lock first and says so when another copy already holds it.
    ap.error("nothing to do — run `sh launchd/bot.sh` to start the listener (it takes the "
             "single-instance lock first), or --whoami to find a chat id")


if __name__ == "__main__":
    sys.exit(main())
