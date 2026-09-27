#!/usr/bin/env python3
"""Slice 2 — a project name can only ever name a direct child of `projects_root`.

This is the security boundary, which is why it is its own slice and its own file. SPEC.md §10.4
is the claim being defended: *no message can express a directory outside `~/Projects`*. The bot
spawns `--dangerously-skip-permissions` sessions, so the working directory it picks is the whole
of the blast radius — `claude ../../etc` and `claude ~/Documents/Junction` have to be a `help`
reply and not a session in `/etc`.

The four checks of SPEC.md §3 get a class each, in order, because the order is load-bearing: a
name that escapes the root is refused at check 3 whether or not the directory it escapes to
happens to exist, so an attacker learns nothing about the filesystem from which error came back.

The second property, asserted on every rejection: **the message never contains a path.** These
strings reach var/bot.log and, as a `help` reply, Telegram — which per §10 sees everything. A
refusal that helpfully quoted the resolved path would turn every rejected name into a one-shot
filesystem probe.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import os
import shutil
import tempfile
import unittest

import config
from tests.support import POSIX, needs_symlinks


class Base(unittest.TestCase):
    """A projects root with one real project in it, plus somewhere outside to escape to.

    Note that `tempfile.mkdtemp()` hands back a path under `/var/folders/...`, and `/var` on
    macOS is a symlink to `/private/var`. So the root here is *not* its own realpath, which is
    the same shape the real config has and exactly the case a naive string prefix check gets
    wrong. Every test below runs against the unresolved spelling on purpose.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="centrion-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "Projects")
        os.mkdir(self.root)
        self.beacon = os.path.join(self.root, "beacon")
        os.mkdir(self.beacon)
        self.outside = os.path.join(self.tmp, "Documents")
        os.mkdir(self.outside)

    def refuses(self, name, because):
        """Assert resolve() raises, that it says why, and that it never says a path."""
        with self.assertRaises(config.ProjectError) as cm:
            config.resolve(name, self.root)
        msg = str(cm.exception)
        self.assertIn(because, msg.lower(), "message did not name the problem: %r" % msg)
        # §10: this text reaches Telegram. It must not describe the filesystem.
        for leak in (self.root, self.tmp, self.outside, os.path.realpath(self.root),
                     os.path.expanduser("~")):
            self.assertNotIn(leak, msg, "the refusal leaked a path: %r" % msg)
        # Skip `.` and `..`: they are substrings of any sentence with punctuation in it, so the
        # echo check is only meaningful for names long enough to be distinctive.
        if isinstance(name, str) and len(name) > 2:
            self.assertNotIn(name, msg, "the refusal echoed the attempted name: %r" % msg)
        return msg


class TestCheck1TheNameIsAName(Base):
    """§3.1 — reject `/`, `\\`, a leading `.`, and the names `.` and `..`."""

    def test_a_name_with_a_slash_is_refused(self):
        self.refuses("sub/dir", "name")

    def test_a_name_with_a_backslash_is_refused(self):
        self.refuses("sub\\dir", "name")

    def test_dot_dot_is_refused(self):
        self.refuses("..", "name")

    def test_dot_is_refused(self):
        self.refuses(".", "name")

    def test_a_leading_dot_is_refused(self):
        # ~/Projects/.git and friends are not projects, and `.telegram.json` lives one level up.
        self.refuses(".hidden", "name")

    def test_the_empty_string_is_refused(self):
        # os.path.join(root, "") is the root itself — a session in ~/Projects, not in a project.
        with self.assertRaises(config.ProjectError):
            config.resolve("", self.root)

    def test_a_traversal_is_refused(self):
        # §10.4 names this one explicitly.
        self.refuses("../../etc", "name")

    def test_a_traversal_dressed_as_a_project_is_refused(self):
        self.refuses("beacon/../../..", "name")

    def test_an_absolute_path_is_refused(self):
        # The reason check 1 has to come first: os.path.join(root, "/etc") is "/etc". Join
        # silently discards the root when the second argument is absolute, so a containment
        # check that trusted join alone would be comparing /etc against /etc's own parent.
        self.refuses("/etc", "name")

    def test_a_home_relative_path_is_refused(self):
        # Nothing expands `~` here, but §10.4 promises `claude ~/Documents/Junction` is a help
        # reply, and the slash is what makes that true.
        self.refuses("~/Documents/Junction", "name")

    def test_an_embedded_null_is_refused(self):
        # realpath() raises ValueError on a NUL rather than returning a path; refuse it as a
        # malformed name instead of letting it surface as a crash in the poll loop.
        self.refuses("beacon\x00", "name")

    def test_a_non_string_is_refused(self):
        # A parser bug must land here, not in os.path.join's TypeError.
        self.refuses(None, "name")
        self.refuses(3, "name")


    def test_a_control_character_is_not_a_name_either(self):
        # Both verbs, one rule (§12 slice 11). A directory with an escape sequence in its name
        # repaints var/bot.log around itself when §14 reads it with `tail -f`; loggable()
        # defends the log, and this defends everything else.
        for name in ("a\bb", "red\x1b[31m", "two\nlines"):
            self.refuses(name, "name")


