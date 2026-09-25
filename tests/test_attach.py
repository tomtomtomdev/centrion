#!/usr/bin/env python3
"""attach.py — the terminal window onto a session. POSIX only, no network, no window opened.

Stdlib only: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import os
import shutil
import socket
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

import config
import session

if sys.platform != "win32":
    import attach

posix_only = unittest.skipIf(sys.platform == "win32", "Unix sockets and Terminal.app")


class FakeTerminal:
    def __init__(self, rows=50, cols=200):
        self.rows, self.cols = rows, cols
        self.written = []
        self.sizes = []

    def write(self, data):
        self.written.append(data)

    def size(self):
        return self.rows, self.cols

    def resize(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.sizes.append((rows, cols))


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@posix_only
class TestTheServer(unittest.TestCase):
    def setUp(self):
        # Short: a Unix socket path is capped at 104 bytes on the Mac.
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.terminal = FakeTerminal()
        self.server = attach.Server(self.dir, self.terminal, log=lambda _m: None)
        self.addCleanup(self.server.close)

    def connect(self):
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(os.path.join(self.dir, attach.SOCKET))
        conn.settimeout(3.0)
        self.addCleanup(conn.close)
        self.assertTrue(wait_for(lambda: len(self.server.clients) == 1))
        return conn

    def frame(self, kind, body):
        return attach.HEADER.pack(kind, len(body)) + body

    def test_the_socket_is_this_users_alone(self):
        mode = os.stat(os.path.join(self.dir, attach.SOCKET)).st_mode
        self.assertTrue(stat.S_ISSOCK(mode))
        self.assertEqual(stat.S_IMODE(mode) & 0o077, 0)

    def test_output_reaches_the_viewer(self):
        conn = self.connect()
        self.server.broadcast(b"hello")
        self.assertEqual(conn.recv(100), b"hello")

    def test_keystrokes_reach_the_terminal(self):
        conn = self.connect()
        conn.sendall(self.frame(attach.INPUT, b"ls\r"))
        self.assertTrue(wait_for(lambda: self.terminal.written == [b"ls\r"]))

    def test_a_frame_split_across_sends_is_reassembled(self):
        conn = self.connect()
        data = self.frame(attach.INPUT, b"abc") + self.frame(attach.INPUT, b"def")
        for i in range(len(data)):
            conn.sendall(data[i:i + 1])
        self.assertTrue(wait_for(lambda: b"".join(self.terminal.written) == b"abcdef"))

    def test_a_resize_is_not_a_keystroke(self):
        conn = self.connect()
        conn.sendall(self.frame(attach.SIZE, attach.WINSIZE.pack(24, 80)))
        self.assertTrue(wait_for(lambda: self.terminal.size() == (24, 80)))
        self.assertEqual(self.terminal.written, [])

    def test_a_resize_to_the_same_size_still_makes_the_child_redraw(self):
        self.server.resize(50, 200)
        self.assertEqual(self.terminal.sizes, [(50, 199), (50, 200)])

    def test_a_viewer_that_goes_is_dropped(self):
        conn = self.connect()
        conn.close()
        self.assertTrue(wait_for(lambda: not self.server.clients))
        self.server.broadcast(b"nobody")          # and nothing is raised for it

    def test_closing_ends_the_viewers_and_removes_the_socket(self):
        conn = self.connect()
        self.server.close()
        self.assertEqual(conn.recv(100), b"")
        self.assertFalse(os.path.exists(os.path.join(self.dir, attach.SOCKET)))


@posix_only
class TestARealPty(unittest.TestCase):
    """The same round trip over session_posix's Terminal, with `cat` as the session."""

    def test_typed_text_comes_back_through_the_pty(self):
        d = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, d, True)
        pid, terminal = session.spawn(["/bin/cat"], d, dict(os.environ))
        self.addCleanup(terminal.close)
        self.addCleanup(session.terminate, pid, 1.0, lambda _m: None)
        server = attach.Server(d, terminal, log=lambda _m: None)
        self.addCleanup(server.close)

        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(os.path.join(d, attach.SOCKET))
        self.addCleanup(conn.close)
        conn.sendall(attach.HEADER.pack(attach.SIZE, 4) + attach.WINSIZE.pack(30, 100))
        conn.sendall(attach.HEADER.pack(attach.INPUT, 6) + b"hello\r")

        seen = b""
        deadline = time.time() + 3
        while b"hello" not in seen and time.time() < deadline:
            seen += terminal.read(0.1)
        self.assertIn(b"hello", seen)
        self.assertTrue(wait_for(lambda: terminal.size() == (30, 100)))


