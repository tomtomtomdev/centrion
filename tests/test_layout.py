#!/usr/bin/env python3
"""Slice 0 — the repository cannot commit a bot token.

`.telegram.json` holds a credential that is, per SPEC.md §10, full shell access to this Mac.
Committing it once is unrecoverable: it is in the reflog, and on any remote it is public. This
is the only test that can prevent that, and it has to exist before the token does.

The counterpart matters just as much. `tests/fixtures/*.log` is a captured PTY transcript that
the URL scrape is tested against (§12 slice 6), and the obvious `*.log` ignore rule would make
it vanish from the repo without a word. So this asserts in both directions.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ignored(path):
    """True if git ignores `path`. Asks git rather than re-implementing its matching rules."""
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", path],
                       cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode not in (0, 1):
        raise AssertionError(
            "git check-ignore failed (exit %d) — is %s a git repository?" % (r.returncode, ROOT))
    return r.returncode == 0


class TestRepositoryExists(unittest.TestCase):
    def test_the_project_is_a_git_repository(self):
        self.assertTrue(os.path.isdir(os.path.join(ROOT, ".git")),
                        "no .git — slice 0 starts with `git init`")

    def test_there_is_a_gitignore(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".gitignore")))


class TestSecretsAreIgnored(unittest.TestCase):
    def test_the_telegram_config_is_ignored(self):
        self.assertTrue(ignored(".telegram.json"),
                        "the bot token would be committable")

    def test_a_backup_of_the_telegram_config_is_ignored(self):
        # An editor swapfile or a hand-made copy carries the same token.
        for name in (".telegram.json.bak", ".telegram.json.orig", ".telegram.json~"):
            self.assertTrue(ignored(name), name)

    def test_runtime_state_is_ignored(self):
        # var/ holds session transcripts, which per §10 may quote anything the session saw.
        for name in ("var/bot.log", "var/offset", "var/sessions/3f2a91/pty.log"):
            self.assertTrue(ignored(name), name)


class TestFixturesAreNotIgnored(unittest.TestCase):
    def test_the_captured_transcript_is_tracked(self):
        # A blanket `*.log` rule would silently drop the slice 6 fixture.
        self.assertFalse(ignored("tests/fixtures/rc_startup.log"),
                         "the RC startup fixture must stay in the repo")

    def test_the_fixture_is_actually_present(self):
        f = os.path.join(ROOT, "tests", "fixtures", "rc_startup.log")
        self.assertTrue(os.path.isfile(f))
        with open(f, encoding="utf-8", errors="replace") as fh:
            self.assertIn("claude.ai/code/session_", fh.read())

    def test_the_source_files_are_tracked(self):
        for name in ("bot.py", "commands.py", "config.py", "session.py", "telegram.py",
                     "SPEC.md", "tests/test_layout.py"):
            self.assertFalse(ignored(name), name)


if __name__ == "__main__":
    unittest.main()