@unittest.skipIf(POSIX, "the drive and UNC shapes are NTFS path syntax; on the Mac `C:foo` is "
                 "an ordinary directory name: WINDOWS.md §5.3")
class TestCheck1OnWindows(Base):
    r"""§3.1 on NTFS — the two path shapes `os.path.isabs` does not call absolute.

    Check 1's job is that a name is a name, and on the Mac `/` carries every way of saying
    otherwise. Windows has two more, and only one of them is caught by anything already here:

    - `C:foo` is *drive-relative*. It means "foo, under whatever the process's current
      directory on drive C: happens to be" — a per-drive cwd the runner never sets and cannot
      read back. `isabs("C:foo")` is False and `join(root, "C:foo")` keeps the root, so check 3
      would compare a path against its parent and pass, and the session would start in a
      directory nobody named. This is the one the backslash rule does not see.
    - `C:\x` and `\\srv\share` are already refused, by the backslash in them. They are pinned
      here anyway because §5.3 names all three shapes as one rule: the containment argument
      must not rest on the incidental fact that this platform spells its separator with a
      character check 1 happens to list.
    """

    #: Check 1's exact wording. Asserting on "name" alone would pass on check 4's "no project
    #: by that name exists here" — which is what a drive-relative name got before this rule,
    #: and is the wrong refusal for the right-looking reason.
    CHECK_1 = "not a project name"

    def test_a_drive_relative_name_is_refused(self):
        self.refuses("C:foo", self.CHECK_1)

    def test_a_drive_absolute_name_is_refused(self):
        self.refuses(r"C:\Windows", self.CHECK_1)

    def test_a_unc_name_is_refused(self):
        self.refuses(r"\\srv\share", self.CHECK_1)

    def test_creating_one_refuses_the_same_shapes(self):
        """`new` shares checks 1-3 with `claude` through _child() (§12 slice 11).

        The assertion that matters is the second one: `create` inverts check 4, so a name
        check 1 lets through does not stop at "no project by that name exists" — it makes the
        directory. Refusing late and refusing not at all are the same thing here.
        """
        before = sorted(os.listdir(self.root))
        for name in ("C:foo", r"C:\Windows", r"\\srv\share"):
            with self.assertRaises(config.ProjectError):
                config.create(name, self.root)
        self.assertEqual(sorted(os.listdir(self.root)), before)


