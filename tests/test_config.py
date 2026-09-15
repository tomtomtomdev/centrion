#!/usr/bin/env python3
"""Slice 1 — config.load() fails closed, and never says the token out loud.

Two properties, and the second is the one that is easy to lose later. Every rejection path is
asserted to raise ConfigError rather than return something half-usable, because the thing being
configured is a bot that spawns bypass-permissions shells: a config that is 90% valid must not
start it. And every error message is asserted not to contain the bot token, because these
messages go to a log file, and in slice 3 some of them go to Telegram.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

import config
from tests.support import POSIX, needs_symlinks

TOKEN = "7654321:AAF-ThisIsAFakeBotTokenForTests_xyz"


class Base(unittest.TestCase):
    """A temp home with a projects root and a fake claude binary, so cases differ by one field."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="centrion-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.projects = os.path.join(self.tmp, "Projects")
        os.mkdir(self.projects)
        self.claude = os.path.join(self.tmp, "claude")
        with open(self.claude, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.claude, 0o755)
        if not POSIX:
            # WINDOWS.md 5.1, W2b: stat.S_IMODE is 0666 for every ordinary file on Windows,
            # so the 0600 check refuses everything and no other rule in this file could be
            # tested. Stood down here until the DACL check replaces it; TestPermissions is
            # the Mac's until then, and W2b's tests are the Windows secrecy check.
            stood_down = mock.patch.object(config, "REQUIRED_MODE", 0o666)
            stood_down.start()
            self.addCleanup(stood_down.stop)

    def valid(self, **over):
        d = {"bot_token": TOKEN, "allowed_chat_ids": [987654321],
             "projects_root": self.projects, "claude_bin": self.claude}
        d.update(over)
        for k in [k for k, v in d.items() if v is _ABSENT]:
            del d[k]
        return d

    def write(self, data, mode=0o600, raw=None):
        p = os.path.join(self.tmp, ".telegram.json")
        with open(p, "w") as fh:
            fh.write(raw if raw is not None else json.dumps(data))
        os.chmod(p, mode)
        return p

    def refuses(self, data, because, mode=0o600, raw=None):
        """Assert load() raises ConfigError, that it says why, and that it never says the token."""
        path = self.write(data, mode=mode, raw=raw)
        with self.assertRaises(config.ConfigError) as cm:
            config.load(path)
        msg = str(cm.exception)
        self.assertIn(because, msg.lower(), "message did not name the problem: %r" % msg)
        self.assertNotIn(TOKEN, msg, "the error message leaked the bot token")
        self.assertNotIn(TOKEN[:12], msg, "the error message leaked part of the bot token")
        return msg


_ABSENT = object()


class TestFileItself(Base):
    def test_a_missing_file_is_refused(self):
        with self.assertRaises(config.ConfigError) as cm:
            config.load(os.path.join(self.tmp, "nope.json"))
        self.assertIn("not found", str(cm.exception).lower())

    def test_malformed_json_is_refused(self):
        self.refuses(None, "json", raw="{not json at all")

    def test_a_json_array_is_refused(self):
        self.refuses(None, "object", raw="[1, 2, 3]")


@unittest.skipUnless(POSIX, "stat modes are the Mac's secrecy check; Windows reports 0666 for "
                     "every file and the DACL check is WINDOWS.md W2b")
class TestPermissions(Base):
    def test_a_world_readable_config_is_refused(self):
        # 0644 in ~/Library or a synced folder is how a token gets read by something else.
        msg = self.refuses(self.valid(), "0600", mode=0o644)
        self.assertIn("644", msg)

    def test_a_group_readable_config_is_refused(self):
        self.refuses(self.valid(), "0600", mode=0o640)

    def test_mode_0600_is_accepted(self):
        cfg = config.load(self.write(self.valid(), mode=0o600))
        self.assertEqual(cfg.bot_token, TOKEN)

    def test_mode_0400_is_accepted(self):
        # Stricter than required is not an error.
        cfg = config.load(self.write(self.valid(), mode=0o400))
        self.assertEqual(cfg.bot_token, TOKEN)


