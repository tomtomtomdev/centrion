#!/usr/bin/env python3
"""Configuration for centrion: load it, or refuse to run.

Everything here fails closed. The thing being configured starts Claude Code sessions with
`--dangerously-skip-permissions`, so a config that is *almost* valid is worse than no config at
all — it would start the bot with, say, an allowlist that matches nobody and a token that works,
which is an open shell waiting for someone to find it. Every problem raises ConfigError and
nothing half-built is ever returned.

The second rule is that no message here ever contains the bot token. These strings reach
var/bot.log, and from slice 3 some of them reach Telegram. The token is the credential
(SPEC.md §10), so it is named by its key and never by its value — including in repr(), because
an object like this ends up in a traceback eventually.

See SPEC.md §3 for the file's shape and §15 for why there is no `default_project`.
"""
import json
import os
import shutil
import stat
import sys

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".telegram.json")

#: Where `claude` is, when `.telegram.json` does not say. Two different questions per platform.
#: On the Mac it is the installer's symlink, kept current by self-update, and `_binary` is
#: careful not to resolve it. Windows has no such symlink: `claude install` leaves a copy under
#: `%USERPROFILE%\.local\bin` and winget leaves another on PATH, and on this box (WINDOWS.md
#: W0d) the `.local\bin` one is *stale* — v2.1.231 against winget's v2.1.268, with auto-updates
#: off. So the nearest thing to "whatever `claude` means right now" is PATH, and preferring
#: `.local\bin` would silently pin every session to a build from a month ago. WINDOWS.md §5.2.
#: `which` returns None when Claude Code is not installed at all; `_binary` says so by name.
if sys.platform == "win32":
    DEFAULT_CLAUDE_BIN = shutil.which("claude")
else:
    DEFAULT_CLAUDE_BIN = "~/.local/bin/claude"
DEFAULT_MAX_SESSIONS = 2          # SPEC.md §3: 8 GB on this box.
#: Open a terminal window onto every session once it is live (attach.py). Mac only: the window
#: is Warp's — Terminal.app where there is no Warp — and the socket it attaches through is a
#: Unix one.
DEFAULT_TERMINAL_WINDOW = sys.platform == "darwin"
REQUIRED_MODE = 0o600             # The Mac's secrecy check. Windows has no mode; see below.

#: Windows: the principals that may appear in the config file's DACL. Everyone else is a
#: refusal, including principals nobody thinks of as a leak — `Guests`, a second local
#: account, a domain group. WINDOWS.md §5.1 named the three that arrive by accident
#: (`Everyone`, `Users`, `Authenticated Users`) and refusing only those is a deny-list, which
#: fails *open*: the first line of this module says the opposite, and a token is exactly the
#: thing you do not get a second chance at. So the rule is an allow-list of "us, or already
#: root here": the current user, plus these three.
#:   S-1-5-18     NT AUTHORITY\SYSTEM   — the machine; the Mac's 0600 does not exclude root either
#:   S-1-5-32-544 BUILTIN\Administrators — likewise
#:   S-1-3-4      OWNER RIGHTS           — "whoever owns this", which under %TEMP% is how the
#:                                         owner's own access is spelled instead of by SID
ALWAYS_ALLOWED_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4"})

#: Windows: the access bits that would let a principal read the token or take the file over.
#: An ACE is a bitmask, not a yes/no — %USERPROFILE% carries an AppContainer ACE for
#: SYNCHRONIZE|FILE_TRAVERSE and `icacls /grant X:(X)` writes a bare FILE_EXECUTE, neither of
#: which can read a byte. Refusing those would refuse ordinary machines for nothing.
#:   FILE_READ_DATA 0x1 · FILE_WRITE_DATA 0x2 · FILE_APPEND_DATA 0x4 · DELETE 0x10000
#:   WRITE_DAC 0x40000 (grant yourself the read) · WRITE_OWNER 0x80000 (same, the long way)
#:   GENERIC_ALL 0x10000000 · GENERIC_WRITE 0x40000000 · GENERIC_READ 0x80000000
SECRET_BITS = 0x1 | 0x2 | 0x4 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000 | 0x80000000

ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1

#: The line the Windows refusal tells you to run, built by `_fix` below. WINDOWS.md §5.1.
ICACLS_FIX = 'icacls "%s" /inheritance:r%s /grant:r "%%USERNAME%%":F'

KNOWN_KEYS = frozenset({
    "bot_token", "allowed_chat_ids", "projects_root", "claude_bin", "max_sessions",
    "terminal_window",
})


