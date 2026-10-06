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

# The six intents, spelled as the words that produce them so a logged or printed intent reads
# back as the message that made it.
START = "claude"      # §5 tier 1: start a session (or, with no project, list the projects).
NEW = "new"           # §5 tier 1: make the directory first, then start a session in it.
LIST = "ls"           # §5 tier 2: the live sessions.
STOP = "stop"         # §5 tier 2: signal one runner, or all of them.
POWER = "power"       # §5 tier 2: pmset's shutdown schedule — show it, cancel it, set it again.
HELP = "help"         # §5: and everything else.

#: `stop all`'s target. A string, so it can never collide with an `ls` index, which is an int.
ALL = "all"

#: `power`'s two targets, the words launchd/power.sh already uses for them. Not `off` and `on`:
#: `power off` reads as "switch the Mac off now", and the phone is the last place to guess.
CANCEL = "cancel"
SET = "set"

#: What the phone's `/` menu carries (§5). The listener owns the descriptions and registers them.
VERBS = (START, NEW, LIST, STOP, POWER, HELP)

Intent = collections.namedtuple("Intent", "verb project prompt target")
Intent.__new__.__defaults__ = (None, None, None)

#: Shared because it is immutable and returned constantly. Always empty: a help intent that
#: carried an argument would be a caller acting on a message nobody understood.
_HELP = Intent(HELP)


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
        prompt = args[1].rstrip() if len(args) > 1 else ""
        return Intent(verb, args[0], prompt or None)

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
        # isascii() as well as isdigit(), because `²` and `٢` are digits to isdigit() and a
        # ValueError to int(). §5 numbers sessions from 1, so 0 can never name one.
        if target.isascii() and target.isdigit() and int(target) >= 1:
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

    if verb in (LIST, HELP) and not args:
        return Intent(verb)

    # Unknown verb, or a known one carrying an argument it has no use for — `ls beacon` is
    # someone expecting a filter that does not exist, and listing everything would look like
    # the filter worked.
    return _HELP


if __name__ == "__main__":
    import sys

    print(parse(" ".join(sys.argv[1:])))
