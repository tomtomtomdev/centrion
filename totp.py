#!/usr/bin/env python3
"""TOTP, the arithmetic Google Authenticator does. Pure apart from the clock it is handed.

SPEC.md §12 slice 17. RFC 6238 over RFC 4226 with the app's parameters and no others —
HMAC-SHA1, 6 digits, 30-second steps — because the app ignores `algorithm`, `digits` and
`period` on some platforms and builds, and a "stronger" secret is then one whose codes this bot
never accepts. Stdlib only (§3).

Two ways a check like this goes wrong quietly, and both are refused here rather than tuned:

- **Leniency.** Only exactly six ASCII digits are a code. `" 123456"`, `"²²²²²²"` and an int are
  not coerced into one: a message that *nearly* is a code is a message, and §5's rule is that a
  near miss never becomes a dangerous approximation of itself.
- **Replay.** A code is good for its own step and one either side, which is up to 90 seconds,
  and Telegram carries it in the clear (§10). So the caller hands back the last step it
  accepted, and nothing at or before that step verifies again.

The clock is the caller's `time.time()`, never monotonic: wall-clock time is the protocol. A Mac
whose clock is wrong fails as every code refused, which is the safe direction.
"""
import base64
import binascii
import hashlib
import hmac
import struct

DIGITS = 6
PERIOD = 30
#: Steps either side of now that still verify: ±30s, RFC 6238 §5.2's suggested allowance.
SKEW = 1
#: RFC 4226 §4 requires 128 bits and recommends 160; `bot.py --totp-setup` draws 160.
MIN_SECRET_BYTES = 16


def decode(text):
    """A base32 key as a person copies it — any case, spaces, no padding — to its bytes.

    Raises ValueError on anything else, including an empty key.
    """
    if not isinstance(text, str):
        raise ValueError("a TOTP key is a base32 string")
    key = "".join(text.split()).upper()
    if not key:
        raise ValueError("the TOTP key is empty")
    key += "=" * (-len(key) % 8)
    try:
        return base64.b32decode(key)
    except (binascii.Error, ValueError):
        raise ValueError("the TOTP key is not base32") from None


def code(secret, step):
    """The code for one 30-second step (`unix time // 30`), as the app shows it: six digits,
    leading zeros kept."""
    mac = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return "%0*d" % (DIGITS, value % 10 ** DIGITS)


def is_code(text):
    """Is `text` shaped like a code — exactly six ASCII digits? Says nothing about whether it is
    right. isascii() as well as isdigit(), for commands._index's reason."""
    return (isinstance(text, str) and len(text) == DIGITS
            and text.isascii() and text.isdigit())


def verify(secret, text, now, last_step=None):
    """The step `text` is the code for, or None.

    `now` is wall-clock seconds. `last_step` is the step the caller last accepted from this
    secret; that step and every one before it are refused, so a code read off the chat cannot
    be used a second time inside its own window.
    """
    if not is_code(text):
        return None
    current = int(now) // PERIOD
    for step in range(current - SKEW, current + SKEW + 1):
        if last_step is not None and step <= last_step:
            continue
        # compare_digest for every candidate, and no early exit on a near miss: what the
        # timing could leak is small, and the comparison that cannot leak it costs nothing.
        if hmac.compare_digest(code(secret, step), text):
            return step
    return None
