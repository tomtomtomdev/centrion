#!/usr/bin/env python3
"""Slice 14 — the sessions on this Mac that the bot did not start, read off Claude Code's own
registry, and the hand-over that ends one once the bot has resumed it.

`~/.claude/sessions/<pid>.json` is written by Claude Code, not by this repository, so every file
in it is untrusted input in a shape that can change with any upgrade (§14). The tests are mostly
about what is *not* listed, for the same reason `ls`'s are about what is: a session offered for
claiming is one `rc <n>` resumes with permissions bypassed.

§9.14 is why the liveness check is not optional. A registry file outlives its process — both of
the spike's files were still there after SIGTERM — so a file says a session *was* started, and
only the pid and its start time say it is running.

Stdlib only, no network: `/usr/bin/python3 -m unittest discover -s tests -t . -v`.
"""
import json
import os
import shutil
import signal
import tempfile
import unittest

import local

SID = "cf6c1378-f496-40e7-9f1f-9442392ef5a1"
OTHER = "a5cfe917-2e21-4cd4-92f0-98408b74f97a"
BORN = 1791526050.0          # when the fake process started, in epoch seconds


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.projects = os.path.join(self.tmp, "Projects")
        for name in ("beacon", "centrion"):
            os.makedirs(os.path.join(self.projects, name))
        os.makedirs(os.path.join(self.projects, "backend", "account-service"))
        self.registry = os.path.join(self.tmp, "sessions")
        os.makedirs(self.registry)
        # pid → start time. A pid missing from here is a process that is not running.
        self.processes = {}
        self.signalled = []

    def register(self, pid, session_id=SID, project="beacon", born=BORN, running=True,
                 **fields):
        record = {"pid": pid, "sessionId": session_id,
                  "cwd": os.path.join(self.projects, project), "kind": "interactive",
                  "entrypoint": "cli", "name": "%s-7f" % project, "status": "idle",
                  "startedAt": int(born * 1000) + 400, "version": "2.1.282"}
        record.update(fields)
        record = {k: v for k, v in record.items() if v is not DROP}
        with open(os.path.join(self.registry, "%d.json" % pid), "w") as fh:
            json.dump(record, fh)
        if running:
            self.processes[pid] = born
        return record

    def alive(self, pid):
        return pid in self.processes

    def started(self, pid):
        return self.processes.get(pid)

    def kill(self, pid, signum):
        self.signalled.append((pid, signum))
        self.processes.pop(pid, None)

    def claimable(self):
        return local.claimable(self.projects, registry=self.registry, alive=self.alive,
                               started=self.started)


DROP = object()