class ConfigError(Exception):
    """A .telegram.json that is missing, unreadable, or not safe to start the bot with."""


class Config:
    """Validated settings. Immutable, and deliberately unprintable in full."""

    __slots__ = ("bot_token", "allowed_chat_ids", "projects_root", "claude_bin", "max_sessions",
                 "terminal_window")

    def __init__(self, bot_token, allowed_chat_ids, projects_root, claude_bin, max_sessions,
                 terminal_window=False):
        object.__setattr__(self, "bot_token", bot_token)
        object.__setattr__(self, "allowed_chat_ids", allowed_chat_ids)
        object.__setattr__(self, "projects_root", projects_root)
        object.__setattr__(self, "claude_bin", claude_bin)
        object.__setattr__(self, "max_sessions", max_sessions)
        object.__setattr__(self, "terminal_window", terminal_window)

    def __setattr__(self, *_):
        raise AttributeError("Config is immutable")

    def __repr__(self):
        # No token, ever. This is what a traceback or a log line will show.
        return ("Config(bot_token=<redacted>, allowed_chat_ids=%d id(s), projects_root=%r, "
                "claude_bin=%r, max_sessions=%d)"
                % (len(self.allowed_chat_ids), self.projects_root, self.claude_bin,
                   self.max_sessions))


def _raw(path):
    """The file's parsed contents, after checking it is ours alone to read."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise ConfigError("%s: not found — create it (see SPEC.md §3)" % path) from None
    except OSError as e:
        raise ConfigError("%s: cannot stat (%s)" % (path, e.strerror)) from None

    _secret(path, st)

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as e:
        raise ConfigError("%s: not valid JSON (%s)" % (path, e)) from None
    except OSError as e:
        raise ConfigError("%s: cannot read (%s)" % (path, e.strerror)) from None

    if not isinstance(data, dict):
        raise ConfigError("%s: must be a JSON object, found %s" % (path, type(data).__name__))
    return data


def _fix(path, offenders=()):
    r"""The icacls line that lands this file at owner-only, given who is on it right now.

    `/inheritance:r` drops the *inherited* entries and nothing else. WINDOWS.md §5.1's line
    stopped there, and measured (W2b), that leaves an explicit `Users` grant exactly where it
    was — so in the one case the message is ever printed, it told you to run something that
    did not fix it. Explicit entries come off with `/remove:g`, and the principals to name are
    the ones we just read off the file, so the line is both true and minimal.
    """
    return ICACLS_FIX % (path, "".join(' /remove:g "%s"' % who for who in offenders))


def _secret_by_mode(path, st):
    """The Mac: nobody but the owner may read or write it, and `stat` says so plainly."""
    mode = stat.S_IMODE(st.st_mode)
    if mode & ~REQUIRED_MODE:
        raise ConfigError(
            "%s: mode is %04o, must be 0600 — it holds the bot token, which is shell access to "
            "this machine. Fix with: chmod 600 %s" % (path, mode, path))


def _win32security():
    """The module the DACL check needs. A function so the ImportError has one place to be
    raised from, and so a test can take the module away without uninstalling anything."""
    import win32security
    return win32security


def _descriptor(path):
    """The file's security descriptor. Its own function for the same reason as above: the
    interesting case is the one where this fails, and no unreadable file is needed to test it."""
    win32security = _win32security()
    return win32security.GetFileSecurity(path, win32security.DACL_SECURITY_INFORMATION)


def _dacl(sd):
    """The discretionary ACL, or None. None means NULL — see the caller; it is not 'empty'."""
    return sd.GetSecurityDescriptorDacl()


def _aces(dacl):
    """Every entry in the ACL, in order, as GetAce returns them."""
    return [dacl.GetAce(i) for i in range(dacl.GetAceCount())]


def _me(win32security):
    """This process's own user SID, as a string."""
    import win32api
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        win32api.CloseHandle(token)
    return win32security.ConvertSidToStringSid(sid)


def _principal(win32security, sid, text):
    """A name for the error message. Unresolvable SIDs are named by their string, which is
    still enough to hand to icacls."""
    try:
        name, domain, _ = win32security.LookupAccountSid(None, sid)
    except Exception:
        return text
    return "%s\\%s" % (domain, name) if domain else name


