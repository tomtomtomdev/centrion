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
import stat

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".telegram.json")

DEFAULT_CLAUDE_BIN = "~/.local/bin/claude"
DEFAULT_MAX_SESSIONS = 2          # SPEC.md §3: 8 GB on this box.
REQUIRED_MODE = 0o600

KNOWN_KEYS = frozenset({
    "bot_token", "allowed_chat_ids", "projects_root", "claude_bin", "max_sessions",
})


class ConfigError(Exception):
    """A .telegram.json that is missing, unreadable, or not safe to start the bot with."""


class Config:
    """Validated settings. Immutable, and deliberately unprintable in full."""

    __slots__ = ("bot_token", "allowed_chat_ids", "projects_root", "claude_bin", "max_sessions")

    def __init__(self, bot_token, allowed_chat_ids, projects_root, claude_bin, max_sessions):
        object.__setattr__(self, "bot_token", bot_token)
        object.__setattr__(self, "allowed_chat_ids", allowed_chat_ids)
        object.__setattr__(self, "projects_root", projects_root)
        object.__setattr__(self, "claude_bin", claude_bin)
        object.__setattr__(self, "max_sessions", max_sessions)

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

    mode = stat.S_IMODE(st.st_mode)
    if mode & ~REQUIRED_MODE:
        raise ConfigError(
            "%s: mode is %04o, must be 0600 — it holds the bot token, which is shell access to "
            "this machine. Fix with: chmod 600 %s" % (path, mode, path))

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
    if not isinstance(raw, str):
        raise ConfigError("%s: `claude_bin` must be a string" % path)
    binary = os.path.abspath(os.path.expanduser(raw))
    if check:
        if not os.path.exists(binary):
            raise ConfigError("%s: `claude_bin` does not exist: %s — `claude install` may have "
                              "moved it" % (path, binary))
        if not os.access(binary, os.X_OK):
            raise ConfigError("%s: `claude_bin` is not executable: %s" % (path, binary))
    return binary


def _cap(path, data):
    n = data.get("max_sessions", DEFAULT_MAX_SESSIONS)
    if type(n) is not int or n < 1:
        raise ConfigError("%s: `max_sessions` must be an integer of 1 or more, found %r"
                          % (path, n))
    return n


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
    )
