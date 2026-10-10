#!/usr/bin/env python3
"""Slice 19 — the gate: with `totp_secret` set, nothing starts, ends or changes without a code.

The listener is the real one over slice 7's fakes; only the TOTP clock and the monotonic clock
are wound by hand, so a fifteen-minute grant and a two-minute pending slot are arithmetic. The
property under every test is the one SPEC.md §12 slice 17 opens with: an allowlisted Telegram
account is no longer enough on its own, and a code read off the chat is no use a second time.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import json
import os
import unittest

import bot
import commands
import config
import totp
from tests.support import POSIX
from tests.test_bot import (LINK, ME, START, TOKEN, Base, Clock, FakeTelegram,
                            message)

SECRET = bytes(range(20))
OTHER = 1908330999           # a second allowlisted chat
WALL = 1789198410            # the TOTP clock: ten seconds after the daemon started
STEP = WALL // 30


def code(step=STEP):
    return totp.code(SECRET, step)


def wrong(step=STEP):
    return "%06d" % ((int(code(step)) + 1) % 10 ** 6)


class GateTelegram(FakeTelegram):
    """slice 7's fake, plus the one call the gate adds."""

    def __init__(self, *batches):
        FakeTelegram.__init__(self, *batches)
        self.deleted = []
        self.delete_result = True

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return self.delete_result


class Gated(Base):
    def setUp(self):
        Base.setUp(self)
        self.cfg = config.Config(TOKEN, frozenset([ME, OTHER]), self.projects, "/bin/echo", 2,
                                 totp_secret=SECRET, unlock_minutes=15)
        self.clock = Clock(1000.0)
        self.wall = WALL
        self.powered = []
        self.uid = 100

    def build(self, tg=None):
        self.tg = tg or GateTelegram()
        self.l = self.listener(self.tg, clock=self.clock.time, wall=lambda: self.wall,
                               claimable=lambda: [],
                               power_sh=lambda argv: self.powered.append(argv))
        return self.l

    def send(self, text, chat=ME):
        """One message through the listener built last, and what it replied."""
        self.uid += 1
        before = len(self.tg.sent)
        self.tg.batches.append([message(text, chat=chat, sender=chat, uid=self.uid)])
        self.l.tick()
        self.settle(self.l)
        return self.tg.texts[before:]

    def unlock(self, chat=ME, step=STEP):
        replies = self.send(code(step), chat=chat)
        self.assertTrue(any("🔓" in r for r in replies), replies)
        return replies


class TestWithoutASecretNothingChanges(Base):
    def test_six_digits_are_still_help_and_lock_is_still_help(self):
        for text in ("123456", "lock"):
            tg = self.deliver(message(text))
            self.assertIn("ls ", tg.texts[0])
            self.assertNotIn("🔒", tg.texts[0])

    def test_the_menu_has_no_lock(self):
        tg = FakeTelegram([])
        with self.assertRaises(BaseException):
            self.listener(tg).run()
        self.assertNotIn(commands.LOCK, [verb for verb, _ in tg.menus[0]])


class TestLocked(Gated):
    def test_every_gated_verb_is_answered_with_the_lock_and_does_nothing(self):
        self.build()
        self.place("aaaaaa")
        for text in ("claude beacon", "claude beacon fix it", "new scratchpad", "rc 1",
                     "stop 1", "stop all", "power cancel", "power set",
                     "claude .none beacon"):
            with self.subTest(text=text):
                replies = self.send(text)
                self.assertEqual(len(replies), 1)
                self.assertIn("🔒", replies[0])
        self.assertEqual(self.sessions.started, [])
        self.assertEqual(self.sessions.stopped, [])
        self.assertEqual(self.powered, [])
        self.assertFalse(os.path.exists(os.path.join(self.projects, "scratchpad")))

    def test_the_open_verbs_answer_as_before(self):
        self.build()
        for text in ("help", "claude", "ls", "rc", "power"):
            with self.subTest(text=text):
                replies = self.send(text)
                self.assertEqual(len(replies), 1)
                self.assertNotIn("🔒", replies[0])

    def test_help_and_the_menu_mention_lock(self):
        self.build()
        self.assertIn("lock", self.send("help")[0])
        tg = GateTelegram([])
        with self.assertRaises(BaseException):
            self.build(tg).run()
        self.assertIn(commands.LOCK, [verb for verb, _ in tg.menus[0]])


