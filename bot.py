#!/usr/bin/env python3
"""The listener: long-polls Telegram, decides who is allowed, and answers.

One process, started by launchd and restarted by it forever (SPEC.md §8). It owns three things
and nothing else: the allowlist, the poll offset, and the reply. Sessions belong to the runner,
which slice 6 writes and slice 7 wires in; the listener never blocks on one and never holds a
handle to one (§2).

**Slice 5 echoes.** Every verb is understood and none of them acts — `claude beacon` resolves
the directory and says so, and starts nothing. That is why §12 puts the launchd install here:
this is the last commit at which a wrong allowlist is harmless, so it is the right one to leave
running unattended overnight before anything can spawn a shell. `tests/test_bot.py` asserts the
harmlessness rather than promising it.

Three properties matter more than what it says back.

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

**It does not die.** §7: launchd would restart a crash, but a crash-loop against
ThrottleInterval is a worse failure mode than a patient retry. telegram.py already never raises
at the poll; everything above it here is wrapped too, per update and per tick, so one malformed
message cannot take the loop down with it.
"""
import argparse
import os
import sys
import time

import commands
import config
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

#: How much of a prompt the echo quotes back. Telegram caps an incoming message at 4096 and a
#: reply at the same 4096 (telegram.LIMIT), so quoting a maximal message verbatim and adding a
#: word to it is a guaranteed 400 — a silent non-reply at the moment the phone is waiting.
#: Slice 8 owns the real truncation policy for `ls` and error tails; this is just the echo
#: declining to read an essay back to its author.
PROMPT_ECHO = 200

#: Appended to every reply for as long as the listener is an echo. It goes away in slice 7,
#: with the spawn — until then the manual step in §12 has no other way to tell a working
#: listener from a working launcher.
NOT_YET = "Slice 5: the listener only echoes. Sessions arrive in slice 7 (SPEC.md §12)."

HOME = os.path.expanduser("~")


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


def clip(text, limit=PROMPT_ECHO):
    """`text`, short enough to quote back. See PROMPT_ECHO."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def loggable(text, limit=60):
    """User text on its way into var/bot.log, which is read on a terminal with `tail -f`.

    `repr` rather than the string itself: this arrives from the wire, and a project name is
    free to contain ANSI escapes, which would otherwise rewrite the display of the very log
    they appear in. Clipped because a 4096-character message is a legal message and a
    4096-character log line is not a useful one.
    """
    return repr(text if len(text) <= limit else text[:limit - 1] + "…")


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

    def __init__(self, cfg, tg, started=None, offset_path=OFFSET, log=log, sleep=time.sleep):
        self.cfg = cfg
        self.tg = tg
        self.offset_path = offset_path
        self.log = log
        self.sleep = sleep
        self.started = int(time.time()) if started is None else started

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
        """An intent → the text to send. The only place slice 5 differs from slice 7."""
        if intent.verb == commands.START:
            if intent.project is None:
                # §5: bare `claude` starts nothing. One extra tap, and in exchange no session
                # can ever begin in a repository that was not named. §15 records the decision.
                return self.project_list() + "\n\nSend `claude <project>` to start one there."
            try:
                path = config.resolve(intent.project, self.cfg.projects_root)
            except config.ProjectError as e:
                # §3: any resolution failure is a help reply, and the message names the rule
                # that was broken rather than the path that broke it.
                return "%s\n\n%s" % (e, self.help())
            reply = "would start: %s\n  in %s" % (intent.project, tilde(path))
            if intent.prompt:
                reply += "\n  prompt: %s" % clip(intent.prompt)
            return reply

        if intent.verb == commands.LIST:
            return "No live sessions."

        if intent.verb == commands.STOP:
            return "Nothing to stop — no sessions yet."

        return self.help()

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
        sent = self.tg.send_message(chat_id, "%s\n\n%s" % (self.answer(intent), NOT_YET))
        # One line per handled message, and it is what makes the refusal lines above legible:
        # §14 sends you here when the phone gets nothing, and an empty log has to mean "nothing
        # arrived" rather than "answered, and the reply went missing". The verb and the project
        # are also the audit trail for which repository a session was started in — from slice 7
        # that is the only local record of it. The prompt is deliberately not here: it is
        # content, it is unbounded, and Telegram already has it (§10).
        self.log("chat %d: %s%s — %s" % (chat_id, intent.verb,
                                         " " + loggable(intent.project) if intent.project else "",
                                         "replied" if sent else "reply NOT delivered"))

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