def _secret_by_dacl(path, st):
    r"""Windows: nobody but us may read it, and `stat` is no help at all in saying so.

    `st` is unread here, and is in the signature so that `_secret` is one shape on both
    platforms; on this one the `os.stat` it comes from is only how "not found" is answered.

    `stat.S_IMODE` answers 0666 for every ordinary file on NTFS — a number invented for
    programs that insist on asking — so the Mac's check ported verbatim refuses a private file
    and accepts a world-readable one with equal confidence. The DACL is where the answer is.
    WINDOWS.md §5.1.

    Everything here fails closed, because a check that cannot read the permissions and shrugs
    is worse than no check: a missing `win32security`, an unreadable descriptor, a NULL DACL,
    an ACE in a shape this does not parse. None of those mean "it is private"; they mean the
    question was not answered, and the token does not get the benefit of the doubt.
    """
    try:
        win32security = _win32security()
    except ImportError:
        raise ConfigError(
            "%s: cannot check who may read this file — pywin32 is not installed, and on "
            "Windows it is the only way to ask. Install it with: pip install -r "
            "requirements-win.txt" % path) from None

    try:
        me = _me(win32security)
        sd = _descriptor(path)
        dacl = _dacl(sd)
    except Exception as e:
        # pywintypes.error is not an OSError, and a descriptor we could not read is a refusal
        # whatever the exception's class. `strerror` is absent on some of them.
        raise ConfigError(
            "%s: cannot read its permissions (%s) — it holds the bot token, so an unanswered "
            "question is a refusal" % (path, getattr(e, "strerror", None) or e)) from None

    if dacl is None:
        # A NULL DACL is not an empty one. An empty ACL grants nobody anything; NULL means the
        # object has no discretionary control at all, which is to say everyone, everything.
        raise ConfigError(
            "%s: has no access-control list, which on Windows grants everyone full access — "
            "it holds the bot token, which is shell access to this machine. Fix with: %s"
            % (path, _fix(path)))

    allowed = ALWAYS_ALLOWED_SIDS | {me}
    readers, seen = [], set()     # One principal can hold several ACEs; name it once.
    for ace in _aces(dacl):
        (ace_type, _flags) = ace[0]
        if ace_type == ACCESS_DENIED_ACE_TYPE:
            continue                      # A deny entry grants nothing; it is not a reader.
        if ace_type != ACCESS_ALLOWED_ACE_TYPE or len(ace) != 3:
            # Object ACEs (types 5-11) come back from GetAce as a longer tuple with a GUID
            # in it. Skipping an entry we cannot parse would be skipping a grant.
            raise ConfigError(
                "%s: carries an access-control entry of type %d that this check does not "
                "understand, so it cannot say who may read the file. Read the list with "
                "`icacls \"%s\"`, or throw it away and start again: `icacls \"%s\" /reset`, "
                "then %s" % (path, ace_type, path, path, _fix(path)))
        mask, sid = ace[1], ace[2]
        if not mask & SECRET_BITS:
            continue                      # Traverse, synchronise, read-attributes: not the bytes.
        text = win32security.ConvertSidToStringSid(sid)
        if text not in allowed and text not in seen:
            seen.add(text)
            readers.append(_principal(win32security, sid, text))

    if readers:
        raise ConfigError(
            "%s: %s may read it — it holds the bot token, which is shell access to this "
            "machine. Fix with: %s" % (path, ", ".join(readers), _fix(path, readers)))


#: Same question on both platforms, asked of the only thing that can answer it there.
_secret = _secret_by_dacl if sys.platform == "win32" else _secret_by_mode


def _keys(path, data):
    if "default_project" in data:
        raise ConfigError(
            "%s: `default_project` is not supported — bare `claude` answers with the project "
            "list and starts nothing, so that no session can begin in a repository you did not "
            "name (SPEC.md §5). Remove the key." % path)
    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        raise ConfigError("%s: unknown key(s): %s. Known keys: %s"
                          % (path, ", ".join(unknown), ", ".join(sorted(KNOWN_KEYS))))


def _token(path, data):
    token = data.get("bot_token")
    if not token:
        raise ConfigError("%s: `bot_token` is missing or empty — get one from @BotFather. "
                          "It must be centrion's own bot, not the stock-watch one (SPEC.md §3)."
                          % path)
    if not isinstance(token, str):
        raise ConfigError("%s: `bot_token` must be a string" % path)
    return token


