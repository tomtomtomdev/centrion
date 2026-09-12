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

Two gaps this commit leaves, both slice 8's: nothing yet enforces `max_sessions` (§10.6), and a
listener killed between a spawn and its reply leaves a session running that nobody has been
told about — §4's reconciliation pass is what finds it again, and `ls` is what asks.
"""
import argparse
import os
import re
import subprocess
import sys
import threading
import time

import commands
import config
import session
import telegram

HERE = os.path.dirname(os.path.abspath(__file__))
VAR = os.path.join(HERE, "var")
OFFSET = os.path.join(VAR, "offset")

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

#: §4.6: how much of pty.log goes back when there is no link to send instead. It is almost
#: always the actual error — §9.7's expired login most of all, which produces no other
#: evidence anywhere.
TAIL_LINES = 15

#: How much of the transcript is read to find those lines. A 200-column terminal redrawing
#: itself fills this within a few seconds of startup, which is the whole of what a tail is for.
TAIL_BYTES = 65536

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

    def start(self, sid, name, cwd, project, chat_id, prompt=None):
        """Fork a runner for this session and return its pid. Never waits for it (§4.2).

        `cwd` has already been through `config.resolve()` and goes through it again inside the
        runner (§3): the listener resolves, the runner re-checks, and the string that came off
        the wire is never a working directory.
        """
        argv = [self.python, self.script,
                "--sid", sid, "--name", name, "--cwd", cwd, "--project", project,
                "--chat-id", str(chat_id), "--root", self.root]
        if prompt:
            argv.extend(["--prompt", prompt])

        os.makedirs(self.root, exist_ok=True)
        # A list and no shell, which is the whole of the defence here: `project` and `prompt`
        # arrived from a phone, and there are no quoting rules to get wrong when there is
        # nothing to quote for (§10). stdin is /dev/null because the runner has a terminal of
        # its own for the session and no use for launchd's; stdout and stderr are inherited, so
        # the runner's log lines land in var/bot.log beside the listener's (§14).
        with open(os.devnull, "rb") as devnull:
            child = subprocess.Popen(argv, stdin=devnull, cwd=HERE, close_fds=True)
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
                 timeout=SESSION_TIMEOUT, poll_every=SESSION_POLL):
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
        # One thread per session still coming up. Pruned as they finish, in watch().
        self.waiters = []

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
        return "Projects in %s:\n%s" % (root, "\n".join("  " + n for n in names))

    def help(self):
        """§5's two tables, and the directories currently in the root."""
        return (
            "centrion — Claude Code sessions on this Mac, from here.\n"
            "\n"
            "claude                   the projects below, and nothing else\n"
            "claude <project>         a session there\n"
            "claude <project> <text>  a session there, then type that\n"
            "ls                       the live sessions\n"
            "stop <n> · stop all      end one, or all of them\n"
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
            return "No live sessions."

        if intent.verb == commands.STOP:
            return "Nothing to stop — no sessions yet."

        return self.help()

    # -- starting one ----------------------------------------------------------------------

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
        try:
            cwd = config.resolve(intent.project, self.cfg.projects_root)
        except config.ProjectError as e:
            # §3: any resolution failure is a help reply, and it names the rule that was broken
            # rather than the path that broke it.
            self.say(chat_id, "%s\n\n%s" % (e, self.help()))
            return "refused, not a project"

        sid = self.mint()
        # §3's meta.json: sid 3f2a91 gives the name centrion-3f2a. The spelling is the one that
        # was sent, not the one on disk (§9.9) — it is a label in the Claude app, not a path.
        name = "%s-%s" % (intent.project, sid[:4])
        try:
            pid = self.sessions.start(sid, name, cwd, intent.project, chat_id, intent.prompt)
        except Exception as e:
            # The one failure with no transcript behind it: nothing was spawned, so nothing
            # will ever write a record, and if the reply is not composed here the phone simply
            # never hears back.
            self.log("could not start a runner for %s: %s"
                     % (loggable(intent.project), self.tg.redact(e)))
            self.say(chat_id, "Could not start a session: %s"
                     % scrub(str(e), self.tg.redact))
            return "spawn failed"

        self.watch(chat_id, sid, intent.project, name)
        return "session %s, runner pid %d" % (sid, pid)

    def watch(self, chat_id, sid, project, name):
        """Wait for this session somewhere other than the poll loop. §4.2."""
        waiter = threading.Thread(target=self.waited, name="session-" + sid, daemon=True,
                                  args=(chat_id, sid, project, name))
        # A thread per pending session is the design; a thread per message ever received would
        # be a leak that takes a week to show itself on a process that never exits.
        self.waiters = [w for w in self.waiters if w.is_alive()]
        self.waiters.append(waiter)
        waiter.start()

    def waited(self, chat_id, sid, project, name):
        """One waiter, start to finish. Runs on its own thread and swallows everything."""
        try:
            record, state = self.wait(sid)
            sent = self.say(chat_id, self.outcome(record, state, sid, project, name))
            self.log("session %s: %s — %s" % (sid, state or "no link in %ds" % self.timeout,
                                              "replied" if sent else "reply NOT delivered"))
        except Exception as e:
            # A thread that dies takes its reply with it and nothing else, which is worse than
            # a crash rather than better: the phone hears nothing, the session is running, and
            # §14's log is the only place that could ever say so.
            self.log("session %s: waiting failed (%s: %s)"
                     % (sid, type(e).__name__, self.tg.redact(e)))

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

    def outcome(self, record, state, sid, project, name):
        """What the phone gets when a session has resolved one way or the other. §4.6, §5."""
        if state == session.LIVE:
            # §5: the link on a line of its own, because that is what Telegram will make
            # tappable. The third line is not decoration either — what has just started is a
            # session on this Mac that will not ask before it acts, and this is the only moment
            # that fact is in front of the person who caused it.
            return "▶ %s · %s\n%s\nbypass permissions on" % (project, name, record["url"])

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

        tail = self.tail(sid)
        return "%s\n\n%s" % (head, tail) if tail else \
               "%s\n\nThe transcript is empty." % head

    def tail(self, sid, lines=TAIL_LINES):
        """The last few lines of that session's terminal, fit to leave this machine. §4.6.

        Split on `\r` as well as `\n`: a TUI redraws a line in place, so what the renderer
        wrote last is often separated from what came before it by a bare carriage return, and
        splitting on newlines alone returns one enormous line of overwritten text. Blank lines
        go for the same reason — fifteen lines of a cleared panel say nothing, and §4.6 is
        promising the error.
        """
        path = os.path.join(self.sessions.directory(sid), "pty.log")
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - TAIL_BYTES))
                raw = fh.read()
        except OSError:
            return ""
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
        if intent.verb == commands.START and intent.project is not None:
            done = self.begin(chat_id, intent)
        else:
            done = "replied" if self.say(chat_id, self.answer(intent)) \
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

    def say(self, chat_id, text):
        """Every reply leaves through here, so §7's 4096 is enforced in exactly one place."""
        return self.tg.send_message(chat_id, fit(text))

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
