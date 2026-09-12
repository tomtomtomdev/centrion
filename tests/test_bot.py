#!/usr/bin/env python3
"""Slice 5 — the listener: it answers exactly one chat, forgets nothing, and never dies.

This is the first slice that runs unattended under launchd, so the tests are about the three
ways an unattended poll loop goes wrong rather than about what it says back.

**Who it answers.** SPEC.md §10 is blunt: this bot is a remote code execution endpoint for this
Mac with permissions bypassed, and the allowlist is the whole of the door. So the refusals get
more tests than the happy path — a stranger, a group, a sender who is not the chat, a chat with
no sender at all, and the `True == 1` hole that a JSON `"id": true` would otherwise walk
straight through. Every one of them gets *silence*, because §10.3 says a reply confirms the bot
exists and is worth attacking.

**What it forgets.** §7: the offset is persisted, or a restart replays the batch — which in
slice 7 means starting the same session twice. And the 24h backlog is real, so a message that
predates startup by more than two minutes is a message from a weekend of downtime and must not
be honoured on Monday.

**Whether it dies.** §7 again: launchd would restart a crash, but a crash-loop against
ThrottleInterval is a worse failure mode than a patient retry. A failed send, a raising send, a
malformed update and an outright bug in a tick all have to leave the loop running.

Slice 5 replied with an echo and started nothing, which was deliberate (§12) — the launchd
install lands there precisely so that the first thing to run unattended is harmless if the
allowlist is wrong.

**Slice 7 is where that stops being true**, and the tests it adds are about the two properties
the wiring has to have. The first is §4.6's: one reply per session, carrying the link when it
comes up and the tail of the transcript when it does not — the tail because §9.7's expired
login is the likeliest failure after week one and it produces no error anywhere else. The
second is §4.2's, and it is the one with teeth: **the listener never blocks on a session.**
Forty-five seconds of polling meta.json inside the poll loop would be forty-five seconds of a
deaf bot, so the waiting happens on its own thread and `TestTheLoopIsNeverBlocked` holds a
session still with an Event while a second message is answered around it.

What slice 5 asserted as "nothing dangerous is reachable yet" therefore changes shape rather
than disappearing: see `TestWhatTheListenerMayReach`.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import ast
import errno
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import bot
import commands
import config
import session
import telegram

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Shaped like the real thing, and asserted never to escape — same fixture as test_telegram.py.
TOKEN = "8960211893:AAHreallyNotTheRealTokenJustAFake_x"
SECRET = TOKEN.split(":")[1]

ME = 1908330607          # the one allowlisted chat
STRANGER = 5550001234    # someone who found the bot
GROUP = -1001234567890   # a supergroup someone added it to

START = 1789198400       # the daemon's start time in every test, so staleness is arithmetic

#: What a session comes up with. ULID-shaped and not a UUID (§9.5); the same link
#: test_session.py reads out of the captured transcript, so the two files agree on the shape.
LINK = "https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF"


def message(text="claude", chat=ME, sender=None, kind="private", date=START, uid=7):
    m = {"message_id": uid, "date": date, "text": text,
         "chat": {"id": chat, "type": kind},
         "from": {"id": ME if sender is None else sender}}
    if text is None:
        del m["text"]
    return {"update_id": uid, "message": m}


class Exhausted(BaseException):
    """The fake's way of ending `run()`.

    Deliberately a BaseException and not an Exception: `run()` swallows Exception forever by
    design, so a test that wants the loop to stop must throw something the loop is *not*
    allowed to catch. That it works is itself the assertion that run() catches Exception and
    not everything.
    """


class FakeTelegram:
    """The four methods bot.py calls, and nothing else — mocked at the object boundary.

    `poll()` advances its own offset the way the real client does (telegram.py sets it on
    return, before the caller sees the batch), because the offset file is written from it.
    """

    def __init__(self, *batches):
        self.batches = list(batches)
        self.offset = None
        self.sent = []
        self.results = []          # per-send: True, False, or an exception to raise
        self.polls = 0
        self.webhooks_deleted = 0

    def poll(self, timeout=None):
        self.polls += 1
        if not self.batches:
            raise Exhausted()
        batch = self.batches.pop(0)
        if isinstance(batch, BaseException):
            raise batch
        ids = [u["update_id"] for u in batch if isinstance(u.get("update_id"), int)]
        if ids:
            self.offset = max(ids) + 1
        return batch

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        result = self.results.pop(0) if self.results else True
        if isinstance(result, BaseException):
            raise result
        return result

    def delete_webhook(self):
        self.webhooks_deleted += 1
        return True

    def redact(self, text):
        # Part of the boundary, not a convenience: the listener logs exceptions built from the
        # wire and reaches through the client to scrub them, because the client is the object
        # that holds the token. telegram.Telegram.redact is the real one.
        return str(text).replace(TOKEN, "<token>").replace(SECRET, "<token>")

    @property
    def texts(self):
        return [t for _, t in self.sent]


class Clock:
    """A hand-wound clock, so §4.6's forty-five seconds can be tested in no time at all.

    Sleeping advances it, which is the only property that matters: it makes the wait loop's
    deadline arithmetic — how long it waits, and how often it looks — visible to an assertion
    instead of to a stopwatch.
    """

    def __init__(self, now=0.0):
        self.now = now
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSessions(bot.Sessions):
    """The runner without the fork — and *only* without the fork.

    `start()` records what it was asked for and writes the same meta.json the real runner would
    have written at that point in its life; everything the listener does next is the real code
    reading a real file off disk, because §2's whole claim is that these two processes talk
    through files and nothing else. What is faked is the one thing a unit test must never do,
    which is start a Claude Code session with permissions bypassed.

    `gate` is how a session is held still. A waiter blocked on it is a session that has not
    come up yet, which is the ordinary case for the ten to twenty seconds §4 budgets — and it
    is what lets `TestTheLoopIsNeverBlocked` assert on the poll loop while one is pending.
    """

    def __init__(self, root, outcome=session.LIVE, url=LINK, transcript=None):
        bot.Sessions.__init__(self, root=root, log=lambda m: None)
        self.outcome = outcome
        self.url = url
        self.transcript = transcript
        self.started = []
        self.fail = None          # raised by start() instead of spawning
        self.boom = None          # raised by read(), to break a waiter mid-wait
        self.gate = None          # an Event every read() waits on first
        self.reads = 0

    def start(self, sid, name, cwd, project, chat_id, prompt=None):
        self.started.append({"sid": sid, "name": name, "cwd": cwd, "project": project,
                             "chat_id": chat_id, "prompt": prompt})
        if self.fail is not None:
            raise self.fail
        self.write(sid, self.outcome)
        if self.transcript is not None:
            self.transcribe(sid, self.transcript)
        return 44213

    def write(self, sid, state, url=None):
        """The record the runner writes. §3's shape, down to the keys the listener reads."""
        if state is None:
            return
        os.makedirs(self.directory(sid), exist_ok=True)
        session.write_meta(self.directory(sid), {
            "sid": sid, "state": state, "project": "beacon",
            "cwd": "/Users/nobody/Projects/beacon", "name": "beacon-" + sid[:4],
            "runner_pid": os.getpid(), "claude_pid": 44215, "started": START, "chat_id": ME,
            "url": url or (self.url if state == session.LIVE else None)})

    def transcribe(self, sid, data):
        os.makedirs(self.directory(sid), exist_ok=True)
        with open(os.path.join(self.directory(sid), "pty.log"), "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))

    def read(self, sid):
        self.reads += 1
        if self.gate is not None:
            self.gate.wait(30)
        if self.boom is not None:
            raise self.boom
        return bot.Sessions.read(self, sid)


class Base(unittest.TestCase):
    """A listener over a temporary projects root and a temporary var/, with a fake Telegram."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.projects = os.path.join(self.tmp, "Projects")
        for name in ("beacon", "centrion", "stock-watch-project"):
            os.makedirs(os.path.join(self.projects, name))
        self.offset_path = os.path.join(self.tmp, "var", "offset")
        os.makedirs(os.path.dirname(self.offset_path))
        self.sessions = FakeSessions(os.path.join(self.tmp, "var", "sessions"))

        self.cfg = config.Config(TOKEN, frozenset([ME]), self.projects, "/bin/echo", 2)
        self.tg = FakeTelegram()
        self.logged = []
        self.slept = []

    def listener(self, tg=None, **kw):
        kw.setdefault("started", START)
        kw.setdefault("sessions", self.sessions)
        kw.setdefault("sleep", self.slept.append)
        return bot.Listener(self.cfg, tg or self.tg, offset_path=self.offset_path,
                            log=self.logged.append, **kw)

    def deliver(self, *updates):
        """Hand `updates` to a listener as one batch and return what it replied.

        `settle()` and not just `tick()`, because from slice 7 a session's reply comes from its
        own thread: §4.2 has the listener returning to the poll the moment the runner is
        spawned, so a tick returning is no longer the end of what a message causes.
        """
        tg = FakeTelegram(list(updates))
        listener = self.listener(tg)
        listener.tick()
        self.settle(listener)
        return tg

    def settle(self, listener, timeout=30):
        """Wait for every session waiter to finish. A thread still running is a failed test."""
        for waiter in list(listener.waiters):
            waiter.join(timeout)
            self.assertFalse(waiter.is_alive(), "a session waiter never finished")

    def assertSilent(self, update, because):
        tg = self.deliver(update)
        self.assertEqual(tg.sent, [], because)
        self.assertTrue(self.logged, "a silent refusal must still leave a line in bot.log (§14)")


class TestOnlyTheAllowlistIsAnswered(Base):
    """SPEC.md §10.1-§10.3. Every refusal here is silent, and every one is logged."""

    def test_an_allowed_private_chat_is_answered(self):
        tg = self.deliver(message("help"))
        self.assertEqual(len(tg.sent), 1)
        self.assertEqual(tg.sent[0][0], ME)

    def test_a_stranger_gets_no_reply_at_all(self):
        # §10.3: a reply confirms the bot exists and is worth attacking. Log the id, send
        # nothing — not an error, not a "not authorised", nothing.
        self.assertSilent(message("claude beacon", chat=STRANGER, sender=STRANGER),
                          "a stranger must get silence, not a refusal")

    def test_a_group_chat_gets_no_reply_even_when_its_id_is_allowlisted(self):
        """§10.2 stands on its own, and this is the case that proves it is not redundant.

        An ordinary group already fails the allowlist — a group id is negative and nobody
        allowlists it. The rule exists for the id someone *pasted* into the allowlist after
        reading it off `--whoami`, which is why --whoami prints a warning beside a non-private
        chat. Anyone can add a bot to a group; a group id is not an identity.
        """
        self.cfg = config.Config(TOKEN, frozenset([ME, GROUP]), self.projects, "/bin/echo", 2)
        self.assertSilent(message("claude beacon", chat=GROUP, sender=ME, kind="supergroup"),
                          "a group must get silence even with its id in the allowlist")

    def test_an_ordinary_group_is_refused_too(self):
        self.assertSilent(message("claude beacon", chat=GROUP, sender=ME, kind="group"),
                          "a group the bot was added to must get silence")

    def test_an_allowed_chat_with_an_unknown_sender_is_refused(self):
        # Both ids are checked (§10.1). In a private chat they are the same number; when they
        # are not, something has been forwarded, impersonated, or sent by a bot on someone's
        # behalf, and none of those is the owner typing.
        self.assertSilent(message("claude beacon", chat=ME, sender=STRANGER),
                          "the sender must be allowlisted too, not just the chat")

    def test_a_message_with_no_sender_is_refused(self):
        u = message("claude beacon")
        del u["message"]["from"]
        self.assertSilent(u, "a channel post has no `from` and is not a person")

    def test_a_boolean_id_does_not_pass_as_an_allowlisted_one(self):
        """`True == 1` and `True in frozenset({1})` is True, in Python and therefore here.

        JSON's `true` parses to a bool, and bool is a subclass of int, so a crafted update
        carrying `"id": true` would match an allowlist that happens to contain 1. config.py
        already refuses to *build* such an allowlist with `type(i) is not int`; this is the
        same check on the other side of the wire.
        """
        self.cfg = config.Config(TOKEN, frozenset([1]), self.projects, "/bin/echo", 2)
        self.assertSilent(message("claude beacon", chat=True, sender=True),
                          "a boolean id must not match an integer allowlist entry")

    def test_a_missing_or_unusable_id_is_refused(self):
        for chat in (None, "1908330607", 1.0, []):
            u = message("claude beacon")
            u["message"]["chat"]["id"] = chat
            self.logged = []
            self.assertSilent(u, "chat id %r must not be usable" % (chat,))

    def test_an_update_that_is_not_a_message_is_ignored(self):
        # allowed_updates is a request, not a guarantee (telegram.py says so where it advances
        # the offset), so something else can arrive. It must not raise.
        tg = self.deliver({"update_id": 1}, {"update_id": 2, "message": None},
                          {"update_id": 3, "message": []}, {"update_id": 4})
        self.assertEqual(tg.sent, [])

    def test_an_answered_message_is_logged_too(self):
        """Not symmetry for its own sake — it is what makes the refusal log usable.

        §14 sends you to var/bot.log when the phone gets no reply. If only refusals are
        recorded, an empty log is ambiguous between "nothing ever arrived" (a 409, a webhook, a
        sleeping Mac) and "it was answered and Telegram dropped the reply" — two problems with
        nothing in common. One line per handled message separates them. A human typing on a
        phone is not a volume worth economising against.
        """
        self.deliver(message("claude beacon"))
        self.assertTrue(any("beacon" in line for line in self.logged), self.logged)

    def test_a_reply_that_did_not_land_says_so(self):
        tg = FakeTelegram([message("help")])
        tg.results = [False]
        self.listener(tg).tick()
        self.assertTrue(any("NOT delivered" in line for line in self.logged), self.logged)

    def test_the_log_cannot_be_rewritten_by_a_message(self):
        """var/bot.log is read with `tail -f` on a terminal (§14), and a project name is wire
        content. A raw escape sequence in one would repaint the log around it — including,
        with enough cursor movement, the refusal lines above it."""
        self.deliver(message("claude \x1b[2J\x1b[1;1Hnothing-to-see-here"))
        for line in self.logged:
            self.assertNotIn("\x1b", line)

    def test_a_huge_project_name_does_not_become_a_huge_log_line(self):
        self.deliver(message("claude " + "x" * 4000))
        for line in self.logged:
            self.assertLess(len(line), 300, line)

    def test_the_log_does_not_quote_the_prompt(self):
        # The verb and the project are the audit trail — which repository a session was started
        # in is the thing worth keeping. The prompt is content, it is arbitrarily long, and
        # bot.log is not where it earns its keep.
        self.deliver(message("claude beacon rm -rf something quite private"))
        self.assertFalse(any("quite private" in line for line in self.logged), self.logged)

    def test_the_refusal_is_logged_with_the_id(self):
        # §14: bot.log is the first thing to look at when the phone gets no reply, and a silent
        # drop there is §10.3 working as designed rather than a bug. It has to be legible.
        self.deliver(message("claude beacon", chat=STRANGER, sender=STRANGER))
        self.assertTrue(any(str(STRANGER) in line for line in self.logged), self.logged)


class TestStaleMessagesAreDropped(Base):
    """SPEC.md §7: Telegram holds undelivered updates for 24 hours."""

    def test_a_stale_message_does_not_start_a_session(self):
        """The weekend-of-downtime case, and the reason the guard is belt as well as braces.

        Come back from two days off with var/offset lost — a disk full, a var/ wiped by hand,
        a first run after a reinstall — and a naive loop replays every `claude` sent in the
        meantime, all at once, each one a bypass-permissions session in a repo whose state has
        moved on. The offset file is the belt; this is the braces, and it is the one that
        still works when the file is the thing that went wrong.
        """
        self.assertSilent(message("claude beacon", date=START - bot.STALE_AFTER - 1),
                          "a message older than startup by more than STALE_AFTER must be dropped")

    def test_a_message_from_just_before_startup_is_still_answered(self):
        # The window exists because the ordinary case is real: message the bot, and it is
        # restarted by launchd a few seconds later before it ever saw the message.
        tg = self.deliver(message("help", date=START - bot.STALE_AFTER + 1))
        self.assertEqual(len(tg.sent), 1)

    def test_a_message_sent_after_startup_is_answered(self):
        tg = self.deliver(message("help", date=START + 3600))
        self.assertEqual(len(tg.sent), 1)

    def test_the_guard_is_one_sided(self):
        # A phone's clock can run ahead of this Mac's. That is not a stale message and must not
        # be treated as one; only the past is suspicious.
        tg = self.deliver(message("help", date=START + 86400))
        self.assertEqual(len(tg.sent), 1)

    def test_a_message_with_no_usable_date_is_dropped(self):
        # Telegram always sends `date`. Without one, freshness cannot be established, and the
        # whole point of this guard is that it fails closed rather than guessing.
        for date in (None, "1789198400", 1789198400.5):
            u = message("claude beacon")
            u["message"]["date"] = date
            self.logged = []
            self.assertSilent(u, "date %r must not be usable" % (date,))

    def test_the_window_is_the_two_minutes_the_spec_names(self):
        self.assertEqual(bot.STALE_AFTER, 120)


class TestTheOffsetSurvivesARestart(Base):
    """SPEC.md §7: without it, a restart replays the batch and starts duplicate sessions."""

    def test_the_offset_is_written_after_a_batch(self):
        self.deliver(message("help", uid=41))
        with open(self.offset_path) as fh:
            self.assertEqual(fh.read().strip(), "42")

    def test_a_restart_resumes_from_the_file(self):
        self.deliver(message("help", uid=41))
        fresh = FakeTelegram()
        bot.Listener(self.cfg, fresh, offset_path=self.offset_path, log=self.logged.append)
        self.assertEqual(fresh.offset, 42, "a new listener must pick the offset back up")

    def test_the_offset_is_saved_before_the_batch_is_dispatched(self):
        """At-most-once, not at-least-once, and the asymmetry is the whole point.

        A message lost to a crash mid-batch costs one tap on a phone. A message replayed after
        a crash mid-batch costs a second bypass-permissions session in a repository that
        already has one, started without anybody asking for it (§7). So the file is written
        from the batch the moment it is in hand, before a single update is acted on.
        """
        saved = []
        tg = FakeTelegram([message("help", uid=41)])

        def watching(chat_id, text):
            with open(self.offset_path) as fh:
                saved.append(fh.read().strip())
            return True

        tg.send_message = watching
        self.listener(tg).tick()
        self.assertEqual(saved, ["42"], "the offset must already be on disk when a reply is sent")

    def test_an_empty_batch_does_not_rewrite_the_file(self):
        # An idle bot returns from getUpdates every 50 seconds forever. Rewriting an unchanged
        # offset 1,700 times a day is a pointless write amplification on an SSD.
        self.deliver(message("help", uid=41))
        before = os.stat(self.offset_path).st_mtime_ns
        tg = FakeTelegram([], [])
        listener = self.listener(tg)
        listener.tick()
        listener.tick()
        self.assertEqual(os.stat(self.offset_path).st_mtime_ns, before)

    def test_a_missing_offset_file_is_not_an_error(self):
        # The first run ever, and the case §7's staleness guard exists to make survivable.
        self.assertIsNone(bot.read_offset(os.path.join(self.tmp, "nope")))

    def test_a_corrupt_offset_file_does_not_stop_the_bot(self):
        for junk in ("", "   ", "banana", "12.5", "-1", "\x00\x01", "9" * 400):
            with open(self.offset_path, "w") as fh:
                fh.write(junk)
            self.assertIsNone(bot.read_offset(self.offset_path), "offset %r" % junk)

    def test_the_offset_is_written_atomically(self):
        # A listener killed by launchd mid-write must not leave a half-written number that
        # reads back as a plausible offset. Temp file plus os.replace, the same discipline
        # meta.json gets in slice 6.
        bot.write_offset(self.offset_path, 12345)
        self.assertEqual(bot.read_offset(self.offset_path), 12345)
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.offset_path))], ["offset"])

    def test_an_unwritable_offset_path_does_not_kill_the_loop(self):
        # A full disk, or a var/ owned by someone else. Losing the offset is survivable; a
        # daemon that exits over it is not.
        listener = self.listener(FakeTelegram([message("help", uid=41)]))
        listener.offset_path = os.path.join(self.tmp, "no-such-dir", "offset")
        listener.tick()
        self.assertTrue(any("offset" in line for line in self.logged), self.logged)


class TestTheLoopSurvives(Base):
    """SPEC.md §7: never exit. A crash-loop against ThrottleInterval is the worse failure."""

    def test_a_send_failure_does_not_kill_the_loop(self):
        tg = FakeTelegram([message("help", uid=1)], [message("help", uid=2)])
        tg.results = [False]
        listener = self.listener(tg)
        listener.tick()
        listener.tick()
        self.assertEqual(len(tg.sent), 2, "the second message must still be answered")

    def test_a_send_that_raises_does_not_stop_the_next_message(self):
        # telegram.py's send_message returns False rather than raising, so this is defence
        # against a bug in it — the listener must not be the thing that turns one into an
        # outage.
        tg = FakeTelegram([message("help", uid=1), message("help", uid=2)])
        tg.results = [RuntimeError("boom")]
        self.listener(tg).tick()
        self.assertEqual(len(tg.sent), 2)

    def test_a_malformed_update_does_not_stop_the_ones_behind_it(self):
        tg = FakeTelegram([{"update_id": 1, "message": {"chat": "not a dict"}},
                           message("help", uid=2)])
        self.listener(tg).tick()
        self.assertEqual(len(tg.sent), 1, "the well-formed message behind it must be answered")

    def test_an_unexpected_error_in_a_tick_is_logged_and_the_loop_continues(self):
        tg = FakeTelegram(RuntimeError("bug"), [message("help", uid=1)])
        with self.assertRaises(Exhausted):
            self.listener(tg).run()
        self.assertEqual(len(tg.sent), 1, "the tick after the bug must still run")
        self.assertEqual(self.slept, [bot.ERROR_PAUSE],
                         "a failing tick must pause, or a persistent bug becomes a hot loop")

    def test_run_deletes_the_webhook_once_at_startup(self):
        # §7: a webhook set at any point in this bot's past makes getUpdates 409 forever.
        tg = FakeTelegram([], [], [])
        with self.assertRaises(Exhausted):
            self.listener(tg).run()
        self.assertEqual(tg.webhooks_deleted, 1)

    def test_nothing_a_message_can_contain_makes_a_tick_raise(self):
        hostile = ["claude " + "x" * 4000, "claude beacon \x00\x1b[31m", "stop ²", None,
                   "🧑‍💻", "/" * 3000, "claude ../../etc", "claude /etc passwd",
                   "claude " + "‮" + "beacon"]
        for text in hostile:
            tg = FakeTelegram([message(text)])
            listener = self.listener(tg)
            listener.tick()
            self.settle(listener)
            self.assertEqual(len(tg.sent), 1, "no reply to %.30r" % (text,))


class TestWhatItSaysBack(Base):
    """SPEC.md §5: what each verb answers with. The starting verb has its own classes below."""

    def reply_to(self, text):
        tg = self.deliver(message(text))
        self.assertEqual(len(tg.sent), 1, "no reply to %r" % (text,))
        return tg.texts[0]

    def test_bare_claude_lists_the_projects_and_starts_nothing(self):
        # §5: the one extra tap that buys never starting a session in a repository you did not
        # name. §15 records the decision and what would reopen it.
        got = self.reply_to("claude")
        for name in ("beacon", "centrion", "stock-watch-project"):
            self.assertIn(name, got)

    def test_help_lists_the_verbs_and_the_projects(self):
        got = self.reply_to("help")
        for verb in ("claude", "ls", "stop", "help"):
            self.assertIn(verb, got)
        self.assertIn("beacon", got)

    def test_a_project_that_resolves_is_named_back(self):
        got = self.reply_to("claude beacon fix the failing probe test")
        self.assertIn("beacon", got)

    def test_a_project_that_does_not_resolve_gets_help(self):
        # §3: any resolution failure is a help reply.
        got = self.reply_to("claude nosuchproject")
        self.assertIn("beacon", got, "the help reply carries the project list")

    def test_an_escape_from_the_root_is_refused_and_says_nothing_about_paths(self):
        """§10.4 and §3: no message can express a directory outside the root, and the reply
        must not turn the bot into a filesystem oracle for whoever is reading over a shoulder.
        Telegram sees every message either way (§10)."""
        for text in ("claude ../../etc", "claude /etc", "claude ..", "claude .ssh",
                     "claude beacon/../.."):
            got = self.reply_to(text)
            self.assertNotIn("/etc", got)
            self.assertNotIn("/Users", got, "a reply must never carry a home path (§7)")

    def test_the_verbs_that_need_a_session_say_there_are_none(self):
        for text in ("ls", "stop 1", "stop all"):
            self.assertTrue(self.reply_to(text).strip())

    def test_garbage_gets_help(self):
        for text in ("hello", "/start", "👋", "", "stop"):
            self.assertIn("beacon", self.reply_to(text), "%r should get help" % text)

    def test_a_reply_always_fits_in_one_telegram_message(self):
        # §7: 4096 is the cap, and a send over it is a 400 — a silent non-reply at exactly the
        # moment the phone is waiting. Telegram caps an incoming message at 4096 too, so the
        # longest prompt anyone can send is the case to check.
        for text in ("claude beacon " + "x" * 4096, "claude " + "y" * 4096, "z" * 4096):
            self.assertLessEqual(len(self.reply_to(text)), telegram.LIMIT)

    def test_no_reply_carries_the_bot_token(self):
        for text in ("claude", "help", "claude beacon", "nonsense", TOKEN, "claude " + TOKEN):
            got = self.reply_to(text)
            self.assertNotIn(SECRET, got)

    def test_a_reply_never_carries_this_machines_home_path(self):
        # §7: collapse /Users/tomtomtomtom to ~. Telegram is not end-to-end encrypted (§10).
        for text in ("claude", "help", "claude beacon"):
            self.assertNotIn(os.path.expanduser("~"), self.reply_to(text))


class TestTheProjectList(Base):
    """`config.projects()` — the inverse of `resolve()`, and it must agree with it.

    It lives in config.py rather than in the listener because it shares the rules in §3 with
    resolve(), and §3 is emphatic that those rules live in one place. Tested here because it is
    slice 5 that first needs it: it is the reply to bare `claude`.
    """

    def test_it_lists_the_directories_in_the_root(self):
        self.assertEqual(config.projects(self.projects),
                         ["beacon", "centrion", "stock-watch-project"])

    def test_it_offers_only_names_that_resolve(self):
        """The invariant that makes the list trustworthy rather than decorative.

        A name in the reply that `claude <name>` then refuses is a bug the owner cannot
        diagnose from a phone. So the listing is filtered by resolve() itself rather than by a
        second copy of its rules that can drift.
        """
        os.symlink("/etc", os.path.join(self.projects, "escape"))
        open(os.path.join(self.projects, "README.md"), "w").close()
        os.makedirs(os.path.join(self.projects, ".hidden"))
        for name in config.projects(self.projects):
            config.resolve(name, self.projects)   # raises ProjectError if the list lies

    def test_it_skips_files_dotfiles_and_symlinks_out_of_the_root(self):
        os.symlink("/etc", os.path.join(self.projects, "escape"))
        open(os.path.join(self.projects, "README.md"), "w").close()
        os.makedirs(os.path.join(self.projects, ".hidden"))
        got = config.projects(self.projects)
        self.assertNotIn("escape", got)
        self.assertNotIn("README.md", got)
        self.assertNotIn(".hidden", got)

    def test_a_symlink_to_a_directory_inside_the_root_is_kept(self):
        # It resolves to a direct child of the root, so resolve() accepts it and so must this.
        os.symlink(os.path.join(self.projects, "beacon"),
                   os.path.join(self.projects, "beacon-alias"))
        self.assertIn("beacon-alias", config.projects(self.projects))

    def test_the_order_does_not_depend_on_case(self):
        # §9.9: this volume is case-insensitive. A list that puts `Lab` above `beacon` because
        # of ASCII ordering reads as a bug on a phone.
        os.makedirs(os.path.join(self.projects, "Lab"))
        self.assertEqual(config.projects(self.projects),
                         ["beacon", "centrion", "Lab", "stock-watch-project"])

    def test_an_unreadable_root_is_an_empty_list_and_not_a_crash(self):
        # §9.2: TCC. A launchd process that has lost its reach into the root must still answer
        # the phone, because the reply is the only way to find out that it has.
        self.assertEqual(config.projects(os.path.join(self.tmp, "gone")), [])


class TestWhatLeavesTheMachine(unittest.TestCase):
    """SPEC.md §7 and §10: Telegram sees every reply, and this box's home holds credentials."""

    def test_the_home_directory_is_collapsed_to_a_tilde(self):
        home = os.path.expanduser("~")
        self.assertEqual(bot.tilde(home), "~")
        self.assertEqual(bot.tilde(os.path.join(home, "Projects", "beacon")), "~/Projects/beacon")

    def test_a_path_outside_the_home_directory_is_left_alone(self):
        self.assertEqual(bot.tilde("/etc/hosts"), "/etc/hosts")

    def test_a_lookalike_sibling_of_home_is_not_collapsed(self):
        # /Users/tomtomtomtom-old must not become ~-old.
        self.assertEqual(bot.tilde(os.path.expanduser("~") + "-old"),
                         os.path.expanduser("~") + "-old")


class TestWhatTheListenerMayReach(Base):
    """§2 and §10: what the listener is now allowed to start, and what it still may not touch.

    Slice 5 had this class asserting that bot.py could not spawn at all — true at that commit,
    and the reason §12 puts the launchd install there. Slice 7 is where the capability arrives,
    so the claim changes shape rather than disappearing. The listener starts exactly one
    program, in exactly one directory, with no shell anywhere in the path.
    """

    def source(self):
        with open(os.path.join(ROOT, "bot.py")) as fh:
            return fh.read()

    def test_the_only_program_the_listener_starts_is_the_runner(self):
        self.assertEqual(os.path.basename(bot.Sessions(root=self.tmp).script), "session.py")
        self.assertTrue(os.path.exists(bot.Sessions(root=self.tmp).script))

    def test_it_runs_the_interpreter_that_is_running_this(self):
        # §3 pins /usr/bin/python3 for the daemon, and the runner must be the same one: a
        # second interpreter is a second set of stdlib behaviours to be surprised by (§13
        # slice 0 found one), and 3.9 is the version this code is written against.
        self.assertEqual(bot.Sessions(root=self.tmp).python, sys.executable)

    def test_it_never_reaches_for_a_shell(self):
        """§10: the prompt and the project name arrive from a phone and go into an argv.

        Through `subprocess` with a list that is the whole of the safety: no shell, so no
        quoting rules to get wrong and nothing a prompt containing `;` or a backtick can do.
        """
        for dangerous in ("shell=True", "os.system", "os.popen", "os.exec", "os.fork",
                          "pty.fork", "openpty"):
            self.assertNotIn(dangerous, self.source(), "bot.py reaches for %s" % dangerous)

    def test_the_listener_never_names_the_claude_binary(self):
        """§2's three processes, and the middle one is not ceremony.

        The listener spawns the runner and the runner spawns claude, so that a kickstart of
        the listener cannot reach a live session. A bot.py that knew `claude_bin` would be one
        refactor away from being the parent of a session it must never be the parent of.
        """
        self.assertNotIn("claude_bin", self.source())
        self.assertNotIn("--remote-control", self.source())

    def test_a_session_only_ever_starts_in_a_resolved_directory(self):
        # §3/§10.4: the listener resolves, the runner re-checks, and nothing in between ever
        # sees the string that came off the wire.
        self.deliver(message("claude beacon"))
        started = self.sessions.started[0]["cwd"]
        self.assertTrue(os.path.isabs(started))
        self.assertEqual(os.path.dirname(started), os.path.realpath(self.projects))


class TestASessionIsStarted(Base):
    """SPEC.md §4.1-§4.2: resolve, mint, spawn detached, and go straight back to polling."""

    def started(self, text="claude beacon"):
        self.deliver(message(text))
        self.assertEqual(len(self.sessions.started), 1, "expected one spawn for %r" % text)
        return self.sessions.started[0]

    def test_a_named_project_starts_a_runner_in_that_directory(self):
        self.assertTrue(os.path.samefile(self.started()["cwd"],
                                         os.path.join(self.projects, "beacon")))

    def test_the_session_is_named_for_the_project_and_the_sid(self):
        # §3's meta.json: sid `3f2a91` gives name `centrion-3f2a`. The short half is what
        # shows up in the Claude app's session list, where four hex is enough to tell two
        # sessions in one repo apart and six is just noise.
        started = self.started()
        self.assertEqual(len(started["sid"]), 6)
        self.assertEqual(started["name"], "beacon-" + started["sid"][:4])

    def test_the_sid_is_hex_and_not_guessable(self):
        # It names a directory under var/ and nothing more — it is not a credential (§10) —
        # but a counter would collide with whatever the last boot left behind.
        self.assertRegex(self.started()["sid"], r"^[0-9a-f]{6}$")

    def test_two_sessions_do_not_share_a_sid(self):
        self.deliver(message("claude beacon", uid=1), message("claude centrion", uid=2))
        sids = [s["sid"] for s in self.sessions.started]
        self.assertEqual(len(set(sids)), 2)

    def test_a_sid_that_is_already_taken_is_not_reused(self):
        """Vanishingly unlikely at six hex, and its consequence is not: two runners writing
        one meta.json, and a link that belongs to the other session."""
        os.makedirs(self.sessions.directory("3f2a91"))
        minted = iter(["3f2a91", "3f2a91", "aa11bb"])
        self.assertEqual(self.listener(newsid=lambda: next(minted)).mint(), "aa11bb")

    def test_the_prompt_reaches_the_runner_verbatim(self):
        # §5 and §9.9: a prompt is English, and it arrives spelled the way it was sent.
        started = self.started("claude beacon Fix the PROBE test, please")
        self.assertEqual(started["prompt"], "Fix the PROBE test, please")

    def test_no_prompt_is_no_prompt(self):
        self.assertIsNone(self.started()["prompt"])

    def test_the_originating_chat_is_handed_to_the_runner(self):
        # §4's reconciliation pushes the `ended` notice to the chat that started the session,
        # and meta.json is where it finds the id. This is where it enters the record.
        self.assertEqual(self.started()["chat_id"], ME)

    def test_bare_claude_still_starts_nothing(self):
        # §5 and §15: the one extra tap, and what it buys is that no bypass-permissions
        # session can begin in a repository nobody named. Slice 7 is the slice that makes the
        # decision cost something, so it is the slice that has to re-assert it.
        tg = self.deliver(message("claude"))
        self.assertEqual(self.sessions.started, [])
        self.assertIn("beacon", tg.texts[0])

    def test_a_name_that_does_not_resolve_starts_nothing(self):
        for text in ("claude nosuchproject", "claude ../../etc", "claude /etc", "claude ..",
                     "claude .ssh", "claude beacon/../..", "claude README.md"):
            tg = self.deliver(message(text))
            self.assertEqual(self.sessions.started, [], "%r reached the runner" % text)
            self.assertIn("beacon", tg.texts[0], "%r should still get help" % text)

    def test_a_stranger_cannot_start_one(self):
        # The allowlist was already tested for silence; this is the same rule with the
        # consequence slice 7 gives it.
        self.deliver(message("claude beacon", chat=STRANGER, sender=STRANGER))
        self.assertEqual(self.sessions.started, [])

    def test_a_group_cannot_start_one(self):
        self.cfg = config.Config(TOKEN, frozenset([ME, GROUP]), self.projects, "/bin/echo", 2)
        self.deliver(message("claude beacon", chat=GROUP, kind="supergroup"))
        self.assertEqual(self.sessions.started, [])

    def test_a_message_from_before_the_weekend_cannot_start_one(self):
        # §7's braces, and slice 7 is where they earn their keep: a lost offset file used to
        # cost a replayed echo and now costs a room full of sessions.
        self.deliver(message("claude beacon", date=START - 86400))
        self.assertEqual(self.sessions.started, [])

    def test_a_spawn_that_fails_is_answered_rather_than_swallowed(self):
        """A runner that cannot be started is the one failure with no transcript to send.

        ENOENT on the interpreter, a var/ that has gone read-only, an argv with a NUL in it
        from a prompt off a phone keyboard — none of them reach meta.json, so if this reply is
        not composed here the phone simply never hears back.
        """
        self.sessions.fail = OSError(errno.ENOENT, "No such file or directory")
        tg = self.deliver(message("claude beacon"))
        self.assertEqual(len(tg.sent), 1)
        self.assertTrue(tg.texts[0].strip())
        self.assertTrue(self.logged)

    def test_a_spawn_that_fails_does_not_kill_the_loop(self):
        self.sessions.fail = OSError(errno.ENOENT, "No such file or directory")
        tg = FakeTelegram([message("claude beacon", uid=1)], [message("help", uid=2)])
        listener = self.listener(tg)
        listener.tick()
        listener.tick()
        self.settle(listener)
        self.assertIn("stop", tg.texts[-1], "the next message went unanswered")

    def test_a_spawn_failure_never_says_the_token(self):
        self.sessions.fail = RuntimeError("failed calling %s" % TOKEN)
        tg = self.deliver(message("claude beacon"))
        self.assertNotIn(SECRET, tg.texts[0])
        self.assertNotIn(SECRET, "\n".join(self.logged))


class TestTheReplyIsTheLink(Base):
    """§4.6 and §5: one reply per session, and on a phone it has one job — be tappable."""

    def reply_to(self, text="claude beacon"):
        tg = self.deliver(message(text))
        self.assertEqual(len(tg.sent), 1, "expected exactly one reply to %r" % (text,))
        return tg.texts[0]

    def test_the_link_comes_back_once_the_record_says_live(self):
        self.assertIn(LINK, self.reply_to())

    def test_the_link_is_on_a_line_of_its_own(self):
        # §5 says so and means it literally: Telegram autolinks a bare URL, and anything
        # sharing the line is one more thing to miss with a thumb.
        self.assertIn(LINK, self.reply_to().splitlines())

    def test_the_reply_names_the_project_and_the_session(self):
        got = self.reply_to()
        self.assertIn("beacon", got)
        self.assertIn(self.sessions.started[0]["name"], got)

    def test_the_reply_says_the_permissions_are_bypassed(self):
        """§5's third line, and it is not decoration.

        What has just started is a session on this Mac that will not ask before it acts,
        started by a thumb on a phone. The line is the only moment that fact is in front of
        the person who caused it.
        """
        self.assertIn("bypass permissions", self.reply_to())

    def test_the_reply_carries_no_path_from_this_machine(self):
        # §7/§10: Telegram is not end-to-end encrypted, and meta.json's `cwd` is right there
        # in the record this reply is built from.
        self.assertNotIn("/Users/", self.reply_to())

    def test_a_session_that_goes_live_late_is_still_answered(self):
        """The ordinary case, in fact — §4 budgets ten to twenty seconds, and for all of it
        the record says `starting`."""
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.sessions.outcome = session.STARTING
        self.sessions.gate = gate
        tg = FakeTelegram([message("claude beacon")])
        listener = self.listener(tg, timeout=30, poll_every=0.005, sleep=time.sleep)
        listener.tick()
        self.sessions.write(self.sessions.started[0]["sid"], session.LIVE)
        gate.set()
        self.settle(listener)
        self.assertEqual(len(tg.sent), 1)
        self.assertIn(LINK, tg.texts[0])

    def test_a_live_record_with_no_link_is_not_reported_as_a_link(self):
        # §3 has the runner writing `live` and the url in one atomic record, so this should be
        # impossible — which is exactly why it should not be trusted to be. A reply reading
        # "here it is: None" would be worse than the timeout it replaced.
        self.sessions.url = None
        tg = FakeTelegram([message("claude beacon")])
        listener = self.listener(tg, timeout=0.05, poll_every=0.01, sleep=time.sleep)
        listener.tick()
        self.settle(listener)
        self.assertEqual(len(tg.sent), 1)
        self.assertNotIn("None", tg.texts[0])
        self.assertNotIn("claude.ai/code", tg.texts[0])


class TestWhenNothingComesUp(Base):
    """§4.6: on `failed` or a timeout, the phone gets the tail of the transcript.

    §9.7 is the whole reason. The likeliest failure after week one is an expired claude.ai
    login: the session comes up into a `/login` prompt and waits for a human who is not at the
    keyboard. Nothing crashes, nothing is logged, and the only evidence anywhere is on a
    terminal nobody is looking at. The tail is how it reaches the phone.
    """

    def failing(self, transcript, outcome=session.FAILED, **kw):
        self.sessions.outcome = outcome
        self.sessions.transcript = transcript
        tg = self.deliver(message("claude beacon"))
        self.assertEqual(len(tg.sent), 1)
        return tg.texts[0]

    def test_a_failed_session_replies_with_the_transcript(self):
        got = self.failing("Invalid API key · Please run /login\r\n")
        self.assertIn("/login", got)

    def test_the_reply_says_which_project_did_not_start(self):
        self.assertIn("beacon", self.failing("boom\r\n"))

    def test_the_tail_is_stripped_of_its_escapes(self):
        # §4.6 says ANSI-stripped, and a phone would otherwise get `[31m` sprayed through the
        # one message that is supposed to explain what went wrong.
        got = self.failing("\x1b[31m\x1b[1mInvalid API key\x1b[0m\r\n")
        self.assertIn("Invalid API key", got)
        self.assertNotIn("\x1b", got)
        self.assertNotIn("[31m", got)

    def test_the_tail_is_the_last_lines_and_not_the_whole_transcript(self):
        got = self.failing("".join("line %d\r\n" % i for i in range(200)))
        self.assertIn("line 199", got)
        self.assertNotIn("line 150", got)

    def test_the_tail_is_the_fifteen_lines_the_spec_names(self):
        self.assertEqual(bot.TAIL_LINES, 15)
        got = self.failing("".join("line %d\r\n" % i for i in range(200)))
        self.assertEqual(len([ln for ln in got.splitlines() if ln.startswith("line ")]), 15)
        self.assertIn("line 185", got)
        self.assertNotIn("line 184", got)

    def test_a_redrawn_line_counts_as_a_line(self):
        """A TUI rewrites one line in place with a bare carriage return, so a transcript with
        two `\\n` in it can still hold fifty renderings of a progress bar. Splitting on `\\n`
        alone makes the tail one enormous line of overwritten text."""
        got = self.failing("\r".join("frame %d" % i for i in range(60)) + "\r\n")
        self.assertIn("frame 59", got)
        self.assertNotIn("frame 44", got)

    def test_a_blank_tail_is_not_fifteen_blank_lines(self):
        # An RC panel that cleared the screen and gave up leaves exactly this behind.
        got = self.failing("\x1b[2J\x1b[H" + "\r\n" * 40 + "Trust the files in this folder?\r\n")
        self.assertIn("Trust the files", got)

    def test_a_missing_transcript_is_not_a_crash(self):
        got = self.failing(None)
        self.assertTrue(got.strip())
        self.assertIn("beacon", got)

    def test_the_tail_never_carries_this_machines_home_path(self):
        # §7's scrub. The home path names the account whose keychain, OAuth credentials and
        # tokens every session started here can reach.
        home = os.path.expanduser("~")
        got = self.failing("cannot read %s/.claude/.credentials.json\r\n" % home)
        self.assertNotIn(home, got)
        self.assertIn("~/.claude/.credentials.json", got)

    def test_a_sibling_of_home_is_not_mangled_into_a_tilde(self):
        home = os.path.expanduser("~")
        self.assertIn(home + "-old", bot.scrub("ls: %s-old: no such file" % home))

    def test_the_tail_never_carries_the_bot_token(self):
        got = self.failing("curl https://api.telegram.org/bot%s/getMe: 401\r\n" % TOKEN)
        self.assertNotIn(SECRET, got)

    def test_the_tail_never_carries_a_token_shaped_run_this_code_has_never_seen(self):
        """§7 says token-shaped, not "our token" — a session's transcript is full of other
        people's credentials, and the one that leaks will be the one nobody thought to name."""
        other = "7100000000:AAG" + "b" * 32
        got = self.failing("TELEGRAM_TOKEN=%s\r\n" % other)
        self.assertNotIn(other, got)

    def test_the_tail_never_carries_an_api_key(self):
        key = "sk-ant-api03-" + "c" * 40
        self.assertNotIn(key, self.failing("ANTHROPIC_API_KEY=%s\r\n" % key))

    def test_the_scrub_does_not_eat_the_link(self):
        """The one thing a token-shaped rule must never match.

        A session id is twenty-six characters of mixed-case base62 — exactly what a generic
        "long opaque run" rule is looking for. A scrub that redacted it would break the only
        reply that matters, and would do it silently.
        """
        self.assertIn(LINK, bot.scrub("here: %s\r\n" % LINK))
        self.assertIn(LINK, self.failing("gave up after: %s\r\n" % LINK))

    def test_an_enormous_transcript_still_fits_in_one_message(self):
        # §7: 4096 is the cap and anything over it is a 400 — a silent non-reply at exactly
        # the moment the phone is waiting for an explanation. Fifteen lines of a 200-column
        # terminal is already 3000 characters before the header.
        got = self.failing("".join("%d %s\r\n" % (i, "x" * 2000) for i in range(40)))
        self.assertLessEqual(len(got), telegram.LIMIT)

    def test_a_session_that_ended_before_the_link_says_so(self):
        # `ended` rather than `failed`: the runner found no url, but the child exited cleanly.
        got = self.failing("goodbye\r\n", outcome=session.ENDED)
        self.assertIn("beacon", got)
        self.assertIn("goodbye", got)


class TestGivingUpAtFortyFiveSeconds(Base):
    """§4.6: `Listener polls meta.json for up to 45s (0.25s interval).`"""

    def test_the_deadline_and_the_interval_are_the_ones_the_spec_names(self):
        # 45 because a cold start after a Claude Code update is slower than the 10-20s §4
        # budgets, and 0.25 because the link should reach a phone about as fast as it reaches
        # the terminal.
        self.assertEqual(bot.SESSION_TIMEOUT, 45)
        self.assertEqual(bot.SESSION_POLL, 0.25)

    def test_the_wait_gives_up_at_the_deadline(self):
        clock = Clock()
        listener = self.listener(clock=clock.time, sleep=clock.sleep,
                                 timeout=bot.SESSION_TIMEOUT, poll_every=bot.SESSION_POLL)
        record, state = listener.wait("3f2a91")
        self.assertIsNone(state, "a session that never came up must not read as an outcome")
        self.assertIsNone(record)
        self.assertGreaterEqual(clock.now, bot.SESSION_TIMEOUT)
        self.assertLess(clock.now, bot.SESSION_TIMEOUT + bot.SESSION_POLL * 2)

    def test_it_looks_at_the_interval_the_spec_names_and_does_not_spin(self):
        # A wait that polled without sleeping would read meta.json a few hundred thousand
        # times per session and keep a core busy doing it.
        clock = Clock()
        listener = self.listener(clock=clock.time, sleep=clock.sleep,
                                 timeout=bot.SESSION_TIMEOUT, poll_every=bot.SESSION_POLL)
        listener.wait("3f2a91")
        self.assertEqual(set(clock.sleeps), {bot.SESSION_POLL})
        self.assertAlmostEqual(self.sessions.reads, bot.SESSION_TIMEOUT / bot.SESSION_POLL,
                               delta=2)

    def test_a_session_that_never_comes_up_is_reported_rather_than_forgotten(self):
        """§9.3's trust dialog is the case to have in mind: a directory created in the root
        since the last time anyone opened it by hand comes up into `Do you trust the files in
        this folder?` and waits forever. No crash, no failed state, nothing in any log."""
        self.sessions.outcome = session.STARTING
        self.sessions.transcript = "Do you trust the files in this folder?\r\n"
        tg = FakeTelegram([message("claude beacon")])
        listener = self.listener(tg, timeout=0.05, poll_every=0.01, sleep=time.sleep)
        listener.tick()
        self.settle(listener)
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("trust", tg.texts[0])

    def test_giving_up_does_not_claim_the_session_is_gone(self):
        # It may well come up at second fifty. The listener stopped waiting; it did not stop
        # the session, and saying otherwise would send someone looking in the wrong place.
        self.sessions.outcome = session.STARTING
        tg = FakeTelegram([message("claude beacon")])
        listener = self.listener(tg, timeout=0.05, poll_every=0.01, sleep=time.sleep)
        listener.tick()
        self.settle(listener)
        self.assertIn(self.sessions.started[0]["sid"], tg.texts[0])


class TestTheLoopIsNeverBlocked(Base):
    """§4.2: **the listener never blocks on a session.** The one architectural claim in slice 7.

    §4.6 has it polling meta.json for up to forty-five seconds, and §4.2 has it returning to
    getUpdates immediately. Both are true only if the waiting happens somewhere else, so it
    does — one thread per pending session, which is also what makes a second `claude` while
    the first is still starting an ordinary thing to do rather than a wedged bot.
    """

    def test_a_second_message_is_answered_while_the_first_session_is_still_starting(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.sessions.outcome = session.STARTING
        self.sessions.gate = gate
        tg = FakeTelegram([message("claude beacon", uid=1), message("help", uid=2)])
        listener = self.listener(tg, timeout=30, poll_every=0.005, sleep=time.sleep)

        began = time.time()
        listener.tick()
        self.assertLess(time.time() - began, 5, "the tick waited for the session")
        self.assertEqual(len(tg.sent), 1, "the pending session answered before it was live")
        self.assertIn("stop", tg.texts[0], "the message behind it did not get its help")

        self.sessions.write(self.sessions.started[0]["sid"], session.LIVE)
        gate.set()
        self.settle(listener)
        self.assertIn(LINK, tg.texts[1])

    def test_a_second_session_starts_while_the_first_is_still_starting(self):
        """Two pending sessions at once, which is the ordinary way to use two hands on a phone.

        Both waiters are released rather than left to run out their deadline: a test that ends
        by waiting out a timeout is racing `settle()`'s own, which is §9.11's nested graces in
        miniature — and it costs the whole suite that timeout on every run.
        """
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.sessions.outcome = session.STARTING
        self.sessions.gate = gate
        tg = FakeTelegram([message("claude beacon", uid=1), message("claude centrion", uid=2)])
        listener = self.listener(tg, timeout=30, poll_every=0.005, sleep=time.sleep)
        listener.tick()
        self.assertEqual(len(self.sessions.started), 2,
                         "the second message waited for the first session to come up")
        for started in self.sessions.started:
            self.sessions.write(started["sid"], session.LIVE)
        gate.set()
        self.settle(listener)
        self.assertEqual(len(tg.sent), 2, "one of the two sessions was never answered")

    def test_the_waiters_do_not_pile_up(self):
        # One thread per pending session is the design; one thread per message ever received
        # is a leak that takes a week to show up on a daemon that never exits.
        tg = FakeTelegram(*[[message("claude beacon", uid=i)] for i in range(5)])
        listener = self.listener(tg, timeout=30, poll_every=0.005, sleep=time.sleep)
        for _ in range(5):
            listener.tick()
            self.settle(listener)
        self.assertEqual(len(self.sessions.started), 5)
        self.assertLessEqual(len(listener.waiters), 1, "finished waiters are never dropped")

    def test_a_waiter_that_raises_takes_nothing_down_with_it(self):
        """A thread that dies takes its reply with it and nothing else — which is worse than a
        crash, not better, because the phone just never hears back and §14's log is the only
        place that could have said why."""
        self.sessions.boom = RuntimeError("meta.json went sideways")
        tg = FakeTelegram([message("claude beacon", uid=1)], [message("help", uid=2)])
        listener = self.listener(tg)
        listener.tick()
        self.settle(listener)
        listener.tick()
        self.settle(listener)
        self.assertIn("stop", tg.texts[-1])
        self.assertTrue([line for line in self.logged if "meta.json went sideways" in line],
                        "a waiter that died left nothing in bot.log")


class TestSpawningForReal(unittest.TestCase):
    """The one place in this file that actually forks. Everything above it fakes the runner.

    Not an integration test with `claude` in it — §12's manual step is what proves that, and a
    unit test that started a real session would be the single worst thing in this repository.
    What is under test is the wiring: the argv the runner is handed, the shell that is not
    involved in handing it over, the lock fd that must not travel, and the corpse that must
    not be left behind.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "sessions")
        self.out = os.path.join(self.tmp, "spawned.json")
        self.stub = os.path.join(self.tmp, "stub.py")
        with open(self.stub, "w") as fh:
            fh.write("import json, os, sys\n"
                     "try:\n"
                     "    os.fstat(9)\n"
                     "    lock = 'open'\n"
                     "except OSError:\n"
                     "    lock = 'closed'\n"
                     # Written and renamed, not written in place: the poll below waits on the
                     # path existing, and a file that exists and is still empty is exactly the
                     # kind of flake that shows up once a fortnight in CI and never by hand.
                     "with open(%r + '.tmp', 'w') as fh:\n"
                     "    json.dump({'argv': sys.argv[1:], 'lock': lock,\n"
                     "               'sid': os.getsid(0) == os.getpid()}, fh)\n"
                     "os.replace(%r + '.tmp', %r)\n" % (self.out, self.out, self.out))

    def hold_the_lock_fd(self):
        """launchd/bot.sh's `exec 9>>`, reproduced: an fd 9 with no close-on-exec flag."""
        spare = os.open(os.devnull, os.O_RDWR)
        os.dup2(spare, 9)
        os.close(spare)
        self.addCleanup(os.close, 9)

    def spawn(self, prompt=None):
        sessions = bot.Sessions(root=self.root, script=self.stub, log=lambda m: None)
        pid = sessions.start("3f2a91", "beacon-3f2a", self.tmp, "beacon", ME, prompt)
        self.addCleanup(self.finish, sessions)
        deadline = time.time() + 30
        while time.time() < deadline and not os.path.exists(self.out):
            time.sleep(0.01)
        with open(self.out) as fh:
            return sessions, pid, json.load(fh)

    def finish(self, sessions):
        """Leave no child behind — the listener reaps on every tick, and a test has no tick."""
        for child in list(sessions.children):
            child.wait(timeout=30)
        sessions.reap()

    def test_the_runner_is_handed_the_arguments_the_spec_names(self):
        # §4.2, and every one of them matters to a runner that re-reads nothing from the
        # message: the directory it chdirs into, the name the session appears under, and the
        # chat that gets told when it ends.
        _, _, got = self.spawn(prompt="fix the probe test")
        argv = got["argv"]
        self.assertEqual(argv[argv.index("--sid") + 1], "3f2a91")
        self.assertEqual(argv[argv.index("--cwd") + 1], self.tmp)
        self.assertEqual(argv[argv.index("--name") + 1], "beacon-3f2a")
        self.assertEqual(argv[argv.index("--project") + 1], "beacon")
        self.assertEqual(argv[argv.index("--chat-id") + 1], str(ME))
        self.assertEqual(argv[argv.index("--root") + 1], self.root)
        self.assertEqual(argv[argv.index("--prompt") + 1], "fix the probe test")

    def test_a_prompt_is_one_argument_and_never_a_command_line(self):
        """§10: the prompt arrives from a phone and goes into an argv.

        A list and no shell is the whole of the defence, and it is worth an assertion rather
        than a comment — the day someone reaches for `shell=True` to make quoting simpler,
        this is what says no.
        """
        hostile = "; touch %s/pwned ; echo `whoami` $(id) 'quoted'" % self.tmp
        _, _, got = self.spawn(prompt=hostile)
        self.assertIn(hostile, got["argv"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "pwned")))

    def test_no_prompt_means_no_prompt_flag(self):
        _, _, got = self.spawn()
        self.assertNotIn("--prompt", got["argv"])

    def test_the_lock_fd_does_not_travel_to_the_runner(self):
        """§8, verified on this box: a detached runner still held fd 9 and therefore still
        held the flock. The listener exits, its sessions look perfectly healthy, and launchd's
        restarted listener can never take the lock again — a bot that has gone silent for good
        with nothing anywhere to say why. session.detach() closes it too; this is the half
        that keeps it from ever being inherited in the first place."""
        self.hold_the_lock_fd()
        _, _, got = self.spawn()
        self.assertEqual(got["lock"], "closed")

    def test_the_runner_is_left_to_detach_itself(self):
        """`start_new_session=True` is the obvious way to spawn a detached child and it is the
        wrong one here: it calls setsid() before the exec, so session.detach()'s own setsid()
        then fails with EPERM — before the pty is allocated, before meta.json says anything,
        and the phone waits out all forty-five seconds for a session that died instantly."""
        _, _, got = self.spawn()
        self.assertFalse(got["sid"], "the runner was already a session leader")
        # Read off the syntax tree rather than the text, because the paragraph above says the
        # words and a substring check would find them there.
        with open(os.path.join(ROOT, "bot.py")) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.assertNotIn("start_new_session", [kw.arg for kw in node.keywords])

    def test_setsid_twice_is_the_error_that_would_produce(self):
        # The claim above, on this box rather than in a comment.
        proc = subprocess.Popen([sys.executable, "-c", "import os; os.setsid()"],
                                start_new_session=True, stderr=subprocess.PIPE)
        _, err = proc.communicate(timeout=30)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"PermissionError", err)

    def test_a_finished_runner_is_not_left_as_a_zombie(self):
        """An unreaped child answers `os.kill(pid, 0)` forever, and §4's reconciliation is
        about to use exactly that call to decide whether a session is still alive. A zombie
        runner would count against max_sessions until the listener restarted."""
        sessions, pid, _ = self.spawn()
        deadline = time.time() + 30
        while time.time() < deadline and sessions.children:
            sessions.reap()
            time.sleep(0.01)
        self.assertEqual(sessions.children, [])
        with self.assertRaises(OSError) as caught:
            os.kill(pid, 0)
        self.assertEqual(caught.exception.errno, errno.ESRCH)


