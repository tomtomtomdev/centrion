#!/usr/bin/env python3
"""Telegram Bot API client: long polling out, best-effort replies back.

A cut-down lift of stock-watch-project/notify.py — same `_open()` seam so the retry policy is
testable offline, same "raise our own error type" rule — with three things changed for a daemon
that must not die and must not talk.

**It never raises at the poll.** SPEC.md §7: launchd would restart a crash, but a crash-loop
against ThrottleInterval is a worse failure mode than a patient retry. `poll()` returns a list
on every path — network down, Telegram down, garbage on the wire — and the caller's loop stays
simple. It also *returns* on every path rather than retrying internally, because §4 makes every
return from getUpdates the reconciliation tick.

**It never says the token.** Every call is `/bot<token>/<method>`, so the credential is in the
URL, and `HTTPError` carries the URL in both `.url` and its `str()`. §10 is blunt about what
that credential is: shell access to this Mac. So nothing built from an exception reaches a log
line or a Telegram reply without going through `redact()` — which bot.py reuses, since §7's
rule is about everything outbound and not only about this module — errors are raised
`from None` so a traceback cannot print the original in a `__cause__` chain, and `repr()` shows
nothing.

**It sends plain text.** notify.py uses HTML parse mode, which suits a report it composes
itself. Here the payload is often a tail of a PTY transcript full of `<`, `&` and `_`, and one
stray character would turn the send into a 400 at exactly the moment that tail is the only
diagnostic there is (§7).

Stdlib only: urllib, json, socket. See SPEC.md §7 for the transport and §10 for the rules.
"""
import json
import socket
import sys
import time
import urllib.error
import urllib.request

API = "https://api.telegram.org"

POLL_TIMEOUT = 50        # how long Telegram holds an idle getUpdates open
SOCKET_TIMEOUT = 60      # must exceed POLL_TIMEOUT — see _call
SEND_TIMEOUT = 30

SEND_ATTEMPTS = 3        # §7: outbound is best-effort, 3 attempts, as notify.py already does
FIRST_BACKOFF = 1
MAX_BACKOFF = 60
CONFLICT_BACKOFF = 30    # §7: a 409 is two consumers on one token

# Transient by contract. Everything else — 400 chat not found, 401 bad token, 403 blocked —
# will not become true by asking again.
RETRY_CODES = (429, 500, 502, 503, 504)

LIMIT = 4096             # Telegram's cap on one sendMessage. Slice 8 owns the truncation
                         # policy (head and tail with a marked elision); this is the number.


def _stderr(message):
    """Default log sink. launchd routes stdout and stderr to var/bot.log (§8)."""
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


class TelegramError(Exception):
    """The Bot API refused or could not be reached. Carries no token, by construction.

    `code` is the HTTP status where there was one, `timed_out` marks a socket timeout — which
    under a 50s long poll is ordinary and not a failure — and `retry_after` is Telegram's own
    rate-limit hint when it sent one.
    """

    def __init__(self, message, code=None, timed_out=False, retry_after=None):
        Exception.__init__(self, message)
        self.code = code
        self.timed_out = timed_out
        self.retry_after = retry_after


