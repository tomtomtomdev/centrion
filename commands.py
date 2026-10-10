#!/usr/bin/env python3
"""Message text to intent. Pure, no I/O.

What a message *asks for*, never whether the answer is allowed. Nothing here reads config,
touches the filesystem, or talks to Telegram, so the whole command surface (SPEC.md §5) can be
pinned by a table of strings and the listener in slice 5 is left with one job: deciding what to
do about an intent it already understands.

Two rules the parser deliberately does not enforce, because something else enforces them better:

- **A project name is not checked here.** `claude ../../etc` parses cleanly into a start intent
  carrying the string `../../etc`; `config.resolve()` refuses it (§3's four checks, §10.4's
  promise). One security boundary, in one place, tested on its own terms — a second, weaker
  copy of that check in the parser is how the two quietly drift apart.
- **A project name is not case-folded.** Only the verb is a keyword; a project name is a
  filesystem name and a prompt is English, and both reach their destination spelled the way
  they were sent (§9.9).

`new <name>` is deliberately the same grammar as `claude <project>` and deliberately a
different verb (§5, §15): the name is not checked here either, so `new ../etc` parses cleanly
and `config.create()` refuses it through the very checks that refuse `claude ../etc`.

The grammar is `verb [argument [rest]]` and everything unrecognised is `help` (§5), which makes
one shape of bug worth naming: a message that *nearly* parses must never become a dangerous
approximation of itself. `stop` on its own is the case that matters — one of its two readings
ends every live session on the box — so it is help, not `stop all`.

The help *text* is not here: §5 says the reply lists the directories currently in the projects
root, and that is a directory listing. It belongs to the listener; this module only decides that
help is what was asked for.
"""
import collections

import config
import totp

# The seven intents, spelled as the words that produce them so a logged or printed intent reads
# back as the message that made it.
START = "claude"      # §5 tier 1: start a session (or, with no project, list the projects).
NEW = "new"           # §5 tier 1: make the directory first, then start a session in it.
LIST = "ls"           # §5 tier 2: the live sessions.
STOP = "stop"         # §5 tier 2: signal one runner, or all of them.
RC = "rc"             # §12 slice 14: claim a terminal session — list them, or resume one.
POWER = "power"       # §5 tier 2: pmset's shutdown schedule — show it, cancel it, set it again.
HELP = "help"         # §5: and everything else.
LOCK = "lock"         # §12 slice 19: end the unlock now.
#: §12 slice 19: a message that is exactly a TOTP code. Not a word anyone types — the code is
#: the whole message — so `code` itself is still help, like any other unknown word.
CODE = "code"

#: `stop all`'s target. A string, so it can never collide with an `ls` index, which is an int.
ALL = "all"

#: `power`'s two targets, the words launchd/power.sh already uses for them. Not `off` and `on`:
#: `power off` reads as "switch the Mac off now", and the phone is the last place to guess.
CANCEL = "cancel"
SET = "set"

#: What the phone's `/` menu carries (§5). The listener owns the descriptions and registers them.
VERBS = (START, NEW, LIST, STOP, RC, POWER, HELP)

#: §12 slice 19: on the menu only while the gate is on. A `lock` offered by a bot that has no
#: lock would be a button that does nothing, so the listener adds it rather than VERBS.
GATE_VERBS = (LOCK,)

#: §12 slice 16: what opens a word before the project to choose the window for that session.
#: §3 refuses a project name that starts with it, so neither can be read as the other.
OPTION = "."

#: `window` is a config.TERMINAL_APPS value from that option, or None to leave it to the config.
Intent = collections.namedtuple("Intent", "verb project prompt target window")
Intent.__new__.__defaults__ = (None, None, None, None)

#: Shared because it is immutable and returned constantly. Always empty: a help intent that
#: carried an argument would be a caller acting on a message nobody understood.
_HELP = Intent(HELP)


def _index(word):
    """Is `word` one of the numbers a listing printed?

    isascii() as well as isdigit(), because `²` and `٢` are digits to isdigit() and a
    ValueError to int(). Listings number from 1, so 0 can never name anything.
    """
    return word.isascii() and word.isdigit() and int(word) >= 1


def _option(word):
    """Is `word` one of the window options — `.terminal`, `.warp`, `.auto`, `.none`?

    Only those four. `.wrap`, `../etc` and `.ssh` start with a `.` too, and they stay project
    names: §3 refuses every one of them, in the resolver, by its own rules. Reading them as a
    mistyped option here would be a second copy of those rules (see the top of this module).
    """
    return word.startswith(OPTION) and word[len(OPTION):].lower() in config.TERMINAL_APPS


