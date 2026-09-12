#!/usr/bin/env python3
"""Slice 3 — the Bot API client: it polls, it retries, and it never says the token.

Mocked at the object boundary the way test_notify.py does it — a Telegram subclass with only
its one `_open()` seam replaced, so the retry and backoff policy is exercised offline without
patching urllib globally. No network, ever.

Two things are being pinned here beyond "it works".

**The token is in the URL.** Every Bot API call is `/bot<token>/<method>`, so `HTTPError`
carries the credential in `.url` and `.reason`, and so does any exception or log line built by
interpolating them. SPEC.md §7 and §10 both say the same thing from different directions: this
process writes to var/bot.log and talks to Telegram, and neither may ever receive the one
secret that is shell access to this Mac. `test_the_token_never_appears_*` is that rule, and it
is asserted against a token-shaped fixture on every failure path there is.

**The listener must never die.** §7: network errors back off and retry forever, because launchd
would restart a crash but a crash-loop against ThrottleInterval is a worse failure mode than a
patient retry. So `poll()` returns a list on every path and raises on none of them — and it
returns on every path, error or not, because §4 makes every return from getUpdates the
reconciliation tick.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import io
import json
import socket
import unittest
import urllib.error

import telegram

# Shaped like the real thing: <bot id>:<35 chars>. The tests assert this never escapes, and a
# realistic shape matters — a short fake could pass a substring check by luck.
TOKEN = "8960211893:AAHreallyNotTheRealTokenJustAFake_x"
SECRET = TOKEN.split(":")[1]


def ok(result):
    return json.dumps({"ok": True, "result": result}).encode()


def http_error(code, body=b"{}"):
    """As urllib raises it — including the URL, which is where the token is."""
    return urllib.error.HTTPError("https://api.telegram.org/bot%s/getUpdates" % TOKEN,
                                  code, "Conflict", {}, io.BytesIO(body))


def update(uid, text="hi", chat=42, date=1789198400, kind="message"):
    return {"update_id": uid,
            kind: {"message_id": uid, "date": date, "text": text,
                   "chat": {"id": chat, "type": "private"},
                   "from": {"id": chat}}}


class Stub(telegram.Telegram):
    """Only `_open` replaced. Everything around it — retries, backoff, offsets — is the test."""

    def __init__(self, responses, **kw):
        telegram.Telegram.__init__(self, TOKEN, sleep=self.record, log=self.note, **kw)
        self.responses = list(responses)
        self.requests, self.timeouts, self.slept, self.logged = [], [], [], []

    def record(self, seconds):
        self.slept.append(seconds)

    def note(self, message):
        self.logged.append(message)

    def _open(self, req, timeout):
        self.requests.append(req)
        self.timeouts.append(timeout)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def sent(self, n=-1):
        """The JSON body of the nth request."""
        return json.loads(self.requests[n].data)

    def method(self, n=-1):
        return self.requests[n].full_url.rsplit("/", 1)[-1]


class TestThePollRequest(unittest.TestCase):
    def test_the_first_poll_sends_no_offset(self):
        # There is nothing to acknowledge yet, and offset=0 would mean something else.
        tg = Stub([ok([])])
        tg.poll()
        self.assertNotIn("offset", tg.sent())

    def test_it_long_polls_and_asks_only_for_messages(self):
        tg = Stub([ok([])])
        tg.poll()
        body = tg.sent()
        self.assertEqual(tg.method(), "getUpdates")
        self.assertEqual(body["timeout"], 50)
        self.assertEqual(body["allowed_updates"], ["message"])

    def test_the_socket_timeout_is_above_the_server_hold(self):
        # §7: below it and every single idle poll looks like a network failure, so the client
        # would spend its life backing off from a server that is behaving perfectly.
        self.assertGreater(telegram.SOCKET_TIMEOUT, telegram.POLL_TIMEOUT)
        tg = Stub([ok([])])
        tg.poll()
        self.assertEqual(tg.timeouts[-1], telegram.SOCKET_TIMEOUT)


class TestTheOffsetAdvances(unittest.TestCase):
    def test_the_offset_moves_past_the_batch(self):
        tg = Stub([ok([update(11), update(12), update(13)]), ok([])])
        tg.poll()
        self.assertEqual(tg.offset, 14)
        tg.poll()
        self.assertEqual(tg.sent()["offset"], 14)

    def test_an_empty_batch_leaves_the_offset_alone(self):
        tg = Stub([ok([update(11)]), ok([]), ok([])])
        tg.poll()
        tg.poll()
        self.assertEqual(tg.offset, 12)
        tg.poll()
        self.assertEqual(tg.sent()["offset"], 12)

    def test_an_out_of_order_batch_still_ends_past_the_highest_id(self):
        # Nothing promises ordering; taking the last element would re-fetch the rest forever.
        tg = Stub([ok([update(13), update(11), update(12)])])
        tg.poll()
        self.assertEqual(tg.offset, 14)

    def test_a_non_message_update_is_acknowledged_but_not_returned(self):
        """The one that silently wedges the bot if it is wrong.

        allowed_updates should keep these out, but it is a request and not a guarantee — an
        edited message or a channel post can arrive. Returning it would confuse the dispatcher;
        *not advancing past it* would re-fetch the same update forever and the bot would go
        deaf, because the offset is what acknowledges it. So: acknowledge everything, hand back
        only messages.
        """
        tg = Stub([ok([update(11), update(12, kind="edited_message"), update(13)])])
        got = tg.poll()
        self.assertEqual([u["update_id"] for u in got], [11, 13])
        self.assertEqual(tg.offset, 14)

    def test_the_messages_come_back_whole(self):
        tg = Stub([ok([update(11, text="claude beacon")])])
        got = tg.poll()
        self.assertEqual(got[0]["message"]["text"], "claude beacon")


class TestPollingSurvivesEverything(unittest.TestCase):
    """§7: never exit. poll() returns a list on every path and raises on none."""

    def test_a_409_backs_off_rather_than_raising(self):
        # Two consumers on one token. lockf stops a second copy of this daemon; a second *bot*
        # is on you, so this has to be loud and survivable rather than fatal.
        tg = Stub([http_error(409, b'{"description": "terminated by other getUpdates request"}')])
        self.assertEqual(tg.poll(), [])
        self.assertEqual(tg.slept, [telegram.CONFLICT_BACKOFF])

    def test_a_409_says_so_loudly(self):
        tg = Stub([http_error(409)])
        tg.poll()
        self.assertTrue(any("409" in m or "conflict" in m.lower() for m in tg.logged),
                        "a 409 must be logged: %r" % tg.logged)

    def test_a_socket_timeout_is_not_an_error(self):
        """On 3.9 this is the bug the lift would have inherited.

        `socket.timeout` is a subclass of OSError and *not* of TimeoutError until 3.10 —
        verified on this box's 3.9.6 — so notify.py's `except (URLError, TimeoutError)` does
        not catch it at all. Under a 50s long poll a dropped connection produces one of these
        routinely, and uncaught it takes the listener down.

        Caught, it is still not an error: the right response to an idle poll that timed out is
        to poll again immediately, not to start a backoff against a healthy server.
        """
        tg = Stub([socket.timeout("timed out"), ok([])])
        self.assertEqual(tg.poll(), [])
        self.assertEqual(tg.slept, [], "a timeout must not sleep")
        tg.poll()
        self.assertEqual(len(tg.requests), 2)

    def test_a_timeout_does_not_count_towards_the_backoff(self):
        # Proof it is not merely swallowed: the next genuine failure still starts at 1s.
        tg = Stub([socket.timeout(), socket.timeout(), urllib.error.URLError("down")])
        tg.poll()
        tg.poll()
        tg.poll()
        self.assertEqual(tg.slept, [1])

    def test_network_errors_back_off_exponentially_and_hold_at_sixty(self):
        tg = Stub([urllib.error.URLError("down")] * 8)
        for _ in range(8):
            self.assertEqual(tg.poll(), [])
        self.assertEqual(tg.slept, [1, 2, 4, 8, 16, 32, 60, 60])

    def test_the_backoff_resets_after_a_good_poll(self):
        tg = Stub([urllib.error.URLError("down")] * 3 + [ok([])] + [urllib.error.URLError("d")])
        for _ in range(5):
            tg.poll()
        self.assertEqual(tg.slept, [1, 2, 4, 1])

    def test_a_500_backs_off_rather_than_raising(self):
        tg = Stub([http_error(500)])
        self.assertEqual(tg.poll(), [])
        self.assertEqual(tg.slept, [1])

    def test_malformed_json_backs_off_rather_than_raising(self):
        tg = Stub([b"<html>502 Bad Gateway</html>"])
        self.assertEqual(tg.poll(), [])
        self.assertEqual(tg.slept, [1])

    def test_an_ok_false_body_backs_off_rather_than_raising(self):
        tg = Stub([json.dumps({"ok": False, "description": "Unauthorized"}).encode()])
        self.assertEqual(tg.poll(), [])
        self.assertEqual(tg.slept, [1])

    def test_a_failed_poll_does_not_move_the_offset(self):
        # Moving it would acknowledge a batch that was never seen, losing those messages.
        tg = Stub([ok([update(11)]), urllib.error.URLError("down"), ok([])])
        tg.poll()
        tg.poll()
        self.assertEqual(tg.offset, 12)
        tg.poll()
        self.assertEqual(tg.sent()["offset"], 12)


class TestSending(unittest.TestCase):
    def test_a_message_is_sent_as_plain_text(self):
        """§7: no parse_mode. A PTY error tail is full of `<`, `&` and `_`.

        notify.py uses HTML mode, which is right for a report it composes itself and wrong
        here: one stray character in a captured terminal tail turns the whole send into a 400,
        exactly at the moment something has already gone wrong and the tail is the only
        diagnostic there is.
        """
        tg = Stub([ok({"message_id": 1})])
        self.assertTrue(tg.send_message(42, "a < b & c_d"))
        body = tg.sent()
        self.assertEqual(tg.method(), "sendMessage")
        self.assertEqual(body["chat_id"], 42)
        self.assertEqual(body["text"], "a < b & c_d")
        self.assertNotIn("parse_mode", body)
        # The important reply is a claude.ai link on its own line, and Telegram's preview of
        # one is a sign-in card that fills half a phone screen and says nothing. The link is
        # tappable either way.
        self.assertTrue(body["disable_web_page_preview"])

    def test_it_retries_three_times_then_gives_up_quietly(self):
        # Quietly to the caller — the poll loop must not die over a failed reply — but never
        # silently: §14 makes var/bot.log the first place to look when the phone gets nothing.
        tg = Stub([http_error(503)] * 3)
        self.assertFalse(tg.send_message(42, "hi"))
        self.assertEqual(len(tg.requests), 3)
        self.assertTrue(tg.logged, "giving up must leave a trace in the log")

    def test_it_retries_a_network_error_then_succeeds(self):
        tg = Stub([urllib.error.URLError("down"), ok({"message_id": 1})])
        self.assertTrue(tg.send_message(42, "hi"))
        self.assertEqual(len(tg.requests), 2)

    def test_it_retries_a_socket_timeout(self):
        tg = Stub([socket.timeout(), ok({"message_id": 1})])
        self.assertTrue(tg.send_message(42, "hi"))

    def test_a_400_is_not_retried(self):
        # "chat not found" will not become true by asking again.
        tg = Stub([http_error(400, b'{"description": "chat not found"}')])
        self.assertFalse(tg.send_message(42, "hi"))
        self.assertEqual(len(tg.requests), 1)

    def test_it_honours_retry_after(self):
        tg = Stub([http_error(429, b'{"parameters": {"retry_after": 7}}'),
                   ok({"message_id": 1})])
        tg.send_message(42, "hi")
        self.assertEqual(tg.slept, [7])

    def test_it_caps_a_silly_retry_after(self):
        tg = Stub([http_error(429, b'{"parameters": {"retry_after": 9000}}'),
                   ok({"message_id": 1})])
        tg.send_message(42, "hi")
        self.assertEqual(tg.slept, [telegram.MAX_BACKOFF])

    def test_a_send_failure_never_raises(self):
        for responses in ([http_error(400)], [http_error(503)] * 3,
                          [urllib.error.URLError("down")] * 3, [socket.timeout()] * 3,
                          [b"not json"] * 3):
            self.assertFalse(Stub(responses).send_message(42, "hi"))


class TestStartup(unittest.TestCase):
    def test_delete_webhook_is_callable_and_does_not_drop_the_backlog(self):
        """§7: a webhook set at any point in this bot's past makes getUpdates 409 forever.

        Deliberately without drop_pending_updates. The 24h backlog is real, but §7 handles it
        with the message-date guard, which can tell a message sent ten seconds before startup
        from one sent over the weekend. Dropping here cannot.
        """
        tg = Stub([ok(True)])
        self.assertTrue(tg.delete_webhook())
        self.assertEqual(tg.method(), "deleteWebhook")
        self.assertNotIn("drop_pending_updates", tg.sent())

    def test_a_failing_delete_webhook_does_not_raise(self):
        tg = Stub([urllib.error.URLError("down")] * 3)
        self.assertFalse(tg.delete_webhook())

    def test_get_me_returns_the_bot(self):
        tg = Stub([ok({"id": 8960211893, "username": "Centrionetbot"})])
        self.assertEqual(tg.get_me()["username"], "Centrionetbot")


class TestTheTokenNeverEscapes(unittest.TestCase):
    """The rule that matters most. §10: the bot token is shell access to this machine."""

    def test_the_token_really_is_in_the_url(self):
        # Establishes that the redaction below is load-bearing and not decorative.
        tg = Stub([ok([])])
        tg.poll()
        self.assertIn(TOKEN, tg.requests[0].full_url)

    def test_the_token_never_appears_in_a_log_line(self):
        failures = [http_error(409), http_error(500), http_error(400, b'{"description": "no"}'),
                    urllib.error.URLError("connection refused"), socket.timeout("timed out"),
                    b"<html>502</html>", json.dumps({"ok": False}).encode()]
        for f in failures:
            # Four: one for the poll, three for the send's full retry run.
            tg = Stub([f] * 4)
            tg.poll()
            tg.send_message(42, "hi")
            for line in tg.logged:
                self.assertNotIn(TOKEN, line, "log line leaked the token: %r" % line)
                self.assertNotIn(SECRET, line, "log line leaked the token: %r" % line)

    def test_the_token_never_appears_in_an_exception(self):
        # get_me and delete_webhook are the calls a human runs by hand, so their failures land
        # on a terminal rather than in the log.
        tg = Stub([http_error(401, b'{"description": "Unauthorized"}')])
        with self.assertRaises(telegram.TelegramError) as caught:
            tg.get_me()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertIn("401", str(caught.exception))

    def test_a_raised_http_error_is_replaced_not_wrapped(self):
        # `raise X from e` keeps the original in __cause__, and a traceback prints the chain —
        # which would put the URL, and the token, on the terminal anyway.
        tg = Stub([http_error(401)])
        try:
            tg.get_me()
        except telegram.TelegramError as e:
            self.assertIsNone(e.__cause__)
            self.assertNotIn(TOKEN, repr(e))

    def test_the_repr_does_not_leak_the_token(self):
        self.assertNotIn(TOKEN, repr(Stub([])))
        self.assertNotIn(SECRET, repr(Stub([])))


if __name__ == "__main__":
    unittest.main()