class Telegram:
    """The four Bot API calls centrion needs. One instance, long-lived, owns the offset."""

    def __init__(self, token, sleep=time.sleep, log=None):
        self.token = token
        self.offset = None      # None until the first batch; bot.py persists it to var/offset
        self.sleep = sleep
        self.log = log or _stderr
        self._backoff = 0       # 0 means healthy; otherwise the last wait, which doubles

    def __repr__(self):
        return "Telegram(token=<redacted>, offset=%r)" % (self.offset,)

    def redact(self, text):
        """Anything derived from an exception or a response goes through here first."""
        s = str(text)
        if self.token:
            s = s.replace(self.token, "<token>")
            # The half after the colon is the secret; the bot id before it is public. Replace
            # it separately in case something has split the token apart along the way.
            secret = self.token.split(":")[-1]
            if secret and secret != self.token:
                s = s.replace(secret, "<token>")
        return s

    def _open(self, req, timeout):
        """The one seam the tests replace, so the policy around it is exercised offline."""
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def _detail(self, e):
        try:
            body = json.loads(e.read())
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def _call(self, method, payload, timeout):
        """One request. Returns `result`, or raises a TelegramError that knows why.

        POST with a JSON body rather than the query string §7 writes the call as — same
        request to Telegram, and it keeps `allowed_updates` from needing to be URL-encoded
        JSON inside a URL that already holds the token.

        The socket timeout is above the server's hold on purpose: Telegram keeps an idle
        getUpdates open for POLL_TIMEOUT seconds and then answers with an empty list. Set the
        socket below that and every quiet minute looks like a network failure, and the client
        spends its life backing off from a server that is behaving perfectly.
        """
        url = "%s/bot%s/%s" % (API, self.token, method)
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            raw = self._open(req, timeout)
        except urllib.error.HTTPError as e:
            detail = self._detail(e)
            raise TelegramError(
                "%s: HTTP %d %s" % (method, e.code,
                                    self.redact(detail.get("description") or "no description")),
                code=e.code,
                retry_after=(detail.get("parameters") or {}).get("retry_after"),
            ) from None
        except socket.timeout:
            # Not TimeoutError: on 3.9 socket.timeout is an OSError and *not* a TimeoutError
            # subclass (it only became an alias in 3.10). notify.py's `except (URLError,
            # TimeoutError)` does not catch this at all, and under a long poll it happens.
            raise TelegramError("%s: socket timeout" % method, timed_out=True) from None
        except urllib.error.URLError as e:
            timed_out = isinstance(e.reason, socket.timeout)
            raise TelegramError("%s: %s" % (method, self.redact(e.reason)),
                                timed_out=timed_out) from None
        except OSError as e:
            raise TelegramError("%s: %s" % (method, self.redact(e))) from None

        try:
            body = json.loads(raw)
        except ValueError:
            # A captive portal or a proxy answering with HTML. Transient, not fatal.
            raise TelegramError("%s: response was not JSON" % method) from None
        if not isinstance(body, dict) or not body.get("ok"):
            description = body.get("description") if isinstance(body, dict) else None
            raise TelegramError("%s: %s" % (method, self.redact(description or "ok=false")))
        return body.get("result")

    def _wait(self):
        """The next network backoff: 1, 2, 4, … 60, then hold at 60 (§7)."""
        self._backoff = FIRST_BACKOFF if not self._backoff \
            else min(self._backoff * 2, MAX_BACKOFF)
        return self._backoff

    def poll(self, timeout=POLL_TIMEOUT):
        """One getUpdates. Returns the message updates, possibly empty. Never raises.

        `timeout=0` asks Telegram to answer immediately with whatever is pending, which is what
        `bot.py --whoami` wants; the daemon uses the default and lets the call hang.
        """
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if self.offset is not None:
            payload["offset"] = self.offset

        try:
            result = self._call("getUpdates", payload, SOCKET_TIMEOUT)
        except TelegramError as e:
            if e.timed_out:
                # Ordinary under a long poll: the connection went away while nothing was
                # happening. Poll again at once — backing off here would add a minute of
                # deafness to a bot whose server is fine.
                return []
            if e.code == 409:
                # §7: two consumers on one token. lock.sh stops a second copy of this daemon,
                # so this is a second *bot* on the same token, or a webhook still set — and it
                # never resolves on its own, which is why it is logged every time rather than
                # counted quietly.
                self.log("getUpdates: HTTP 409 Conflict — another consumer is polling this "
                         "token. A second centrion (check lock.sh), a webhook still set, or a "
                         "shared bot token (SPEC.md §3). Backing off %ds." % CONFLICT_BACKOFF)
                self.sleep(CONFLICT_BACKOFF)
                return []
            wait = self._wait()
            self.log("getUpdates: %s — retrying in %ds" % (self.redact(e), wait))
            self.sleep(wait)
            return []

        self._backoff = 0
        updates = result if isinstance(result, list) else []
        # Acknowledge every id in the batch, message or not. allowed_updates is a request and
        # not a guarantee, and an unacknowledged update is re-delivered forever: leaving one
        # behind would pin the offset and the bot would go permanently deaf.
        ids = [u["update_id"] for u in updates
               if isinstance(u, dict) and isinstance(u.get("update_id"), int)]
        if ids:
            self.offset = max(ids) + 1
        return [u for u in updates if isinstance(u, dict) and "message" in u]

    def _best_effort(self, method, payload):
        """True if it went, False if it did not. Never raises — a failed reply must not end
        the poll loop. Never silent either: §14 makes var/bot.log the first place to look
        when the phone gets no answer."""
        for attempt in range(SEND_ATTEMPTS):
            last = attempt == SEND_ATTEMPTS - 1
            try:
                self._call(method, payload, SEND_TIMEOUT)
                return True
            except TelegramError as e:
                if e.code is not None and e.code not in RETRY_CODES:
                    self.log("%s: giving up — %s" % (method, self.redact(e)))
                    return False
                if last:
                    self.log("%s: giving up after %d attempts — %s"
                             % (method, SEND_ATTEMPTS, self.redact(e)))
                    return False
                self.sleep(min(e.retry_after or FIRST_BACKOFF * (attempt + 1), MAX_BACKOFF))
        return False

    def send_message(self, chat_id, text):
        """Plain text, no parse_mode (§7). True if Telegram took it."""
        return self._best_effort("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            # The session link is the payload of the important reply, and Telegram's preview of
            # it is a claude.ai sign-in card — half a screen on a phone, saying nothing. The
            # link stays tappable without it.
            "disable_web_page_preview": True,
        })

    def delete_webhook(self):
        """§7: a webhook set at any point in this bot's past makes getUpdates 409 forever.

        Without `drop_pending_updates`: the 24h backlog is real, but §7 answers it with the
        message-date guard, which can tell a message sent ten seconds before startup from one
        sent over the weekend. Dropping here cannot.
        """
        return self._best_effort("deleteWebhook", {})

    def get_me(self):
        """The bot's own record. Raises, unlike the others — this one is run by hand."""
        return self._call("getMe", {}, SEND_TIMEOUT)