class TestTheCode(Gated):
    def test_a_code_runs_the_held_command_once_and_opens_the_grant(self):
        self.build()
        self.send("claude beacon fix it")
        replies = self.unlock()
        self.assertEqual(len(self.sessions.started), 1)
        self.assertEqual(self.sessions.started[0]["project"], "beacon")
        self.assertEqual(self.sessions.started[0]["prompt"], "fix it")
        self.assertIn(LINK, replies[-1])
        # Inside the grant the bot is today's bot.
        self.send("claude centrion")
        self.assertEqual(len(self.sessions.started), 2)

    def test_a_second_code_does_not_run_it_again(self):
        self.build()
        self.send("claude beacon")
        self.unlock()
        self.wall += 30
        self.unlock(step=STEP + 1)
        self.assertEqual(len(self.sessions.started), 1)

    def test_a_held_command_older_than_two_minutes_is_dropped(self):
        self.build()
        self.send("claude beacon")
        self.clock.now += bot.PENDING_FOR + 1
        self.unlock()
        self.assertEqual(self.sessions.started, [])

    def test_a_newer_held_command_replaces_the_older(self):
        self.build()
        self.send("claude beacon")
        self.send("claude centrion")
        self.unlock()
        self.assertEqual([s["project"] for s in self.sessions.started], ["centrion"])

    def test_a_code_with_nothing_held_just_opens_the_grant(self):
        self.build()
        replies = self.unlock()
        self.assertEqual(len(replies), 1)
        self.assertEqual(self.sessions.started, [])

    def test_the_code_is_deleted_from_the_chat(self):
        self.build()
        self.unlock()
        self.assertEqual(self.tg.deleted, [(ME, self.uid)])

    def test_a_delete_that_fails_is_logged_and_nothing_else(self):
        self.build()
        self.tg.delete_result = False
        self.send("claude beacon")
        self.unlock()
        self.assertEqual(len(self.sessions.started), 1)
        self.assertTrue(any("delete" in line for line in self.logged))

    def test_no_log_line_ever_contains_a_code(self):
        self.build()
        self.send(wrong())
        self.send("claude beacon")
        self.unlock()
        for line in self.logged:
            self.assertNotIn(code(), line)
            self.assertNotIn(wrong(), line)


class TestTheGrant(Gated):
    def test_it_lasts_unlock_minutes_on_the_monotonic_clock(self):
        self.build()
        self.unlock()
        self.clock.now += 15 * 60 - 1
        self.assertNotIn("🔒", self.send("stop all")[0])
        self.clock.now += 2
        self.assertIn("🔒", self.send("stop all")[0])

    def test_lock_ends_it(self):
        self.build()
        self.unlock()
        self.send("lock")
        self.assertIn("🔒", self.send("claude beacon")[0])
        self.assertEqual(self.sessions.started, [])

    def test_a_rebuilt_listener_starts_locked(self):
        self.build()
        self.unlock()
        self.build()
        self.assertIn("🔒", self.send("claude beacon")[0])

    def test_one_chats_grant_does_not_open_another(self):
        self.build()
        self.unlock(chat=ME)
        self.assertIn("🔒", self.send("claude beacon", chat=OTHER)[0])


class TestReplay(Gated):
    def test_a_code_cannot_be_used_twice(self):
        self.build()
        self.unlock()
        self.send("lock")
        self.assertNotIn("🔓", " ".join(self.send(code())))

    def test_nor_after_a_restart(self):
        self.build()
        self.unlock()
        self.build()
        self.assertNotIn("🔓", " ".join(self.send(code())))

    def test_nor_in_another_allowlisted_chat(self):
        # One secret, so one record of what has been spent: a per-chat record would let a code
        # read off one chat open the other.
        self.build()
        self.unlock(chat=ME)
        self.assertNotIn("🔓", " ".join(self.send(code(), chat=OTHER)))

    @unittest.skipUnless(POSIX, "file modes are the Mac's")
    def test_the_record_is_private(self):
        self.build()
        self.unlock()
        path = os.path.join(os.path.dirname(self.offset_path), bot.TOTP_STATE)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path) as fh:
            self.assertEqual(json.load(fh), {"last_step": STEP})

    def test_a_corrupt_record_refuses_this_step_and_accepts_the_next(self):
        path = os.path.join(os.path.dirname(self.offset_path), bot.TOTP_STATE)
        with open(path, "w") as fh:
            fh.write("{not json")
        self.build()
        self.assertNotIn("🔓", " ".join(self.send(code())))
        self.wall += 30
        self.unlock(step=STEP + 1)


class TestWrongCodes(Gated):
    def test_a_wrong_code_is_answered(self):
        self.build()
        replies = self.send(wrong())
        self.assertEqual(len(replies), 1)
        self.assertIn("✗", replies[0])

    def test_the_sixth_attempt_in_ten_minutes_is_refused_even_when_right(self):
        self.build()
        for _ in range(bot.STRIKES):
            self.send(wrong())
        replies = self.send(code())
        self.assertNotIn("🔓", " ".join(replies))
        self.assertIn("15 minutes", " ".join(replies))
        self.clock.now += bot.LOCKOUT_FOR + 1
        self.unlock()

    def test_strikes_older_than_ten_minutes_do_not_count(self):
        self.build()
        for _ in range(bot.STRIKES - 1):
            self.send(wrong())
        self.clock.now += bot.STRIKE_WINDOW + 1
        self.send(wrong())
        self.unlock()


if __name__ == "__main__":
    unittest.main()