class TestCheck2AndCheck3ItStaysInsideTheRoot(Base):
    """§3.2/§3.3 — realpath the join, and require its parent to be exactly realpath(root)."""

    @needs_symlinks
    def test_a_symlink_pointing_outside_the_root_is_refused(self):
        # The case the realpath is for. The name is a perfectly ordinary one — no slash, no
        # dot — so it survives check 1, and only resolving it reveals it leaves the root.
        os.symlink(self.outside, os.path.join(self.root, "junction"))
        self.refuses("junction", "root")

    @needs_symlinks
    def test_a_symlink_pointing_outside_is_refused_even_though_its_target_exists(self):
        # Refused at check 3, before check 4 ever asks whether it is a directory — so the
        # reply is the same whether the escape target exists or not, and probing is useless.
        os.symlink("/etc", os.path.join(self.root, "etc"))
        self.refuses("etc", "root")

    @needs_symlinks
    def test_a_symlink_to_a_sibling_inside_the_root_is_allowed(self):
        # Check 3 is "stays in the root", not "is not a symlink". A link to a real project
        # beside it is still a direct child of the root and must keep working.
        os.symlink(self.beacon, os.path.join(self.root, "beacon-alias"))
        self.assertEqual(config.resolve("beacon-alias", self.root),
                         os.path.realpath(self.beacon))

    @needs_symlinks
    def test_a_symlink_to_a_grandchild_of_the_root_is_refused(self):
        # ~/Projects/thing -> ~/Projects/mono/packages/thing resolves to a path whose parent is
        # not the root. §3.3 says direct child, so this is refused; the fix is to name the real
        # directory, not to loosen the check.
        nested = os.path.join(self.beacon, "inner")
        os.mkdir(nested)
        os.symlink(nested, os.path.join(self.root, "inner-alias"))
        self.refuses("inner-alias", "root")

    def test_an_unresolved_root_still_resolves_its_children(self):
        # self.root is under /var/folders, and /var is a symlink to /private/var. If resolve()
        # compared against the root as given rather than its realpath, every single name would
        # be refused as "outside the root" on this machine.
        if self.root == os.path.realpath(self.root):
            # Windows: %TEMP% is its own realpath, so the premise is the Mac's alone.
            self.skipTest("this box's temp dir is its own realpath; the premise is /var -> /private/var")
        self.assertEqual(config.resolve("beacon", self.root), os.path.realpath(self.beacon))


class TestCheck4ItIsADirectory(Base):
    """§3.4 — a project is a directory, and one that is actually there."""

    def test_a_regular_file_is_refused(self):
        open(os.path.join(self.root, "README.md"), "w").close()
        self.refuses("README.md", "directory")

    def test_a_name_that_does_not_exist_is_refused(self):
        self.refuses("nosuchproject", "exist")

    @needs_symlinks
    def test_a_dangling_symlink_is_refused(self):
        r"""The target is spelled through the *resolved* root, and W6 is why.

        This test had never run anywhere — this desk's account cannot create symlinks — and
        the first GitHub Windows runner to execute it failed it, refusing at check 3 instead
        of check 4. The cause is a property of `realpath` worth writing down: for a link
        whose target does not exist, `ntpath` cannot ask the OS to canonicalise it and falls
        back to `_readlink_deep`, which returns **the spelling stored in the reparse point,
        verbatim**. A runner's `%TEMP%` is `C:\Users\RUNNER~1\...`, an 8.3 alias, so the
        stored target's dirname is the short spelling and `realpath(root)` is the long one:
        not equal, so check 3 fires first and the message names containment, not existence.

        Nothing is unsafe about that — a dangling link is refused either way, and a stored
        target that *did* match the resolved root would name a direct child that is not
        there, which is check 4's refusal — but it is the wrong refusal for this test's
        subject, which is check 4. Spelling the target through `realpath(self.root)` makes
        the fixture a dangling sibling on both platforms instead of an aliased one, without
        weakening the assertion. WINDOWS.md §5.3 carries the finding.
        """
        os.symlink(os.path.join(os.path.realpath(self.root), "gone"),
                   os.path.join(self.root, "dangling"))
        self.refuses("dangling", "exist")