def _allowlist(path, data):
    """The one that must never be lenient: no allowlist can ever mean 'anyone'."""
    if "allowed_chat_ids" not in data:
        raise ConfigError(
            "%s: `allowed_chat_ids` is missing. It is not optional — without it the bot would "
            "take orders from anyone who finds it. Run `bot.py --whoami` for your id." % path)
    ids = data["allowed_chat_ids"]
    if not isinstance(ids, list):
        raise ConfigError("%s: `allowed_chat_ids` must be a list, found %s"
                          % (path, type(ids).__name__))
    if not ids:
        raise ConfigError(
            "%s: `allowed_chat_ids` is empty, so the bot would answer nobody. That is safe but "
            "useless — run `bot.py --whoami` for your id." % path)
    for i in ids:
        # bool is a subclass of int, and a quoted id compares equal to nothing at all.
        if type(i) is not int:
            raise ConfigError("%s: `allowed_chat_ids` must contain integers, found %s (%r)"
                              % (path, type(i).__name__, i))
    return frozenset(ids)


def _directory(path, data):
    root = data.get("projects_root")
    if not root:
        raise ConfigError("%s: `projects_root` is missing — it is the whole of the bot's "
                          "reachable world (SPEC.md §9.2)" % path)
    if not isinstance(root, str):
        raise ConfigError("%s: `projects_root` must be a string" % path)
    root = os.path.realpath(os.path.expanduser(root))
    if not os.path.exists(root):
        raise ConfigError("%s: `projects_root` does not exist: %s" % (path, root))
    if not os.path.isdir(root):
        raise ConfigError("%s: `projects_root` is not a directory: %s" % (path, root))
    return root


def _binary(path, data, check):
    """SPEC.md §9.8: `claude install` can move it, so catch that once at startup.

    Deliberately abspath and *not* realpath. On this box ~/.local/bin/claude is a symlink to
    .local/share/claude/versions/<version> — resolving it would pin the daemon to whichever
    build was installed the day it last started, and Claude Code updates itself. The listener
    only re-reads config at startup, so a resolved path would keep working until the next
    update deleted that version directory, and then every spawn would fail with ENOENT on a
    daemon that looks perfectly healthy. Following the symlink at exec time is the whole point
    of the symlink.
    """
    raw = data.get("claude_bin") or DEFAULT_CLAUDE_BIN
    if raw is None:
        # Windows only, and only with Claude Code not installed: there the default *is* the
        # lookup, so there is nothing to fall back to. Falling through to the type check below
        # would answer "`claude_bin` must be a string" about a key this file does not contain,
        # which sends the reader to fix the wrong thing. WINDOWS.md §5.2.
        raise ConfigError("%s: `claude_bin` is not set and `claude` is not on PATH — "
                          "install Claude Code, or name the exe with `claude_bin`" % path)
    if not isinstance(raw, str):
        raise ConfigError("%s: `claude_bin` must be a string" % path)
    binary = os.path.abspath(os.path.expanduser(raw))
    if check:
        if not os.path.exists(binary):
            raise ConfigError("%s: `claude_bin` does not exist: %s — `claude install` may have "
                              "moved it" % (path, binary))
        # No executable bit on Windows: os.access(X_OK) there is os.access(F_OK) under another
        # name, true for a text file and for one marked read-only. A check that cannot fail
        # reads like the Mac's guarantee without being one, so it is not asked. WINDOWS.md §5.2.
        if sys.platform != "win32" and not os.access(binary, os.X_OK):
            raise ConfigError("%s: `claude_bin` is not executable: %s" % (path, binary))
    return binary


def _cap(path, data):
    n = data.get("max_sessions", DEFAULT_MAX_SESSIONS)
    if type(n) is not int or n < 1:
        raise ConfigError("%s: `max_sessions` must be an integer of 1 or more, found %r"
                          % (path, n))
    return n


def _window(path, data):
    on = data.get("terminal_window", DEFAULT_TERMINAL_WINDOW)
    if type(on) is not bool:
        raise ConfigError("%s: `terminal_window` must be true or false, found %r" % (path, on))
    if on and sys.platform != "darwin":
        raise ConfigError("%s: `terminal_window` opens Warp or Terminal.app and is Mac only"
                          % path)
    return on


def load(path=CONFIG, check_claude=True):
    """Read and validate `path`. Raises ConfigError, never returns a partial Config."""
    data = _raw(path)
    _keys(path, data)
    return Config(
        bot_token=_token(path, data),
        allowed_chat_ids=_allowlist(path, data),
        projects_root=_directory(path, data),
        claude_bin=_binary(path, data, check_claude),
        max_sessions=_cap(path, data),
        terminal_window=_window(path, data),
    )


