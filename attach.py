#!/usr/bin/env python3
"""A terminal window onto a running session: watch it, and type into it. POSIX only.

The runner still owns the pty, and that is the whole reason this exists as a second process
rather than as `claude` started straight into a terminal app. §2: sessions must outlive the
listener, the link is scraped off the master, and a window somebody closes by accident must not
take the session with it. So the runner keeps the terminal exactly as before and also serves it
on a Unix socket in the session directory; a viewer connects, sees what the pty draws, and what
it types is written to the master as if it had been typed there.

**The socket is shell access** in the same sense the bot token is (§10), so it is created 0600
inside a session directory under var/, and nothing but this user can connect to it.

The wire is deliberately small. Runner → viewer is the raw pty output, unframed. Viewer →
runner is framed, because it carries two kinds of message — keystrokes, and the window size —
and a resize has to be told apart from a keystroke that happens to look like one.

**Attaching resizes the session to the window.** Claude Code redraws on SIGWINCH and on nothing
else a viewer can cause, so the resize is also how a viewer that arrives mid-session gets a
screen rather than the next diff. The pty starts at §6's 200x50 because the link has to fit on
one line when it is scraped; the window is only opened once there is a link (see
`Runner.went_live`), so by then the size has done its job.

Stdlib only, same as session.py (SPEC.md §3).
"""
import fcntl
import os
import select
import shlex
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import tty

#: The files in a session directory this module adds. Next to session.META and friends.
SOCKET = "tty.sock"
COMMAND = "attach.command"

#: Viewer → runner frames: one type byte and a length, then the body.
HEADER = struct.Struct(">cI")
INPUT, SIZE = b"i", b"w"
WINSIZE = struct.Struct(">HH")

#: Ctrl-] leaves the viewer and the session running, as telnet taught everybody.
DETACH = b"\x1d"

#: How long a send to a viewer may block the runner's read loop before the viewer is dropped.
#: A viewer that stops reading (a suspended Terminal, a stuck ssh) must cost the session one
#: second once, not its life — `broadcast` is called from the loop that holds the session open.
SEND_TIMEOUT = 1.0

#: Which terminal the window is opened in, most wanted first. The window is Warp's: it is what
#: this Mac reaches for, and a `.command` runs in it exactly as it does in Terminal, because Warp
#: declares itself a handler for `com.apple.terminal.shell-script` too. So the app is the only
#: thing that changes — not `open`, not the `.command`, not the socket underneath it. Terminal
#: stays at the end of the list as the one app a Mac is guaranteed to have: a Mac without Warp
#: still gets a window rather than nothing.
APPS = ("Warp", "Terminal")

#: `terminal_app` (config.TERMINAL_APPS) → the app that value names, for the two that name one.
#: `auto` is `preferred_app()` and `none` is no window. §12 slice 15.
NAMED = {"warp": "Warp", "terminal": "Terminal"}

#: How long the resize nudge holds the off-by-one size. Long enough for the child to see two
#: SIGWINCHes rather than coalescing them into none.
NUDGE = 0.05


def _stderr(message):
    sys.stderr.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    sys.stderr.flush()


class Server:
    """The runner's side: accept viewers, mirror output to them, feed their input to the pty."""

    def __init__(self, directory, terminal, log=_stderr):
        self.path = os.path.join(directory, SOCKET)
        self.terminal = terminal
        self.log = log
        self.clients = []
        self.lock = threading.Lock()
        self.closed = False
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o177)
        try:
            self.sock.bind(self.path)
        finally:
            os.umask(previous)
        self.sock.listen(4)
        self.sock.settimeout(0.5)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.settimeout(SEND_TIMEOUT)
            with self.lock:
                self.clients.append(conn)
            self.log("viewer attached")
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        try:
            while not self.closed:
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                while len(buf) >= HEADER.size:
                    kind, n = HEADER.unpack_from(buf)
                    if len(buf) < HEADER.size + n:
                        break
                    body, buf = buf[HEADER.size:HEADER.size + n], buf[HEADER.size + n:]
                    if kind == INPUT:
                        self.terminal.write(body)
                    elif kind == SIZE and n == WINSIZE.size:
                        self.resize(*WINSIZE.unpack(body))
        except (OSError, ValueError):
            pass            # the viewer went, or the terminal did
        finally:
            self._drop(conn)

    def resize(self, rows, cols):
        """Size the pty to the viewer, and make sure the child redraws even if it already was.

        A resize to the size it already has sends no SIGWINCH, and then a viewer arriving late
        sees nothing until the next thing changes — so the same size is reached by way of one
        column fewer.
        """
        if not rows or not cols:
            return
        if self.terminal.size() == (rows, cols):
            self.terminal.resize(rows, max(cols - 1, 1))
            time.sleep(NUDGE)
        self.terminal.resize(rows, cols)

    def broadcast(self, chunk):
        """Pty output to every viewer. Called from the runner's read loop, so it never blocks
        for longer than SEND_TIMEOUT, and a viewer that fails is dropped rather than retried."""
        with self.lock:
            clients = list(self.clients)
        for conn in clients:
            try:
                conn.sendall(chunk)
            except OSError:
                self._drop(conn)

    def _drop(self, conn):
        with self.lock:
            if conn not in self.clients:
                return
            self.clients.remove(conn)
        try:
            conn.close()
        except OSError:
            pass
        self.log("viewer detached")

    def close(self):
        """Idempotent. Closing the viewers' sockets is how they learn the session ended."""
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass
        with self.lock:
            clients, self.clients = self.clients, []
        for conn in clients:
            try:
                conn.close()
            except OSError:
                pass