class TestAnUnresolvableReparseTarget(Base):
    r"""W7's decision: `_child` does **not** canonicalise a link whose target is not there.

    W6 found the behaviour and left the call to W7 (WINDOWS.md §5.3). The shape: on Windows,
    for a dangling reparse point `ntpath.realpath` cannot ask the OS and falls back to
    `_readlink_deep`, which returns **the spelling stored in the link, verbatim** — no
    canonicalisation of any part of it. So a dangling link inside the root whose stored
    target is spelled through an *alias* of the root (a runner's `%TEMP%` is the 8.3
    `C:\Users\RUNNER~1\...`) has a dirname the resolved root does not equal, and check 3
    refuses it for containment rather than check 4 for existence. On the Mac
    `posixpath.realpath` resolves every component it can and only appends the missing leaf,
    so the same fixture reaches check 4 there.

    W7 decided to leave `_child` as it is, and the reason is the first test below: **the
    refusal is the same refusal either way.** What canonicalising would buy is one nicer
    sentence on a phone, for a name that only exists if somebody made a broken link by hand
    inside `projects_root`; what it would cost is a second resolution pass inside the one
    function §10.4 is a promise about, whose whole job is to give a name already judged
    outside the root another chance — and it would run `realpath` over a string read out of
    a reparse point, which is attacker-controlled text that check 1 never saw and that the
    OS itself declined to resolve. `config.py`'s first paragraph says everything in it fails
    closed; "the resolution did not complete, so refuse" is the closed answer and "resolve
    it again, differently" is not.

    Every test here needs a symlink, so **none of them can run at this desk** (W1c's
    `needs_symlinks`: this account has neither Developer Mode nor
    `SeCreateSymbolicLinkPrivilege`). They run on CI, which is where the finding came from
    and where the guard has to live.
    """

    def alias_root(self):
        r"""The root spelled the way it was handed to us, when that is not its realpath.

        `tempfile.mkdtemp` gives `/var/folders/...` on macOS (`/var` → `/private/var`) and
        `C:\Users\RUNNER~1\...` on a GitHub Windows runner, and on both the premise holds. On
        a Windows box whose `%TEMP%` is already its own realpath there is no alias to store
        and nothing here has a subject — the same skip `test_an_unresolved_root_still_
        resolves_its_children` takes, for the same reason.
        """
        if self.root == os.path.realpath(self.root):
            self.skipTest("this box's temp dir is its own realpath; there is no alias to store")
        return self.root

    @needs_symlinks
    def test_a_dangling_link_is_refused_whichever_spelling_of_the_root_it_stores(self):
        """The invariant the decision rests on: refused on every path through.

        This is the safety half, and it is portable because the property is. Whether
        `realpath` canonicalised the stored target or handed it back verbatim, the name does
        not start a session and the refusal carries no path. Nothing below this line is a
        security question; it is only ever about which sentence the phone gets.
        """
        os.symlink(os.path.join(self.alias_root(), "gone"),
                   os.path.join(self.root, "dangling-alias"))
        # `""` on purpose: the two platforms name two different rules here and this test is
        # deliberately indifferent to which. What it still gets from `refuses` is the whole of
        # what matters — a `ProjectError`, and a message with no path and no echoed name in it.
        self.refuses("dangling-alias", "")

    @needs_symlinks
    @unittest.skipIf(POSIX, "ntpath's realpath is the mechanism; posix resolves the dirname")
    def test_an_unresolved_reparse_target_is_not_canonicalised(self):
        """The guard on W7's decision, and the one test a "fix" to `_child` would fail.

        Check 3, for containment — not check 4, for existence. If someone teaches `_child`
        to re-resolve the dirname of a path that failed check 3, this goes red and they have
        to come back and read the class docstring above, which is the entire point of it.
        """
        os.symlink(os.path.join(self.alias_root(), "gone"),
                   os.path.join(self.root, "dangling-alias"))
        self.refuses("dangling-alias", "root")

    @needs_symlinks
    @unittest.skipUnless(POSIX, "the Mac's half: realpath resolves every component it can")
    def test_the_mac_reaches_check_4_with_the_same_fixture(self):
        """Why this is a Windows question and not a `config.py` question.

        Same link, same spelling, and on the Mac `posixpath.realpath` walks the components
        that do exist and appends the one that does not — so the dirname *is* the resolved
        root and the refusal is check 4's. The platforms disagree about the sentence and
        agree about the answer, which is why the fix belongs in neither.
        """
        os.symlink(os.path.join(self.alias_root(), "gone"),
                   os.path.join(self.root, "dangling-alias"))
        self.refuses("dangling-alias", "exist")


