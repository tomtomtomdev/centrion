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

**Slice 8 is about the sessions nobody is waiting for any more.** Everything above answers a
message; the reconciliation pass answers a fifty-second timer, and the three things it has to
get right are all things this box has already done wrong. A record whose runner a reboot took
still says `live` and holds a slot against `max_sessions` (§4) — that state was sitting in
`var/` this morning. A `stop` that reports success without having signalled anything leaves a
bypass-permissions session running (§9.10, §9.11) — slice 7 shipped that twice. And a session
that reaches `live` an hour after its waiter gave up is never mentioned to anyone (§9.12) —
which is the one entry in §9 that no test could have found, and the reason the pass announces
arrivals as well as departures.

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

#: Distinguishes "no url given" from "explicitly no url". A `live` record with no link in it
#: should be impossible — §3 writes the state and the url in one atomic record — which is
#: exactly why both the waiter and the tick are tested against one.
UNSET = object()

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
        # Slice 8. Whether a runner is still there is a set here rather than a process on
        # this box: §4's reconciliation asks the question of pids this test never forked, and
        # the real answer involves `ps`. Empty by default, so every slice-7 test above sees
        # the fleet it always saw — one where nothing has died.
        self.dead = set()
        self.unstoppable = set()  # runner pids that outlive a SIGKILL, per §9.11
        self.stopped = []         # (sid, grace) per stop(), so §9.11's nesting is assertable

    def start(self, sid, name, cwd, project, chat_id, prompt=None):
        self.started.append({"sid": sid, "name": name, "cwd": cwd, "project": project,
                             "chat_id": chat_id, "prompt": prompt})
        if self.fail is not None:
            raise self.fail
        # The record says what this session was actually asked for. It used to say `beacon`
        # in a directory nobody named, which made §5's same-directory warning untestable
        # against a session the listener itself had started — the only way it ever happens.
        self.write(sid, self.outcome, cwd=cwd, project=project, name=name, chat_id=chat_id)
        if self.transcript is not None:
            self.transcribe(sid, self.transcript)
        return 44213

    def write(self, sid, state, url=UNSET, **fields):
        """The record the runner writes. §3's shape, down to the keys the listener reads.

        `fields` overrides any of them, which is how slice 8 puts a fleet on disk: a session
        that started an hour ago, one belonging to another chat, one whose runner is a pid
        nothing on this box has. The defaults are slice 7's, so nothing above notices.
        """
        if state is None:
            return
        os.makedirs(self.directory(sid), exist_ok=True)
        record = {
            "sid": sid, "state": state, "project": "beacon",
            "cwd": "/Users/nobody/Projects/beacon", "name": "beacon-" + sid[:4],
            "runner_pid": os.getpid(), "claude_pid": 44215, "started": START, "chat_id": ME,
            "url": (self.url if state == session.LIVE else None) if url is UNSET else url}
        record.update(fields)
        session.write_meta(self.directory(sid), record)
        return record

    # -- slice 8's two process-shaped seams. Everything else it adds reads and writes real
    # files under the temporary var/, because that is §2's claim and the point of this fake.

    def alive(self, record):
        return record.get("runner_pid") not in self.dead

    def stop(self, record, grace=None):
        self.stopped.append((record.get("sid"), grace))
        if record.get("runner_pid") in self.unstoppable:
            return False
        self.dead.add(record.get("runner_pid"))
        return True

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

    def place(self, sid, state=session.LIVE, announced=True, **fields):
        """A session already on disk, as a previous listener or a previous boot left it.

        Slice 8's whole subject: every record the reconciliation pass walks got there before
        this listener existed, which is what makes it a *reconciliation* rather than
        bookkeeping.

        `announced` is the ordinary case and it is why it defaults to True — a live session's
        waiter sent its link the moment the record said so (§4.6), leaving the tick nothing to
        say about it. §9.12's tests are the ones that pass False, because that is the case
        where the waiter had already given up before the link existed.
        """
        record = self.sessions.write(sid, state, **fields)
        if announced and state == session.LIVE:
            self.sessions.claim(sid, bot.LINK_SENT)
        return record

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
        #
        # Room for all five deliberately: what is under test is whether finished waiters are
        # dropped, and slice 8's cap would otherwise refuse three of them and prove nothing.
        self.cfg = config.Config(TOKEN, frozenset([ME]), self.projects, "/bin/echo", 5)
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

    def test_a_second_copy_started_by_any_path_is_refused_before_it_polls(self):
        """§7/§8, and the one property that must survive §8 coming back.

        There are two supported ways to start the listener again — the LaunchAgent, and
        `bot.sh` from a shell for debugging — so "both were started" stops being a mistake
        somebody has to remember not to make. A 409 is mutual: the copy that loses the race
        takes the working one off the air with it, and neither gets the message afterwards.
        So the second copy has to be refused *before* it reaches getUpdates, not after.

        The test above asserts that ordering in the text of the script. This one runs it, in a
        throwaway tree with a stand-in listener, and its teeth are the last assertion: the
        stand-in must have been started exactly once. A refusal that still got as far as
        starting the listener would satisfy an exit code and not the property.
        """
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.mkdir(os.path.join(tmp, "launchd"))
        for name in ("bot.sh", "lock.sh"):
            shutil.copy(os.path.join(self.dir, name), os.path.join(tmp, "launchd", name))

        # Stands in for the real listener: records that it ran, then holds fd 9 open the way a
        # polling bot.py would, so the lock stays taken for as long as this copy is alive.
        ran = os.path.join(tmp, "ran")
        with open(os.path.join(tmp, "bot.py"), "w") as fh:
            fh.write("import sys, time\n"
                     "open(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                     "time.sleep(60)\n" % ran)

        bot_sh = os.path.join(tmp, "launchd", "bot.sh")
        first = subprocess.Popen(["/bin/sh", bot_sh],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(first.stdout.close)
        self.addCleanup(first.wait)
        self.addCleanup(first.kill)

        deadline = time.time() + 20
        while time.time() < deadline and not os.path.exists(ran):
            time.sleep(0.05)
        self.assertTrue(os.path.exists(ran), "the first copy never started the listener")

        second = subprocess.run(["/bin/sh", bot_sh], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=30)

        said = second.stdout.decode()
        self.assertEqual(second.returncode, 0, said)
        self.assertIn("already holds", said)
        self.assertIsNone(first.poll(), "the refused copy took the working one down with it")
        self.assertEqual(open(ran).read().count("--serve"), 1,
                         "the second copy reached the listener: %s" % said)


# ===========================================================================================
# Slice 8 — fleet control and reconciliation. §5 tier 2, §4's pass, §10.6's cap.
# ===========================================================================================


class TestHowLongASessionHasBeenUp(unittest.TestCase):
    """§5: `ls` shows uptime, which is arithmetic on a clock that does not only go forwards."""

    NOW = 1789265427

    def up(self, seconds):
        return bot.uptime(self.NOW - seconds, self.NOW)

    def test_it_reads_as_a_duration_at_every_scale_a_session_reaches(self):
        self.assertEqual(self.up(9), "9s")
        self.assertEqual(self.up(90), "1m")
        self.assertEqual(self.up(3600), "1h")
        self.assertEqual(self.up(3600 * 3 + 60 * 12), "3h 12m")
        self.assertEqual(self.up(86400 * 2 + 3600 * 5), "2d 5h")

    def test_a_session_that_started_in_the_future_does_not_read_as_negative(self):
        """Not hypothetical, and §4.6 already knows it: the wait loop uses a monotonic clock
        precisely because this box's wall clock moves backwards at every NTP correction and
        every wake from sleep. `started` is a wall clock timestamp, so a record from the
        future is an ordinary thing to find after a laptop has been asleep."""
        self.assertEqual(bot.uptime(self.NOW + 30, self.NOW), "0s")

    def test_a_record_with_no_usable_start_time_is_not_a_crash(self):
        # §4 walks every directory under var/sessions, including whatever a half-written
        # record or an older build of this bot left behind there.
        for value in (None, "", "soon", [1], {}):
            self.assertIsInstance(bot.uptime(value, self.NOW), str, repr(value))


class TestTheFleetIsListed(Base):
    """§5 tier 2: `ls` → index, project, name, uptime, link.

    The only view of this machine a phone gets, and §12's run step for this slice ends in
    `stop all` — so the index printed here is the index `stop` is about to be given. A listing
    that is merely approximately right is a `stop` aimed at the wrong session.
    """

    def ls(self):
        return self.listener().answer(commands.parse("ls"))

    def test_it_says_so_when_there_is_nothing_to_list(self):
        self.assertIn("no live sessions", self.ls().lower())
        self.place("aa11aa", session.ENDED)
        self.assertIn("no live sessions", self.ls().lower(),
                      "a session that has ended is not a live session")
        self.place("bb22bb", session.LIVE)
        self.assertNotIn("no live sessions", self.ls().lower())

    def test_it_lists_the_index_project_name_uptime_and_link(self):
        self.place("aa11aa", project="beacon", name="beacon-aa11",
                   started=int(time.time()) - 3600, url=LINK)
        text = self.ls()
        first = text.splitlines()[0]
        self.assertTrue(first.startswith("1."),
                        "§5 numbers them, and `stop 1` takes that number: %r" % first)
        self.assertIn("beacon", first)
        self.assertIn("beacon-aa11", first)
        self.assertIn("1h", first)
        self.assertIn(LINK, text.splitlines(),
                      "§5: the link on a line of its own, or a thumb cannot take it")

    def test_the_index_follows_the_order_the_sessions_started(self):
        # Not the order the directory happens to be read in, which is arbitrary and changes
        # under you: `stop 2` a minute after an `ls` has to mean the same session it meant.
        now = int(time.time())
        self.place("bb22bb", project="centrion", name="centrion-bb22", started=now - 60)
        self.place("aa11aa", project="beacon", name="beacon-aa11", started=now - 3600)
        lines = [line for line in self.ls().splitlines() if line[:2] in ("1.", "2.")]
        self.assertEqual(len(lines), 2, "two sessions, two numbered lines")
        self.assertIn("beacon-aa11", lines[0], "the oldest session is 1")
        self.assertIn("centrion-bb22", lines[1])

    def test_a_session_still_starting_is_listed_without_a_link_it_does_not_have(self):
        # It is real, it is holding a slot against the cap, and `stop` must be able to reach
        # it — §9.10's orphan is exactly a session nobody could see.
        self.place("aa11aa", session.STARTING, name="beacon-aa11", url=None)
        text = self.ls()
        self.assertIn("beacon-aa11", text)
        self.assertNotIn("None", text)
        self.assertNotIn("claude.ai/code", text)

    def test_a_session_that_has_ended_or_failed_is_not_listed(self):
        self.place("aa11aa", session.ENDED, name="beacon-aa11")
        self.place("bb22bb", session.FAILED, name="beacon-bb22")
        self.place("cc33cc", session.LIVE, name="beacon-cc33")
        text = self.ls()
        self.assertIn("beacon-cc33", text)
        self.assertNotIn("beacon-aa11", text)
        self.assertNotIn("beacon-bb22", text)

    def test_an_unreadable_record_does_not_hide_the_ones_beside_it(self):
        """§4 has read_meta returning None rather than raising for exactly this reason, and
        this is the consequence it was protecting: one truncated record must not be able to
        hide a live bypass-permissions session from the only listing anybody has."""
        self.place("aa11aa", name="beacon-aa11")
        broken = self.sessions.directory("bad999")
        os.makedirs(broken)
        with open(os.path.join(broken, "meta.json"), "w") as fh:
            fh.write("{not json at a")
        os.makedirs(self.sessions.directory("none00"))       # no record in it at all
        text = self.ls()
        self.assertIn("beacon-aa11", text)
        self.assertTrue(text.startswith("1."), text)

    def test_a_listing_too_long_for_telegram_is_truncated_rather_than_rejected(self):
        """§7: 4096 is a 400 from the API and not a truncation, and it would arrive precisely
        on the tick where there was most to say."""
        for i in range(60):
            self.place("%04dff" % i, name="beacon-%04d" % i,
                       project="a-project-with-a-name-long-enough-to-matter-%d" % i)
        tg = self.deliver(message("ls"))
        self.assertEqual(len(tg.sent), 1)
        self.assertLessEqual(len(tg.texts[0]), telegram.LIMIT)

    def test_the_listing_carries_no_path_from_this_machine(self):
        # §7/§10: Telegram is not end-to-end encrypted, and `cwd` is right there in every
        # record this listing is built from.
        self.place("aa11aa", cwd=os.path.join(bot.HOME, "Projects", "beacon"))
        self.assertNotIn(bot.HOME, self.ls())


class TestStopping(Base):
    """§5 tier 2, and §9.10/§9.11 are why it has this many tests.

    `stop` exists so that a session cannot outlive your attention, which makes "reported
    stopped, still running" the one failure it must never have — and slice 7 shipped exactly
    that, twice, in two disguises, both of which reported success.
    """

    def stop(self, text, **kw):
        tg = FakeTelegram([message(text)])
        listener = self.listener(tg, **kw)
        listener.tick()
        self.settle(listener)
        return tg

    def two(self):
        now = int(time.time())
        self.place("aa11aa", name="beacon-aa11", runner_pid=4001, started=now - 3600)
        self.place("bb22bb", name="centrion-bb22", project="centrion", runner_pid=4002,
                   started=now - 60)
        return "aa11aa", "bb22bb"

    def stopped(self):
        return [sid for sid, _ in self.sessions.stopped]

    def test_an_index_signals_only_that_runner(self):
        _, second = self.two()
        self.stop("stop 2")
        self.assertEqual(self.stopped(), [second])

    def test_stop_all_signals_every_one(self):
        first, second = self.two()
        self.stop("stop all")
        self.assertEqual(sorted(self.stopped()), sorted([first, second]))

    def test_the_index_is_the_one_the_listing_showed(self):
        first, _ = self.two()
        listing = self.listener().answer(commands.parse("ls"))
        self.stop("stop 1")
        self.assertIn(self.sessions.read(first)["name"], listing.splitlines()[0])
        self.assertEqual(self.stopped(), [first])

    def test_an_index_past_the_end_is_a_clean_error(self):
        self.two()
        tg = self.stop("stop 9")
        self.assertEqual(self.sessions.stopped, [], "a miss must signal nothing at all")
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("2", tg.texts[0], "say how many there are, so the next message is right")

    def test_stopping_when_there_is_nothing_to_stop_is_an_answer_and_not_a_crash(self):
        empty = {}
        for text in ("stop 1", "stop all"):
            tg = self.stop(text)
            self.assertEqual(len(tg.sent), 1, text)
            self.assertTrue(tg.texts[0].strip(), text)
            self.assertEqual(self.sessions.stopped, [], text)
            empty[text] = tg.texts[0]
        self.two()
        self.assertNotEqual(self.stop("stop all").texts[0], empty["stop all"],
                            "an empty fleet and a stopped one cannot get the same answer")

    def test_the_runner_is_given_more_time_than_the_runner_gives_claude(self):
        """§9.11, and the subtlest thing in the spec: ending a runner is two kills in
        sequence, not one. The runner catches SIGTERM and then spends up to its own GRACE
        ending claude — over five seconds for a real session on this box. A caller that allows
        it the same GRACE SIGKILLs it in the middle of that, orphaning the session and leaving
        meta.json saying `live` for something that is already on its way out.
        """
        self.two()
        self.stop("stop 1")
        (_, grace), = self.sessions.stopped
        self.assertIsNotNone(grace, "stop must say how long it is prepared to wait")
        self.assertGreater(grace, session.GRACE)

    def test_a_stopped_session_is_recorded_as_ended(self):
        first, _ = self.two()
        self.stop("stop 1")
        self.assertEqual(self.sessions.read(first)["state"], session.ENDED)

    def test_a_stopped_session_does_not_come_back_as_an_unprompted_notice(self):
        """§4's pass pushes a record that has newly ended, which is right for a session that
        died on its own and wrong for one the phone just asked to stop: the answer has already
        been sent, and a second message a minute later reads like it came back."""
        self.two()
        tg = self.stop("stop 1")
        self.assertEqual(len(tg.sent), 1)
        after = FakeTelegram([])
        self.listener(after).tick()
        self.assertEqual(after.sent, [], "the stop was the answer; the tick must not repeat it")

    def test_a_runner_that_will_not_die_is_not_recorded_as_stopped(self):
        """§9.10 and §9.11 are one failure in two disguises, and both reported success: the
        reply says stopped and the session is still there with permissions bypassed."""
        first, second = self.two()
        self.sessions.unstoppable.add(4001)
        tg = self.stop("stop all")
        self.assertEqual(self.sessions.read(first)["state"], session.LIVE,
                         "a session that would not die must not be recorded as ended")
        self.assertEqual(self.sessions.read(second)["state"], session.ENDED,
                         "and the one that did die must still be stopped")
        self.assertIn("beacon-aa11", tg.texts[0])
        self.assertIn("centrion-bb22", tg.texts[0])

    def test_a_session_that_would_not_die_is_still_listed(self):
        first, _ = self.two()
        self.sessions.unstoppable.add(4001)
        self.stop("stop 1")
        self.assertIn("beacon-aa11", self.listener().answer(commands.parse("ls")))

    def test_the_reply_says_the_session_can_be_reattached(self):
        """§9.4: killing the PTY leaves the remote session registered but offline — it stays
        in the claude.ai/code list without the green dot, and `claude --continue` in that
        directory picks it up for about four hours. §9.4 says `stop` should say so, because
        the alternative is assuming the work went with it."""
        self.two()
        self.assertIn("--continue", self.stop("stop 1").texts[0])

    def test_a_session_that_has_already_ended_is_not_signalled_again(self):
        self.place("cc33cc", session.ENDED, runner_pid=4003)
        self.place("dd44dd", session.LIVE, runner_pid=4004)
        self.stop("stop all")
        self.assertEqual(self.stopped(), ["dd44dd"],
                         "`stop all` signals what is running, and only what is running")


class TestTheCap(Base):
    """§10.6: `Without a cap at all, a held-down claude fills RAM with Claude Code processes.`

    §3 owns the number and §15 records why it is 2 on this box. Nothing here restates it:
    §10.6 used to carry its own copy and it drifted to 4.
    """

    def capped(self, n):
        self.cfg = config.Config(TOKEN, frozenset([ME]), self.projects, "/bin/echo", n)

    def start(self, text="claude beacon", **kw):
        tg = FakeTelegram([message(text)])
        kw.setdefault("timeout", 0.05)
        kw.setdefault("poll_every", 0.01)
        kw.setdefault("sleep", time.sleep)
        listener = self.listener(tg, **kw)
        listener.tick()
        self.settle(listener)
        return tg

    def full(self):
        self.place("aa11aa", name="beacon-aa11", runner_pid=4001)
        self.place("bb22bb", name="beacon-bb22", runner_pid=4002)

    def test_a_spawn_past_the_cap_is_refused_with_a_count(self):
        self.full()
        tg = self.start()
        self.assertEqual(self.sessions.started, [], "the cap is not a warning")
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("2", tg.texts[0])

    def test_the_refusal_says_what_to_do_about_it(self):
        # A number on its own is a dead end on a phone, and the reply is the only place `ls`
        # and `stop` are ever mentioned to somebody who is not reading §5.
        self.full()
        self.assertIn("stop", self.start().texts[0].lower())

    def test_the_cap_is_the_configured_one(self):
        self.capped(1)
        self.place("aa11aa", runner_pid=4001)
        tg = self.start()
        self.assertEqual(self.sessions.started, [])
        self.assertIn("1", tg.texts[0])

    def test_a_session_that_is_still_starting_counts_against_it(self):
        """The cap is about memory, and a session that is starting has already paid for its
        Claude Code process. §4.1 checks the count before it mints, not after it connects."""
        self.capped(1)
        self.place("aa11aa", session.STARTING, runner_pid=4001, url=None)
        self.start()
        self.assertEqual(self.sessions.started, [])

    def test_a_session_that_has_ended_does_not_count_against_it(self):
        self.capped(1)
        self.place("aa11aa", session.ENDED, runner_pid=4001)
        self.start()
        self.assertEqual(len(self.sessions.started), 1,
                         "an ended session must not hold a slot")
        self.start()
        self.assertEqual(len(self.sessions.started), 1,
                         "but the one just started does hold one")

    def test_a_spawn_the_runner_has_not_recorded_yet_still_counts(self):
        """The hole a count taken off the filesystem alone would leave, and §10.6 names the
        message that finds it: a held-down `claude`. §4.2 has the listener returning to the
        poll the moment it has forked, so for the first fraction of a second a session exists
        as a process and as nothing else — and the next message in the same batch counts a
        fleet that does not include it yet."""
        self.capped(1)
        self.sessions.outcome = None          # the runner has not written its record yet
        tg = FakeTelegram([message("claude beacon", uid=1),
                           message("claude centrion", uid=2)])
        listener = self.listener(tg, timeout=0.05, poll_every=0.01, sleep=time.sleep)
        listener.tick()
        self.settle(listener)
        self.assertEqual(len(self.sessions.started), 1,
                         "two sessions were started against a cap of one")

    def test_the_reply_that_starts_one_says_how_many_are_running(self):
        """§5's third line, in full: `bypass permissions on · 2 of 2 sessions`. On a phone it
        is the only warning that the next `claude` will be refused."""
        self.place("aa11aa", runner_pid=4001)
        self.assertIn("2 of 2", self.start().texts[-1])


class TestASecondSessionInTheSameDirectory(Base):
    """§5 allows it and flags it. §9.9 is why the flag is harder than it looks.

    `os.path.realpath` is lexical and this volume is case-insensitive, so `beacon` and
    `BEACON` are one directory reached through two strings that compare unequal — and §5
    contains exactly one comparison, this one. Compared with `==` the warning silently never
    fires in the one case it exists for.
    """

    def setUp(self):
        Base.setUp(self)
        # Room to reach the case under test: this is about §9.9's comparison, and §10.6's cap
        # would otherwise refuse the third session before the warning could be composed.
        self.cfg = config.Config(TOKEN, frozenset([ME]), self.projects, "/bin/echo", 5)

    def start(self, text, **kw):
        tg = FakeTelegram([message(text)])
        kw.setdefault("timeout", 0.05)
        kw.setdefault("poll_every", 0.01)
        kw.setdefault("sleep", time.sleep)
        listener = self.listener(tg, **kw)
        listener.tick()
        self.settle(listener)
        return tg

    def beacon(self, **fields):
        self.place("aa11aa", cwd=os.path.join(self.projects, "beacon"), runner_pid=4001,
                   **fields)

    def test_the_second_session_in_a_directory_is_flagged(self):
        self.beacon()
        self.assertIn("⚠", self.start("claude beacon").texts[-1])

    def test_the_first_session_in_a_directory_is_not(self):
        """Started through the listener rather than placed, because that is the sequence this
        warning actually has to survive: one `claude beacon`, then another."""
        self.assertNotIn("⚠", self.start("claude beacon").texts[-1])
        self.assertIn("⚠", self.start("claude beacon").texts[-1],
                      "the second session in that directory is the one to flag")

    def test_a_differently_cased_name_is_still_the_same_directory(self):
        """§9.9 pinned from the other end. `claude BEACON` starts a real session in beacon's
        directory under a path string no directory on disk has; both sessions are in one
        repository and will happily edit the same files underneath each other, which is the
        entire thing the warning is for."""
        if not os.path.exists(os.path.join(self.projects, "BEACON")):
            self.skipTest("this volume is case-sensitive, so §9.9 does not arise on it")
        self.beacon()
        self.assertIn("⚠", self.start("claude BEACON").texts[-1])

    def test_the_warning_does_not_refuse_the_session(self):
        """§5: refusing would be wrong — two sessions on one repo is a normal way to work,
        and anything more than the one line belongs to git rather than to this bot."""
        self.beacon()
        tg = self.start("claude beacon")
        self.assertEqual(len(self.sessions.started), 1)
        self.assertIn(LINK, tg.texts[-1])

    def test_a_directory_whose_session_has_ended_is_not_a_second_session(self):
        self.beacon(state=session.ENDED)
        self.assertNotIn("⚠", self.start("claude beacon").texts[-1])
        self.place("bb22bb", cwd=os.path.join(self.projects, "beacon"), runner_pid=4002)
        self.assertIn("⚠", self.start("claude beacon").texts[-1],
                      "a live one in the same directory still is")

    def test_a_record_naming_a_directory_that_has_gone_does_not_break_the_comparison(self):
        """`os.path.samefile` raises rather than returning False when a path is not there,
        and a record naming a directory since renamed or deleted is an ordinary thing to find
        in var/sessions after a week."""
        self.place("aa11aa", cwd=os.path.join(self.projects, "renamed-last-tuesday"),
                   runner_pid=4001)
        self.assertIn(LINK, self.start("claude beacon").texts[-1])


class TestWhetherARunnerIsStillThere(unittest.TestCase):
    """§4: `Verify the runner pid with os.kill(pid, 0)` — and then do not trust it alone.

    `pid reuse is theoretically possible between reboots; started is in the record, so compare
    it against the process start time before trusting a pid that is alive.` Not hypothetical
    here: two records survived a reboot on this box still saying `live`, and the pids they
    name are ordinary four-digit numbers that a fresh boot hands out inside a minute.
    """

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions = bot.Sessions(root=os.path.join(self.tmp, "sessions"),
                                     log=lambda m: None)

    def child(self):
        p = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    def test_a_running_process_is_alive(self):
        p = self.child()
        self.assertTrue(self.sessions.alive({"runner_pid": p.pid,
                                             "started": int(time.time())}))

    def test_a_process_that_has_exited_is_not(self):
        p = subprocess.Popen(["/usr/bin/true"])
        p.wait()
        self.assertFalse(self.sessions.alive({"runner_pid": p.pid,
                                              "started": int(time.time())}))

    def test_a_pid_that_has_been_reused_is_not_the_runner(self):
        """The record predates the process, so whatever answers to that pid now is somebody
        else. After a reboot that is true of every pid in every record."""
        p = self.child()
        self.assertFalse(self.sessions.alive({"runner_pid": p.pid, "started": 1}))

    def test_a_process_that_belongs_to_somebody_else_is_still_a_process(self):
        # EPERM is not death. session.py's _reaped() makes the same distinction for the same
        # reason: "not permitted" means it is there.
        self.assertTrue(self.sessions.alive({"runner_pid": 1, "started": int(time.time())}))

    def test_a_record_with_no_usable_pid_is_not_alive(self):
        """0 and -1 are the two that matter, and not because they are unlikely: `kill(0, sig)`
        signals this process's whole group and `kill(-1, sig)` signals every process this user
        owns. A record corrupted into either one turns the reconciliation pass — and then
        `stop all` — into something that takes the daemon and the desktop with it."""
        for value in (None, 0, -1, "4001", True, 2 ** 62, 1.5):
            self.assertFalse(self.sessions.alive({"runner_pid": value,
                                                  "started": int(time.time())}), repr(value))

    def test_a_record_with_no_start_time_falls_back_to_the_pid_alone(self):
        """Every record §3 writes has `started`, so this is a corrupt one — and of the two
        ways to be wrong about it, showing a session that may not exist is recoverable and
        hiding one that does is not. `ls` is the only view of a bypass-permissions session
        this machine offers."""
        p = self.child()
        self.assertTrue(self.sessions.alive({"runner_pid": p.pid}))

    def test_the_start_time_is_read_from_the_process_table(self):
        before = time.time()
        p = self.child()
        started = bot.process_started(p.pid)
        self.assertIsNotNone(started)
        # `ps` reports whole seconds, hence the slack at both ends.
        self.assertGreaterEqual(started, before - 2)
        self.assertLessEqual(started, time.time() + 2)

    def test_a_pid_that_is_not_there_has_no_start_time(self):
        p = subprocess.Popen(["/usr/bin/true"])
        p.wait()
        self.assertIsNone(bot.process_started(p.pid))


class TestReconciliation(Base):
    """§4: `Every return from getUpdates, message or not, is the tick.`

    The listener is otherwise purely reactive — it sits in getUpdates and acts only on
    messages — so without this pass it never notices a session ending or a runner dying. This
    box has already shown what that costs: two records still saying `live` this morning for
    processes a reboot took three hours earlier, holding the whole of `max_sessions` against a
    bot that had no idea either had happened.
    """

    def ticked(self, *batches):
        tg = FakeTelegram(*(batches or ([],)))
        listener = self.listener(tg)
        listener.tick()
        return tg, listener

    def test_a_live_record_whose_runner_is_gone_is_marked_ended(self):
        self.place("aa11aa", runner_pid=4001)
        self.sessions.dead.add(4001)
        self.ticked()
        self.assertEqual(self.sessions.read("aa11aa")["state"], session.ENDED)

    def test_a_record_whose_runner_is_alive_is_left_exactly_as_it_was(self):
        before = self.place("bb22bb", runner_pid=4002)
        self.ticked()
        self.assertEqual(self.sessions.read("bb22bb"), before)

    def test_the_pass_runs_on_a_tick_that_carried_no_message(self):
        """§4 says the tick is every *return* from getUpdates, and an idle bot returns from
        one every fifty seconds forever. A pass that ran only when somebody messaged would
        mean a session that ended at midnight is announced when you say good morning."""
        self.place("aa11aa", runner_pid=4001)
        self.sessions.dead.add(4001)
        tg, _ = self.ticked([])
        self.assertEqual(self.sessions.read("aa11aa")["state"], session.ENDED)
        self.assertEqual(len(tg.sent), 1)

    def test_a_newly_ended_record_is_pushed_to_the_chat_that_started_it(self):
        """§4: `This is the only message the bot sends unprompted.` `chat_id` is in the record
        for exactly this, and it is not necessarily the chat that is messaging now."""
        self.place("aa11aa", runner_pid=4001, chat_id=ME, name="beacon-aa11")
        self.sessions.dead.add(4001)
        tg, _ = self.ticked()
        self.assertEqual(len(tg.sent), 1)
        chat, text = tg.sent[0]
        self.assertEqual(chat, ME)
        self.assertIn("beacon-aa11", text)
        # It says when the session *started*, not how long it ran. This pass is the only thing
        # that notices a session a reboot took, and it cannot know when that happened — the
        # two records on this box would have been reported as eleven hours of work watched,
        # three of which the machine spent switched off.
        self.assertIn("started", text)

    def test_it_is_not_pushed_again_on_the_next_tick(self):
        self.place("aa11aa", runner_pid=4001)
        self.sessions.dead.add(4001)
        tg = FakeTelegram([], [])
        listener = self.listener(tg)
        listener.tick()
        listener.tick()
        self.assertEqual(len(tg.sent), 1)

    def test_it_is_not_pushed_again_after_a_restart(self):
        """A marker held in memory passes the test above and fails this one, and the failure
        arrives as every session that ever ended announcing itself again — at every login,
        because launchd restarts this daemon at boot."""
        self.place("aa11aa", runner_pid=4001)
        self.sessions.dead.add(4001)
        self.listener(FakeTelegram([])).tick()
        tg = FakeTelegram([])
        self.listener(tg).tick()
        self.assertEqual(tg.sent, [])

    def test_a_session_that_ended_on_its_own_is_pushed_too(self):
        """The runner writes `ended` itself when claude exits, so this record arrives already
        terminal with nothing left to mark — the push is still owed."""
        self.place("aa11aa", session.ENDED, runner_pid=4001, name="beacon-aa11")
        self.sessions.dead.add(4001)
        tg, _ = self.ticked()
        self.assertEqual(len(tg.sent), 1)
        self.assertIn("beacon-aa11", tg.texts[0])

    def test_a_reboot_leaves_no_phantoms_and_gives_the_cap_back(self):
        """The exact state this box was in on the morning of 2026-09-13: two records saying
        `live`, both runners taken by a reboot three hours earlier, `max_sessions` entirely
        consumed by sessions that did not exist. Without the cap it was invisible; with the
        cap and without this pass it is a bot that refuses to start anything, permanently,
        and says `2 of 2` while doing it.

        Note what this also pins: the pass runs *before* the batch is dispatched. Reconciling
        after would answer this message against the fleet that a reboot already emptied.
        """
        self.place("aa11aa", runner_pid=4001, chat_id=ME, name="beacon-aa11")
        self.place("bb22bb", runner_pid=4002, chat_id=ME, name="beacon-bb22")
        self.sessions.dead.update({4001, 4002})
        tg = FakeTelegram([message("claude beacon")])
        listener = self.listener(tg, timeout=0.05, poll_every=0.01, sleep=time.sleep)
        listener.tick()
        self.settle(listener)
        self.assertEqual(len(self.sessions.started), 1, "the cap was never given back")
        self.assertNotIn("beacon-aa11", self.listener().answer(commands.parse("ls")))

    def test_an_unreadable_record_does_not_stop_the_pass(self):
        broken = self.sessions.directory("bad999")
        os.makedirs(broken)
        with open(os.path.join(broken, "meta.json"), "w") as fh:
            fh.write("{")
        self.place("aa11aa", runner_pid=4001)
        self.sessions.dead.add(4001)
        self.ticked()
        self.assertEqual(self.sessions.read("aa11aa")["state"], session.ENDED)

    def test_a_record_with_no_chat_to_tell_is_still_marked_ended(self):
        self.place("aa11aa", runner_pid=4001, chat_id=None)
        self.sessions.dead.add(4001)
        tg, _ = self.ticked()
        self.assertEqual(self.sessions.read("aa11aa")["state"], session.ENDED)
        self.assertEqual(tg.sent, [])

    def test_a_push_that_does_not_land_is_not_retried_forever(self):
        """At-most-once, the same side bot.py errs on for the offset: a send that failed is a
        line in §14's log, not a message that arrives forty times over an afternoon because
        the network happened to be down when it was first tried."""
        self.place("aa11aa", runner_pid=4001)
        self.sessions.dead.add(4001)
        tg = FakeTelegram([], [])
        tg.results = [False]
        listener = self.listener(tg)
        listener.tick()
        listener.tick()
        self.assertEqual(len(tg.sent), 1)
        self.assertTrue(self.logged, "§14 has to be able to say the push was lost")

    def test_a_reconciliation_that_raises_does_not_take_the_tick_down(self):
        """§7: the loop never dies — and this pass now runs on every tick, so a bug in it is
        a bot that stops answering the phone at all."""
        def boom(*_a, **_kw):
            raise RuntimeError("var is on fire")
        self.sessions.records = boom
        tg = FakeTelegram([message("help")])
        listener = self.listener(tg)
        listener.tick()
        self.assertEqual(len(tg.sent), 1,
                         "the message in the same batch must still be answered")
        self.assertTrue(any("var is on fire" in line for line in self.logged),
                        "swallowed silently, §14 sends you to a log that says nothing")

    def test_the_listener_writes_no_record_it_does_not_own(self):
        """The runner owns meta.json — §3 has it writing atomically so that a listener reading
        mid-write never sees half a record — and there is no lock between the two processes.
        The listener may write one only once it has established there is nobody on the other
        side, which is precisely what this pass establishes. Anything else is a
        read-modify-write race against a process that is still running."""
        before = self.place("aa11aa", session.STARTING, runner_pid=4001, url=None)
        gone = self.place("bb22bb", session.STARTING, runner_pid=4002, url=None)
        self.sessions.dead.add(4002)
        self.ticked()
        self.assertEqual(self.sessions.read("aa11aa"), before,
                         "its runner is alive, so the record is not the listener's to write")
        self.assertNotEqual(self.sessions.read("bb22bb"), gone,
                            "and once the runner is gone, it is")


class TestALateLinkIsStillAnnounced(Base):
    """§9.12 — the only entry in §9 that no test could have found.

    Two sessions started three minutes apart both sat past the 45s deadline and were answered
    with the timeout tail; both then reached `live` within one second of each other,
    sixty-eight minutes later. meta.json had a working link in it and nobody was ever told.
    **The delay is not the defect. The silence is** — no deadline covers 68 minutes, so the
    same pass that announces a session which has ended announces one that has finally arrived.
    """

    def test_a_session_that_came_up_after_its_waiter_gave_up_is_announced(self):
        self.place("aa11aa", announced=False, runner_pid=4001, name="beacon-aa11",
                   chat_id=ME, url=LINK)
        tg = FakeTelegram([])
        self.listener(tg).tick()
        self.assertEqual(len(tg.sent), 1)
        chat, text = tg.sent[0]
        self.assertEqual(chat, ME)
        self.assertIn(LINK, text.splitlines())
        self.assertIn("beacon-aa11", text)

    def test_two_that_come_up_together_are_both_announced(self):
        # What actually happened: one external trigger, most likely the network, released both
        # within a second of each other.
        self.place("aa11aa", announced=False, runner_pid=4001, name="beacon-aa11")
        self.place("bb22bb", announced=False, runner_pid=4002, name="beacon-bb22")
        tg = FakeTelegram([])
        self.listener(tg).tick()
        self.assertEqual(len(tg.sent), 2)

    def test_it_is_announced_once_and_not_on_every_tick(self):
        """A `live` record stays `live` for hours, so unlike an ended one its state is not its
        own marker. This is the announcement that needs something written down."""
        self.place("aa11aa", announced=False, runner_pid=4001)
        tg = FakeTelegram([], [], [])
        listener = self.listener(tg)
        for _ in range(3):
            listener.tick()
        self.assertEqual(len(tg.sent), 1)

    def test_it_is_not_announced_again_after_a_restart(self):
        self.place("aa11aa", announced=False, runner_pid=4001)
        first = FakeTelegram([])
        self.listener(first).tick()
        self.assertEqual(len(first.sent), 1, "it is announced once")
        tg = FakeTelegram([])
        self.listener(tg).tick()
        self.assertEqual(tg.sent, [], "and a restarted listener does not announce it again")

    def test_a_session_its_waiter_answered_is_not_announced_again(self):
        """The ordinary case, and the one this must not break: §4.6's waiter sent the link
        three seconds after the spawn and the tick behind it has nothing to add."""
        tg = FakeTelegram([message("claude beacon")], [], [])
        listener = self.listener(tg, timeout=0.05, poll_every=0.01, sleep=time.sleep)
        listener.tick()
        self.settle(listener)
        listener.tick()
        self.assertEqual(len(tg.sent), 1, "the waiter answered; the tick has nothing to add")
        # And the distinction being drawn is a real one: the same record, unclaimed, is
        # exactly what §9.12 says must be announced.
        self.place("bb22bb", announced=False, runner_pid=4002, name="beacon-bb22")
        listener.tick()
        self.assertEqual(len(tg.sent), 2)

    def test_a_session_that_is_still_starting_is_not_announced(self):
        # The ten to twenty seconds §4 budgets, during which its waiter is still watching and
        # there is nothing to say. The tick speaks only once the record does.
        self.place("aa11aa", session.STARTING, announced=False, runner_pid=4001, url=None)
        tg = FakeTelegram([], [])
        listener = self.listener(tg)
        listener.tick()
        self.assertEqual(tg.sent, [])
        self.place("aa11aa", session.LIVE, announced=False, runner_pid=4001, url=LINK)
        listener.tick()
        self.assertEqual(len(tg.sent), 1, "and once it says live, it is announced")

    def test_a_live_record_with_no_link_in_it_is_not_announced(self):
        # §3 writes the state and the url in one atomic record, so this should be impossible —
        # which is why it should not be trusted to be. Slice 7 has the same test on the waiter.
        self.place("aa11aa", session.LIVE, announced=False, runner_pid=4001, url=None)
        tg = FakeTelegram([], [])
        listener = self.listener(tg)
        listener.tick()
        self.assertEqual(tg.sent, [], "a link that is not there cannot be sent")
        self.place("aa11aa", session.LIVE, announced=False, runner_pid=4001, url=LINK)
        listener.tick()
        self.assertEqual(len(tg.sent), 1, "and one that is there must be")

    def test_the_announcement_says_how_long_it_took(self):
        """Sixty-eight minutes after a message saying it had failed. An announcement that read
        like a fresh `▶` would leave the phone holding two contradictory messages about one
        session and no way to tell which one is current."""
        self.place("aa11aa", announced=False, runner_pid=4001,
                   started=int(time.time()) - 68 * 60)
        tg = FakeTelegram([])
        self.listener(tg).tick()
        self.assertIn("1h", tg.texts[0])

    def test_only_one_of_the_waiter_and_the_tick_can_claim_a_session(self):
        """Both run at once — §4.2 put the waiter on its own thread precisely so it would —
        and the 45s deadline falls inside the ≤50s tick interval, so the window in which both
        are looking at the same live record is not a narrow one. The claim is the arbitration
        and it has to be the filesystem's to make: two threads now, two processes after a
        restart, and no lock anywhere between them."""
        self.place("aa11aa", announced=False)
        self.assertTrue(self.sessions.claim("aa11aa", bot.LINK_SENT))
        self.assertFalse(self.sessions.claim("aa11aa", bot.LINK_SENT))

    def test_the_link_and_the_ending_are_claimed_separately(self):
        # A session announces its link when it arrives and its ending when it ends. One must
        # never spend the other's claim.
        self.place("aa11aa", announced=False)
        self.assertTrue(self.sessions.claim("aa11aa", bot.LINK_SENT))
        self.assertTrue(self.sessions.claim("aa11aa", bot.END_SENT))


if __name__ == "__main__":
    unittest.main()
