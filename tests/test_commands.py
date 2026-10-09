#!/usr/bin/env python3
"""Slice 4 — message text to intent, and nothing else.

`parse()` is pure: no config, no filesystem, no network. It decides what a message *asks for*,
never whether the answer is allowed. That split is deliberate and this file exists to hold it in
place, because the three things it would be natural to do here are all wrong:

- **It does not validate the project name.** `claude ../../etc` parses into a perfectly ordinary
  start intent carrying the string `../../etc`, and `config.resolve()` refuses it (slice 2, the
  security boundary, SPEC.md §3). One boundary, tested on its own terms, is worth more than two
  half-checks that each assume the other is stricter.
- **It does not lowercase anything but the verb.** Project names are filesystem names and prompts
  are English; only the verb is a keyword. SPEC.md §9.9 is the cost of getting this wrong in the
  other direction.
- **It never raises.** Whatever arrives from Telegram — `None` for a photo, bytes, an emoji, a
  megabyte of junk — comes back as an intent, because §7 requires the poll loop to survive
  every message it is sent. `test_nothing_makes_it_raise` is that promise.

The fallback is `help` for everything unrecognised (SPEC.md §5), which makes one shape of bug the
thing to watch for: a message that *nearly* parses must not become a dangerous approximation of
itself. Hence `test_a_bare_stop_is_help_not_stop_all` — the single most expensive misreading
available in this grammar.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import unittest

import commands


class Base(unittest.TestCase):
    def assertIntent(self, text, verb, project=None, prompt=None, target=None):
        got = commands.parse(text)
        self.assertEqual(got, commands.Intent(verb, project, prompt, target),
                         "parse(%r) gave %r" % (text, got))
        return got

    def assertHelp(self, text):
        got = commands.parse(text)
        self.assertEqual(got.verb, commands.HELP, "parse(%r) gave %r" % (text, got))
        # Help is the catch-all, so it must arrive empty — a leftover project or target here
        # would mean a caller could act on a message the parser did not actually understand.
        self.assertEqual((got.project, got.prompt, got.target, got.window),
                         (None, None, None, None),
                         "a help intent carried an argument: %r" % (got,))
        return got


class TestTheVerb(Base):
    """SPEC.md §5: strip one leading `/`, strip a trailing `@botname`, lowercase the verb."""

    def test_the_bare_word_works(self):
        self.assertIntent("claude", commands.START)

    def test_the_slash_form_works(self):
        # What BotFather's /setcommands menu sends when tapped.
        self.assertIntent("/claude", commands.START)

    def test_the_slash_form_with_the_bot_name_works(self):
        # Telegram appends @botname to a slash command; in a group it always does.
        self.assertIntent("/claude@centrionbot", commands.START)

    def test_the_bare_word_with_the_bot_name_works(self):
        self.assertIntent("claude@centrionbot", commands.START)

    def test_only_one_leading_slash_is_stripped(self):
        # "one leading /" is the rule, so //claude is not a verb — it is a typo, and a typo
        # should read as one rather than starting a bypass-permissions session.
        self.assertHelp("//claude")

    def test_the_verb_is_case_insensitive(self):
        # A phone capitalises the first word of a message by default, so `Claude beacon` is
        # what gets sent about as often as the lowercase form. It cannot be a failure mode.
        self.assertIntent("Claude", commands.START)
        self.assertIntent("CLAUDE", commands.START)
        self.assertIntent("/Claude@CentrionBot", commands.START)

    def test_surrounding_whitespace_is_ignored(self):
        self.assertIntent("  claude  ", commands.START)
        self.assertIntent("\nls\n", commands.LIST)

    def test_a_bot_name_that_is_not_ours_is_still_stripped(self):
        # The parser does not know its own name, and does not need to: a message only reaches
        # this process if Telegram delivered it to this bot's token.
        self.assertIntent("/ls@someotherbot", commands.LIST)


class TestStartingASession(Base):
    """SPEC.md §5 tier 1 — the point of the thing."""

    def test_bare_claude_names_no_project(self):
        # §5: it answers with the project list and starts nothing. The absent project is how
        # the listener tells the two apart, so it must be None and not "" or a default.
        self.assertIntent("claude", commands.START, project=None)

    def test_claude_with_a_project(self):
        self.assertIntent("claude beacon", commands.START, project="beacon")

    def test_claude_with_a_project_and_a_prompt(self):
        self.assertIntent("claude beacon fix the probe test",
                          commands.START, project="beacon", prompt="fix the probe test")

    def test_the_prompt_is_preserved_verbatim(self):
        # It is about to be typed into a Claude session, so case, punctuation and internal
        # spacing are all content. Only the verb is a keyword.
        text = "claude beacon Fix TestFoo.test_bar in src/Foo.py — it's the ONLY red one!"
        self.assertIntent(text, commands.START, project="beacon",
                          prompt="Fix TestFoo.test_bar in src/Foo.py — it's the ONLY red one!")

    def test_a_multi_line_prompt_keeps_its_newlines(self):
        # Sending a two-line message from a phone is ordinary, and the second line is prompt.
        self.assertIntent("claude beacon fix the test\nthen run the suite",
                          commands.START, project="beacon",
                          prompt="fix the test\nthen run the suite")

    def test_an_at_sign_inside_the_prompt_survives(self):
        # The @botname strip applies to the verb only. A prompt is free to contain an address,
        # a decorator, or an npm scope.
        self.assertIntent("claude beacon bump @anthropic-ai/sdk", commands.START,
                          project="beacon", prompt="bump @anthropic-ai/sdk")

    def test_the_project_name_keeps_the_case_it_was_given(self):
        # §9.9: this volume is case-insensitive but realpath() is not, so the spelling that
        # arrives is the spelling that reaches resolve(). Lowercasing here would quietly make
        # `claude Junction` and `claude junction` the same string and hide that from slice 8.
        self.assertIntent("claude BEACON", commands.START, project="BEACON")
        self.assertIntent("Claude Stock-Watch-Project", commands.START,
                          project="Stock-Watch-Project")

    def test_a_traversal_is_left_for_the_resolver_to_refuse(self):
        # Not the parser's job, and deliberately so — §3's four checks are the only place a
        # path is judged. A second, weaker copy of that check here is how the two drift apart.
        self.assertIntent("claude ../../etc", commands.START, project="../../etc")
        self.assertIntent("claude /etc passwd", commands.START, project="/etc", prompt="passwd")

    def test_extra_whitespace_between_the_words_is_not_significant(self):
        self.assertIntent("claude   beacon   fix it", commands.START,
                          project="beacon", prompt="fix it")

    def test_a_prompt_that_is_only_whitespace_is_no_prompt(self):
        # Otherwise the runner would type an empty line into a fresh session for no reason.
        self.assertIntent("claude beacon   ", commands.START, project="beacon", prompt=None)


class TestCreatingAProject(Base):
    """§5: `new scratchpad` is its own verb, and that is the whole of its safety.

    Folding creation into `claude <name>` would make every typo an empty repository with a
    bypass-permissions session in it. The parser's half of that is small and absolute: `new`
    parses exactly like `claude` and bare `new` is help, because a `new` with nothing to name
    cannot be given a sensible default without inventing a directory nobody asked for.

    Whether the name is *allowed* is not decided here — `new ../etc` parses cleanly and
    `config.create()` refuses it, the same way `claude ../etc` parses cleanly and
    `config.resolve()` refuses it. One boundary, one place (§10.4).
    """

    def test_new_names_the_project_to_create(self):
        self.assertIntent("new scratchpad", commands.NEW, "scratchpad")

    def test_the_slash_form_is_the_same_verb(self):
        self.assertIntent("/new scratchpad", commands.NEW, "scratchpad")

    def test_it_takes_a_prompt_like_claude_does(self):
        # §5 says it starts a session there `as above`, and above is `claude beacon fix it`.
        self.assertIntent("new scratchpad write me a README", commands.NEW, "scratchpad",
                          "write me a README")

    def test_bare_new_is_help_and_never_a_default_name(self):
        """Bare `claude` answers with the project list because there is a list to give. There
        is no such answer here: any default would be a directory nobody named."""
        self.assertHelp("new")
        self.assertHelp("/new")
        self.assertHelp("new   ")

    def test_the_verb_folds_but_the_name_does_not(self):
        # §9.9: the volume is case-insensitive and the name is a filesystem name, so it reaches
        # the boundary spelled the way it was sent.
        self.assertIntent("NEW Scratchpad", commands.NEW, "Scratchpad")

    def test_a_name_that_is_not_a_name_still_parses(self):
        # And is refused by config.create(), which is the same code that refuses it for
        # `claude`. A second copy of that check here is how the two drift apart.
        for name in ("../etc", "/tmp/x", ".ssh", "a/b"):
            self.assertIntent("new " + name, commands.NEW, name)


class TestTheFleetVerbs(Base):
    """SPEC.md §5 tier 2 — enough to not need a laptop to clean up."""

    def test_ls(self):
        self.assertIntent("ls", commands.LIST)
        self.assertIntent("/ls", commands.LIST)

    def test_help(self):
        self.assertIntent("help", commands.HELP)
        self.assertIntent("/help", commands.HELP)

    def test_stop_takes_the_index_from_ls(self):
        self.assertIntent("stop 2", commands.STOP, target=2)

    def test_stop_all(self):
        self.assertIntent("stop all", commands.STOP, target=commands.ALL)

    def test_stop_all_is_case_insensitive(self):
        # `all` is a keyword, not a filesystem name, so unlike a project it is safe to fold —
        # and a phone will happily send `Stop All` at the start of a message.
        self.assertIntent("Stop ALL", commands.STOP, target=commands.ALL)

    def test_a_bare_stop_is_help_not_stop_all(self):
        """The most expensive misreading in this grammar, so it gets its own name.

        `stop` on its own is ambiguous and one of its two readings kills every live session on
        the box mid-turn. A message can arrive truncated, a fat-fingered send happens, and §5
        gives `stop` no meaning without an argument. Help is the only safe answer.
        """
        self.assertHelp("stop")

    def test_an_index_that_is_not_a_number_is_help(self):
        self.assertHelp("stop beacon")
        self.assertHelp("stop 2x")
        self.assertHelp("stop -1")

    def test_an_index_below_one_is_help(self):
        # `ls` numbers from 1 (§5), so 0 can never name a session. Refusing it here keeps the
        # dispatcher's "no such session" message honest — it means a session that is gone, not
        # an index that never existed.
        self.assertHelp("stop 0")

    def test_stop_takes_exactly_one_target(self):
        # `stop 1 2` is not in §5. Guessing at it — stopping 1, or stopping both — is worse
        # than saying so, because the guess is unrecoverable.
        self.assertHelp("stop 1 2")
        self.assertHelp("stop all now")

    def test_the_argument_free_verbs_take_no_argument(self):
        # `ls beacon` is someone expecting a filter that does not exist. Help says so; silently
        # listing everything looks like the filter worked.
        self.assertHelp("ls beacon")
        self.assertHelp("help me")


class TestEverythingElseIsHelp(Base):
    """SPEC.md §5: anything else replies with help."""

    def test_an_empty_message(self):
        self.assertHelp("")

    def test_whitespace_only(self):
        self.assertHelp("   \n\t ")

    def test_no_text_at_all(self):
        # A photo, a sticker, a location: the update has no `text` key and the listener hands
        # through what it found rather than growing a special case of its own.
        self.assertHelp(None)

    def test_an_unknown_word(self):
        self.assertHelp("hello")

    def test_a_sentence(self):
        self.assertHelp("are you there?")

    def test_a_slash_command_that_is_not_ours(self):
        # Every Telegram client sends /start on first contact with a bot. Help is exactly the
        # right answer to it, and it arrives here rather than through a special case.
        self.assertHelp("/start")
        self.assertHelp("/settings")

    def test_punctuation_and_emoji(self):
        self.assertHelp("👋")
        self.assertHelp("?!")

    def test_a_verb_that_only_starts_like_one(self):
        # Prefix matching would make `claudette` and `stopwatch` into commands. Whole words.
        self.assertHelp("claudette")
        self.assertHelp("stopwatch 2")
        self.assertHelp("lsof")

    def test_the_verb_must_be_first(self):
        self.assertHelp("please claude beacon")


class TestPower(Base):
    """§5's `power`: pmset's schedule, read or changed without a session."""

    def test_bare_power_only_reads(self):
        self.assertEqual(commands.parse("power"), commands.Intent(commands.POWER))

    def test_cancel_and_set(self):
        self.assertEqual(commands.parse("power cancel").target, commands.CANCEL)
        self.assertEqual(commands.parse("/power set").target, commands.SET)

    def test_the_target_folds(self):
        self.assertEqual(commands.parse("Power Cancel"),
                         commands.Intent(commands.POWER, target=commands.CANCEL))

    def test_anything_else_is_help(self):
        # `power off` above all: it reads as "switch the Mac off now", and must never be
        # quietly taken as either of the two things this verb can do.
        for text in ("power off", "power on", "power of", "power cancel now", "power 1"):
            self.assertEqual(commands.parse(text).verb, commands.HELP, text)


class TestTheContract(Base):
    """What the listener is allowed to assume about what comes back."""

    def test_the_intent_is_a_tuple(self):
        # §12 calls it an intent tuple: it compares, unpacks and logs like one, and nothing
        # downstream should need to know the class to read it.
        got = commands.parse("claude beacon fix it")
        self.assertIsInstance(got, tuple)
        self.assertEqual(tuple(got), (commands.START, "beacon", "fix it", None, None))

    def test_the_verb_is_always_one_of_the_five(self):
        for text in ("claude", "claude beacon", "new scratchpad", "ls", "stop 1", "stop all",
                     "power", "power cancel", "power set", "help", "", None, "garbage",
                     "/start", "rc", "rc 2"):
            self.assertIn(commands.parse(text).verb, commands.VERBS)

    def test_every_verb_is_one_telegram_will_offer(self):
        # §5 registers these with BotFather's /setcommands. A verb the parser knows and the
        # menu does not is one nobody on a phone will discover.
        self.assertEqual(commands.VERBS,
                         (commands.START, commands.NEW, commands.LIST, commands.STOP,
                          commands.RC, commands.POWER, commands.HELP))

    def test_nothing_makes_it_raise(self):
        """§7: the poll loop must survive every message anyone can send it.

        Telegram's `text` is absent for a photo, and a caller passing the wrong thing entirely
        is a bug this must not amplify into a dead daemon that launchd restarts into the same
        crash. Everything here is an intent, not an exception.
        """
        hostile = [
            None, 3, 3.5, True, b"claude beacon", ["claude"], {"text": "claude"}, object(),
            "claude " + "x" * 100000,            # Telegram's 4096 cap is on send, not receive.
            "new", "new ../../etc", "new " + "y" * 100000,
            "claude beacon \x00\x1b[31mred",     # NUL and a raw ANSI escape.
            "claude beacon",              # nbsp: whitespace to split(), as it happens.
            "stop ²",                        # isdigit() is True here and int() raises.
            "‮claude",                      # right-to-left override.
            "🧑‍💻 claude beacon",
            "/" * 5000,
        ]
        for text in hostile:
            got = commands.parse(text)
            self.assertIsInstance(got, commands.Intent, "parse(%.40r) gave %r" % (text, got))

    def test_parsing_is_repeatable(self):
        # Pure: no state, no config, no clock. The listener parses on every message forever.
        first = commands.parse("claude beacon fix it")
        for _ in range(3):
            self.assertEqual(commands.parse("claude beacon fix it"), first)


if __name__ == "__main__":
    unittest.main()


class TestClaiming(Base):
    """§12 slice 14's `rc`: the terminal sessions that could be claimed, and claiming one."""

    def test_bare_rc_only_lists(self):
        self.assertIntent("rc", commands.RC)
        self.assertIntent("/rc", commands.RC)
        self.assertIntent("RC", commands.RC)

    def test_rc_takes_the_index_from_its_own_listing(self):
        self.assertIntent("rc 2", commands.RC, target=2)
        self.assertIntent("/rc@centrion_bot 1", commands.RC, target=1)

    def test_anything_but_one_index_is_help(self):
        # `rc all` above all: claiming ends the terminal sessions it claims, so there is no
        # reading of it that is safe to guess at.
        for text in ("rc all", "rc 0", "rc -1", "rc ²", "rc beacon", "rc 1 2", "rc 1 now"):
            self.assertHelp(text)

    def test_it_is_on_the_phone_menu(self):
        self.assertIn(commands.RC, commands.VERBS)