def parse(text):
    """`text` → an `Intent`. Total: every input has an answer, and none of them is an exception.

    §7 requires the poll loop to survive whatever arrives, and what arrives is not always a
    string — Telegram omits `text` entirely for a photo, a sticker or a location, so `None` is
    an ordinary Tuesday rather than a bug. Anything this does not understand is `help`.

        >>> parse("claude beacon fix it")
        Intent(verb='claude', project='beacon', prompt='fix it', target=None)
    """
    if not isinstance(text, str):
        return _HELP

    # §12 slice 19: six ASCII digits and nothing else is a code. Before the split, so that
    # `" 123456"` is not one — the check is exact (totp.is_code), and a near miss is help.
    if totp.is_code(text):
        return Intent(CODE, target=text)

    # Split the verb, its argument, and the rest — at most twice, so the prompt keeps its own
    # spacing and newlines. A whitespace-only message splits to nothing and falls through.
    parts = text.split(None, 2)
    if not parts:
        return _HELP

    # §5: strip one leading `/`, strip a trailing `@botname`, lowercase. One slash, because
    # `//claude` is a typo and should read as one. The @name goes only from the verb — a prompt
    # is free to contain an email address or an npm scope.
    verb = parts[0]
    if verb.startswith("/"):
        verb = verb[1:]
    verb = verb.split("@", 1)[0].lower()
    args = parts[1:]

    if verb in (START, NEW):
        if not args:
            # §5: bare `claude` answers with the project list and starts nothing — one extra
            # tap, and no session can begin in a repository nobody named. Bare `new` has no
            # such answer available: every default it could pick is a directory nobody asked
            # for, so it is help.
            return Intent(START) if verb == START else _HELP
        window = None
        if _option(args[0]):
            # §12 slice 16: `.terminal` before the project. A keyword, so it folds the way
            # `all` does. With no project after it there is nothing to start, so it is help.
            window = args[0][len(OPTION):].lower()
            args = args[1].split(None, 1) if len(args) > 1 else []
            if not args:
                return _HELP
        prompt = args[1].rstrip() if len(args) > 1 else ""
        return Intent(verb, args[0], prompt or None, window=window)

    if verb == RC:
        # Bare `rc` only lists. `rc <n>` claims one, and nothing else is a claim: there is no
        # `rc all`, because a claim ends the terminal session it takes and a bulk one is the
        # same irreversible misreading `stop all` is kept a word away from.
        if not args:
            return Intent(RC)
        if len(args) == 1 and _index(args[0]):
            return Intent(RC, target=int(args[0]))
        return _HELP

    if verb == STOP:
        if len(args) != 1:
            # No target is ambiguous and one of its readings is unrecoverable; two targets is
            # not in §5 at all. Guessing costs more than asking again.
            return _HELP
        target = args[0]
        if target.lower() == ALL:
            # Safe to fold where a project name is not: `all` is a keyword, not a name on disk,
            # and a phone will send `Stop All` at the start of a message.
            return Intent(STOP, target=ALL)
        if _index(target):
            return Intent(STOP, target=int(target))
        return _HELP

    if verb == POWER:
        # Bare `power` only reads the schedule. A word that is not one of the two is help and
        # not a guess — `power of` is a typo of either.
        if not args:
            return Intent(POWER)
        if len(args) == 1 and args[0].lower() in (CANCEL, SET):
            return Intent(POWER, target=args[0].lower())
        return _HELP

    if verb in (LIST, HELP, LOCK) and not args:
        return Intent(verb)

    # Unknown verb, or a known one carrying an argument it has no use for — `ls beacon` is
    # someone expecting a filter that does not exist, and listing everything would look like
    # the filter worked.
    return _HELP


def changes(intent):
    """Does this intent start, end or change something? §12 slice 19's gate asks nothing else.

    The reading verbs — help, bare `claude`, `ls`, bare `rc`, bare `power` — are not, and stay
    answerable without a code: they are where the phone finds out what a code would be for.
    """
    if intent.verb in (START, NEW):
        return intent.project is not None
    if intent.verb in (RC, POWER):
        return intent.target is not None
    return intent.verb == STOP


if __name__ == "__main__":
    import sys

    print(parse(" ".join(sys.argv[1:])))