class TestCreatingOne(Base):
    """§5's `new`, and §12 slice 11: checks 1-3 unchanged, check 4 inverted.

    The reason it is a separate verb rather than a flag on `claude` is in §5, and the reason it
    is the *same code* is here: `new` must refuse everything `claude` refuses, or the boundary
    §10.4 defends has a second, weaker door beside it. Only the last check moves, and it moves
    into its opposite — `claude` needs the directory to be there, `new` does not.

    The property that matters more than any single refusal: **a refused `new` creates nothing.**
    A check that ran after the `mkdir` would mean `new ../etc` leaving a directory behind on
    its way to being refused.
    """

    def creates(self, name):
        path, created = config.create(name, self.root)
        self.assertTrue(created, "it reported the directory as already there")
        self.assertTrue(os.path.isdir(path))
        return path

    def refuses_to_create(self, name, because):
        before = sorted(os.listdir(self.root))
        with self.assertRaises(config.ProjectError) as cm:
            config.create(name, self.root)
        msg = str(cm.exception)
        self.assertIn(because, msg.lower(), "message did not name the problem: %r" % msg)
        for leak in (self.root, self.tmp, self.outside, os.path.realpath(self.root),
                     os.path.expanduser("~")):
            self.assertNotIn(leak, msg, "the refusal leaked a path: %r" % msg)
        # The whole point of refusing before the mkdir rather than after it.
        self.assertEqual(sorted(os.listdir(self.root)), before,
                         "a refused `new` left something behind")
        return msg

    def test_it_creates_a_direct_child_of_the_root(self):
        path = self.creates("scratchpad")
        self.assertEqual(os.path.dirname(path), os.path.realpath(self.root))
        self.assertEqual(os.path.basename(path), "scratchpad")

    def test_the_directory_is_empty_and_ordinary(self):
        # §9.3 answers the trust dialog for an empty directory and for nothing else, so what
        # `new` makes has to actually be empty.
        self.assertEqual(os.listdir(self.creates("scratchpad")), [])

    def test_a_name_that_is_already_there_is_not_a_failure(self):
        """§12 slice 11: `new beacon` starts a session and says the directory was already
        present. Refusing would be the wrong answer to a phone — the directory is there, which
        is what was being asked for."""
        path, created = config.create("beacon", self.root)
        self.assertEqual(path, config.resolve("beacon", self.root))
        self.assertFalse(created, "it claimed to have created a directory that was there")

    def test_twice_creates_one_directory(self):
        # Two `new scratchpad` in one batch is one tap repeated on a phone, not an error.
        first, created_first = config.create("scratchpad", self.root)
        second, created_second = config.create("scratchpad", self.root)
        self.assertEqual(first, second)
        self.assertTrue(created_first)
        self.assertFalse(created_second)

    def test_a_file_by_that_name_is_still_refused(self):
        open(os.path.join(self.root, "notes.txt"), "w").close()
        self.refuses_to_create("notes.txt", "file")

    def test_it_refuses_everything_the_name_checks_refuse(self):
        # Check 1, unchanged. Each of these is also a `claude` refusal, by the same code.
        for name in ("sub/dir", "../etc", "..", ".", ".ssh", "a\\b", "x\x00y", "", None):
            self.refuses_to_create(name, "name")

    def test_it_refuses_a_name_with_a_control_character_in_it(self):
        """`new` is the first verb that writes a name to the filesystem, and that is what makes
        this worth refusing rather than merely surviving: `claude` can only ever reach a
        directory somebody already made, while `new` can manufacture one from a phone message —
        and a directory whose name holds a backspace or an ANSI escape cannot be cleaned up
        from a phone, because this bot has no verb that deletes anything."""
        for name in ("a\bb", "red\x1b[31m", "two\nlines", "tab\there"):
            self.refuses_to_create(name, "name")

    def test_it_refuses_a_path_that_leaves_the_root(self):
        # Check 3, unchanged: join() drops the root for an absolute second argument, which is
        # why check 1 runs first and why this one is here at all.
        self.refuses_to_create(os.path.join(self.outside, "x"), "name")

    @needs_symlinks
    def test_it_refuses_a_name_that_escapes_through_a_symlink(self):
        """Check 3 again, and the case it exists for: the name is a name, the join stays inside
        the root, and the realpath comes out somewhere else entirely."""
        os.symlink(self.outside, os.path.join(self.root, "escape"))
        self.refuses_to_create("escape", "root")
        self.assertEqual(os.listdir(self.outside), [], "it created a directory outside the root")

    def test_a_grandchild_is_not_a_direct_child(self):
        self.refuses_to_create("a/b", "name")
        self.assertFalse(os.path.exists(os.path.join(self.root, "a")))

    def test_it_does_not_create_the_root_itself(self):
        # `new .` and `new ..` are check 1, but the failure they would cause is worth naming:
        # the root is not a project and must never be handed back as one.
        for name in (".", ".."):
            self.refuses_to_create(name, "name")

    def test_what_it_creates_is_what_resolve_accepts(self):
        """The two halves of §3 have to agree, or `new x` is followed by `claude x` refusing —
        on a phone, with no way to tell which of the two is wrong."""
        path = self.creates("scratchpad")
        self.assertEqual(config.resolve("scratchpad", self.root), path)
        self.assertIn("scratchpad", config.projects(self.root))