class TestChoosingTheWindow(Base):
    """§12 slice 16: `.terminal`, `.warp`, `.auto` or `.none` before the project picks the
    window for that one session. A leading `.` because §3 refuses a project name that starts
    with one, so the option and a project can never be read as each other."""

    def window(self, text):
        return commands.parse(text).window

    def test_the_option_comes_before_the_project(self):
        got = commands.parse("claude .terminal beacon")
        self.assertEqual(got, commands.Intent(commands.START, "beacon", None, None, "terminal"))

    def test_the_prompt_after_it_keeps_its_spacing(self):
        got = commands.parse("claude .warp beacon fix  the\nprobe")
        self.assertEqual((got.project, got.prompt, got.window),
                         ("beacon", "fix  the\nprobe", "warp"))

    def test_new_takes_it_too(self):
        got = commands.parse("new .none scratchpad")
        self.assertEqual(got, commands.Intent(commands.NEW, "scratchpad", None, None, "none"))

    def test_every_value_is_an_option(self):
        for value in ("auto", "warp", "terminal", "none"):
            self.assertEqual(self.window("claude .%s beacon" % value), value)

    def test_the_option_folds_like_the_verb(self):
        self.assertEqual(self.window("CLAUDE .Terminal beacon"), "terminal")
        self.assertEqual(self.window("/claude@centrion_bot .WARP beacon"), "warp")

    def test_no_option_is_no_window_choice(self):
        self.assertIsNone(self.window("claude beacon"))
        self.assertIsNone(self.window("claude beacon fix it"))
        self.assertIsNone(self.window("new scratchpad"))

    def test_an_option_after_the_project_is_part_of_the_prompt(self):
        got = commands.parse("claude beacon .terminal")
        self.assertEqual((got.project, got.prompt, got.window), ("beacon", ".terminal", None))

    def test_any_other_dotted_word_is_a_project_for_the_resolver_to_refuse(self):
        # §3 refuses a leading `.` by its own rules; the parser must not get there first, and
        # must not guess that `.wrap` meant `.warp`.
        for name in (".wrap", ".iterm", ".terminalx", "../etc", "..", ".", ".ssh"):
            got = self.assertIntent("claude %s beacon" % name, commands.START, project=name,
                                    prompt="beacon")
            self.assertIsNone(got.window)

    def test_an_option_with_no_project_is_help(self):
        for text in ("claude .terminal", "new .none", "claude .warp   "):
            self.assertHelp(text)

    def test_nothing_else_takes_it(self):
        for text in ("ls .terminal", "stop .terminal", "rc .terminal 1", "power .terminal"):
            self.assertHelp(text)