def command_file(directory, script, python=sys.executable):
    """Write the `.command` the terminal app runs, and return its path.

    A `.command` handed to `open` rather than `osascript -e 'tell application "Terminal"'`,
    because the AppleScript route needs an Automation grant for whatever process asks — the
    listener under launchd, here — and a TCC prompt nobody is at the Mac to answer is the same
    hang §9.2 already cost a day. `open` needs no grant at all.
    """
    path = os.path.join(directory, COMMAND)
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\nexec %s %s --attach %s\n"
                 % (shlex.quote(python), shlex.quote(script), shlex.quote(directory)))
    os.chmod(path, 0o700)
    return path


def installed(app):
    """Whether LaunchServices can find `app`. `open -Ra` resolves the name without launching it,
    so asking costs nothing a window does not already cost."""
    try:
        with open(os.devnull, "r+b") as devnull:
            return subprocess.call(["open", "-Ra", app], stdin=devnull, stdout=devnull,
                                   stderr=devnull) == 0
    except OSError:
        return False


def preferred_app(apps=APPS):
    """The first of `apps` this Mac has, and the last of them when it has none of the others —
    an app `open` cannot find is a window that silently never appears, and Terminal is there."""
    for app in apps[:-1]:
        if installed(app):
            return app
    return apps[-1]


def choose(value, apps=APPS, log=_stderr):
    """The app `value` asks for, or None for no window.

    A named app this Mac cannot find is the fallback at the end of `apps`, and a log line: `open
    -a` on an app LaunchServices cannot find is a window that silently never appears, which is
    the reason `preferred_app` ends on Terminal too."""
    if value == "auto":
        return preferred_app(apps)
    app = NAMED.get(value)
    if app is None:
        return None
    if app != apps[-1] and not installed(app):
        log("%s is not installed; the window opens in %s" % (app, apps[-1]))
        return apps[-1]
    return app


def open_window(directory, script, app=None, log=_stderr):
    """Open a terminal window attached to the session in `directory`. Never raises: a session
    without a window is still a session, and this is called from the read loop that holds it."""
    try:
        app = app or preferred_app()
        path = command_file(directory, script)
        with open(os.devnull, "r+b") as devnull:
            subprocess.Popen(["open", "-a", app, path], stdin=devnull, stdout=devnull,
                             stderr=devnull, close_fds=True)
    except OSError as e:
        log("could not open a %s window: %s" % (app, e))
        return False
    return True


def _winsize(fd):
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return rows, cols


def attach(directory, stdin=0, stdout=1):
    """The viewer: this terminal, raw, joined to the session's pty until either side goes.

    Returns 0 when the viewer detached or the session ended, 1 when there was nothing to
    attach to.
    """
    path = os.path.join(directory, SOCKET)
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.connect(path)
    except OSError as e:
        sys.stderr.write("centrion: no session to attach to at %s (%s)\n"
                         % (directory, e.strerror or e))
        return 1

    def send(kind, body):
        conn.sendall(HEADER.pack(kind, len(body)) + body)

    resized = [True]
    previous = signal.signal(signal.SIGWINCH, lambda *_: resized.__setitem__(0, True))
    saved = termios.tcgetattr(stdin)
    tty.setraw(stdin)
    os.write(stdout, b"\x1b[2J\x1b[H")
    ended = False
    try:
        while True:
            if resized[0]:
                resized[0] = False
                send(SIZE, WINSIZE.pack(*_winsize(stdin)))
            # A timeout, because PEP 475 retries an interrupted select and a SIGWINCH would
            # otherwise not be seen until the next keystroke.
            ready, _, _ = select.select([stdin, conn], [], [], 0.2)
            if conn in ready:
                data = conn.recv(65536)
                if not data:
                    ended = True
                    break
                os.write(stdout, data)
            if stdin in ready:
                data = os.read(stdin, 4096)
                if not data:
                    break
                if DETACH in data:
                    before = data[:data.index(DETACH)]
                    if before:
                        send(INPUT, before)
                    break
                send(INPUT, data)
    except OSError:
        ended = True
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, saved)
        signal.signal(signal.SIGWINCH, previous)
        conn.close()
    os.write(stdout, b"\r\n\x1b[0m" + (b"centrion: the session has ended.\r\n" if ended else
                                       b"centrion: detached; the session is still running.\r\n"))
    return 0
