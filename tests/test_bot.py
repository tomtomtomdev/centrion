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

Slice 5 replies with an echo and starts nothing, which is deliberate (§12) — the launchd
install lands here precisely so that the first thing to run unattended is harmless if the
allowlist is wrong. `TestNothingDangerousIsReachableYet` is that claim, asserted rather than
promised.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import os
import plistlib
import shutil
import stat
import tempfile
import unittest

import bot
import commands
import config
import telegram

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Shaped like the real thing, and asserted never to escape — same fixture as test_telegram.py.
TOKEN = "8960211893:AAHreallyNotTheRealTokenJustAFake_x"
SECRET = TOKEN.split(":")[1]

ME = 1908330607          # the one allowlisted chat
STRANGER = 5550001234    # someone who found the bot
GROUP = -1001234567890   # a supergroup someone added it to

START = 1789198400       # the daemon's start time in every test, so staleness is arithmetic


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

        self.cfg = config.Config(TOKEN, frozenset([ME]), self.projects, "/bin/echo", 2)
        self.tg = FakeTelegram()
        self.logged = []
        self.slept = []

    def listener(self, tg=None, **kw):
        kw.setdefault("started", START)
        return bot.Listener(self.cfg, tg or self.tg, offset_path=self.offset_path,
                            log=self.logged.append, sleep=self.slept.append, **kw)

    def deliver(self, *updates):
        """Hand `updates` to a listener as one batch and return what it replied."""
        tg = FakeTelegram(list(updates))
        listener = self.listener(tg)
        listener.tick()
        return tg

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
            self.listener(tg).tick()
            self.assertEqual(len(tg.sent), 1, "no reply to %.30r" % (text,))


class TestWhatItSaysBack(Base):
    """SPEC.md §5. Slice 5 echoes: the verbs are understood, and none of them acts."""

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
        self.assertIn("fix the failing probe test", got)

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

    def test_every_reply_says_that_nothing_starts_yet(self):
        # The echo has to be unmistakable on a phone, or the manual step in §12 cannot tell a
        # working listener from a working launcher.
        for text in ("claude", "claude beacon", "ls", "stop all", "help", "nonsense"):
            self.assertIn(bot.NOT_YET, self.reply_to(text))

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

    def test_a_long_prompt_is_clipped_rather_than_quoted_whole(self):
        got = bot.clip("x" * 5000)
        self.assertLessEqual(len(got), bot.PROMPT_ECHO)
        self.assertTrue(got.endswith("…"))

    def test_a_short_prompt_is_echoed_verbatim(self):
        # §9.9 and commands.py: a prompt is English and reaches its destination as sent.
        self.assertEqual(bot.clip("fix the failing probe test"), "fix the failing probe test")


class TestNothingDangerousIsReachableYet(Base):
    """§12: the launchd install is in slice 5 so the first unattended thing is harmless.

    That claim is only worth making if it is checked. A `claude beacon` at this commit resolves
    a directory and says so; it does not fork, it does not exec, and bot.py does not so much as
    import the module that will one day do both.
    """

    def test_the_listener_does_not_import_the_runner(self):
        self.assertFalse(hasattr(bot, "session"),
                         "bot.py imports session — that is slice 7's wiring, not slice 5's")

    def test_the_listener_cannot_spawn_anything(self):
        with open(os.path.join(ROOT, "bot.py")) as fh:
            source = fh.read()
        for dangerous in ("subprocess", "os.fork", "os.exec", "os.spawn", "os.system",
                          "popen", "pty."):
            self.assertNotIn(dangerous, source, "bot.py reaches for %s" % dangerous)


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