@posix_only
class TestTheWindow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_the_command_file_attaches_to_this_session(self):
        path = attach.command_file(self.dir, "/x/session.py", python="/usr/bin/python3")
        with open(path) as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\nexec /usr/bin/python3 /x/session.py "
                                        "--attach %s\n" % self.dir)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)

    def test_it_is_opened_with_open_and_not_applescript(self):
        with mock.patch.object(attach.subprocess, "Popen") as popen:
            self.assertTrue(attach.open_window(self.dir, "/x/session.py", app="Warp",
                                               log=lambda _m: None))
        argv = popen.call_args[0][0]
        self.assertEqual(argv[:3], ["open", "-a", "Warp"])
        self.assertEqual(argv[3], os.path.join(self.dir, attach.COMMAND))

    def test_the_window_is_warps_when_warp_is_installed(self):
        with mock.patch.object(attach, "installed", lambda app: app == "Warp"):
            self.assertEqual(attach.preferred_app(), "Warp")

    def test_it_falls_back_to_terminal_when_warp_is_not(self):
        with mock.patch.object(attach, "installed", lambda _app: False):
            self.assertEqual(attach.preferred_app(), "Terminal")

    def test_terminal_is_never_asked_after_because_it_is_the_fallback(self):
        asked = []
        with mock.patch.object(attach, "installed", lambda app: asked.append(app) or False):
            attach.preferred_app()
        self.assertEqual(asked, ["Warp"])

    def test_the_default_app_is_the_preferred_one(self):
        with mock.patch.object(attach, "preferred_app", lambda: "Warp"):
            with mock.patch.object(attach.subprocess, "Popen") as popen:
                attach.open_window(self.dir, "/x/session.py", log=lambda _m: None)
        self.assertEqual(popen.call_args[0][0][:3], ["open", "-a", "Warp"])

    def test_an_app_launchservices_cannot_find_is_not_installed(self):
        with mock.patch.object(attach.subprocess, "call", return_value=1) as call:
            self.assertFalse(attach.installed("Warp"))
        self.assertEqual(call.call_args[0][0], ["open", "-Ra", "Warp"])

    def test_it_is_installed_when_open_resolves_it(self):
        with mock.patch.object(attach.subprocess, "call", return_value=0):
            self.assertTrue(attach.installed("Warp"))

    def test_a_window_that_cannot_open_is_not_an_error(self):
        with mock.patch.object(attach.subprocess, "Popen", side_effect=OSError("no")):
            self.assertFalse(attach.open_window(self.dir, "/x/session.py", log=lambda _m: None))


class TestTheConfigKey(unittest.TestCase):
    def check(self, data):
        return config._window("cfg", data)

    def test_the_default_is_on_for_the_mac_only(self):
        self.assertEqual(self.check({}), sys.platform == "darwin")

    def test_it_must_be_a_bool(self):
        for bad in (1, "yes", None):
            with self.assertRaises(config.ConfigError):
                self.check({"terminal_window": bad})

    def test_off_is_always_allowed(self):
        self.assertFalse(self.check({"terminal_window": False}))

    def test_on_is_refused_off_the_mac(self):
        with mock.patch.object(config.sys, "platform", "win32"):
            with self.assertRaises(config.ConfigError):
                self.check({"terminal_window": True})


if __name__ == "__main__":
    unittest.main()