class TestToken(Base):
    def test_an_absent_token_is_refused(self):
        self.refuses(self.valid(bot_token=_ABSENT), "bot_token")

    def test_an_empty_token_is_refused(self):
        self.refuses(self.valid(bot_token=""), "bot_token")

    def test_a_non_string_token_is_refused(self):
        self.refuses(self.valid(bot_token=12345), "bot_token")


class TestAllowlistFailsClosed(Base):
    def test_an_absent_allowlist_is_refused(self):
        # The whole point: no allowlist must never mean "anyone".
        self.refuses(self.valid(allowed_chat_ids=_ABSENT), "allowed_chat_ids")

    def test_an_empty_allowlist_is_refused(self):
        self.refuses(self.valid(allowed_chat_ids=[]), "allowed_chat_ids")

    def test_a_string_id_is_refused(self):
        # "987654321" != 987654321 on comparison, so it would silently match nobody.
        self.refuses(self.valid(allowed_chat_ids=["987654321"]), "integer")

    def test_a_boolean_id_is_refused(self):
        # bool is a subclass of int in Python; True must not sneak through as an id.
        self.refuses(self.valid(allowed_chat_ids=[True]), "integer")

    def test_a_float_id_is_refused(self):
        self.refuses(self.valid(allowed_chat_ids=[987654321.0]), "integer")

    def test_a_bare_integer_instead_of_a_list_is_refused(self):
        self.refuses(self.valid(allowed_chat_ids=987654321), "list")

    def test_several_valid_ids_are_accepted(self):
        cfg = config.load(self.write(self.valid(allowed_chat_ids=[1, -100200300])))
        self.assertEqual(cfg.allowed_chat_ids, frozenset({1, -100200300}))


class TestProjectsRoot(Base):
    def test_an_absent_root_is_refused(self):
        self.refuses(self.valid(projects_root=_ABSENT), "projects_root")

    def test_a_nonexistent_root_is_refused(self):
        self.refuses(self.valid(projects_root=os.path.join(self.tmp, "gone")), "does not exist")

    def test_a_file_as_root_is_refused(self):
        self.refuses(self.valid(projects_root=self.claude), "not a directory")

    def test_a_tilde_in_the_root_is_expanded(self):
        cfg = config.load(self.write(self.valid(projects_root="~")))
        self.assertEqual(cfg.projects_root, os.path.realpath(os.path.expanduser("~")))