class ProjectError(Exception):
    """A project name that does not name a directory directly inside `projects_root`.

    Carries no path, by design. The caller turns this into a `help` reply (SPEC.md §3), and
    that reply goes to Telegram, which per §10 sees every message — so the text says which rule
    was broken and never which path broke it.

    Inside the root it is free to be specific ("that is a file" rather than "no such project"),
    because §10.1/§10.3 mean a reply only ever reaches an allowlisted chat: the audience for
    these is the owner, on a phone, trying to work out why nothing started. What stays
    deliberately uninformative is the boundary itself — a name that escapes the root is refused
    before anything asks whether its target exists, so an escape and a typo read the same.
    """


# `/` and `\` are what a path looks like; NUL is what makes realpath() raise instead of return.
_NOT_IN_A_NAME = ("/", "\\", "\x00")

#: Drive-qualified names, which `os.path.splitdrive` catches and `os.path.isabs` does not.
#: Two shapes, and on Windows `join` gets each of them wrong in a different direction
#: (WINDOWS.md §5.3, measured):
#:
#:   * `C:foo` is drive-relative — "foo, under whatever the current directory on drive
#:     C: happens to be". `isabs` says False. With the root on the same drive, join quietly
#:     *drops the `C:`* and returns `<root>\foo`, so check 3 passes and a session would
#:     start in a directory nobody named — and `new C:foo` would create it. With the root on
#:     another drive, join keeps `C:foo` whole and realpath resolves it against C:'s
#:     per-drive cwd: outside the root entirely, which is §10.4 broken rather than bent.
#:   * `\\srv\share` is a UNC root, and splitdrive returns the whole of it as the drive.
#:     Its backslashes are already refused above, but the containment argument should not
#:     rest on this platform spelling its separator with a character that list happens to
#:     hold.
#:
#: On the Mac `posixpath.splitdrive` finds a drive in nothing at all, so this costs the
#: Mac nothing and `C:foo` stays an ordinary, if odd, directory name there.


def _unprintable(name):
    """True for a name holding a control character. Slice 11, and `new` is why.

    §3's first check was written when every verb could only ever *reach* a directory somebody
    had already made by hand. `new` writes one, from a message, so the name is now wire content
    on its way to the filesystem — and a directory called `a\bb` or `red\x1b[31m` is one this
    bot cannot help you get rid of afterwards, because there is no verb here that deletes
    anything. Both verbs refuse them, because two rules is how the two doors drift apart.
    """
    return any(c < " " or c == "\x7f" for c in name)

#: Verb-agnostic on purpose: `claude` and `new` refuse the same names through the same code
#: (§12 slice 11), and a message naming one of the two verbs would be wrong half the time.
_NOT_A_NAME = ("a project is the name of a directory in the projects root, not a path. Send "
               "`claude` on its own for the list.")


def _child(name, root):
    """Checks 1-3 of §3: `name` → where it would be, whether or not anything is there.

    Split out of `resolve()` for `create()`, which shares these three exactly and inverts the
    fourth (§12 slice 11). It is one function rather than two copies because this is the
    security boundary §10.4 is a promise about, and the way a second copy fails is silently:
    `new` would go on accepting a name that `claude` had started refusing, and the bot would
    have a weaker door beside the one everything else is tested against.
    """
    if root is None:
        root = load().projects_root
    root = os.path.realpath(os.path.expanduser(root))

    # 1. The name is a name. First, because os.path.join(root, "/etc") is "/etc" — and on
    #    Windows because of the two drive shapes below, which join gets wrong in two directions.
    if not isinstance(name, str) or not name:
        raise ProjectError("that is not a project name. " + _NOT_A_NAME)
    if (name.startswith(".") or any(c in name for c in _NOT_IN_A_NAME) or _unprintable(name)
            or os.path.splitdrive(name)[0]):
        raise ProjectError("that is not a project name. " + _NOT_A_NAME)

    # 2. Resolve it.
    path = os.path.realpath(os.path.join(root, name))

    # 3. It is a direct child of the root, after resolution.
    if os.path.dirname(path) != root:
        raise ProjectError("that is not inside the projects root, and the bot cannot start a "
                           "session outside it. " + _NOT_A_NAME)
    return path


