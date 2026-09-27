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
import xml.etree.ElementTree as ET

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

    def test_the_three_startup_files_are_present(self):
        for name in ("bot.cmd", "install.ps1", "centrion.xml"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "windows", name)), name)

    def test_the_startup_files_are_tracked(self):
        # The Mac's launchd/ is in the repo; so is this. An ignore rule that swallowed
        # `windows/` would leave a checkout that installs nothing and says nothing.
        #
        # Vacuous while a file is absent — `git check-ignore --no-index` matches patterns, not
        # files, and answers "not ignored" for a path that is not there (W5a found this the
        # hard way). The presence check above is what makes this one mean something, which is
        # why the two are written as a pair and always listed together.
        for name in ("windows/bot.cmd", "windows/install.ps1", "windows/centrion.xml"):
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

    def test_nothing_under_windows_is_anything_but_ascii(self):
        r"""W5b, and it is here because W5a's `install.ps1` **did not parse at all**.

        Windows PowerShell 5.1 reads a `.ps1` with no byte order mark in the ANSI code page,
        not UTF-8. A U+2014 em dash is `E2 80 94` there, and `0x94` in cp1252 is a curly
        closing quote, which PowerShell honours as a string terminator — so one em dash
        inside one `throw "..."` unbalances every quote after it. The committed file had
        nine of them and `powershell -File windows\install.ps1`, the invocation its own
        header gives, answered nine parse errors and ran nothing (measured, W5b; there is no
        pwsh on this box, and pwsh is the only version that would have read it as UTF-8).

        The other two files have the same hazard in weaker forms: `cmd.exe` reads a batch
        file in the OEM code page, and `schtasks` will not read a task XML with a UTF-8 BOM
        at all. One rule for the directory is cheaper than three exceptions, and this is the
        test that would have caught it — a static one, because the symptom is a startup file
        that silently does nothing.

        Portable on purpose: this is a property of the bytes, and the Mac can read bytes.
        """
        for name in ("bot.cmd", "install.ps1", "centrion.xml"):
            path = os.path.join(ROOT, "windows", name)
            with open(path, "rb") as fh:
                raw = fh.read()
            try:
                raw.decode("ascii")
            except UnicodeDecodeError as e:
                self.fail("%s is not ASCII at byte %d (%r) — see this test's docstring"
                          % (name, e.start, raw[max(0, e.start - 20):e.start + 20]))


#: The Windows counterpart of `test_bot.this_mac_checkout`. The rendered task names this
#: checkout's paths in Windows spelling, so "absolute, and present" is a question only this
#: platform can answer: on the Mac `__CHECKOUT__\windows\bot.cmd` renders to a posix prefix
#: with two backslashes glued on the end, which is not a path anywhere.
this_windows_checkout = unittest.skipUnless(
    sys.platform == "win32",
    "the task XML names Windows paths; the plist's half is test_bot.TestTheLaunchdInstall")

#: What the committed XML says where `install.ps1` writes this checkout's path, and this
#: box's user. The plist needs only the first, and the difference is not cosmetic: a
#: LaunchAgent is this user's because of the directory it is installed into, where a
#: scheduled task lives in one machine-global store and has to name its principal. A
#: `LogonTrigger` with no `UserId` means *any* user's logon and `schtasks /Create` answers
#: "Access is denied" for it as an unprivileged user — measured, W5b.
CHECKOUT = "__CHECKOUT__"
USER = "__USER__"

TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"


def this_user():
    """What `install.ps1` writes where the XML says `__USER__`. `DOMAIN\\name`."""
    return r"%s\%s" % (os.environ.get("USERDOMAIN", "X"), os.environ.get("USERNAME", "y"))


