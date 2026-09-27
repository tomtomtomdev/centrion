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
import re
import subprocess
import sys
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


class TestEverythingImportsHere(unittest.TestCase):
    """WINDOWS.md W1c: every module imports on the platform the tests are running on.

    Until W1c, `import session` on Windows died on `fcntl` before a single portable test could
    run, and the suite reported two import errors instead of a pass/skip count anybody could
    read. The seam (W1a, W1b) is what makes this possible; this test is what makes it stay
    true — a Unix-only import creeping back into a shared module fails here first, on either
    platform.
    """

    MODULES = ("commands", "config", "telegram", "session", "bot")

    def test_imports_on_this_platform(self):
        import importlib
        for name in self.MODULES:
            try:
                importlib.import_module(name)
            except ImportError as e:
                self.fail("%s does not import on %s: %s" % (name, sys.platform, e))

    def test_the_platform_module_is_the_right_one(self):
        import session
        expected = "session_win" if sys.platform == "win32" else "session_posix"
        self.assertEqual(session.procs.__name__, expected)


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


class TestTheWindowsStartup(unittest.TestCase):
    r"""WINDOWS.md §7, W5a: what the scheduled task runs, and what installs it.

    The Windows half of `test_bot.TestTheLaunchdInstall`, and static for exactly the same
    reason: every way these two files go wrong is silent. A `cd` that lands somewhere else
    runs `bot.py` from nowhere; a listener started without `--serve` prints usage and exits 2
    forever; a loop missing its `goto` restarts nothing and looks identical to a listener that
    is simply up. The only symptom of any of them is a phone that gets no reply and a
    `var\bot.log` that says nothing, hours later.

    Not decorated. These read files out of the repository, so the Mac checks them too — the
    Windows startup files are part of this repo on both boxes, and a `windows\` directory that
    goes missing in a merge should fail somewhere other than on the box that cannot notice.
    """

    def source(self, name):
        path = os.path.join(ROOT, "windows", name)
        self.assertTrue(os.path.isfile(path), "%s is missing — WINDOWS.md §7" % path)
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_the_two_startup_files_are_present(self):
        for name in ("bot.cmd", "install.ps1"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "windows", name)), name)

    def test_the_startup_files_are_tracked(self):
        # The Mac's launchd/ is in the repo; so is this. An ignore rule that swallowed
        # `windows/` would leave a checkout that installs nothing and says nothing.
        for name in ("windows/bot.cmd", "windows/install.ps1"):
            self.assertFalse(ignored(name), name)

    def test_the_windows_lockfile_is_present_and_tracked(self):
        # W2b: the suite itself is run from the venv this file builds, so a missing or
        # ignored requirements-win.txt is a box that cannot run its own tests.
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "requirements-win.txt")))
        self.assertFalse(ignored("requirements-win.txt"))

    def test_the_venv_and_the_scratch_directory_are_ignored(self):
        # `.venv` holds three pinned wheels and `scratch` holds spike scripts; neither is
        # source, and `.venv\Scripts\python.exe` committed to a repo is not a small mistake.
        for name in (".venv/pyvenv.cfg", ".venv/Scripts/python.exe", "scratch/w0a.py"):
            self.assertTrue(ignored(name), name)

    def test_bot_cmd_starts_the_listener_with_serve(self):
        # Without it `bot.py` takes the argparse default path, not the listener.
        self.assertIn("bot.py --serve", self.source("bot.cmd"))

    def test_bot_cmd_restarts_the_listener_on_every_exit(self):
        r"""§7's KeepAlive loop: a label, and a `goto` that reaches it from past the python.

        `KeepAlive true, unconditional` is the plist's wording and the reason is in §7 — a
        listener that has exited has stopped listening, and there is no successful version of
        that. So the `goto` must not be guarded by an errorlevel test.
        """
        cmd = self.source("bot.cmd").lower()
        self.assertIn(":loop", cmd)
        self.assertIn("goto loop", cmd)
        self.assertLess(cmd.index("bot.py --serve"), cmd.rindex("goto loop"),
                        "the goto must come after the listener, or nothing ever restarts")
        # Unconditional: no `goto` in this file is reached through an errorlevel test. Scoped
        # to the `goto` rather than to the whole file, because the throttle below has an
        # errorlevel branch of its own and that one is not about restarting.
        for line in cmd.splitlines():
            if "goto" in line:
                self.assertNotIn("errorlevel", line,
                                 "the restart is unconditional (§7): a listener that has "
                                 "exited has stopped listening, successfully or not")

    WAIT = r"(timeout /t 10\b|ping -n 11\b|sleep\(10\)|Start-Sleep 10\b)"

    def test_bot_cmd_throttles_the_restart_by_ten_seconds(self):
        r"""launchd's default ThrottleInterval, and the plist asks for exactly 10.

        A config error that makes the listener exit at once becomes a hot loop without this —
        two log lines every twenty milliseconds — and the loop's own logging is what fills
        the disk.

        One wait has to be reachable without a condition. `timeout` needs a console and
        `ping` does not, so this file has both and the second is behind an `if errorlevel`;
        a version with only the conditional one throttles nothing, and the first draft of
        this test was caught by exactly that mutation passing (W5a).
        """
        cmd = self.source("bot.cmd")
        body = cmd[cmd.index("bot.py --serve"):cmd.rindex("goto loop")]
        waits = [l.strip() for l in body.splitlines()
                 if not l.strip().lower().startswith(("rem ", "::"))
                 and re.search(self.WAIT, l, re.I)]
        self.assertTrue(waits, "no ten-second wait between the listener exiting and the restart")
        self.assertTrue(any(not w.lower().startswith("if ") for w in waits),
                        "every wait here is guarded: %r. One of them has to run whatever the "
                        "last command did, or the throttle is not a throttle." % (waits,))

    def test_bot_cmd_runs_this_checkout_through_its_own_venv(self):
        r"""A scheduled task has no working directory worth the name and no useful PATH.

        `%~dp0..` is the checkout relative to the file itself, which is what makes the same
        `bot.cmd` right in a clone under any path — the plist's `__CHECKOUT__` problem, solved
        by batch rather than by the installer. And the interpreter is the venv's: the system
        one imports everything and then fails every config load for want of pywin32 (W2b).
        """
        cmd = self.source("bot.cmd")
        self.assertIn("%~dp0..", cmd)
        self.assertIn(r".venv\Scripts\python.exe", cmd)

    def test_bot_cmd_logs_both_ends_of_every_run_to_the_listener_log(self):
        r"""§14's first diagnostic is `var\bot.log`, and on Windows nothing else writes it.

        launchd opens `StandardOutPath` itself; Task Scheduler opens nothing, so the append
        redirections here are the whole of the logging. Both ends, because a log that records
        only starts cannot be told apart from a listener that never comes back.
        """
        cmd = self.source("bot.cmd")
        self.assertIn(r"2>&1", cmd, "the listener's stderr is where log() writes")
        # Commands only. `rem` lines name the log too — `type var\bot.log` is the §14
        # instruction — and a comment is not a redirection.
        lines = [l for l in cmd.splitlines()
                 if r"var\bot.log" in l and not l.strip().lower().startswith(("rem ", "::"))]
        self.assertGreaterEqual(len(lines), 3, "start line, the listener itself, and exit line")
        self.assertTrue(all(">>" in l for l in lines),
                        "every redirection appends; `>` would truncate the log each restart")

    def test_install_hard_codes_no_home_and_derives_the_checkout(self):
        # The plist's lesson (test_bot.TestTheLaunchdInstall): a committed installer that
        # names one checkout is wrong in every other one.
        ps1 = self.source("install.ps1")
        self.assertIn("$PSScriptRoot", ps1)
        self.assertNotIn(r"C:\Users\tommy", ps1)

    def test_install_builds_the_venv_from_the_pinned_lockfile(self):
        ps1 = self.source("install.ps1")
        self.assertIn("venv", ps1)
        self.assertIn("requirements-win.txt", ps1)

    def test_install_says_how_to_lock_down_the_token(self):
        # W2b: `config.load()` refuses a `.telegram.json` any other principal can read, and
        # the printed line is documentation that is meant to be executed.
        self.assertIn("icacls", self.source("install.ps1"))


if __name__ == "__main__":
    unittest.main()
