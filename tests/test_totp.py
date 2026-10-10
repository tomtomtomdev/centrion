#!/usr/bin/env python3
"""Slice 17 — the TOTP arithmetic Google Authenticator agrees with, and nothing more lenient.

RFC 6238 Appendix B is the oracle: its SHA1 rows are 8-digit codes, and a 6-digit code is the
same truncated integer modulo 10**6, so the low six digits of each row are what the app shows.
The rest pins the two ways a check like this goes quietly wrong — accepting a code it should
not (a replay, a far-off step, a string that only *nearly* is six digits) and refusing one it
should (a phone a few seconds out).

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import base64
import unittest

import totp

#: RFC 6238 Appendix B's SHA1 seed, the ASCII string "12345678901234567890".
RFC_SECRET = b"12345678901234567890"

#: (unix time, the RFC's 8-digit SHA1 code). The test takes the last six.
RFC_VECTORS = (
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
)

NOW = 1234567890
STEP = NOW // 30


class TestTheRfcVectors(unittest.TestCase):
    def test_every_sha1_row_in_appendix_b_comes_out_as_its_low_six_digits(self):
        for t, eight in RFC_VECTORS:
            with self.subTest(t=t):
                self.assertEqual(totp.code(RFC_SECRET, t // 30), eight[-6:])

    def test_a_code_keeps_its_leading_zero(self):
        # 1111111109's code is 081804: an int would drop the 0 and never match the app.
        self.assertEqual(totp.code(RFC_SECRET, 1111111109 // 30), "081804")


class TestVerify(unittest.TestCase):
    def ok(self, text, now=NOW, last_step=None):
        return totp.verify(RFC_SECRET, text, now, last_step)

    def test_the_current_code_returns_its_step(self):
        self.assertEqual(self.ok("005924"), STEP)

    def test_a_code_one_step_early_or_late_is_accepted(self):
        self.assertEqual(self.ok(totp.code(RFC_SECRET, STEP - 1)), STEP - 1)
        self.assertEqual(self.ok(totp.code(RFC_SECRET, STEP + 1)), STEP + 1)

    def test_a_code_two_steps_away_is_refused(self):
        self.assertIsNone(self.ok(totp.code(RFC_SECRET, STEP - 2)))
        self.assertIsNone(self.ok(totp.code(RFC_SECRET, STEP + 2)))

    def test_a_used_step_cannot_be_used_again(self):
        self.assertIsNone(self.ok("005924", last_step=STEP))

    def test_nor_can_any_step_before_it(self):
        # The step before was still inside the window; a token holder who read it off the chat
        # must not get a second use out of it once a later code has been accepted.
        self.assertIsNone(self.ok(totp.code(RFC_SECRET, STEP - 1), last_step=STEP))

    def test_a_later_step_than_the_last_used_is_accepted(self):
        self.assertEqual(self.ok(totp.code(RFC_SECRET, STEP + 1), last_step=STEP), STEP + 1)

    def test_anything_but_six_ascii_digits_is_refused_rather_than_coerced(self):
        for text in ("", "05924", "0005924", " 005924", "005924 ", "00 5924", "005924\n",
                     "²²²²²²", "٠٠٥٩٢٤", "-05924", "+05924", "abcdef", None, 5924, b"005924"):
            with self.subTest(text=text):
                self.assertIsNone(self.ok(text))


class TestDecode(unittest.TestCase):
    KEY = base64.b32encode(bytes(range(20))).decode()

    def test_a_base32_key_decodes_to_its_bytes(self):
        self.assertEqual(totp.decode(self.KEY), bytes(range(20)))

    def test_the_way_the_app_and_the_setup_print_it_is_accepted_too(self):
        # Lowercase, grouped in fours, no padding: what a person copies off a screen.
        grouped = " ".join(self.KEY.lower()[i:i + 4] for i in range(0, len(self.KEY), 4))
        self.assertEqual(totp.decode(grouped), bytes(range(20)))

    def test_something_that_is_not_base32_is_refused(self):
        for bad in ("not base32!", "0189" * 8, "", None, 12345):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    totp.decode(bad)


if __name__ == "__main__":
    unittest.main()