class TestClaudeBinary(Base):
    def test_a_missing_binary_is_refused(self):
        # SPEC.md §9.8: `claude install` can move it; catch that once at startup.
        self.refuses(self.valid(claude_bin=os.path.join(self.tmp, "gone")), "does not exist")

    @unittest.skipUnless(POSIX, "the executable bit is the Mac's half of this pair: WINDOWS.md §5.2")
    def test_a_non_executable_binary_is_refused(self):
        os.chmod(self.claude, 0o644)
        self.refuses(self.valid(), "not executable")

    @unittest.skipIf(POSIX, "the Windows half of the pair above: WINDOWS.md §5.2")
    def test_executability_is_not_checked_on_windows(self):
        """There is no X_OK bit here, so asking about one can only ever say yes.

        `os.access(path, os.X_OK)` on Windows is `F_OK` wearing a different name — it answers
        True for a text file and for a file whose read-only attribute is set. A check that
        cannot fail is worse than no check: it reads like the Mac's guarantee and is not one.
        It is gone on this platform, and this test is what says so out loud.
        """
        os.chmod(self.claude, 0o444)
        cfg = config.load(self.write(self.valid()))
        self.assertEqual(cfg.claude_bin, self.claude)

    @unittest.skipUnless(POSIX, "the Mac's default is the versioned symlink: WINDOWS.md §5.2")
    def test_the_default_is_used_when_absent(self):
        cfg = config.load(self.write(self.valid(claude_bin=_ABSENT)), check_claude=False)
        self.assertTrue(cfg.claude_bin.endswith("/.local/bin/claude"))

    @unittest.skipIf(POSIX, "the Windows default is whatever is on PATH: WINDOWS.md §5.2")
    def test_the_default_on_windows_is_what_is_on_path(self):
        r"""W0d: this box has two claude.exe, and `~/.local/bin` holds the *stale* one.

        v2.1.231 from August sits under `%USERPROFILE%\.local\bin` while winget's v2.1.268
        is the one on PATH, and `~/.claude.json` says auto-updates are off. The Mac's default
        is a symlink that self-update keeps pointing at the current build; there is no such
        symlink here, so the equivalent of "whatever `claude` means right now" is `which`.
        """
        cfg = config.load(self.write(self.valid(claude_bin=_ABSENT)), check_claude=False)
        self.assertEqual(cfg.claude_bin, shutil.which("claude"))
        self.assertNotIn(".local", cfg.claude_bin)

    def test_a_default_that_is_not_on_path_is_refused_in_its_own_words(self):
        """`shutil.which` returns None when Claude Code is not installed — say that.

        Portable because the message is: with no default to fall back on, `claude_bin` absent
        is a config that cannot start a session, and the reply has to name the reason rather
        than fall through to the type check and say "`claude_bin` must be a string" about a
        key the file does not contain.
        """
        with mock.patch.object(config, "DEFAULT_CLAUDE_BIN", None):
            msg = self.refuses(self.valid(claude_bin=_ABSENT), "on path")
        self.assertIn("claude_bin", msg)

    @needs_symlinks
    def test_a_symlinked_binary_is_not_resolved(self):
        """The symlink must survive into exec, or self-update breaks the daemon silently.

        On this machine ~/.local/bin/claude points at .local/share/claude/versions/2.1.269.
        Resolving it at config load would pin the daemon to that build; Claude Code then
        updates, the version directory goes, and every spawn fails with ENOENT on a listener
        that is otherwise perfectly healthy and only re-reads config at startup.
        """
        versioned = os.path.join(self.tmp, "versions-1.2.3")
        shutil.copy(self.claude, versioned)
        link = os.path.join(self.tmp, "bin-claude")
        os.symlink(versioned, link)
        cfg = config.load(self.write(self.valid(claude_bin=link)))
        self.assertEqual(cfg.claude_bin, link)
        self.assertNotIn("versions-1.2.3", cfg.claude_bin)


class TestMaxSessions(Base):
    def test_it_defaults_to_two(self):
        # SPEC.md §3: 8 GB on this box.
        cfg = config.load(self.write(self.valid()))
        self.assertEqual(cfg.max_sessions, 2)

    def test_an_explicit_value_is_kept(self):
        self.assertEqual(config.load(self.write(self.valid(max_sessions=1))).max_sessions, 1)

    def test_zero_is_refused(self):
        self.refuses(self.valid(max_sessions=0), "max_sessions")

    def test_a_negative_value_is_refused(self):
        self.refuses(self.valid(max_sessions=-1), "max_sessions")

    def test_a_string_is_refused(self):
        self.refuses(self.valid(max_sessions="two"), "max_sessions")


class TestNoDefaultProject(Base):
    def test_a_default_project_key_is_refused_rather_than_ignored(self):
        # SPEC.md §5 and §15: bare `claude` lists projects and starts nothing. A stale key from
        # an older config would otherwise sit there looking effective.
        self.refuses(self.valid(default_project="beacon"), "default_project")

    def test_an_unknown_key_is_refused(self):
        self.refuses(self.valid(chat_id=123), "unknown")


class TestHappyPath(Base):
    def test_a_good_config_loads(self):
        cfg = config.load(self.write(self.valid()))
        self.assertEqual(cfg.bot_token, TOKEN)
        self.assertEqual(cfg.allowed_chat_ids, frozenset({987654321}))
        self.assertEqual(cfg.projects_root, os.path.realpath(self.projects))
        self.assertEqual(cfg.claude_bin, os.path.abspath(self.claude))
        self.assertEqual(cfg.max_sessions, 2)

    def test_the_repr_does_not_leak_the_token(self):
        # This object will end up in a log line or a traceback sooner or later.
        cfg = config.load(self.write(self.valid()))
        self.assertNotIn(TOKEN, repr(cfg))
        self.assertNotIn(TOKEN, str(cfg))


if __name__ == "__main__":
    unittest.main()
