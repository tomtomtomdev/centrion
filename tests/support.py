"""What the test files share about the box they are running on. WINDOWS.md W1c.

Two facts, both decided once per run. `POSIX` is the platform: the tests that fork the Mac's
runner, open a pty, send a signal or run `launchd/*.sh` are the Mac's tests and are skipped on
Windows, where those mechanisms do not exist (W3 and W4 give their properties portable tests
over a fake `procs`). `can_symlink()` is the account: on Windows `os.symlink` needs Developer
Mode or `SeCreateSymbolicLinkPrivilege`, and without either it raises `OSError` 1314 — so the
symlink cases in the §3 boundary tests are skipped for the account, not the platform, and run
again the moment the privilege is there.

Stdlib only, like the rest of the suite.
"""
import os
import shutil
import sys
import tempfile
import unittest

POSIX = sys.platform != "win32"

_CAN_SYMLINK = None


def can_symlink():
    """Whether this account may create a symlink here. Probed once, in a temp directory."""
    global _CAN_SYMLINK
    if _CAN_SYMLINK is None:
        d = tempfile.mkdtemp(prefix="centrion-symlink-probe-")
        try:
            target = os.path.join(d, "target")
            open(target, "w").close()
            os.symlink(target, os.path.join(d, "link"))
            _CAN_SYMLINK = True
        except (OSError, NotImplementedError):
            _CAN_SYMLINK = False
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return _CAN_SYMLINK


needs_symlinks = unittest.skipUnless(
    can_symlink(),
    "this account cannot create symlinks (on Windows: Developer Mode or "
    "SeCreateSymbolicLinkPrivilege)")