def create(name, root=None):
    """`name` → `(directory, created)`, making it if it is not there. §5's `new`, §12 slice 11.

    Checks 1-3 are `resolve()`'s, unchanged and shared — `new` refuses everything `claude`
    refuses, which is the whole of why creating a project is allowed to be a verb at all. Check
    4 is inverted: `claude` needs the directory to exist and this does not.

    **Nothing is created until every check has passed.** A refusal that had already run the
    `mkdir` would leave `new ../etc` writing a directory on its way to being rejected, which is
    the boundary failing while reporting that it held.

    `created` is False for a directory that was already there, and that is not an error: the
    phone asked for a project by that name and there is one. It changes the reply (§5) and it
    is the difference between a session this bot may answer §9.3's trust dialog for and one it
    may not — see session.Trust.
    """
    path = _child(name, root)
    if os.path.isdir(path):
        return path, False
    if os.path.exists(path):
        raise ProjectError("that is a file, not a project directory. Send `claude` on its own "
                           "for the list.")
    try:
        os.mkdir(path)
    except FileExistsError:
        # Two `new scratchpad` in one batch. The loser of the race gets what it asked for.
        return path, False
    except OSError as e:
        # A read-only root, a full disk, a name this filesystem will not take. §7: the phone
        # gets a sentence and the daemon keeps polling — and the sentence carries no path,
        # because this one reaches Telegram like every other refusal (§10).
        raise ProjectError("could not create that project directory (%s)."
                           % (e.strerror or "unknown error"))
    return path, True


def resolve(name, root=None):
    """`name` → the absolute directory it names, or ProjectError. SPEC.md §3, four checks.

    This is the security boundary. The bot starts sessions with `--dangerously-skip-permissions`,
    so whatever directory comes back from here is the blast radius, and §10.4 is the promise
    being kept: no message can express a directory outside `projects_root`.

    The four checks run in the order §3 gives them, and the order is load-bearing:

    1. The name is a name. This has to be first because `os.path.join(root, "/etc")` is `/etc` —
       join drops the root entirely when its second argument is absolute, so a containment check
       that trusted join alone would end up comparing `/etc` against its own parent and passing.
       On Windows two further shapes say "absolute" in a way `isabs` does not; see the note
       beside `_NOT_IN_A_NAME`.
    2. Join and `os.path.realpath`. The root is realpath'd too: on this box `projects_root` may
       be reached through a symlink (`/var` → `/private/var` is the everyday case), and comparing
       a resolved child against an unresolved root refuses everything.
    3. The realpath's parent is exactly the realpath'd root — a *direct* child. This is the check
       a symlink inside the root pointing outside it dies on, and it runs before the existence
       check so that an escape and a typo are indistinguishable from the outside.
    4. It is a directory that exists.

    `root` defaults to the configured one, which is what makes the §12 manual check read
    `config.resolve('beacon')`. Pass it explicitly from the listener, which already holds a
    Config and should not re-read the file per message.

    Returns a resolved absolute path. Note it is *not* case-normalised — see
    test_projects.TestTheHappyCase for why that matters to §5's same-directory warning.
    """
    path = _child(name, root)

    # 4. It is a directory, and it is there.
    if not os.path.exists(path):
        raise ProjectError("no project by that name exists here. Send `claude` on its own for "
                           "the list.")
    if not os.path.isdir(path):
        raise ProjectError("that is a file, not a project directory. Send `claude` on its own "
                           "for the list.")
    return path


def projects(root=None):
    """Every name `claude <project>` will accept, sorted for a phone. Never raises.

    The inverse of `resolve()`, and it is built out of `resolve()` rather than beside it. A
    listing that applied its own copy of §3's rules would drift from the real ones, and the way
    that drift shows up is the worst kind: a name printed in the reply to bare `claude` that
    `claude <name>` then refuses, on a phone, with no way to tell which of the two is wrong.
    Filtering through the boundary itself makes the list true by construction — at the cost of
    a handful of `realpath` calls on a directory with a dozen entries in it, which is nothing.

    Sorted case-insensitively because §9.9's volume is case-insensitive: `Lab` sorting above
    `beacon` is ASCII being correct and the list looking broken.

    Empty rather than raising when the root cannot be read. §9.2 is the case that matters —
    a launchd process that has lost its reach into the root through TCC still has to answer the
    phone, because that reply is the only way to find out that it has.
    """
    if root is None:
        root = load().projects_root
    root = os.path.realpath(os.path.expanduser(root))
    try:
        names = os.listdir(root)
    except OSError:
        return []

    found = []
    for name in names:
        try:
            resolve(name, root)
        except ProjectError:
            continue
        found.append(name)
    return sorted(found, key=lambda n: (n.lower(), n))
