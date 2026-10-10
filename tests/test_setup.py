#!/usr/bin/env python3
"""Slice 18 — `bot.py --totp-setup` enrols Google Authenticator at the Mac, and only there.

What it must never do, more than what it does: send the secret anywhere but the terminal that
asked for it, print the config line for an enrolment that was never proved, or quietly replace a
secret a phone already holds. Everything outside the process is a seam — the entropy, the clock,
the keyboard, `qrencode` — so no test reads a real key or runs a real binary.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import base64
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from unittest import mock

import bot
import totp

ENTROPY = bytes(range(20))
KEY = base64.b32encode(ENTROPY).decode()          # 32 characters, no padding at 20 bytes.
NOW = 1234567890


class Enrol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="centrion-setup-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, ".telegram.json")
        self.write({"bot_token": "1:x", "allowed_chat_ids": [1]})
        self.runs = []
        # Nothing here may reach var/bot.log: the listener's log is the one place §14 sends
        # people to read, and a secret in it is a secret in every pasted excerpt.
        patcher = mock.patch.object(bot, "log", side_effect=AssertionError("logged"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data):
        with open(self.path, "w") as fh:
            json.dump(data, fh)
        os.chmod(self.path, 0o600)

    def enrol(self, answers, qrencode=None):
        """Run setup against `answers` typed in turn; EOF once they run out."""
        answers = list(answers)

        def ask(prompt):
            if not answers:
                raise EOFError
            return answers.pop(0)

        def run(argv, **kw):
            self.runs.append(argv)

        out, err = io.StringIO(), io.StringIO()
        code = bot.totp_setup(path=self.path, ask=ask, out=out, err=err,
                              entropy=lambda n: ENTROPY[:n], now=lambda: NOW,
                              which=lambda name: qrencode, run=run,
                              hostname=lambda: "mini.local")
        return code, out.getvalue(), err.getvalue()

    def good(self):
        return totp.code(ENTROPY, NOW // 30)


class TestEnrolment(Enrol):
    def test_a_verified_code_prints_the_line_to_add(self):
        code, out, _ = self.enrol([self.good()])
        self.assertEqual(code, 0)
        self.assertIn('"totp_secret": "%s"' % KEY, out)

    def test_the_key_is_printed_in_fours_and_decodes_to_the_same_bytes(self):
        _, out, _ = self.enrol([self.good()])
        grouped = " ".join(KEY[i:i + 4] for i in range(0, len(KEY), 4))
        self.assertIn(grouped, out)
        self.assertEqual(totp.decode(grouped), ENTROPY)

    def test_the_uri_round_trips_to_the_same_secret_and_names_the_issuer(self):
        _, out, _ = self.enrol([self.good()])
        uri = next(w for w in out.split() if w.startswith("otpauth://"))
        parts = urllib.parse.urlsplit(uri)
        self.assertEqual((parts.scheme, parts.netloc), ("otpauth", "totp"))
        self.assertEqual(urllib.parse.unquote(parts.path), "/centrion:mini")
        query = urllib.parse.parse_qs(parts.query)
        self.assertEqual(totp.decode(query["secret"][0]), ENTROPY)
        self.assertEqual(query["issuer"], ["centrion"])

    def test_a_wrong_code_is_asked_again_and_the_line_waits_for_a_right_one(self):
        wrong = "%06d" % ((int(self.good()) + 1) % 10 ** 6)
        code, out, err = self.enrol([wrong, self.good()])
        self.assertEqual(code, 0)
        self.assertIn("does not match", err)
        self.assertIn('"totp_secret"', out)

    def test_spaces_typed_inside_the_code_are_forgiven_at_the_terminal(self):
        good = self.good()
        code, _, _ = self.enrol([good[:3] + " " + good[3:]])
        self.assertEqual(code, 0)

    def test_giving_up_exits_non_zero_without_the_config_line(self):
        code, out, err = self.enrol(["000000"] if self.good() != "000000" else ["111111"])
        self.assertNotEqual(code, 0)
        self.assertNotIn('"totp_secret"', out)
        self.assertIn("nothing", err.lower())

    def test_ctrl_c_is_giving_up_too(self):
        def ask(prompt):
            raise KeyboardInterrupt
        out, err = io.StringIO(), io.StringIO()
        code = bot.totp_setup(path=self.path, ask=ask, out=out, err=err,
                              entropy=lambda n: ENTROPY[:n], now=lambda: NOW,
                              which=lambda name: None, run=lambda *a, **k: None,
                              hostname=lambda: "mini")
        self.assertNotEqual(code, 0)
        self.assertNotIn('"totp_secret"', out.getvalue())


class TestWhatItRefuses(Enrol):
    def test_an_existing_secret_is_refused_before_anything_is_generated(self):
        self.write({"bot_token": "1:x", "allowed_chat_ids": [1], "totp_secret": KEY})
        drawn = []
        out, err = io.StringIO(), io.StringIO()
        code = bot.totp_setup(path=self.path, ask=lambda p: self.fail("asked"), out=out,
                              err=err, entropy=lambda n: drawn.append(n) or ENTROPY[:n],
                              now=lambda: NOW, which=lambda name: None,
                              run=lambda *a, **k: None, hostname=lambda: "mini")
        self.assertNotEqual(code, 0)
        self.assertEqual(drawn, [])
        self.assertIn("already", err.getvalue())
        self.assertNotIn(KEY, out.getvalue() + err.getvalue())

    def test_it_draws_160_bits(self):
        drawn = []
        out, err = io.StringIO(), io.StringIO()
        bot.totp_setup(path=self.path, ask=lambda p: self.good(), out=out, err=err,
                       entropy=lambda n: drawn.append(n) or ENTROPY[:n], now=lambda: NOW,
                       which=lambda name: None, run=lambda *a, **k: None,
                       hostname=lambda: "mini")
        self.assertEqual(drawn, [20])

    def test_the_config_file_is_left_as_it_was(self):
        with open(self.path, "rb") as fh:
            before = fh.read()
        self.enrol([self.good()])
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), before)


class TestTheQrCode(Enrol):
    def test_without_qrencode_there_is_no_qr_and_no_error(self):
        code, _, err = self.enrol([self.good()], qrencode=None)
        self.assertEqual(code, 0)
        self.assertEqual(self.runs, [])

    def test_with_qrencode_the_uri_is_drawn_in_the_terminal(self):
        self.enrol([self.good()], qrencode="/opt/homebrew/bin/qrencode")
        self.assertEqual(len(self.runs), 1)
        argv = self.runs[0]
        self.assertEqual(argv[:3], ["/opt/homebrew/bin/qrencode", "-t", "ansiutf8"])
        self.assertTrue(argv[3].startswith("otpauth://totp/"))

    def test_a_qrencode_that_fails_does_not_stop_the_enrolment(self):
        def run(argv, **kw):
            raise OSError("broken")
        out, err = io.StringIO(), io.StringIO()
        code = bot.totp_setup(path=self.path, ask=lambda p: self.good(), out=out, err=err,
                              entropy=lambda n: ENTROPY[:n], now=lambda: NOW,
                              which=lambda name: "/usr/bin/qrencode", run=run,
                              hostname=lambda: "mini")
        self.assertEqual(code, 0)


class TestTheFlag(unittest.TestCase):
    def test_main_runs_setup_for_the_flag(self):
        with mock.patch.object(bot, "totp_setup", return_value=0) as setup, \
                mock.patch("sys.argv", ["bot.py", "--totp-setup"]):
            self.assertEqual(bot.main(), 0)
        setup.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