class TestTheHappyCase(Base):
    def test_a_real_project_resolves_to_its_directory(self):
        self.assertEqual(config.resolve("beacon", self.root), os.path.realpath(self.beacon))

    def test_the_result_is_absolute_and_resolved(self):
        # It is about to be chdir'd into by the runner, which has its own cwd and cannot use a
        # relative path.
        got = config.resolve("beacon", self.root)
        self.assertTrue(os.path.isabs(got))
        self.assertEqual(got, os.path.realpath(got))

    def test_a_name_with_a_dot_inside_it_is_fine(self):
        # Only a *leading* dot is refused; `stock-watch-project` and `site.com` are both names.
        os.mkdir(os.path.join(self.root, "site.com"))
        self.assertEqual(config.resolve("site.com", self.root),
                         os.path.realpath(os.path.join(self.root, "site.com")))

    @unittest.skipUnless(POSIX, "Windows realpath returns the on-disk spelling (WINDOWS.md W1c), so the note this test pins is the Mac's")
    def test_a_miscased_name_finds_the_directory_but_keeps_the_spelling_it_was_given(self):
        """The box is case-insensitive and realpath() is not. Pinned because slice 8 depends.

        macOS APFS is case-insensitive, so `claude BEACON` does land in beacon's directory and
        the session works. But os.path.realpath is a lexical resolver — it follows symlinks and
        nothing else — so the string that comes back is spelled the way it was asked for, not
        the way it is on disk. That is harmless for chdir and wrong for string comparison, and
        SPEC.md §5 has exactly one string comparison in it: the `⚠ 2nd session in beacon` warning
        that fires when a new session shares a directory with a live one. Two sessions started as
        `beacon` and `BEACON` are in the same directory and would compare unequal.

        Normalising here would mean a directory listing on every resolve and a fifth check the
        §3 boundary does not ask for, so the fix belongs at the comparison in slice 8, which must
        use os.path.samefile rather than `==`. This test is the note that says so.
        """
        got = config.resolve("BEACON", self.root)
        self.assertTrue(os.path.samefile(got, self.beacon))
        self.assertEqual(os.path.basename(got), "BEACON")


if __name__ == "__main__":
    unittest.main()
