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
import subprocess
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


@unittest.skipUnless(POSIX, "the file mode is the Mac's half of the secrecy pair; the Windows "
                     "half is TestTheWindowsDacl below (WINDOWS.md 5.1)")
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


@unittest.skipIf(POSIX, "the DACL is the Windows half of the secrecy pair above: WINDOWS.md 5.1")
class TestTheWindowsDacl(Base):
    """W2b: what "nobody else can read this" means on a filesystem with no mode bits.

    `stat.S_IMODE` answers 0666 for every ordinary file on NTFS, so the Mac's check cannot be
    ported -- it would refuse a perfectly private file and accept a world-readable one, both
    for the same reason: it is reading a number Windows made up. The DACL is where the answer
    actually lives, and these tests write real ACEs with `icacls` rather than mocking one,
    because the thing under test is whether we read the real list correctly.
    """

    def icacls(self, path, *args):
        out = subprocess.run(["icacls", path] + list(args), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, "icacls failed: %s%s" % (out.stdout, out.stderr))

    def grant(self, path, sid, right="(R)"):
        """Add an allow ACE for a well-known SID, the way a careless copy or a sync would."""
        self.icacls(path, "/grant", "*%s:%s" % (sid, right))

    def test_a_fresh_file_is_accepted(self):
        """The baseline -- and the reason this is not merely first-run friction.

        WINDOWS.md 5.1 predicted that a file under %USERPROFILE% inherits `Users`-readable ACEs
        and so a fresh install would hit this error once, on purpose. Measured, it does not:
        the profile root grants SYSTEM, Administrators and the user, and nothing else. If this
        test ever fails, the prediction was right after all and 5.1's note is what to re-read.
        """
        cfg = config.load(self.write(self.valid()))
        self.assertEqual(cfg.bot_token, TOKEN)

    def test_a_users_readable_config_is_refused(self):
        path = self.write(self.valid())
        self.grant(path, "S-1-5-32-545")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(path)
        msg = str(cm.exception)
        self.assertIn("Users", msg, "the refusal did not name who can read it: %r" % msg)
        self.assertIn("icacls", msg, "the refusal did not say how to fix it: %r" % msg)
        self.assertNotIn(TOKEN, msg)

    def test_an_everyone_readable_config_is_refused(self):
        path = self.write(self.valid())
        self.grant(path, "S-1-1-0")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(path)
        self.assertIn("Everyone", str(cm.exception))

    def test_an_authenticated_users_readable_config_is_refused(self):
        path = self.write(self.valid())
        self.grant(path, "S-1-5-11")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(path)
        self.assertIn("Authenticated Users", str(cm.exception))

    def test_a_grant_to_any_other_principal_is_refused_too(self):
        """Fail closed, which a list of three well-known SIDs does not.

        WINDOWS.md 5.1 named `Everyone`, `Users` and `Authenticated Users`. Those are the three
        that arrive by accident, but the property config.py claims in its first paragraph is
        that it fails closed, and a deny-list cannot: a grant to a second local account, to a
        domain group, or to `Guests` is exactly as readable and is on none of the three lists.
        The check allows the principals that are us or are already root on this box -- the
        current user, SYSTEM, Administrators, OWNER RIGHTS -- and refuses everything else.
        """
        path = self.write(self.valid())
        self.grant(path, "S-1-5-32-546")          # Guests: on no well-known list.
        with self.assertRaises(config.ConfigError) as cm:
            config.load(path)
        self.assertIn("Guests", str(cm.exception))

    def test_a_deny_ace_is_not_a_grant(self):
        """A DACL is not a list of names; some of its entries take access away.

        Denying `Users` would also deny us -- we are in `Users` -- so this denies `Guests`, who
        we are not. Reading the ACE type wrongly turns every hardened config file into a
        refusal, which is the failure mode that looks exactly like the check working.
        """
        path = self.write(self.valid())
        self.icacls(path, "/deny", "*S-1-5-32-546:(R)")
        cfg = config.load(path)
        self.assertEqual(cfg.bot_token, TOKEN)

    def test_a_traverse_only_ace_is_not_a_grant(self):
        """%USERPROFILE% carries an AppContainer ACE with mask 0x100020 -- SYNCHRONIZE and
        FILE_TRAVERSE, and no right to read a byte. A right is a bitmask, not a yes or no."""
        path = self.write(self.valid())
        self.grant(path, "S-1-5-32-546", "(X)")
        cfg = config.load(path)
        self.assertEqual(cfg.bot_token, TOKEN)

    def test_the_printed_fix_actually_fixes_it(self):
        """The message ends in a command. This runs *that* command -- the literal text, through
        cmd, so `%USERNAME%` expands the way it will when it is pasted -- and then asserts the
        file loads. The message is documentation that is executed, so it cannot quietly rot.

        It did rot once, before it was ever shipped: WINDOWS.md 5.1's line was
        `/inheritance:r /grant:r "%USERNAME%":F`, and `/inheritance:r` removes only *inherited*
        entries. Against an explicit `Users` grant -- the only kind of file this error is ever
        printed about -- it left the grant exactly where it was.
        """
        path = self.write(self.valid())
        self.grant(path, "S-1-5-32-545")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(path)
        line = str(cm.exception).split("Fix with: ", 1)[1]
        out = subprocess.run(line, shell=True, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, "the printed fix would not run: %s" % line)
        cfg = config.load(path)
        self.assertEqual(cfg.bot_token, TOKEN)

    def test_the_fix_line_names_the_principal_it_has_to_remove(self):
        """Not a deny-list of three SIDs in a format string: the line is built from what was
        read off this file, so it removes whoever is actually on it."""
        path = self.write(self.valid())
        self.grant(path, "S-1-5-32-546")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(path)
        self.assertIn('/remove:g "BUILTIN\\Guests"', str(cm.exception))

    def test_the_mode_is_not_consulted(self):
        """0644 here is not a fact about anything, and the file is still ours alone."""
        cfg = config.load(self.write(self.valid(), mode=0o644))
        self.assertEqual(cfg.bot_token, TOKEN)

    def test_an_unreadable_security_descriptor_is_refused(self):
        """Fail closed: not knowing who can read it is not the same as nobody can."""
        path = self.write(self.valid())
        with mock.patch.object(config, "_descriptor", side_effect=OSError(5, "Access is denied")):
            with self.assertRaises(config.ConfigError) as cm:
                config.load(path)
        self.assertIn("permissions", str(cm.exception).lower())

    def test_a_null_dacl_is_refused(self):
        """A NULL DACL is not an empty one -- it grants everyone everything."""
        path = self.write(self.valid())
        with mock.patch.object(config, "_dacl", return_value=None):
            with self.assertRaises(config.ConfigError) as cm:
                config.load(path)
        self.assertIn("everyone", str(cm.exception).lower())

    def test_an_ace_shape_we_do_not_understand_is_refused(self):
        """Object ACEs come back from GetAce as a different tuple. Guess at one, and the guess
        is a grant quietly skipped."""
        path = self.write(self.valid())
        with mock.patch.object(config, "_aces", return_value=[((5, 0), 0x1F01FF, None, "more")]):
            with self.assertRaises(config.ConfigError) as cm:
                config.load(path)
        self.assertIn("access-control entry", str(cm.exception).lower())

    def test_pywin32_missing_is_refused_by_name(self):
        """The check needs `win32security`. Without it there is no answer, so there is no load,
        and the message names `requirements-win.txt` rather than an ImportError at a caller."""
        path = self.write(self.valid())
        with mock.patch.object(config, "_win32security", side_effect=ImportError("nope")):
            with self.assertRaises(config.ConfigError) as cm:
                config.load(path)
        msg = str(cm.exception)
        self.assertIn("requirements-win.txt", msg)
        self.assertNotIn(TOKEN, msg)


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
