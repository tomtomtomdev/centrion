#!/usr/bin/env python3
"""The Claude Code sessions on this Mac that the bot did not start. SPEC.md §12 slice 14.

Claude Code keeps a registry of its own live processes at `~/.claude/sessions/<pid>.json`. A
session started in a terminal has no Remote Control, and its file says so by what it lacks: a
`bridgeSessionId`. This reads that registry to offer such sessions to `rc`, and ends one once
the bot has resumed its conversation under a runner of its own (§9.14) — two processes on one
transcript is the thing a claim must not leave behind.

**Every file here is untrusted input.** It is written by Claude Code rather than by this
repository, its shape can change with any upgrade (§14), and what comes out of it goes into the
argv of a session with permissions bypassed. So a file that is malformed, half-written, or of a
shape this does not recognise is skipped, never raised on: §7's poll loop must not die of it.

**A file says a session was started, never that it is running** (§9.14): the spike's two files
were still there after both processes had been SIGTERMed. The pid and its start time are the
only answer to that, the same two questions bot.Sessions.alive() asks of a runner (§4).

Stdlib only, same as everything else here (SPEC.md §3).
"""
import collections
import json
import os
import signal
import time
import uuid

import config
import session

REGISTRY = os.path.join(os.path.expanduser("~"), ".claude", "sessions")

#: How far a process's start time may sit from the registry's `startedAt`. `ps` answers to the
#: second and Claude Code writes the file a moment after it starts, so a few seconds is the real
#: gap; this is bot.PID_REUSE_SLACK's tolerance, and a reused pid is minutes or a boot away.
SLACK = 120

#: How long an original has to go after SIGTERM before the hand-over calls it stuck.
GRACE = 5.0

#: What `end()` found. Strings, so a log line reads back as what happened.
ENDED = "ended"      # it was idle, it was signalled, and it is gone
BUSY = "busy"        # mid-turn: left alone, because a turn cut off is work lost
GONE = "gone"        # not there any more, or the pid is somebody else's now
ALIVE = "alive"      # signalled, and still there after GRACE

Local = collections.namedtuple("Local", "pid session_id cwd project name status started")


def _uuid(value):
    """`value` if it is a UUID in the canonical spelling Claude Code writes, else None.

    Canonical and not merely parseable: `uuid.UUID` also takes braces, a `urn:` prefix and
    upper case, and none of those is a spelling `--resume` has ever been handed.
    """
    if not isinstance(value, str):
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def _pid(value):
    # `True` is an int, and pid 1 is launchd (bot.Sessions.alive has the long version).
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _born(record):
    """The registry's `startedAt`, in epoch seconds, or None."""
    value = record.get("startedAt")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value / 1000.0


def _running(pid, born, alive, started):
    """Is the process this record was written about still the one at that pid? §4's rule."""
    if born is None or not alive(pid):
        return False
    began = started(pid)
    # Unlike `ls`, not knowing is a no: a session that is not offered is still there to be
    # claimed a minute later, and one offered by mistake is a SIGTERM to somebody's process.
    return began is not None and abs(began - born) <= SLACK


def _entry(record, projects_root, alive, started):
    """One registry record → a `Local`, or None when it is not ours to offer."""
    pid = _pid(record.get("pid"))
    session_id = _uuid(record.get("sessionId"))
    cwd = record.get("cwd")
    if pid is None or session_id is None or not isinstance(cwd, str) or not cwd:
        return None
    if record.get("kind") != "interactive" or record.get("bridgeSessionId"):
        # Not a conversation (`claude -p`, the SDK), or one already reachable from the phone —
        # every session this bot started is the second kind.
        return None

    # §3, by asking the boundary itself rather than copying it: the cwd has to be exactly the
    # directory its own basename resolves to. That rules out anything outside the root, and a
    # subdirectory of a project too — `--resume` has to run in the very cwd the conversation
    # was recorded under, and the runner re-checks `cwd` against its project the same way.
    project = os.path.basename(cwd.rstrip(os.sep))
    try:
        resolved = config.resolve(project, projects_root)
        if not os.path.samefile(resolved, cwd):
            return None
    except (config.ProjectError, OSError, ValueError):
        return None

    born = _born(record)
    if not _running(pid, born, alive, started):
        return None
    status = record.get("status")
    if status not in ("busy", "idle"):
        status = "unknown"
    name = record.get("name")
    return Local(pid, session_id, resolved, project, name if isinstance(name, str) else "?",
                 status, born)


def claimable(projects_root, registry=REGISTRY, alive=None, started=None):
    """The running terminal sessions `rc <n>` could claim, in the order `rc` numbers them.

    Ordered by start time, then pid, because `rc 2` takes the number `rc` printed: readdir
    order is arbitrary, and an index built on it would name a different session a minute later.
    """
    alive = alive or session.procs.alive
    started = started or session.procs.started
    try:
        names = sorted(os.listdir(registry))
    except OSError:
        return []                # no registry: no sessions, not an error
    found = []
    for name in names:
        if not name.endswith(".json"):
            continue
        record = _read(os.path.join(registry, name))
        entry = _entry(record, projects_root, alive, started) if record else None
        if entry is not None:
            found.append(entry)
    found.sort(key=lambda e: (e.started, e.pid))
    return found


def end(entry, registry=REGISTRY, alive=None, started=None, kill=os.kill, sleep=time.sleep,
        grace=GRACE):
    """End the terminal session `entry` names, once its conversation is resumed elsewhere.

    Everything is checked again first, because time has passed since `rc` listed it: the pid
    must still be the process that was listed (same start time, same conversation), and it must
    be idle. A turn that started in the meantime is left to finish — cutting it off loses work,
    and the phone is told the original is still running instead.
    """
    alive = alive or session.procs.alive
    started = started or session.procs.started
    record = _read(os.path.join(registry, "%d.json" % entry.pid))
    if (record is None or _uuid(record.get("sessionId")) != entry.session_id
            or not _running(entry.pid, entry.started, alive, started)):
        return GONE
    if record.get("status") == "busy":
        return BUSY
    try:
        kill(entry.pid, signal.SIGTERM)
    except OSError:
        return GONE
    # Counted rather than timed, so a test's `sleep` that returns at once is a test that
    # finishes at once.
    for _ in range(int(grace / 0.1)):
        if not alive(entry.pid):
            return ENDED
        sleep(0.1)
    return ALIVE if alive(entry.pid) else ENDED