class TestTheScheduledTask(unittest.TestCase):
    r"""WINDOWS.md §7, W5b: `windows\centrion.xml`, the Windows half of the plist.

    Static, for the plist's reasons exactly (`test_bot.TestTheLaunchdInstall`) and one more
    of its own. Task Scheduler validates the *schema* at registration and nothing else: a
    task with `ExecutionTimeLimit` left at its three-day default registers perfectly and
    kills the listener on the fourth day, a `MultipleInstancesPolicy` of `Parallel` registers
    perfectly and starts a second copy that W4d's mutex then refuses every ten seconds
    forever, and `DisallowStartIfOnBatteries` — the *default* on a laptop — is a bot that
    simply never starts when the charger is out. None of those says anything anywhere.

    Most of this is portable and undecorated, like `TestTheWindowsStartup` above: the XML is
    in this repository on both boxes and a merge that drops it should fail on the machine
    that cannot otherwise notice. The three that compare it against *this* checkout are
    gated, and they are the counterparts of the three `@this_mac_checkout` plist checks.
    """

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(ROOT, "windows", "centrion.xml")
        with open(cls.path, "rb") as fh:
            cls.raw = fh.read()
        cls.template = cls.raw.decode("ascii")
        # Checked as install.ps1 would register it here, the way the plist tests do.
        cls.task = ET.fromstring(cls.rendered(ROOT))

    @classmethod
    def rendered(cls, checkout):
        return cls.template.replace(CHECKOUT, checkout).replace(USER, this_user())

    def one(self, path):
        el = self.task.find("/".join(TASK_NS + part for part in path.split("/")))
        self.assertIsNotNone(el, r"%s is missing from windows\centrion.xml — §7" % path)
        return (el.text or "").strip()

    # ---- portable -----------------------------------------------------------------

    def test_the_committed_xml_names_no_home_and_no_user(self):
        r"""The plist's lesson (`test_the_committed_plist_names_no_home`), twice over.

        `fce1d4a` hard-coded one Mac's home into the plist and broke three tests on the
        other. Here there are two things that differ per box, not one, and §9's W5b
        predicted only the checkout.
        """
        self.assertNotIn(r"C:\Users", self.template)
        self.assertIn(CHECKOUT, self.template)
        self.assertIn(USER, self.template)

    def test_the_prolog_is_the_one_spelling_schtasks_and_expat_both_accept(self):
        r"""Measured, W5b, and every wrong answer is "ERROR: The task XML is malformed."

        `schtasks /Create /XML` refuses `encoding="UTF-8"` in the prolog outright — "unable
        to switch the encoding" — and refuses a UTF-8 BOM with "incorrect document syntax"
        at (1,2), which is the byte order mark itself. It accepts `encoding="UTF-16"`, but
        Python's expat then refuses the file the other way round unless the bytes really are
        UTF-16, and a UTF-16 blob in the repository is a file git diffs as binary.

        `<?xml version="1.0"?>`, ASCII, no BOM, is the one spelling both accept. It is also
        the one that a `> centrion.xml` from PowerShell 5.1 silently breaks, because
        `Out-File -Encoding utf8` there writes a BOM.
        """
        import codecs
        for bom, name in ((codecs.BOM_UTF8, "UTF-8"), (codecs.BOM_UTF16_LE, "UTF-16 LE"),
                          (codecs.BOM_UTF16_BE, "UTF-16 BE")):
            self.assertFalse(self.raw.startswith(bom),
                             "a %s BOM: schtasks answers 'incorrect document syntax'" % name)
        prolog = self.template.split("\n", 1)[0]
        self.assertIn("<?xml version=", prolog)
        self.assertNotIn("encoding=", prolog,
                         "an encoding declaration is either refused by schtasks (UTF-8) or "
                         "by expat (UTF-16); %r" % prolog)

    def test_the_settings_are_section_7s_table(self):
        self.assertEqual(self.one("Settings/ExecutionTimeLimit"), "PT0S",
                         "the default is 3 days, after which the listener is killed")
        self.assertEqual(self.one("Settings/MultipleInstancesPolicy"), "IgnoreNew",
                         "belt for W4d's braces")
        # A laptop. Both of these default the wrong way for a bot that should always be up.
        self.assertEqual(self.one("Settings/DisallowStartIfOnBatteries"), "false")
        self.assertEqual(self.one("Settings/StopIfGoingOnBatteries"), "false")
        self.assertEqual(self.one("Settings/StartWhenAvailable"), "true",
                         "a missed logon trigger still fires")
        self.assertEqual(self.one("Settings/Hidden"), "true", "no console on the desktop")
        self.assertEqual(self.one("Settings/Enabled"), "true")
        self.assertEqual(self.one("Settings/RestartOnFailure/Interval"), "PT1M")
        self.assertEqual(self.one("Settings/RestartOnFailure/Count"), "3")

    def test_the_priority_is_not_the_default_that_throttles_every_session(self):
        r"""The plist's `ProcessType Standard, NOT Background`, spelled for Windows.

        A task registered without a `<Priority>` gets **7**, which is
        `BELOW_NORMAL_PRIORITY_CLASS`, and a spawned process inherits its parent's priority
        class — so every Claude Code session started from the phone would run below normal
        for as long as it lived. 4, 5 and 6 are all `NORMAL_PRIORITY_CLASS`. The symptom of
        getting this wrong is "Remote Control feels slow", days later, with nothing in any
        log, which is word for word what the plist says about `ProcessType`, because it is
        the same bug with a different name. §7's table did not have this row before W5b.
        """
        self.assertIn(self.one("Settings/Priority"), ("4", "5", "6"),
                      "7 (the default) and up are BELOW_NORMAL or worse, and the sessions "
                      "inherit it")

    def test_the_task_can_be_started_on_demand(self):
        # W5c's whole checklist is `schtasks /End` then `schtasks /Run`, and a task with
        # AllowStartOnDemand false refuses the second with an error nothing else explains.
        self.assertEqual(self.one("Settings/AllowStartOnDemand"), "true")

    def test_it_runs_as_the_logged_on_user_with_no_stored_password(self):
        self.assertEqual(self.one("Principals/Principal/LogonType"), "InteractiveToken")
        self.assertEqual(self.one("Principals/Principal/UserId"), this_user())

    def test_the_trigger_is_this_users_logon(self):
        # launchd's RunAtLoad. And the UserId is not optional: see the note on USER above.
        trigger = self.task.find(TASK_NS + "Triggers/" + TASK_NS + "LogonTrigger")
        self.assertIsNotNone(trigger, "no LogonTrigger — §7's RunAtLoad analogue")
        self.assertEqual(self.one("Triggers/LogonTrigger/Enabled"), "true")
        self.assertNotEqual(self.one("Triggers/LogonTrigger/UserId"), "")

    def test_the_action_runs_bot_cmd_through_the_command_processor(self):
        r"""`cmd.exe /c <checkout>\windows\bot.cmd`, and the loop is in the batch file.

        Task Scheduler runs an action once and is finished — there is no `KeepAlive` here,
        which is why `bot.cmd` has one. An action that pointed straight at `bot.py` would be
        a listener that stops for good the first time it exits.
        """
        self.assertTrue(self.one("Actions/Exec/Command").lower().endswith("cmd.exe"),
                        self.one("Actions/Exec/Command"))
        self.assertIn(r"\windows\bot.cmd", self.one("Actions/Exec/Arguments"))

    def test_the_only_thing_that_writes_the_log_is_the_file_the_task_runs(self):
        r"""The counterpart of `test_the_logs_land_where_section_14_says_to_look`.

        There is nothing to compare it against directly: **Task Scheduler has no
        `StandardOutPath`**. launchd opens the log itself before the job starts; the
        scheduler opens nothing at all, so the whole of §14's first diagnostic is the
        redirections inside `bot.cmd`. This asserts the join — the action is that file, and
        that file is the one that appends `var\bot.log`.
        """
        # The parsed tree, not the text: the header comment names `type var\bot.log` as the
        # §14 instruction, exactly as the plist's does, and a comment is not a setting.
        for el in self.task.iter():
            self.assertNotIn("bot.log", (el.text or ""),
                             "%s names a log the scheduler will never open" % el.tag)
        with open(os.path.join(ROOT, "windows", "bot.cmd"), encoding="utf-8") as fh:
            cmd = fh.read()
        self.assertIn(r">> var\bot.log", cmd)
        self.assertIn("bot.cmd", self.one("Actions/Exec/Arguments"))

    def test_the_token_is_not_in_the_task(self):
        # The plist's §10.5 rule. A task's XML is readable by anyone who can query the
        # scheduler, and the committed one is readable by anyone at all.
        self.assertNotIn("bot_token", self.template)
        self.assertNotIn("AAH", self.template)

    @staticmethod
    def code(name):
        r"""`windows\<name>` with its comments taken out.

        Written because the first version of the test below was not: `install.ps1`'s header
        block *documents* `schtasks /Create /XML /F`, so deleting the call that actually
        runs it changed nothing any assertion could see. Caught by the mutation pass and
        not by the suite, which is W4c's rule for the third slice running — "the test
        exists" and "the test would fail" are different claims.
        """
        with open(os.path.join(ROOT, "windows", name), encoding="utf-8") as fh:
            body = fh.read()
        body = re.sub(r"(?s)<#.*?#>", "", body)          # the header block
        return "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))

    def test_install_registers_the_task_from_the_rendered_xml(self):
        ps1 = self.code("install.ps1")
        self.assertIn("centrion.xml", ps1)
        # String literals out too, and for the same reason the comments went: the script's
        # own `throw "schtasks /Create failed ..."` kept this assertion green through a
        # mutation that deleted the call it names. Twice in one test, from two directions.
        statements = re.sub(r'"[^"\n]*"', '""', ps1)
        self.assertRegex(statements, r"schtasks\s+/Create\b",
                         "nothing outside the comments and messages registers the task")
        self.assertIn("/TN", statements)
        for placeholder in (CHECKOUT, USER):
            self.assertIn(placeholder, ps1, "%s is never substituted" % placeholder)

    def test_install_writes_the_rendered_xml_without_a_bom(self):
        r"""The trap, and it is the natural PowerShell spelling.

        `Out-File -Encoding utf8` and `Set-Content -Encoding utf8` both write a BOM in
        PowerShell 5.1, which is the shell this script is run under, and `schtasks` answers
        "The task XML is malformed" to it with a column number and no further help.
        """
        ps1 = self.code("install.ps1")
        self.assertIn("UTF8Encoding($false)", ps1,
                      "the rendered XML must be written BOM-less; -Encoding utf8 is not")
        for line in ps1.splitlines():
            if re.search(r"(Out-File|Set-Content)[^#\n]*-Encoding\s+utf8", line, re.I):
                self.fail("PowerShell 5.1 writes a BOM there: %s" % line.strip())

    # ---- this checkout ------------------------------------------------------------

    @this_windows_checkout
    def test_every_path_in_the_task_is_absolute_and_present(self):
        # The scheduler has no shell, no `cd` and a PATH it does not promise. It *does*
        # expand environment variables in Command, which is how `cmd.exe` is named without
        # hard-coding a drive letter.
        for path in (os.path.expandvars(self.one("Actions/Exec/Command")),
                     self.one("Actions/Exec/WorkingDirectory"),
                     self.one("Actions/Exec/Arguments").split('"')[1]):
            self.assertTrue(os.path.isabs(path), path)
            self.assertTrue(os.path.exists(path), path)

    @this_windows_checkout
    def test_it_runs_this_checkout(self):
        self.assertEqual(os.path.realpath(self.one("Actions/Exec/WorkingDirectory")),
                         os.path.realpath(ROOT))
        self.assertEqual(os.path.realpath(self.one("Actions/Exec/Arguments").split('"')[1]),
                         os.path.realpath(os.path.join(ROOT, "windows", "bot.cmd")))

    @this_windows_checkout
    def test_install_renders_the_task_xml_these_checks_read(self):
        r"""`install.ps1 -Print`, the analogue of `sh launchd/install.sh --print`.

        Without it the rendering is only ever exercised by registering a real task, and the
        substitution is the one step between a committed file that is right everywhere and a
        task that runs a checkout which does not exist.
        """
        got = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             os.path.join(ROOT, "windows", "install.ps1"), "-Print"],
            cwd=ROOT, stdout=subprocess.PIPE, check=True).stdout
        text = got.decode("utf-8-sig", errors="replace")
        self.assertNotIn(CHECKOUT, text)
        self.assertNotIn(USER, text)
        printed = ET.fromstring(text.lstrip())
        self.assertEqual(ET.tostring(printed), ET.tostring(self.task))


if __name__ == "__main__":
    unittest.main()