class TestWhatIsListed(Base):
    def test_a_plain_terminal_session_is_listed(self):
        self.register(4242)
        found = self.claimable()
        self.assertEqual(len(found), 1)
        entry = found[0]
        self.assertEqual(entry.pid, 4242)
        self.assertEqual(entry.session_id, SID)
        self.assertEqual(entry.project, "beacon")
        self.assertEqual(entry.cwd, os.path.join(self.projects, "beacon"))
        self.assertEqual(entry.status, "idle")
        self.assertEqual(entry.name, "beacon-7f")

    def test_a_session_with_a_bridge_id_is_not_listed(self):
        """It already has Remote Control — including every session this bot started."""
        self.register(4242, bridgeSessionId="session_01U43kVxVxP8Es1SamPwHni9")
        self.assertEqual(self.claimable(), [])

    def test_a_session_that_is_not_interactive_is_not_listed(self):
        """`claude -p` and the SDK's headless runs have no conversation to carry on."""
        self.register(4242, kind="print")
        self.register(4243, session_id=OTHER, kind=DROP)
        self.assertEqual(self.claimable(), [])

    def test_a_dead_pid_is_not_listed(self):
        """§9.14: the registry file outlives its process."""
        self.register(4242, running=False)
        self.assertEqual(self.claimable(), [])

    def test_a_reused_pid_is_not_listed(self):
        """§4's rule: a live pid with another start time is somebody else's process."""
        self.register(4242)
        self.processes[4242] = BORN + 3600
        self.assertEqual(self.claimable(), [])

    def test_a_pid_that_cannot_be_dated_is_not_listed(self):
        """Not listing is the safe side here, unlike `ls`: nothing is hidden that is ours."""
        self.register(4242)
        self.processes[4242] = None
        self.assertEqual(self.claimable(), [])

    def test_a_session_outside_projects_root_is_not_listed(self):
        self.register(4242, cwd=self.tmp)
        self.register(4243, session_id=OTHER, cwd="/etc")
        self.assertEqual(self.claimable(), [])

    def test_a_session_in_a_subdirectory_of_a_project_is_not_listed(self):
        """`--resume` must run in the exact cwd, and §3 only reaches direct children."""
        self.register(4242, project=os.path.join("backend", "account-service"))
        self.assertEqual(self.claimable(), [])

    def test_a_session_id_that_is_not_a_uuid_is_not_listed(self):
        """It goes into an argv. Only a UUID reaches one."""
        for pid, bad in enumerate(("--dangerously-skip-permissions", "", "x" * 36, 7,
                                   SID.upper() + " ", None), 5000):
            self.register(pid, session_id=bad)
        self.assertEqual(self.claimable(), [])

    def test_a_malformed_registry_file_is_skipped_not_fatal(self):
        self.register(4242)
        for name, body in (("1.json", "{not json"), ("2.json", "[]"), ("3.json", '"x"'),
                           ("4.json", '{"pid": true}'), ("5.json", '{"pid": -1}')):
            with open(os.path.join(self.registry, name), "w") as fh:
                fh.write(body)
        with open(os.path.join(self.registry, "notes.txt"), "w") as fh:
            fh.write("not a record")
        self.assertEqual([e.pid for e in self.claimable()], [4242])

    def test_a_missing_registry_is_no_sessions(self):
        shutil.rmtree(self.registry)
        self.assertEqual(self.claimable(), [])

    def test_they_are_ordered_by_when_they_started(self):
        """`rc 2` takes the number `rc` printed, so the order cannot be readdir's."""
        self.register(9, session_id=OTHER, project="centrion", born=BORN + 60)
        self.register(80000, born=BORN)
        self.assertEqual([e.pid for e in self.claimable()], [80000, 9])

    def test_an_unknown_status_is_reported_as_unknown(self):
        self.register(4242, status={"x": 1})
        self.assertEqual(self.claimable()[0].status, "unknown")


class TestHandingOver(Base):
    def entry(self, **fields):
        self.register(4242, **fields)
        return self.claimable()[0]

    def end(self, entry):
        return local.end(entry, registry=self.registry, alive=self.alive,
                         started=self.started, kill=self.kill, sleep=lambda s: None)

    def test_an_idle_original_is_sent_sigterm(self):
        entry = self.entry()
        self.assertEqual(self.end(entry), local.ENDED)
        self.assertEqual(self.signalled, [(4242, signal.SIGTERM)])

    def test_a_busy_original_is_left_alone(self):
        entry = self.entry()
        self.register(4242, status="busy")
        self.assertEqual(self.end(entry), local.BUSY)
        self.assertEqual(self.signalled, [])

    def test_an_original_that_has_gone_is_not_signalled(self):
        entry = self.entry()
        del self.processes[4242]
        self.assertEqual(self.end(entry), local.GONE)
        self.assertEqual(self.signalled, [])

    def test_a_pid_reused_since_the_listing_is_not_signalled(self):
        entry = self.entry()
        self.processes[4242] = BORN + 3600
        self.assertEqual(self.end(entry), local.GONE)
        self.assertEqual(self.signalled, [])

    def test_a_pid_now_holding_another_conversation_is_not_signalled(self):
        entry = self.entry()
        self.register(4242, session_id=OTHER)
        self.assertEqual(self.end(entry), local.GONE)
        self.assertEqual(self.signalled, [])

    def test_an_original_that_will_not_die_is_reported(self):
        entry = self.entry()
        self.kill = lambda pid, signum: self.signalled.append((pid, signum))
        self.assertEqual(self.end(entry), local.ALIVE)
        self.assertEqual(self.signalled, [(4242, signal.SIGTERM)])


if __name__ == "__main__":
    unittest.main()