class TestFittingIntoOneMessage(unittest.TestCase):
    """§7: 4096 is Telegram's cap, and going over it is a 400 rather than a truncation."""

    def test_short_text_is_left_exactly_as_it_is(self):
        self.assertEqual(bot.fit("▶ beacon\n" + LINK), "▶ beacon\n" + LINK)

    def test_long_text_is_cut_to_the_cap(self):
        self.assertLessEqual(len(bot.fit("x" * 10000)), telegram.LIMIT)

    def test_both_ends_survive_and_the_cut_is_marked(self):
        # §7 asks for head and tail with a marked elision: the first lines of an error say
        # what was being attempted and the last say how it went.
        got = bot.fit("HEAD" + "x" * 10000 + "TAIL")
        self.assertTrue(got.startswith("HEAD"))
        self.assertTrue(got.endswith("TAIL"))
        self.assertIn("elided", got)

    def test_the_elision_counts_what_it_elided(self):
        got = bot.fit("x" * 10000)
        self.assertEqual(got.count("x") + int(re.search(r"(\d+) characters", got).group(1)),
                         10000)


class TestTheLaunchdInstall(unittest.TestCase):
    """SPEC.md §8. Static checks, because the failures here are silent and slow.

    launchd reports almost nothing: a plist with the wrong ProcessType loads perfectly and
    quietly throttles every session it spawns, and a relative path in a plist simply never
    runs. Both are cheap to assert and expensive to notice.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.join(ROOT, "launchd")
        with open(os.path.join(cls.dir, "com.tommy.centrion.bot.plist"), "rb") as fh:
            cls.plist = plistlib.load(fh)

    def test_the_label_matches_the_filename_and_the_box_convention(self):
        self.assertEqual(self.plist["Label"], "com.tommy.centrion.bot")

    def test_the_process_type_is_standard(self):
        """§8, and the one setting here that is wrong by default on this box.

        The other five com.tommy.*/com.beacon.* agents use Background correctly — they are
        short batch runs. Here the setting is inherited by every Claude Code session the
        listener spawns, and throttles its CPU scheduling for as long as the session lives.
        The symptom is "Remote Control feels slow", hours later, with nothing in any log.
        """
        self.assertEqual(self.plist["ProcessType"], "Standard")

    def test_keep_alive_is_unconditional(self):
        # Not {"SuccessfulExit": False}: this daemon has no successful exit, and if it stops
        # for any reason at all it should come back.
        self.assertIs(self.plist["KeepAlive"], True)

    def test_it_starts_at_login_and_throttles_a_crash_loop(self):
        self.assertIs(self.plist["RunAtLoad"], True)
        self.assertEqual(self.plist["ThrottleInterval"], 10)

    def test_every_path_is_absolute_and_present(self):
        # launchd has no shell, no cd and no useful PATH.
        for path in self.plist["ProgramArguments"] + [self.plist["WorkingDirectory"]]:
            self.assertTrue(os.path.isabs(path), path)
            self.assertTrue(os.path.exists(path), path)

    def test_it_runs_this_checkout(self):
        self.assertEqual(os.path.realpath(self.plist["WorkingDirectory"]), os.path.realpath(ROOT))
        self.assertIn(os.path.realpath(os.path.join(self.dir, "bot.sh")),
                      [os.path.realpath(p) for p in self.plist["ProgramArguments"]])

    def test_no_path_is_inside_a_tcc_protected_folder(self):
        """§9.2: a launchd process does not inherit the terminal's Full Disk Access grant.

        It killed the beacon job on 2026-09-10 and the stockwatch one on 2026-09-08, both
        before their first line, exit 126. The log paths count: launchd opens them itself,
        before the script runs, and a failure there is a job that never starts at all.
        """
        guarded = ("/Documents/", "/Desktop/", "/Downloads/")
        paths = (self.plist["ProgramArguments"] + [self.plist["WorkingDirectory"],
                 self.plist["StandardOutPath"], self.plist["StandardErrorPath"]])
        for path in paths:
            for folder in guarded:
                self.assertNotIn(folder, path, "%s is under TCC-guarded %s" % (path, folder))

    def test_the_logs_land_where_section_14_says_to_look(self):
        self.assertEqual(self.plist["StandardOutPath"], self.plist["StandardErrorPath"])
        self.assertEqual(os.path.realpath(self.plist["StandardOutPath"]),
                         os.path.realpath(os.path.join(ROOT, "var", "bot.log")))

    def test_the_token_is_not_in_the_plist(self):
        # §10.5: files in ~/Library/LaunchAgents are world-readable 0644.
        with open(os.path.join(self.dir, "com.tommy.centrion.bot.plist")) as fh:
            body = fh.read()
        self.assertNotIn("bot_token", body)
        self.assertNotIn("AAH", body)

    def test_the_environment_does_not_disable_the_feature_flags(self):
        # §6: each of these switches off the evaluation Remote Control's availability depends
        # on, and the session then starts perfectly and simply never connects.
        env = self.plist.get("EnvironmentVariables", {})
        for name in ("DISABLE_TELEMETRY", "DO_NOT_TRACK", "DISABLE_GROWTHBOOK",
                     "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "ANTHROPIC_API_KEY"):
            self.assertNotIn(name, env)


class TestTheLockGuardsTheToken(unittest.TestCase):
    """SPEC.md §7/§8: two consumers on one token 409 each other, forever and on both sides."""

    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.join(ROOT, "launchd")
        with open(os.path.join(cls.dir, "bot.sh")) as fh:
            cls.bot_sh = fh.read()
        with open(os.path.join(cls.dir, "lock.sh")) as fh:
            cls.lock_sh = fh.read()

    def test_the_scripts_are_executable_or_run_through_sh(self):
        self.assertTrue(self.bot_sh.startswith("#!/bin/sh"))
        self.assertTrue(self.lock_sh.startswith("#!/bin/sh"))

    def test_the_lock_is_taken_before_python_is_exec_ed(self):
        # Order is the whole of it: a second poller that gets as far as getUpdates has already
        # knocked the first one off the air.
        self.assertLess(self.bot_sh.index("take_lock"), self.bot_sh.index('exec "$PY"'),
                        "bot.sh must hold the lock before it starts the listener")

    def test_it_uses_lockf_and_not_shlock(self):
        # lock.sh's own header says why: on macOS 25.6 shlock spots a dead holder and then
        # keeps the lock anyway, so one kill -9 wedges the job until the file is deleted.
        self.assertIn("lockf", self.lock_sh)
        self.assertNotIn("shlock", self.lock_sh.split("LOCK=")[-1])

    def test_the_lock_lives_under_the_gitignored_var(self):
        self.assertIn("var/.bot.lock", self.lock_sh)

    def test_the_runner_is_warned_about_the_inherited_lock_fd(self):
        """§8, and the nastiest delayed failure in this design.

        `exec 9>>` sets no close-on-exec flag, so fd 9 survives into python and into every
        detached runner it forks. A runner that keeps it keeps the flock: the listener exits,
        its sessions look perfectly healthy, and launchd's restarted listener can never take
        the lock again — so the bot goes permanently silent. The fix belongs to session.py in
        slice 6, which is exactly why the warning has to be waiting here when it is written.
        """
        self.assertIn("9", self.lock_sh)
        self.assertIn("close", self.lock_sh.lower())


if __name__ == "__main__":
    unittest.main()
