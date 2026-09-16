# centrion on Windows — port plan

The bot as it stands is a macOS program. `bot.py` and `session.py` both fail on this box at
import time with `ModuleNotFoundError: No module named 'fcntl'`, and that is the shallowest of
the problems: every process and terminal mechanism SPEC.md leans on — `openpty`, `fork`,
`setsid`, `TIOCSCTTY`, `killpg`, `SIGTERM`, `select` on an fd, `lockf` on fd 9, launchd — has no
Windows equivalent. What *is* portable is the shape: three processes, files as the only
protocol, a runner that outlives its launcher, a scraper that finds one URL in a terminal
stream. This document is the plan for keeping that shape and replacing the mechanisms under it.

Status: **W3b done (2026-09-16); W3c next.** The suite runs natively on Windows since W1c:
531 tests, 462 pass, 68 skipped as the Mac's (each skip names its reason or the slice that
un-gates it), one expected failure (W3h's). Since W2b it is run from the venv —
`.venv\Scripts\python -m unittest -q` — because `config.py`'s secrecy check needs pywin32;
`requirements-win.txt` exists as of that slice. The go/no-go question is answered *go*: under
a 200x50 ConPTY, `claude.exe --remote-control` printed its link 6.2 seconds after spawn, as one
contiguous run, and today's `Scrape` finds it unmodified at every chunk size
(`tests/fixtures/rc_startup_win.log`) — and three of those 6.2 seconds were the pseudoconsole
waiting to be told what terminal it had, which W3b now answers (§4); W3g measures what is left. Two Ctrl-C bytes on the ConPTY input ended it in 1.7
seconds with exit status 0. A child of a scheduled task survives the task being stopped, with
no breakaway flag — which is refused there anyway. Everything below that is not in §11's table
is still plan, and the claims about Windows behaviour in it are what the API documents until a
slice turns them into facts.

Facts about this box (2026-09-14): Windows 11 Pro 22621, Python 3.12.10 with pip 25.0.1, no
third-party packages installed (`pywinpty`, `psutil`, `pywin32` all absent). Claude Code
v2.1.268 at `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Anthropic.ClaudeCode_...\claude.exe`,
installed by winget. `claude --help` on Windows lists `--remote-control [name]` and
`--dangerously-skip-permissions`, so the flag form §1 is built on exists here.

---

## 1. Why port rather than use server mode

SPEC.md §1 says to consider `claude remote-control` server mode before building any of this.
The reason not to, here: Remote Control on this machine is fragile and drops often. The bot does
not fix that — it is a launcher, not a bridge — but it makes recovery one Telegram message: when
a session dies or the link goes stale, `claude <project>` from the phone brings up a fresh one
and pushes the new link back, without opening claude.ai and hunting the session list. That is
exactly the one thing §1 says the bot earns its keep on, and on a machine where reconnecting is
routine it earns it more, not less.

---

## 2. What changes and what does not

Per file, with the Unix dependency named. Line counts are today's.

| File | Lines | Portable | Has to change |
|---|---|---|---|
| `telegram.py` | 253 | all of it | nothing |
| `commands.py` | 122 | all of it | nothing |
| `config.py` | 364 | most | the 0600 check (§5.1), `DEFAULT_CLAUDE_BIN` (§5.2), absolute-path rejection for drives and UNC (§5.3) |
| `session.py` | 803 | `Scrape`, `Trust`, `Transcript`, `write_meta`/`read_meta`, `Runner` state machine, `main` | `spawn`, `pump`, `_reaped`, `_signal`, `terminate`, `detach`, `_catch_signals`, `child_env` — everything under the `Runner` loop that touches the pty or the process (§4) |
| `bot.py` | 1354 | the poll loop, dispatch, reconciliation logic, `claim`, `finish`, tails, offsets | `Sessions.alive` (`os.kill(pid, 0)`), `process_started` (`/bin/ps`), `Sessions.stop`, `Sessions.start`'s detach, `serve`'s reliance on bot.sh for the lock (§6) |
| `launchd/` | 3 files | nothing | replaced by `windows/` (§7) |
| `tests/` | ~7,800 | most assertions | `test_session.py` (42 Unix-API references) and `test_bot.py` (32) need platform splits and Windows fixtures (§8) |

Roughly 500–800 lines new or rewritten. The rest is untouched.

### The seam

One decision shapes all of the above: **the platform-specific code moves behind a module
boundary, and the Mac keeps working unchanged.** This is not a fork of the repo for Windows.

```
session.py        Runner, Scrape, Trust, Transcript, meta I/O — portable
  imports  procs  = session_posix.py | session_win.py   chosen by sys.platform
bot.py            Listener, Sessions, dispatch — portable
  imports  procs  for alive(), process_started(), stop(), spawn flags, lock
```

Each platform module exports the same small surface:

```
spawn(argv, cwd, env, rows, cols) -> Terminal      # .read(timeout) .write(b) .pid .close()
alive(pid) -> bool                                 # something answers to this pid
started(pid) -> float | None                       # epoch seconds, for §4's pid-reuse guard
terminate(pid, grace, log) -> bool                 # the session and everything it spawned
detach()                                           # leave the listener behind
spawn_flags() -> dict                              # Popen kwargs for starting a runner
Lock(path) -> .take() -> bool                      # single instance
request_stop(sid_dir) / stop_requested(sid_dir)    # listener → runner (Windows only; posix uses SIGTERM)
```

`session_posix.py` is today's code moved, not rewritten. Slice W1 does that move first and the
Mac test suite has to stay green before a line of Windows code exists.

---

## 3. Stack on Windows

Chosen against §3's Mac stack, with the same bias: what is already here, and what a
Windows Update cannot break at 3am.

| Layer | Mac | Windows | Why |
|---|---|---|---|
| Python | `/usr/bin/python3` 3.9, stdlib only | **3.12.10 from python.org, in a venv under `.venv\`** | There is no system Python on Windows to pin to; a venv with a lockfile is the stable thing. §3's "stdlib only" rule was a defence against brew churn, and it does not survive the pty problem — see next row. |
| Terminal | `os.openpty` + `fork` | **ConPTY via `pywinpty`** | The stdlib has no ConPTY binding. `pywinpty` is the maintained one (compiled wheel, ships for 3.12, what Spyder and Jupyter's terminal use). Window size is a constructor argument, so it is set before the child exists — §6's requirement, and the reason `spawn()` avoids `pty.fork()` on the Mac, comes free. |
| Process tree | `setsid` + `killpg` | **Job Object with `KILL_ON_JOB_CLOSE`** (`pywin32`'s `win32job`) | Windows has no process groups worth the name. A job is the only thing that reliably ends a dev server the session started. It also gives §12's property — kill the runner and claude dies with it — for free, because the runner's job handle closes when the runner dies. |
| Graceful stop | `SIGTERM` to claude | **write `\x03` to the ConPTY, wait `GRACE`, then `TerminateJobObject`** | ConPTY turns a 0x03 on its input into a CTRL_C_EVENT for the attached console. Claude Code asks for a second Ctrl-C to confirm exit, so send it twice with a short gap. Hard kill via the job if it is still there. |
| Listener → runner stop | `SIGTERM` to runner | **a `stop` marker file in the session directory**, polled every `TICK` | Windows cannot deliver a catchable signal to another process. §2 already has the two processes talking only through files, so this is the design extended rather than a new channel. Fallback after `STOP_GRACE`: `TerminateProcess` on the runner, and the job takes claude with it. |
| Liveness | `kill(pid, 0)` + `/bin/ps lstart` | **`psutil.pid_exists` + `psutil.Process(pid).create_time()`** | Both halves of §4's pid-reuse guard in two calls, no `ps` to parse. `psutil` is a compiled wheel, available for 3.12. |
| Detach | `os.setsid()` in the runner | **`Popen(creationflags=DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP)`** in the listener — and **not** `CREATE_BREAKAWAY_FROM_JOB` | *W0c, verified 2026-09-14:* Task Scheduler does put the task in a job, with `LimitFlags = 0` — so `BREAKAWAY` is refused with "Access is denied" and the runner would never start. It is also unnecessary: `Stop-ScheduledTask` terminated the parent and left both a flagless child and a detached child running. The runner survives the listener on Windows by default; the two remaining flags give it no shared console and no inherited Ctrl-C. |
| Lock | `lockf` on fd 9 across an `exec` | **a named mutex, `Local\centrion-<sha1 of config path>`**, taken in `bot.py --serve` before the first `getUpdates` | The kernel releases a mutex when its owner dies, so like `lockf` there is no stale lock to clear. No shell, no inherited fd, and so no equivalent of §8's "the runner must close fd 9" trap — child processes do not inherit a mutex handle unless asked to. |
| Supervision | launchd `KeepAlive` + `RunAtLoad` | **Task Scheduler, at-logon trigger, running `windows\bot.cmd`, which is a restart loop** | See §7. Task Scheduler's own restart-on-failure is capped and slow; a ten-line loop in the wrapper is the honest `KeepAlive`. |
| Config secrecy | `stat` mode 0600 | **DACL check**: refuse any ACE that grants a read or a write to anyone but us, SYSTEM or Administrators | *W2b amended this row:* an allow-list, not the deny-list of three well-known SIDs it used to name. See §5.1. |

Dependencies: `pywinpty`, `psutil`, `pywin32`. Three packages, pinned in `requirements-win.txt`,
installed into `.venv`. All three are wheels; nothing compiles on install. The Mac keeps its
zero-dependency stack — the platform modules are imported lazily, so `session_posix.py` never
imports any of them.

---

## 4. The runner on Windows (`session_win.py`)

What each function in today's `session.py` becomes.

**`spawn(argv, cwd, env, rows, cols)`** — `winpty.PTY(cols, rows)`, then
`pty.spawn(cmdline, cwd=cwd, env=env)`. Two things differ from the Mac and both matter:

- ConPTY takes a **command line string**, not an argv list. `subprocess.list2cmdline(argv)` is
  the quoting rule Windows programs actually parse by, and it has to be that and not a hand
  join: `--prompt` and `project` arrive from a phone (§10), and today's defence is "a list and
  no shell, so nothing to quote". On Windows there is something to quote, so the quoting has
  to be the library's, and `test_session.py` gets a case with a prompt containing `"`, `^`,
  `%` and `&`.
- The child's `argv[0]` is `claude.exe`. `os.access(binary, os.X_OK)` is meaningless on Windows
  (§5.2 covers the config side); `spawn` just tries it, and ConPTY's failure lands on the pty
  and therefore in `pty.log`, which is what §4.6 wants.

*W3b, 2026-09-16 — three corrections, all measured.*

- **pywinpty takes the program and its arguments separately**, and prepends the program to the
  command line itself, quoted (verified against an appname containing a space). So `spawn`
  passes `list2cmdline(argv[1:])`, not the whole argv: passing all of it gives the child its
  own path as `argv[1]`. Silent, and it would have looked like Claude Code rejecting its
  flags.
- **The exec failure does not land on the pty, because there is no child to write it.**
  `CreateProcess` fails synchronously and pywinpty raises `WinptyError` with nothing written to
  the terminal at all — the sentence above is true of the Mac's fork and false here. `spawn`
  raises `OSError` instead, and `Runner.run` now catches it around the spawn and records
  `failed` with the reason in `meta.json`. Without that the runner dies with a traceback over a
  record that still says `starting`, which the listener can only wait out.
- **A pseudoconsole asks the terminal what it is before it lets the child's output through,
  and waits three seconds to be told.** The first bytes off a fresh ConPTY are `ESC[1t`,
  `ESC[c`, `ESC[?1004h`, `ESC[?9001h`; `ESC[c` is DA1, and nothing here was answering it.
  Measured: 3.04s from spawn to the child's first byte, on every shape of child, every run —
  and 0.04s with `Terminal` replying `ESC[?1;0c`. It is answered only once the query has been
  seen, because a reply sent before it would be ordinary input and would reach the child;
  answered after, the pseudoconsole consumes it and an interactive `cmd` never sees it. The old
  WinPTY backend does not have the wait at all (0.22s), which is how the three seconds were
  pinned on ConPTY rather than on pywinpty.

**`pump()`** — no `select`. `pywinpty` reads are `pty.read(length, blocking=False)`, which
returns an empty string when nothing is ready, so the loop becomes: read; if empty, sleep
`TICK`; check `stop_requested()`; check `pty.isalive()`. The `Trust` and prompt timers run on
the same rhythm as today. One consequence to test: `pywinpty` decodes to `str` internally, so
`Scrape.feed` gets text rather than bytes — it already accepts both — and `Transcript.write`
re-encodes to UTF-8. Prefer the raw-bytes API if the installed version exposes one, so the
transcript is what the terminal produced and not a re-encoding of it.

**What ConPTY emits is not what claude emits.** On the Mac the master fd sees the program's
own byte stream. ConPTY re-renders: it keeps a screen buffer and sends a *diff* of it as VT
sequences of its own — cursor moves, `CSI K` erases, colour resets. `Scrape`'s strip regex
covers all of those, and a 55-character link on a 200-column row is drawn as one run, so it
should come through. But "should" is the word W0 exists to remove: the Windows startup capture
becomes `tests/fixtures/rc_startup_win.log` and the existing `Scrape` and `Trust` tests run
against it as well. The `Trust` matcher (whitespace stripped, per §9.3) is the more likely of
the two to need a new fixture, because ConPTY may place the option text with cursor jumps
different from the Mac renderer's.

*W0a, 2026-09-14 — both answered.* The URL was one contiguous run of bytes in the raw stream,
one distinct link in the whole capture, found at every chunk size from 1 to 4096. The trust
dialog came up too — a fresh clone is untrusted on this box, so it is the first thing every
session meets — and ConPTY draws it with `CSI 4 G` column jumps that the squeeze removes, so
`Trust` matches both the question and the moved marker unchanged (`trust_dialog_win.log` is
the panel, and the bytes after it in `rc_startup_win.log` are the redraw after Down). One
thing turned up that is *not* Windows-specific: `Trust` squeezes each chunk on its own and
does not carry a partial escape to the next chunk the way `Scrape` does, so at chunk sizes of
64 bytes and below the escape fragments land inside the phrase and nothing matches. The Mac
gets away with it because 64 KB reads deliver the panel in a few pieces. W3h fixes it.

Also learned: `pywinpty.PTY.read()` returns `str` (it decodes UTF-8 itself) and `write()`
takes `str`. The runner's transcript must re-encode, and W3b's `Terminal.read` should return
bytes so `pump`, `Scrape` and `Transcript` see one type on both platforms.

*W3b: done.* `Terminal.read(timeout)` returns bytes, re-encoded UTF-8 with `surrogateescape` —
the encoding W0a captured the fixtures with, so a fixture and a live read are the same bytes.
3.0.5 exposes no raw-bytes API. Two more things about it are worked around rather than trusted:
`write()` answers `0` for a write that arrived whole, so its return value is not usable, and a
read after the pipe has gone raises `WinptyError` with no errno — which is this mechanism's
`EIO`, so `Terminal.read` treats it as the end of the output and leaves `alive()` to say
whether the session ended. Output is still readable after the child has gone, so `pump` must
drain once more or lose the last of a failure's error text (§4.6). `Terminal.close()` ends the
child on its own — dropping the last reference closes the pseudoconsole and the process is gone
within half a second — which is a second reason for §4's order below, not just a tidiness one.

**`terminate(pid, grace)`** — the runner keeps the job handle it created at spawn. Graceful
first: write `\x03`, wait `SETTLE`, write `\x03` again, wait up to `grace` polling
`pty.isalive()`. Then `win32job.TerminateJobObject(job, 1)`. Then close the pty. **The order is
the point**, as it is on the Mac (§9.10): a `stop` that closes the pty first hangs up a program
that may not yet have attached, and on Windows a program not yet in the job survives a job
kill. So the job assignment happens *before* `spawn` returns — create the job, spawn, assign
by pid, and only then hand the pid back. A pid that will not assign (the process already
exited) is treated as `failed` with the pty tail as the error, same as an exec failure today.

**`_catch_signals()`** — a stop marker instead. `Runner.pump` checks
`os.path.exists(os.path.join(self.dir, "stop"))` each tick; finding it sets `self.stopping`,
which is the flag today's signal handler sets. `SIGINT` from a hand-run `--foreground` console
still works, because `signal.signal(SIGINT)` exists on Windows — keep that one, drop `SIGTERM`
and `SIGHUP`. Also handle `CTRL_BREAK_EVENT` the same way for tidiness; it costs one line.

**`detach()`** — nothing to do in the runner. The listener detaches it at spawn (§6).
`detach()` becomes a no-op on Windows so `main()` is unchanged.

**`Transcript.rotate()`** — today closes then `os.replace`s. That order is already right for
Windows (a rename of an open file fails with `PermissionError` unless it was opened with
`FILE_SHARE_DELETE`, which Python's `open` does not set). Keep the order; add a test that
asserts it, because it is now load-bearing rather than tidy. *W3a: done —
`test_the_transcript_is_closed_before_it_is_renamed` asserts `self.fh.closed` from inside a
recording `replace`. It passed before anything changed, as predicted, so it was checked by
mutation instead: swap the two lines and it fails here with the cap silently switching itself
off, which is the failure it exists to catch and is not a crash.*

**`write_meta()`** — `os.replace` onto a file the *listener* may have open. `read_meta` opens,
reads, closes in microseconds, but the race is real on Windows and the failure is a
`PermissionError` at the moment the runner is writing `live` with the URL in it. The replace is
wrapped in a short retry, five tries 20ms apart. `read_meta` already returns `None` on any
error, so the listener side needs nothing.

*W3a measured all of this rather than assuming it, and the numbers decide two things the plan
left open.* Against a reader polling at `bot.SESSION_POLL` — the listener — **22 of 150 writes
were refused outright without the retry, and none at all with it**, three runs running, for
about 0.2s of waiting across the whole run. The retry is not insurance; it is load-bearing at
the ordinary poll rate. Against a reader that never pauses, though, **nothing wins**: five
tries 20ms apart lost 147 of 150 and ten tries 50ms apart still lost 95 while costing half a
second per write, inside the loop that has to keep reading the terminal. So the bound stays at
five, sized for the real reader and deliberately not widened for the pathological one.

Two things are *not* available as fixes, and both were tried:

- **Opening the reader's handle with `FILE_SHARE_DELETE` does not help.** The obvious reading
  of the rule says a share-delete handle should let the rename through. Measured: `os.replace`
  onto a target held by a `CreateFileW` handle with
  `FILE_SHARE_READ|WRITE|DELETE` fails with `PermissionError`, **winerror 5**, exactly as it
  does against a plain `open()`. `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING` wants more than
  delete-sharing on the target. There is no reader-side fix, so the writer-side retry is the
  whole of the answer.
- **Widening the retry does not help**, per the numbers above.

Giving up stays what it always was — `write_meta` raises what `os.replace` raised, and
`bot.py`'s caller already catches `OSError` — with one repair W3a found on the way: the
temporary file is now unlinked when the last try fails. Its name is fixed by the pid, so before
this every abandoned write left a stale record in the session directory for the next write to
land on top of.

**`child_env()`** — the Mac version replaces `PATH` with a POSIX list and sets `HOME`, `SHELL`,
`TERM`. On Windows: keep the inherited `PATH` and every `SYSTEMROOT`/`COMSPEC`/`APPDATA`/
`LOCALAPPDATA`/`USERPROFILE`/`TEMP` that Windows programs quietly require; apply the same
`CLAUDE*` prefix strip and the named-hazard list; do not set `TERM` (ConPTY does not read it).
`COLUMNS`/`LINES` stay, for the same reason as on the Mac — tools the session runs, not the
terminal size.

---

## 5. Config on Windows

**5.1 — the 0600 check.** `stat.S_IMODE` on Windows reports `0666` for every ordinary file, so
today's check refuses every config file unconditionally. It is replaced, on Windows only, by a
DACL read (`win32security.GetFileSecurity` → `GetSecurityDescriptorDacl`). *Built in W2b, and
two of the three things this section said about it were wrong; the corrected version follows.*

**Who is allowed, not who is forbidden.** The plan named `Everyone` (S-1-1-0), `Users`
(S-1-5-32-545) and `Authenticated Users` (S-1-5-11), which are the three that arrive by
accident. Refusing only those is a deny-list, and a deny-list fails *open*: a grant to a second
local account, to a domain group, to `Guests`, is exactly as readable and is on none of the
three lists. `config.py`'s first paragraph says everything in it fails closed, so the rule is
inverted. An ACE may name the current user, `NT AUTHORITY\SYSTEM` (S-1-5-18),
`BUILTIN\Administrators` (S-1-5-32-544), or `OWNER RIGHTS` (S-1-3-4); anything else is a
refusal, by name. The last two of those are the machine's root, which the Mac's `chmod 600`
does not exclude either. `OWNER RIGHTS` is how the owner's own access is spelled on a file
under `%TEMP%`, in place of their SID.

**An ACE is a bitmask, and half of them are denials.** Three ways to read the list wrongly,
all of which look like the check working:

| Entry | What it is | What the check does |
|---|---|---|
| type 1, `ACCESS_DENIED_ACE_TYPE` | `icacls /deny` — takes access away | Not a grant. Skipped. Reading it as one would refuse every *hardened* file. |
| mask `0x100020` | SYNCHRONIZE \| FILE_TRAVERSE — the AppContainer ACE on `%USERPROFILE%`; `icacls /grant X:(X)` writes a bare `0x20` | Cannot read a byte. Ignored. Only `SECRET_BITS` counts: read/write/append data, DELETE, WRITE_DAC, WRITE_OWNER, GENERIC_ALL/WRITE/READ. |
| types 5–11 | object ACEs; `GetAce` returns a longer tuple with a GUID | Refused, not skipped. An entry we cannot parse is a grant we would have silently dropped. |

A NULL DACL (`GetSecurityDescriptorDacl` → `None`) is refused too: it is not an *empty* list,
which grants nobody anything — it means the object has no discretionary control at all, which
is everyone, everything. So is a descriptor that cannot be read, and so is a missing pywin32.
None of those mean "it is private"; they mean the question went unanswered.

**The fix line, which the first version of got wrong.**

```
icacls "<path>" /inheritance:r /remove:g "BUILTIN\Users" /grant:r "%USERNAME%":F
```

`/inheritance:r` removes only the *inherited* entries. Measured (W2b): against an explicit
`Users` grant — the only kind of file this error is ever printed about — the plan's line left
the grant exactly where it was, so the message told you to run something that did not work.
Explicit entries come off with `/remove:g`, and `config._fix` builds the line naming the
principals it just read off this file, so it is both true and minimal. The test runs the
printed line through `cmd` and then loads the file, which makes the message documentation that
is executed.

**And a fresh install does not hit this.** The plan said a file created under `%USERPROFILE%`
inherits `Users`-readable ACEs from the profile root and so every install would meet this error
once, on purpose. Measured on this box, it does not: `C:\Users\tommy`, `~\Projects` and this
checkout all grant SYSTEM, Administrators and the user, and nothing else, and a
`.telegram.json` written there loads first time. The first-run friction the Mac's `chmod 600`
gives has no counterpart here — which is an argument for the check being an allow-list rather
than a deny-list, not against it: nothing about the default makes the error common, so the
error is worth being right when something has genuinely gone wrong.

**5.2 — `claude_bin`.** `DEFAULT_CLAUDE_BIN = "~/.local/bin/claude"` becomes
`shutil.which("claude")` on Windows, which today resolves to the winget `claude.exe`. The
existence check stays; `os.access(X_OK)` goes (always true on Windows). *W0d, 2026-09-14:*
this box has **both** — `%USERPROFILE%\.local\bin\claude.exe` is v2.1.231 from 2026-08-13 and
stale, the winget one on `PATH` is v2.1.268, and `~/.claude.json` says `installMethod: native`,
`autoUpdates: false`. So the `.local\bin` copy must *not* be preferred: `which` is the default,
and the Mac's "abspath, not realpath" reasoning does not apply because there is no symlink to
follow. With auto-updates off, upgrading is `winget upgrade Anthropic.ClaudeCode`, done by hand,
and the runner picks up the new exe on its next spawn without a restart.

*W2a, 2026-09-15:* built, with one case the plan did not have. `shutil.which` returns `None`
when Claude Code is not installed at all, and on Windows the default *is* that lookup — so
there is nothing to fall back to, and `_binary` would have fallen through to the type check
and answered "`claude_bin` must be a string" about a key the file does not contain. It now
says the file did not set `claude_bin` and `claude` is not on PATH. The check is portable
code reached only on Windows; its test patches `DEFAULT_CLAUDE_BIN` to `None` and so runs on
both platforms.

**5.3 — `resolve()`.** Today's first rule is "the name is a name", because
`os.path.join(root, "/etc")` is `/etc`. On Windows there are two more shapes of that trap:
`C:foo` (drive-relative) and `\\server\share` (UNC), both of which `os.path.isabs` does not
catch the same way. Add `os.path.splitdrive(name)[0] == ""` to the rule. `realpath`, `samefile`
and the case-insensitivity handling in §9.9 already behave correctly on NTFS.

*W2a, 2026-09-15:* built, and the trap is worse than "isabs does not catch it". Measured on
this box, `ntpath.join` gets `C:foo` wrong in **two** directions depending on where the root
is:

| root | `join(root, "C:foo")` | what check 3 then sees |
|---|---|---|
| `C:\Users\tommy\Projects` | `C:\Users\tommy\Projects\foo` | a direct child — **passes** |
| `D:\Projects` | `C:foo` | resolved against C:'s per-drive cwd — outside the root |

Same drive, join silently *drops* the `C:` and the name launders into an ordinary child: a
session starts in a directory nobody named, and `new C:foo` creates it. Different drive, join
keeps `C:foo` whole and `realpath` resolves it against the process's current directory on
drive C:, which is §10.4 broken outright and goes live the day `projects_root` is not on C:.
`isabs("C:foo")` is `False` for both. `splitdrive` also returns the whole of `\\srv\share` as
the drive, so the same one-clause rule covers UNC; the backslashes in it were already refused,
but the containment argument no longer rests on that.

**5.4 — `max_sessions`.** The default of 2 is annotated "8 GB on this box". This box is a
different box; leave the default and set it in `.telegram.json`.

---

## 6. The listener on Windows (`bot.py` through `procs`)

**`Sessions.alive()`** — `psutil.pid_exists(pid)` replaces `os.kill(pid, 0)`. The type guard
before it stays exactly as it is: the comment explains `kill(0)` and `kill(-1)`, and Windows
has its own version of the hazard (pid 0 is the idle process, pid 4 is System; both "exist").

**`process_started()`** — `psutil.Process(pid).create_time()`, `None` on `NoSuchProcess` or
`AccessDenied`. Same `PID_REUSE_SLACK` comparison. Windows reuses pids far more aggressively
than macOS — a freed pid can come back within seconds — so this guard does more work here, and
the test for it should include a pid that exists but started *after* the record.

**`Sessions.stop()`** — write the `stop` marker, then wait up to `STOP_GRACE` for
`alive()` to go false. Only then `psutil.Process(pid).terminate()` on the runner; the job takes
claude with it. §9.11's nesting rule carries over unchanged: the listener's grace must exceed
the runner's `GRACE` plus the two Ctrl-C settles, or the listener kills a runner that was
halfway through ending claude cleanly.

**`Sessions.start()`** — `Popen(argv, stdin=DEVNULL, cwd=HERE, close_fds=True,
creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`. No `CREATE_BREAKAWAY_FROM_JOB`
and no retry logic: W0c showed the scheduler's job refuses breakaway outright, and showed the
runner does not need it — a child of a scheduled task survives the task being stopped. The
runner therefore stays *inside* the scheduler's job for its life. That is harmless today
(`LimitFlags = 0`, no kill-on-close) and is the assumption W5c re-verifies with a real
session: if a Windows update ever gives that job `KILL_ON_JOB_CLOSE`, every session dies with
the listener, and the fix at that point is to spawn the runner through a second scheduled task
or WMI rather than `Popen`. `reap()` stays: `Popen.poll()` works on Windows.

**The lock** — moves from `bot.sh` into `serve()`, before `Telegram()` is constructed, so a
second copy is refused before its first `getUpdates` (§7's 409 is mutual). On the Mac the lock
stays in `bot.sh`, and `serve()` calls `procs.Lock(...).take()`, which is a no-op on posix
returning `True`. A refused copy exits 0 with the same message `bot.sh` prints today, and the
wrapper loop in §7 sleeps ten seconds and tries again — the same "say so every ten seconds
until the hand-run copy exits" behaviour `bot.sh`'s header describes.

**`claim()`** — `os.open(O_CREAT | O_EXCL)` is atomic on NTFS too; the `0o600` mode argument is
ignored there, which is fine because the marker is empty. No change.

**`main()`'s error message** — `run sh launchd/bot.sh` becomes platform-conditional text
pointing at `windows\bot.cmd`.

---

## 7. Startup — `windows\`

Replaces `launchd\` on this platform. Three files.

**`windows\bot.cmd`** — what the scheduled task runs. The `KeepAlive` loop:

```bat
@echo off
cd /d "%~dp0.."
if not exist var mkdir var
:loop
echo === %date% %time% centrion listener starting === >> var\bot.log
.venv\Scripts\python.exe bot.py --serve >> var\bot.log 2>&1
echo === %date% %time% listener exited %errorlevel% === >> var\bot.log
timeout /t 10 /nobreak > nul
goto loop
```

Ten seconds is launchd's default `ThrottleInterval`, and the loop restarts on *every* exit,
which is the `KeepAlive true, unconditional` the plist header insists on — a listener that has
exited has stopped listening, and there is no successful version of that.

**`windows\centrion.xml`** — a Task Scheduler task definition, imported with
`schtasks /Create /TN centrion /XML windows\centrion.xml`. The settings that matter:

| Setting | Value | Why |
|---|---|---|
| Trigger | `LogonTrigger` for this user | `RunAtLoad` |
| Action | `cmd.exe /c "<repo>\windows\bot.cmd"` | |
| `ExecutionTimeLimit` | `PT0S` | the default is 3 days, after which the task is killed |
| `MultipleInstancesPolicy` | `IgnoreNew` | belt for the mutex's braces |
| `DisallowStartIfOnBatteries`, `StopIfGoingOnBatteries` | `false` | this is a laptop |
| `StartWhenAvailable` | `true` | a missed logon trigger still fires |
| `RestartOnFailure` | `PT1M`, count 3 | for the wrapper itself dying; the loop handles the listener |
| `Hidden` | `true` | no console window on the desktop |
| `LogonType` | `InteractiveToken` | run as the logged-on user, no stored password |

`InteractiveToken` means the bot exists only while this user is logged on. That is the same
place launchd's GUI domain leaves the Mac (SPEC.md §14: "a login after a cold boot is still
unwatched"). Moving to `Password` logon type and "run whether user is logged on or not" would
close that gap but runs the bot in a non-interactive session; ConPTY does not care, and
Claude Code's login lives in `%USERPROFILE%\.claude` rather than in an interactive keychain, so
it may well just work — but it is a W5 verification, not an assumption. The paths in the XML
are absolute and specific to this checkout, exactly as the plist's are; the install script
writes them.

**`windows\install.ps1`** — creates `.venv`, installs `requirements-win.txt`, writes the XML
with this checkout's path substituted, registers the task, and prints the `icacls` line for
`.telegram.json`. Idempotent; re-running it re-registers.

**Sleep and hibernate.** Not a launchd concern; very much a Windows-laptop one. A sleeping
machine holds no `getUpdates`, and on wake Telegram returns the queued messages — §7's
message-date guard already discards the stale ones. The session side is worse: an open
Remote Control session does not survive a sleep, and it is the reason for §1 of this document.
Nothing to build; one line in the README saying so.

---

## 8. Tests

The suite is stdlib `unittest` and stays that way. The changes:

- `tests/test_session.py` splits into portable tests (`Scrape`, `Trust`, `Transcript`, meta
  I/O, `Runner` state transitions with a fake `procs`) and `tests/test_session_posix.py` /
  `tests/test_session_win.py`, each decorated `@unittest.skipUnless(sys.platform == ...)`. The
  42 Unix references today are almost all in tests that fork a real child or send a real
  signal; they move to the posix file unchanged.
- Same split for the 32 in `test_bot.py`: `alive`, `process_started`, `stop` get a fake `procs`
  for the portable tests and one real-process test per platform.
- New fixtures: `rc_startup_win.log` (§4), and a `Trust` dialog capture if the Mac one does
  not match.
- New tests named above: command-line quoting of a hostile prompt (§4), `rotate()` ordering
  (§4), `write_meta` retry (§4), `splitdrive` in `resolve()` (§5.3), pid reuse with a
  later-started pid (§6), DACL refusal (§5.1).
- Optional, W6: a GitHub Actions matrix (`windows-latest`, `macos-latest`) running
  `python -m unittest`. Cheap, and it is the only way the Mac side stays green from a Windows
  desk.

---

## 9. Slices

Small, and all the same shape. A slice is one sitting's work, it leaves the Mac suite green
and the Windows suite green (natively, since W1c), and it ends in a commit. No slice starts until
the previous one's progress row in §11 is filled in.

### The ritual

Every slice, in this order, no skipping:

1. **Red.** Write the tests named in the slice first. Run the suite. The new tests fail, or
   are skipped for a missing fixture — and nothing else changes colour. A slice whose tests
   pass before its code exists has the wrong tests.
2. **Green.** Write the least code that passes them. Platform tests are decorated
   `@unittest.skipUnless(sys.platform == "win32", ...)` or `!= "win32"`; portable tests are
   not decorated at all.
3. **Build.** `.venv\Scripts\python -m compileall -q .` on Windows. On the Mac, the same with
   `/usr/bin/python3` — the Mac is 3.9 and Windows is 3.12, so a 3.10+ construct (`match`,
   `X | Y` in annotations, parenthesised context managers) is a Mac break that Windows tests
   will never see.
4. **Run.** The hand-run named in the slice, on the real thing, and read the output. A slice
   with no runnable surface says so.
5. **Test.** The whole suite: `.venv\Scripts\python -m unittest -q` on Windows — from the
   venv since W2b, where `config.py` grew a check that needs pywin32; the system
   interpreter still *imports* everything and fails 41 config tests. For slices that touch
   posix code or shared code, also on the Mac (`/usr/bin/python3 -m unittest -q`) before the
   commit, not after.
6. **Commit.** One commit per slice, message `W<n>: <what>`, body naming the tests added.
   Nothing half-done gets committed as "WIP"; if a slice is too big to finish in a sitting,
   split it in this document first.
7. **Progress.** Fill in the row in §11: date, commit hash, what the run step showed, and
   anything learned that changes a later slice. Amend the later slice's text in the same
   commit or the next — the plan is the record, not the chat.

The Mac suite is the regression guard for everything that moves in W1. Until a CI matrix
exists (W6), "green on the Mac" means somebody ran it there; write down that it happened.

### W0 — spike

Exploratory by nature, but it still starts red: the tests that will consume its output are
written first and skip on the missing fixture.

**W0a — a link out of ConPTY.**
- Red: `test_session.py::test_scrape_finds_link_in_windows_capture` loads
  `tests/fixtures/rc_startup_win.log` and asserts `Scrape` returns one `session_` URL;
  `skipUnless(os.path.exists(fixture))`. Same shape for the `Trust` matcher against a
  `tests/fixtures/trust_dialog_win.log`, if W0a can provoke the dialog in an empty directory.
- Green: `pip install pywinpty` into `.venv`; `scratch\w0a.py` spawns
  `claude.exe --remote-control spike --dangerously-skip-permissions` in a real project under
  `PTY(200, 50)` and appends every raw chunk to the fixture for 60s. Then the tests un-skip
  and pass — or they fail, and that is the go/no-go answer (§4: fallback is scraping the
  session file Claude Code writes under `~/.claude`).
- Run: the script. Read the fixture by eye once; note whether the URL arrived as one run.
- Commit: fixtures and tests. `scratch\` is gitignored.

**W0b — graceful exit.**
- Red: nothing to test yet; the question is a fact. Record the intended assertion in §11
  first: "after two `\x03` writes, `isalive()` is false within `GRACE`".
- Run: extend `w0a.py` to write `\x03`, sleep `SETTLE`, write `\x03`, and poll `isalive()`.
  Record the seconds it took, and whether one Ctrl-C was enough.
- Commit: nothing (`scratch\`), unless the answer changes §4's `terminate` text — then that
  edit is the commit.

**W0c — a child that outlives its parent under Task Scheduler.**
- Red: write `tests/test_procs_win.py::test_detached_child_survives_parent_exit` — spawn a
  Python parent that spawns a Python grandchild with the §6 flags and exits; assert the
  grandchild is alive 2s later. Runs from a console, so it passes in W4c's context; here it
  is the specification, and it is *expected to say nothing yet about the scheduled-task
  case*.
- Run: register a throwaway task running the same parent; after it runs, check the grandchild
  with `tasklist`; then `schtasks /End` the task and check again. Record the result. If the
  grandchild dies, §6's fallback (`wmic`/a second task) is promoted in W4c before W1 starts.
- Commit: the test file, skipped-on-posix.

**W0d — where `claude.exe` lives after an update.**
- Run: `claude --version`, check for `%USERPROFILE%\.local\bin\claude.exe`, and read the
  updater's behaviour from `claude doctor` or its log. Record in §11; it decides W2a's default.
- Commit: none unless §5.2 changes.

### W1 — the seam (posix side moves, nothing changes)

**W1a — `session_posix.py` exists and `session.py` uses it.**
- Red: `test_session.py::test_procs_surface` asserts `session.procs` has `spawn`, `alive`,
  `started`, `terminate`, `detach`, `spawn_flags`, `Lock`, `request_stop`, `stop_requested`.
  `test_procs_is_posix_off_windows` patches `sys.platform` to `darwin`, reloads, asserts the
  module name.
- Green: move `spawn`, `_reaped`, `_signal`, `terminate`, `detach` and the signal-handler
  setup into `session_posix.py` verbatim; `session.py` gets `procs = _platform()` and calls
  through it. `Lock.take()` returns `True`; `request_stop`/`stop_requested` are no-ops.
- Run: `python3 session.py --foreground --cwd <project> --name w1a` on the Mac still
  produces a link.
- Test: Mac suite green, unchanged count except the two new tests.

**W1b — `bot.py`'s process calls go through `procs`.**
- Red: `test_bot.py` tests for `alive()` and `process_started()` patch `bot.procs.alive` /
  `bot.procs.started` instead of `os.kill` / `subprocess.check_output`; existing assertions
  unchanged. A new test asserts `Sessions.stop` calls `procs.terminate`.
- Green: `alive`, `process_started` (renamed `started` inside `procs`), `stop`, and the
  `Popen` kwargs in `start()` go through `procs`. `serve()` calls `procs.Lock(LOCK).take()`.
- Run: `sh launchd/bot.sh` on the Mac, one `ls` from the phone.
- Test: Mac green.

**W1c — everything imports on Windows.**
- Red: `tests/test_layout.py::test_imports_on_this_platform` does `importlib.import_module`
  for `bot`, `session`, `config`, `commands`, `telegram`. On Windows today it fails on
  `fcntl`.
- Green: `session_win.py` stub whose every function raises `NotImplementedError("W3")`;
  `session.py`/`bot.py` choose it on `win32`. Move the top-level `import fcntl / termios /
  select / signal` into `session_posix.py`. Decorate every existing test that forks, opens a
  pty, or sends a signal with `skipUnless(sys.platform != "win32")` — expected to be ~40 in
  `test_session.py` and ~30 in `test_bot.py`; count them in §11.
- Run: `python -c "import bot, session"` on Windows.
- Test: **first Windows-green run.** Record the pass/skip counts on both platforms.

### W2 — config

**W2a — `claude_bin` default and drive-letter rule.**
- Red: `test_config.py`: on `win32`, `DEFAULT_CLAUDE_BIN` resolves via `shutil.which`; the
  `X_OK` check is skipped on `win32`; `resolve("C:foo", root)`, `resolve(r"\\srv\share",
  root)` and `resolve("C:\\x", root)` raise `ProjectError`. Posix cases unchanged.
- Green: §5.2 and §5.3.
- Un-gate (W1c): `test_config.py::test_a_non_executable_binary_is_refused` and
  `test_the_default_is_used_when_absent` are `skipUnless(POSIX)` naming this slice; they become
  the posix half of a platform pair here, alongside the win32 tests above.
- Run: `python -c "import config; print(config.load(check_claude=True))"` on Windows with a
  minimal `.telegram.json` — expect the 0600 error, which is W2b's red.
- Test: both platforms.

**W2b — DACL check.**
- Red: `test_config.py::test_refuses_world_readable_dacl` (win32) creates a temp file, grants
  `Users` read via `icacls`, asserts `ConfigError` mentioning `icacls`;
  `test_accepts_owner_only_dacl` strips inheritance and passes. `pywin32` joins
  `requirements-win.txt`.
- Green: §5.1.
- Un-gate (W1c): `test_config.Base.setUp` patches `REQUIRED_MODE` to `0666` on win32 so the
  other thirty rules in that file can be tested at all, and `TestPermissions` (4) is
  `skipUnless(POSIX)`. Both go when the DACL check lands: the patch becomes unnecessary
  because the win32 check no longer reads the mode. `test_bot.py::
  test_a_creation_that_fails_is_answered_rather_than_raised` (a `chmod 0500` root) is also
  posix-only since W1c — `os.chmod` only toggles the read-only attribute on Windows, and that
  does not stop `mkdir` inside a directory — and wants a win32 twin here: a root whose DACL
  denies the account write, via `icacls /deny`.
- Run: `config.load()` on this box succeeds after running the printed `icacls` line.
- Test: both platforms.

### W3 — the runner

**W3a — two portable fixes made load-bearing.**
- Red: `test_session.py::test_rotate_closes_before_replace` asserts the order via a
  recording `replace`; `test_write_meta_retries_permission_error` patches `os.replace` to
  raise `PermissionError` twice then succeed, asserts one file written and two sleeps.
- Green: the retry (five tries, 20ms) in `write_meta`; `rotate` already has the order.
- Un-gate (W1c): `test_session.py::test_a_reader_never_sees_a_partial_record` is
  `skipUnless(POSIX)` naming this slice — on Windows its writer dies with `PermissionError`
  at the first collision. Its reader holds the file open across `json.loads` of a 100 KB
  record in a tight loop, so five tries 20ms apart may still lose against it; this slice
  decides whether the retry widens or the test's reader is made to release between reads,
  and un-gates the test either way.
- Run: none.
- Test: both platforms.

**W3b — `Terminal` and `spawn` on ConPTY.**
- Red: `test_session_win.py::test_spawn_reports_size` spawns `cmd.exe /c mode con` and
  asserts `Columns: 200` and `Lines: 50` in the read output (§6 on Windows);
  `test_spawn_returns_pid_and_terminal`; `test_read_timeout_returns_empty`.
- Green: `session_win.Terminal` wrapping `winpty.PTY` — `read(timeout)`, `write(bytes)`,
  `alive()`, `close()`, `pid`; `spawn()` builds the command line with
  `subprocess.list2cmdline`. `pywinpty` in `requirements-win.txt`.
- Run: `python -c` spawn of `cmd /c echo hello` and print what came back.
- Test: Windows.

*W3b: done.* Twelve tests in the new `tests/test_session_win.py`, all against real processes,
plus one portable test for the spawn failure `Runner.run` now records. §4 above carries the
three corrections. The run step spawned `cmd /c echo hello` (`hello` back in 0.05s) and then
`cmd /c mode con`, which answered **`'mode' is not recognized`** — not a ConPTY problem but
`child_env()`, which is still the Mac's and hands the child a POSIX `PATH`. That is W3g's
work, and it is now visible rather than predicted: until W3g, nothing the runner starts can
find a Windows program that is not a shell builtin.

**W3c — command-line quoting.**
- Red: `test_session_win.py::test_hostile_prompt_survives_quoting` spawns
  `python -c "import sys; print(sys.argv[1])"` with an argument containing `" ^ % & | < >`
  and a trailing backslash, asserts the child printed it back byte-for-byte.
- Green: usually nothing beyond `list2cmdline`; if it fails, the fix is here and nowhere else.
- Run: none.
- Test: Windows.

**W3d — `pump` without `select`.**
- Red: portable `test_session.py::test_pump_with_fake_terminal` drives `Runner.pump` with a
  fake `Terminal` yielding the W0a fixture in chunks, asserts state goes `LIVE` with the URL
  and the transcript equals the input. Windows `test_pump_real_echo` spawns
  `cmd /c echo https://claude.ai/code/session_w3d_test` and asserts `LIVE`.
- Green: `pump` takes a `Terminal`; posix `Terminal` wraps the master fd with `select`
  inside `read(timeout)` so the loop body is one implementation on both platforms.
- Run: the W0a fixture through `Scrape` again — it should still pass; it is the same code.
- Test: both platforms; the Mac `--foreground` hand-run again.

**W3e — Job Object and `terminate`.**
- Red: `test_session_win.py::test_terminate_kills_tree` spawns `cmd /c "start /b ping -n 100
  localhost & ping -n 100 localhost"`, collects `psutil.Process(pid).children(recursive=True)`,
  terminates, asserts none alive. `test_terminate_graceful_first` spawns a Python child that
  catches `KeyboardInterrupt`, prints `caught`, exits 0; asserts `caught` is in the
  transcript and the exit was not a job kill.
- Green: create job before spawn, assign after, `terminate` = `\x03`, settle, `\x03`, wait
  `grace` on `alive()`, then `TerminateJobObject`, then close.
- Run: none beyond the tests.
- Test: Windows.

**W3f — the stop marker.**
- Red: portable `test_stop_marker_ends_pump` — fake terminal that never ends; a thread
  writes the marker after 0.3s; `pump` returns and the runner writes `ended`. Windows
  `test_request_stop_is_atomic_and_idempotent`.
- Green: `request_stop`/`stop_requested` on both platforms (posix keeps `SIGTERM` *and*
  honours the marker, which costs nothing and makes the tests one set); `pump` checks it each
  tick; `_catch_signals` on win32 handles `SIGINT` and `CTRL_BREAK_EVENT` only.
- Run: none.
- Test: both platforms.

**W3g — `child_env` on Windows, and the real thing.**
- Red: `test_child_env_win_keeps_windows_essentials` — `SYSTEMROOT`, `COMSPEC`, `APPDATA`,
  `LOCALAPPDATA`, `USERPROFILE`, `TEMP`, `PATH` survive; `CLAUDE_*` and the hazard list do
  not; `TERM` is not set.
- Un-gate (W1c): `test_session.py::test_claude_is_first_on_the_path` (the Mac's `PATH`) is
  `skipUnless(POSIX)` naming this slice; the test above is its win32 twin.
- Green: §4's last paragraph.
- Run: **`python session.py --foreground --cwd <project> --name w3g` on this box produces
  a link and `pty.log` under `var\sessions\`. Ctrl-C ends it and `meta.json` says `ended`.**
  This is the runner's acceptance run; record the time-to-link in §11.
- Test: Windows; Mac suite for the shared `pump` changes.

**W3h — `Trust` carries a partial escape, like `Scrape`.** Portable; found by W0a.
- Red: already written. `test_session.py::TestTheWindowsTrustDialog::
  test_the_answer_does_not_depend_on_chunking` is decorated `@unittest.expectedFailure`
  and fails at chunk sizes 64, 16 and 1. Remove the decorator; it is now the red test. Add the
  same sweep against the Mac fixture in `TestTheTrustDialog` for the question half.
- Green: give `Trust.feed` the carry `Scrape.feed` has — hold a trailing `\x1b...` fragment
  (bounded by `CARRY_LIMIT`) and prepend it to the next chunk before squeezing. Same for a
  split multi-byte character: use an incremental decoder as `Scrape` does, or squeeze bytes
  through one shared helper.
- Run: none.
- Test: both platforms.

### W4 — the listener

**W4a — `alive` and `started` via psutil.**
- Red: `test_procs_win.py::test_alive_true_for_running_false_after_exit`;
  `test_started_matches_create_time`; `test_bot.py::test_alive_rejects_pid_reused_later`
  (portable, via patched `procs.started` returning `started + PID_REUSE_SLACK + 1`).
- Green: `psutil` in `requirements-win.txt`; the two functions.
- Un-gate (W1c): `test_bot.py::TestWhetherARunnerIsStillThere` (8, `/bin/sleep` and
  `session_posix.started`) is posix-only since W1c, and `TestSpawningForReal` (fd 9,
  `os.getsid`) with it; their properties get the portable twins over a fake `procs` named
  here and in W4b–W4c, and one real-process test per platform.
- Run: `python -c` printing `alive`/`started` for this shell's pid.
- Test: both platforms.

**W4b — `Sessions.stop` on Windows.**
- Red: `test_bot.py::test_stop_writes_marker_then_waits` (portable, fake `procs`);
  `test_bot_win.py::test_stop_real_runner` starts the real `session.py` against
  `cmd /c ping -n 60 localhost` as the argv (an `--argv` debug flag, or a test hook), calls
  `stop`, asserts `ended` in `meta.json` and no pids left.
- Green: `stop` = `request_stop`, wait `STOP_GRACE` on `alive`, then `terminate` runner.
- Run: none beyond the tests.
- Test: both platforms.

**W4c — the runner outlives the listener.**
- Red: W0c's tests already pass from a console; the new red is
  `test_bot.py::test_start_uses_the_platform_flags` (portable, fake `procs`) asserting
  `Popen` is called with `procs.spawn_flags()`, and `test_procs_win.py::
  test_spawn_flags_exclude_breakaway` asserting the real `spawn_flags()` matches W0c's
  finding. No retry logic — W0c removed the need for it.
- Green: `spawn_flags()` on win32 returns `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`;
  `start()` passes it through.
- Run: from a console, `python bot.py --serve`, start a session from the phone, Ctrl-C the
  listener, `tasklist` shows the runner and `claude.exe` still there; restart the listener
  and `ls` shows the session as live, same pids.
- Test: both platforms.

**W4d — the mutex.**
- Red: `test_procs_win.py::test_second_lock_refused`; `test_lock_released_on_owner_death`
  (a subprocess takes it and is killed; retake succeeds within 1s);
  `test_bot.py::test_serve_exits_zero_when_lock_refused` (portable, fake `Lock`).
- Green: `Lock` via `CreateMutexW` (`pywin32` or `ctypes`); `serve()` takes it before
  constructing `Telegram`.
- Run: two consoles both running `bot.py --serve`; the second prints the refusal and exits 0.
- Test: both platforms.

**W4e — end to end from the phone.** No new code expected.
- Red: the acceptance checklist written into §11 before the run: `claude <project>` → link
  within 45s; `ls` shows it; `stop 1` ends it and `ls` agrees; `new <name>` answers the
  trust dialog; `max_sessions` refuses the one past the cap; a `failed` session sends the
  pty tail.
- Run: all six from the phone, listener in a console.
- Commit: whatever the run found, each with its own test; otherwise a §11 row only.

### W5 — startup

**W5a — `windows\bot.cmd` and `install.ps1`.**
- Red: `test_layout.py`: `windows/bot.cmd`, `windows/install.ps1`, `requirements-win.txt`
  exist; `.gitignore` covers `.venv/` and `scratch/`; `bot.cmd` contains `--serve` and
  `goto loop`.
- Green: §7's two files.
- Run: `install.ps1` on a clean clone in `%TEMP%`; `windows\bot.cmd` from a console, then
  kill the Python process — the loop restarts it after 10s and `var\bot.log` shows both lines.
- Test: both platforms (layout tests run everywhere).

**W5b — the scheduled task.**
- Red: `test_layout.py::test_task_xml_settings` parses `windows/centrion.xml` and asserts
  the §7 table: `PT0S`, `IgnoreNew`, batteries `false`, `StartWhenAvailable true`,
  `InteractiveToken`, a `LogonTrigger`; and the win32 counterparts of the three
  `TestTheLaunchdInstall` checks W1c gated as `this_mac_checkout` — every path in the XML
  absolute and present, the working directory is this checkout, the log is `var\bot.log`.
- Green: the XML template; `install.ps1` substitutes the path and runs `schtasks /Create`.
- Run: register; log off and on; `var\bot.log` shows the listener up within a minute and a
  phone `ls` answers. Record the seconds.
- Test: both platforms.

**W5c — restart survival, the launchd §12 slice 9 test.** No new code expected.
- Red: checklist into §11 first: start a session; `schtasks /End /TN centrion`; `tasklist`
  shows runner and claude alive; `schtasks /Run`; `ls` shows the session live with the same
  runner pid; `stop 1` ends it.
- Run: the checklist.
- Commit: fixes if any, else the §11 row.

**W5d — the not-logged-on case.** Optional.
- Run: switch the task to `LogonType Password` and "run whether user is logged on or not";
  reboot; do not log in; message the bot. Record what happens. Revert if it does not work.

### W6 — CI matrix. Optional.
- Red: none meaningful; the workflow is the test.
- Green: `.github/workflows/test.yml`, `windows-latest` and `macos-latest`, `python -m
  unittest -q`, Windows job installs `requirements-win.txt`.
- Run: push; both jobs green.

### Time items

Not slices; §11 gets a row when each happens. The first day of §10.7's retention on NTFS
(`shutil.rmtree` against a directory whose `pty.log` something may still hold); the first
sleep/wake with a session open; the first Claude Code self-update under a live runner.

---

## 10. What this does not do

- It does not make Remote Control less fragile. Nothing on this machine can; the bot makes
  the fragility cost one message instead of a trip to a laptop.
- It does not add a second transport. §2's "launcher, not a bridge" stands.
- It does not run as a Windows service. Services live in session 0 without the user's
  environment or credential store; Task Scheduler as the user is the analogue of a
  LaunchAgent in the GUI domain, and that is what the Mac design is.
- It does not touch `telegram.py` or `commands.py`. If a change there looks necessary during
  the port, it is a bug in the plan.

---

## 11. Progress

One row per slice, filled in at step 7 of the ritual and nowhere else. `Status` is one of
`todo`, `red` (tests written, failing), `green` (passing, not yet committed), `done`
(committed), `skipped` (with the reason). `Suite` is `pass/skip` counts on Windows, then on
the Mac when the slice touched shared or posix code.

| Slice | Status | Date | Commit | Suite (win · mac) | Run step showed / learned |
|---|---|---|---|---|---|
| — baseline before any slice | — | 2026-09-14 | 7a09f8d | 163 ran, 23 F, 18 E · not run | Windows: `test_bot` and `test_session` fail to import (`fcntl`); `test_config`'s 0600 tests and `test_projects` fail. Mac suite not run from this desk. |
| W0a link out of ConPTY | done | 2026-09-14 | d6701b9 | 6 pass, 1 xfail (stubbed run, see note) · not run | **Go.** Link 6.2s after spawn, contiguous, one distinct link, found at every chunk size. Trust dialog met first (fresh clone is untrusted) and answered via ConPTY arrow keys — second fixture for free. `Trust` loses the dialog at chunks ≤64 bytes: new slice W3h. pywinpty I/O is `str`, not bytes. New tests run on Windows via `scratch\run_win_tests.py`, which stubs `fcntl`/`termios` until W1c; they run natively on the Mac. Full suite unchanged from baseline. |
| W0b graceful exit | done | 2026-09-14 | — (scratch only) | — | Two `\x03` 0.4s apart: exit status 0 after 1.71s. One Ctrl-C alone was not tried; the pair is what §4 specifies. Job kill stays as the fallback, not the path. |
| W0c child outlives parent under Task Scheduler | done | 2026-09-14 | 0c549c6 | 3 pass (`tests.test_procs_win`) · n/a | **Both children survive; breakaway is refused.** Under a scheduled task the parent is in a job with `LimitFlags = 0`: `CREATE_BREAKAWAY_FROM_JOB` → "Access is denied" and no child at all. `Stop-ScheduledTask` killed the parent (task was Running) and left the flagless control *and* the `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` child alive. §3, §6 and W4c amended: no breakaway, no retry. Round 1's "parent_in_job: false" was a bad ctypes call (no `wintypes`), corrected in round 2. |
| W0d `claude.exe` location after update | done | 2026-09-14 | d6701b9 | — | `which` → winget exe v2.1.268. `~\.local\bin\claude.exe` also present, v2.1.231, stale. `autoUpdates: false`, `installMethod: native`. §5.2 amended: do not prefer `.local\bin`. |
| W1a `session_posix.py` | done · Mac pending | 2026-09-14 | 30f3ccc | 29 pass, 1 xfail (stubbed run) · **not run** | `spawn`/`_reaped`/`_signal`/`terminate`/`detach` moved verbatim; `session.spawn`/`terminate` stay as wrappers because their defaults (`ROWS`/`COLS`, `GRACE`) are session.py's constants; `detach`/`_reaped` are the platform's. Signal setup went through `procs.catch_signals`. `alive()` added for W1b; `started()` raises `NotImplementedError("W1b")`. On Windows `session.py` now fails on `session_win` instead of `fcntl` — same count. **Mac hand-run and Mac suite not done from this desk; see "Pending on the Mac".** |
| W1b `bot.py` through `procs` | done · Mac pending | 2026-09-14 | 42fb3d4 | 15 pass (stubbed run: W1b's 10 + W1a's 6, one shared) · **not run** | `Sessions.alive` → `procs.alive` + `procs.started`; `process_started` moved to `session_posix.started` verbatim, alias kept for the real-process tests; `start()` passes `**procs.spawn_flags()`; `serve()` takes `procs.Lock(LOCK)` before constructing `Telegram`, exits 0 when refused; `bot.LOCK = var/.bot.lock`, the file lock.sh uses. `errno` import gone from bot.py. First red run caught that a module-level alias is not patchable — `alive` calls `procs.started` directly. |
| W1c imports on Windows | done | 2026-09-15 | 12100d6, ef04618 | **492 ran: 422 pass, 69 skip, 1 xfail**, 5.9s · **not run** | **First Windows-green run.** `python -c "import bot, session"` → `procs = session_win`. Skips by file — `test_session` 31 (26 fork/pty/signal, 3 import `session_posix`, 1 → W3a, 1 → W3g), `test_bot` 23 (16 fork/shell/`ps`, 3 plist-vs-checkout → W5b, 3 symlink, 1 `chmod` → W2b), `test_config` 7 (4 modes → W2b, 2 → W2a, 1 symlink), `test_projects` 8 (6 symlink, 1 premise, 1 realpath case); every skip names its reason or its slice, and the plan's guess of ~40/~30 was 31/23. The full-suite hang was `TestSpawningForReal` alone — its stub child dies on `os.getsid` and the test waits for output that never comes; no other class blocks on Windows. Two assertions were Mac-*shaped* rather than Mac-only and are now portable: `tilde()`'s expected string (`os.path.join("~", …)`) and the read of `bot.py` as UTF-8 (the Windows default codec is cp1252, which has no 0x81). `test_config.Base` stands the 0600 check down on win32 so the other thirty rules run (W2b). `tests/support.py` is new: `POSIX`, and `needs_symlinks`, which skips for the *account* — this one cannot create symlinks (error 1314; Developer Mode is off) — so 10 §3 boundary tests skip here and run again the moment the privilege exists; whether they then pass on NTFS is unverified. Windows `realpath` returns the on-disk spelling (`resolve("BEACON")` → `…\beacon`), so the miscased-name note test is the Mac's. `scratch\run_win_tests.py` retired. Mac: not run — see Pending. |
| W2a `claude_bin` default, drive rule | done · Mac pending | 2026-09-15 | 1355e76 | **499 ran: 429 pass, 69 skip, 1 xfail**, 5.9s · **not run** | **The drive rule was not a tidy-up; it was a live hole.** `resolve("C:foo")` refused before this slice only by falling through to check 4's "no project by that name exists" — so the red test had to assert check 1's wording, and `create("C:foo")`, which inverts check 4, made the directory. Measured: `ntpath.join` drops the `C:` when the root is on the same drive (`C:foo` → `<root>\foo`, a name laundered into a different one that check 3 accepts) and keeps it whole when the root is on another drive (`C:foo` resolved against C:'s per-drive cwd, outside the root — §10.4 broken, and live the day `projects_root` is not on C:). `splitdrive` returns the whole of `\\srv\share` as the drive too, so one clause covers UNC. §5.3 amended with the table. `shutil.which` has a `None` case the plan did not: with Claude Code not installed the Windows default *is* the lookup, so `_binary` now says so by name instead of falling through to "`claude_bin` must be a string" (§5.2 amended). Run step: the literal command gave W2b's 0600 error as predicted; with the mode check stood down, `claude_bin` resolved to the winget exe and not to the stale `.local\bin` copy, and the three drive shapes were refused against the real `~\Projects`. +7 tests, no skip count change: the two W2a-gated posix tests now have win32 twins, and the twin for `X_OK` was green before the code changed — `os.access(X_OK)` here is `F_OK` under another name, so removing the call changes no answer, only what the code claims. The placeholder `.telegram.json` the run step needed was deleted after; W2b's run step writes its own. |
| W2b DACL check | done · Mac pending | 2026-09-16 | 382c534 | **514 ran: 444 pass, 69 skip, 1 xfail**, 14.8s · **not run** | **The plan's fix line did not fix it, and the plan's rule failed open.** Both found by tests that had to do the real thing rather than mock one. `/inheritance:r /grant:r "%USERNAME%":F` removes *inherited* entries only, so against the explicit `Users` grant this error is printed about it changed nothing — the message named a command that did not work. `config._fix` now builds the line from the principals it just read off the file (`/remove:g "BUILTIN\Users"`), and the test extracts the printed line from the exception, runs it through `cmd` so `%USERNAME%` expands, and loads the file: the message is documentation that is executed. The rule itself went from the plan's deny-list of three well-known SIDs to an allow-list — current user, SYSTEM, Administrators, OWNER RIGHTS — because `config.py`'s first paragraph says it fails closed and a deny-list cannot: `Guests`, a second local account and a domain group are all as readable and on none of the three lists. §5.1 rewritten. Three ACE shapes needed separating and each is a test: a deny ACE is not a grant (and denying `Users` denies *us* — the test denies `Guests`), a traverse-only mask (`0x100020` on `%USERPROFILE%`, `0x20` from `icacls /grant X:(X)`) reads no bytes, and an object ACE is refused rather than skipped. **§5.1's prediction that a fresh install hits this once was wrong**: the profile root, `~\Projects` and this checkout all grant only SYSTEM, Administrators and the user, and the run step's placeholder loaded first time — then was refused after `icacls /grant Users:(R)`, then loaded again after the printed line. Un-gated: `test_config.Base`'s `REQUIRED_MODE` stand-down is gone and the thirty other rules are tested on Windows for the first time; `TestPermissions` keeps `skipUnless(POSIX)` because a file mode is the Mac's mechanism, and its reason now says so instead of naming this slice. `test_bot`'s `chmod 0500` root gets its win32 twin via `icacls /deny <user>:(W)`, which does stop `mkdir` where the read-only attribute does not, and `config.create` catches WinError 5 as the OSError it already caught. `pywin32==312` and `requirements-win.txt` are new, so **the suite is run from `.venv` on Windows from here**; `import config` still works without pywin32 (the import is inside the check) but no config loads, which is the fail-closed answer. +15 tests, no skip count change. Mac: not run — see Pending. |
| W3a rotate order, `write_meta` retry | done · Mac pending | 2026-09-16 | ec55e8e | **518 ran: 449 pass, 68 skip, 1 xfail**, 7.0s · **not run** | **The retry is load-bearing, and no retry could have un-gated the test it was supposed to.** Measured against a reader polling at `bot.SESSION_POLL`: 22 of 150 writes refused without it, 0 with it (three runs), ~0.2s of waiting total. Against a reader that never pauses: 5×20ms lost 147/150, 10×50ms lost 95/150 at half a second a write — so five stays, sized for the listener and not for the spin. **`FILE_SHARE_DELETE` on the reader's handle does not let the rename through** — `PermissionError` winerror 5, same as a plain `open()`; the obvious reader-side fix does not exist and the writer-side retry is the whole answer. So `test_a_reader_never_sees_a_partial_record` was un-gated by fixing the *test*, not by widening anything: its reader recorded every exception alike, and the two failures are not the same failure — `PermissionError` is a sharing artifact that happens on both sides and is transient (`read_meta` already answers `None`, `write_meta` now retries), while a torn record is a `ValueError` out of `json` and is the only thing the test is about. **Torn reads across every run of the spike and the suite: zero, on both platforms.** The test now counts the three outcomes separately, asserts no torn reads on either platform and no refusals at all on posix, and patches `META_RETRY_DELAY` to nothing because against its own reader the retry can only add 15s of stalling to a 6s suite. `rotate`'s order was already right, as the plan said, so its new test passed before any code changed and was checked by mutation instead — swapped, it fails, with the cap switching itself off rather than crashing. One repair found on the way: `write_meta` now unlinks its temporary file when the last try fails; the name is fixed by the pid, so every abandoned write used to leave a stale record in the session directory. +4 tests, one skip returned to the suite. Mac: not run — see Pending. |
| W3b `Terminal` and `spawn` on ConPTY | done · Mac pending | 2026-09-16 | | **531 ran: 462 pass, 68 skip, 1 xfail**, 11.5s · **not run** | **Three seconds of every session start were the pseudoconsole waiting for an answer nobody was giving it.** A fresh ConPTY sends `ESC[c` — DA1, *what terminal are you* — and holds the child's output for 3.04s before giving up: measured on every shape of child, every run, and 0.04s once `Terminal` replies `ESC[?1;0c`. The old WinPTY backend has no such wait (0.22s), which is what pinned it on ConPTY rather than on pywinpty. The reply is sent only after the query has been seen, because before it the bytes would be ordinary input and would reach the child; after it, the console consumes them — an interactive `cmd` driven through this never sees them. This is three of W0a's 6.2 seconds to a link, and three of the phone's forty-five. Two more corrections to §4: **pywinpty takes the program and its arguments separately** and prepends the program itself (quoted — verified against an appname with a space), so `spawn` passes `list2cmdline(argv[1:])`; passing all of argv gives the child its own path as `argv[1]`, silently. And **an exec failure does not land on the pty**, because `CreateProcess` fails before there is a child to write it: pywinpty raises, nothing reaches the terminal, so `spawn` raises `OSError` and `Runner.run` catches it around the spawn — without that a missing binary is a traceback over a record still saying `starting`. `Terminal.close()` ends the child by itself (the pseudoconsole closes with its last reference, process gone in under half a second), which is a second reason for §4's close-last order. Run step: `cmd /c echo hello` came back in 0.05s; `cmd /c mode con` answered `'mode' is not recognized`, which is `child_env()` still handing out a POSIX `PATH` — W3g's, now visible instead of predicted. One repair found on the way: **W3a's torn-read test guards itself with `assertTrue(written)`, and on Windows that was a coin toss** — its reader held the file so continuously that 0–8 of 150 replaces landed and 3 of 32 measured runs landed none. It failed exactly that way once here, under the load of these ConPTY tests. A millisecond of pause between reads takes it to 38–42 landing, every run, busy or idle — so the Windows half now exercises the property instead of asserting nothing, and the reader is still open across the whole of every `json.loads`. Torn reads: still zero. +13 tests, no skip count change here (the new file skips whole on the Mac). Mac: not run — see Pending. |
| W3c command-line quoting | todo | | | | |
| W3d `pump` without `select` | todo | | | | |
| W3e Job Object and `terminate` | todo | | | | |
| W3f stop marker | todo | | | | |
| W3g `child_env`, runner acceptance run | todo | | | | time to link: |
| W3h `Trust` carries a partial escape | red | 2026-09-14 | d6701b9 | | red test exists as an `expectedFailure`; fails at chunk sizes 64, 16, 1; passes at 128+ |
| W4a `alive`/`started` via psutil | todo | | | | |
| W4b `Sessions.stop` | todo | | | | |
| W4c runner outlives listener | todo | | | | |
| W4d mutex | todo | | | | |
| W4e end to end from the phone | todo | | | | checklist: link ≤45s · ls · stop · new+trust · cap · failed tail |
| W5a `bot.cmd`, `install.ps1` | todo | | | | |
| W5b scheduled task | todo | | | | seconds from logon to first poll: |
| W5c restart survival | todo | | | | checklist: End → pids alive · Run → ls same pid · stop |
| W5d not-logged-on | todo | | | | |
| W6 CI matrix | todo | | | | |
| retention day 1 on NTFS | — | | | | |
| first sleep/wake with a session open | — | | | | |
| first self-update under a live runner | — | | | | |

### Pending on the Mac

The ritual's step 5 says shared and posix changes are tested on the Mac *before* the commit.
This work is being done from the Windows box, so that step is a debt, listed here and cleared
by running each item on the Mac and moving its row to `done`. Nothing below is considered
verified until then, and W1c's first Windows-green run is not a substitute.

| Slice | What to run on the Mac | Expect |
|---|---|---|
| W1a | `/usr/bin/python3 -m compileall -q .` | clean — 3.9 syntax; no 3.9 on the Windows box to check with |
| W1a | `/usr/bin/python3 -m unittest -q` | green; the count is the pre-W0 count plus the new `TestThePlatformSeam` (6) and fixture tests (7, one xfail) |
| W1a | `python3 session.py --foreground --cwd <project> --name w1a` | a link, and Ctrl-C leaves `meta.json` at `ended` — the signal path now goes through `session_posix.catch_signals` |
| W1b | `/usr/bin/python3 -m unittest -q` | green; `TestTheListenerUsesThePlatformSeam` (10) added; the real-process tests at the end of `test_bot.py` still pass through the `process_started` alias |
| W1b | `sh launchd/bot.sh`, then `ls` from the phone | the listener starts (the `Lock` no-op returns True under bot.sh's lockf), answers `ls`; a second `sh launchd/bot.sh` is still refused by bot.sh, not by python |
| W1c | `/usr/bin/python3 -m compileall -q .` | clean — `tests/support.py` and the decorators are 3.9 syntax, but nobody has compiled them with 3.9 |
| W2a | `/usr/bin/python3 -m compileall -q .` | clean — nothing here reaches for 3.10 syntax, but the ritual's step 3 is the only thing that knows that |
| W2a | `/usr/bin/python3 -m unittest -q` | green, and **nothing newly skipped**: of the four new tests the Mac runs, three are `skipIf(POSIX)` win32 twins it skips by design and one — the `DEFAULT_CLAUDE_BIN = None` case — is portable and must pass there. `TestCheck1OnWindows` skips whole. The two `skipUnless(POSIX)` tests in `TestClaudeBinary` must still *run* and pass: they are the posix half of the pair now, not leftovers. |
| W2a | `python3 -c "import config; print(config.load())"` | a Config whose `claude_bin` is still `~/.local/bin/claude` expanded and **not** resolved through the version symlink — §5.2's split must not have moved the Mac's default |
| W2a | `python3 -c "import config; print(config.resolve('C:foo', '<root>'))"` | `ProjectError` only if a directory of that name is absent; `posixpath.splitdrive` finds no drive, so on the Mac this is an ordinary name and check 4 is what refuses it. A check-1 refusal there means the rule was applied portably by mistake. |
| W1c | `/usr/bin/python3 -m unittest -q` | green, and **nothing newly skipped**: every W1c decorator is `skipUnless(POSIX)` or `needs_symlinks`, and the Mac can create symlinks. The count is W1b's plus `TestEverythingImportsHere` (2). A skip on the Mac means a decorator landed on the wrong test. |
| W2b | `/usr/bin/python3 -m compileall -q .` | clean — 3.9 again; nothing new reaches past it, and nothing on this box can check that |
| W2b | `/usr/bin/python3 -m unittest -q` | green, and **`TestPermissions` must still run there, all four**. It is the Mac's half of the secrecy pair, not a leftover: the whole of W2b is downstream of the fact that its 0600 check cannot be ported, so a skip on the Mac means `_secret` was bound to the wrong function. `TestTheWindowsDacl` (13) and the `_on_windows` creation twin skip whole, by design. The count is W2a's plus 15. |
| W2b | `python3 -c "import config; print(config.load())"` | loads, exactly as before — `_secret` is `_secret_by_mode` off win32 and the mode check moved into it *verbatim*, message included. A `chmod 644 .telegram.json` there must still say `mode is 0644, must be 0600` and name `chmod 600`, not `icacls`. |
| W2b | `/usr/bin/python3 -c "import config"` with pywin32 absent (it is) | no error. The import is inside `_win32security()`, reached only on win32; if the Mac ever raises ImportError from `config`, the platform split leaked out of the function. |
| W3a | `/usr/bin/python3 -m compileall -q .` | clean — 3.9; the retry loop is plain `for`/`try`, but nothing here has compiled it with 3.9 |
| W3a | `/usr/bin/python3 -m unittest -q` | green, and **one fewer skip than W2b**: `test_a_reader_never_sees_a_partial_record` is portable now and must *run* there. The count is W2b's plus 4. |
| W3a | the same test, watched | on the Mac it must reach the end with `denied == []` and `lost == 0` — the two assertions that only run under `POSIX`. A refusal there would mean rename-over-an-open-file is not what this has always assumed it is. |
| W3a | `python3 session.py --foreground --cwd <project> --name w3a`, then Ctrl-C | a link, and `meta.json` at `ended`. `write_meta` is on every state change, so this is the path the retry sits in; the Mac must never take the retry branch at all. |
| W3b | `/usr/bin/python3 -m compileall -q .` | clean — 3.9 again. `tests/test_session_win.py` is compiled there even though every test in it skips, and it is the first file in the port written without a 3.9 interpreter anywhere near it. |
| W3b | `/usr/bin/python3 -m unittest -q` | green, and **twelve newly skipped, all in one file**: `tests/test_session_win.py` is `skipUnless(WIN)` whole — it drives a real ConPTY. The thirteenth new test is the portable one, `TestTheRunner::test_a_terminal_that_cannot_be_started_is_failed_not_a_traceback`, and it must *run* and pass there: it patches `session.spawn` to raise `OSError` and asserts the runner records `failed` rather than dying. A skip on that one means the `try` around the spawn landed behind a platform check it has no business being behind. |
| W3b | `test_a_reader_never_sees_a_partial_record`, watched | unchanged from W3a: `denied == []`, `lost == 0`, and now `written == 150`. The `time.sleep(0.001)` added to its reader is for the Windows half, where it takes the number of replaces that land from 0–8 to 38–42; on the Mac every replace lands either way, so if this run shows a refusal or a loss the pause has changed something it was not supposed to touch. |
| W3b | `python3 session.py --foreground --cwd <project> --name w3b`, then Ctrl-C | a link, and `meta.json` at `ended` — the same run W3a asks for, because `run()` now has a `try` around the spawn that the Mac takes the happy path of. What must *not* appear is `could not start the terminal` in the log. |

### Decisions changed by evidence

Appended, dated, when a run step contradicts the plan above and a section was amended.

- **2026-09-14, W0d → §5.2.** The plan allowed for preferring `%USERPROFILE%\.local\bin\
  claude.exe` if the self-updater installed there. It is there, and it is the *stale* one
  (v2.1.231 against winget's v2.1.268); auto-updates are off. Default is `shutil.which`, full
  stop.
- **2026-09-14, W0a → new slice W3h.** `Trust` was assumed portable and untouched. It is
  portable, but it drops the dialog at chunk sizes ≤64 bytes on any platform, because it does
  not carry a partial escape across chunks. A red test is in place as an `expectedFailure`.
- **2026-09-14, W0a → §4, W3b.** `pywinpty` reads and writes `str`, not bytes. `Terminal.read`
  is specified to return bytes so the shared loop sees one type.
- **2026-09-14, W0c → §3, §6, W4c.** The plan called `CREATE_BREAKAWAY_FROM_JOB` "the single
  riskiest claim" and planned a retry without it. Measured: Task Scheduler's job has
  `LimitFlags = 0`, breakaway is refused, and children survive the task being stopped without
  it. The flag is removed, the retry is removed, and the runner's survival is now a property
  of the scheduler's job having no kill-on-close — which W5c re-checks with a real session.
- **2026-09-14, W0a → §9 ritual.** Until W1c, tests that import `session` cannot run natively
  on Windows. `scratch\run_win_tests.py` stubs `fcntl`/`termios` so the portable classes can;
  it is throwaway and W1c retires it. The full suite's Windows count stays at the baseline
  until then, and that is recorded rather than hidden.
- **2026-09-15, W1c → §8.** The plan gated tests by platform only. Ten of the §3 boundary
  tests need `os.symlink`, which on Windows needs Developer Mode or
  `SeCreateSymbolicLinkPrivilege` and fails with error 1314 without them — a property of
  the account, not the platform. `tests/support.py::needs_symlinks` probes once and skips
  for the account, so those tests come back by themselves on a box that has the privilege.
- **2026-09-15, W1c → §5.3, §9.9.** Windows `os.path.realpath` returns the on-disk case
  (`BEACON` → `beacon`), where the Mac's keeps the spelling it was given. Slice 8's
  `samefile` comparison is right on both; the test that pins the Mac's behaviour is
  posix-only, and §5.3's claim that `realpath` behaves on NTFS is now measured.
- **2026-09-15, W1c → §8.** Two tests failed on Windows for being Mac-*shaped*, not
  Mac-only: an expected string with `/` in it, and `open()` of a UTF-8 source file with
  the platform default codec (cp1252 here). Both are fixed portably rather than gated;
  the rule for the rest of the port is that a test only gets a platform decorator when
  the *mechanism* it exercises is the platform's.
- **2026-09-15, W2a → §5.3.** The plan called the drive rule a third shape of check 1's
  trap. Measured, it is two bugs with opposite symptoms, and which one you get depends on
  where `projects_root` lives: same drive, `ntpath.join` *drops* the `C:` and `C:foo` becomes
  `<root>\foo` — a name silently turned into a different, valid one that check 3 accepts;
  different drive, join keeps `C:foo` and realpath resolves it against C:'s per-drive current
  directory, outside the root. The second is a §10.4 escape that nothing on this box would
  have shown, because this box's root is on C:. §5.3 carries the table.
- **2026-09-15, W2a → §8.** A red test that asserts only the substring `name` cannot tell
  check 1 from check 4 — "that is not a project name" and "no project by that name exists
  here" both contain it, and `resolve("C:foo")` passed the first draft of this slice's test
  before the rule existed. `create()` is what exposed it: it inverts check 4, so a name check 1
  lets through is not refused late, it is not refused at all. Every boundary test added from
  here asserts the wording of the check it means, and every check-1 case gets a `create` twin.
- **2026-09-15, W2a → §5.2.** `shutil.which` returns `None`, and on Windows the default *is*
  the lookup — there is no second place to look, unlike the Mac's constant. Not having a
  branch for it meant the message for "Claude Code is not installed" was "`claude_bin` must be
  a string", about a key the file does not set.
- **2026-09-15, W1c → W2b.** `stat.S_IMODE` reports `0666` for every file on Windows, so
  the 0600 check refused every `test_config` case and hid the other thirty rules. Until
  the DACL check exists, `test_config.Base` stands the check down on win32 and
  `TestPermissions` is the Mac's; W2b removes both. *Done: the stand-down is gone and those
  thirty rules now run here. `TestPermissions` keeps its decorator — a file mode is the Mac's
  mechanism, which is the §8 rule, not a gate waiting on a slice.*
- **2026-09-16, W2b → §5.1, §3.** The plan refused an ACE for `Everyone`, `Users` or
  `Authenticated Users`. That is a deny-list, and it fails open: a grant to `Guests`, to a
  second local account, or to a domain group is exactly as readable and on none of the three
  lists. `config.py`'s first paragraph promises the opposite, and a bot token is not the place
  to spend the promise. The rule is now an allow-list — current user, SYSTEM, Administrators,
  OWNER RIGHTS — and every other principal is refused by name. The three well-known SIDs are
  still what the tests use, because they are still what actually turns up.
- **2026-09-16, W2b → §5.1.** The fix line in the error message was wrong, and would have
  shipped wrong, because nothing had run it. `/inheritance:r` drops inherited entries only;
  the file this error is printed about has an *explicit* `Users` grant, which survived it
  untouched. The line is now built per-file from the principals just read off it, with
  `/remove:g`, and the test pulls the line out of the exception text and executes it through
  `cmd`. Any message that names a command should be tested by running that command.
- **2026-09-16, W2b → §5.1.** "A fresh install *will* hit this error once, on purpose" is not
  true on this box. `%USERPROFILE%`, `~\Projects` and this checkout grant SYSTEM,
  Administrators and the user and nothing else, and a config written into the checkout loaded
  on the first try. Windows' default is already owner-only here; the Mac's first-run
  `chmod 600` friction has no counterpart. `TestTheWindowsDacl.test_a_fresh_file_is_accepted`
  is the tripwire if that is ever untrue somewhere else.
- **2026-09-16, W3a → §4.** The plan expected the `write_meta` retry to be belt-and-braces
  and the gated test to be the hard case. It is the other way round. At the listener's actual
  poll rate the retry saves 22 writes in 150 — without it the URL is lost about one time in
  seven, which is not a rare path, it is a broken one. Against the gated test's spinning
  reader no policy wins at any size worth paying for. The retry stays at five tries because
  the number that matters is the first one, not the last.
- **2026-09-16, W3a → §4.** `FILE_SHARE_DELETE` on the reading handle does *not* let
  `os.replace` through. Measured with `CreateFileW` and
  `FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE` held on the target: `PermissionError`,
  winerror 5, identical to a plain `open()`. Recorded because it is the first thing anyone
  reaching for a reader-side fix will try, and it does not work — `MoveFileExW` with
  `MOVEFILE_REPLACE_EXISTING` wants more of the target than delete-sharing.
- **2026-09-16, W3a → §8.** `test_a_reader_never_sees_a_partial_record` was gated because its
  reader recorded every exception as the same failure. It is two failures: a `PermissionError`
  is a sharing artifact, it happens on *both* sides on Windows, it is transient, and both
  sides already handle it; a `ValueError` out of `json` is a torn record and is the property.
  Separating them un-gated the test on its own, which is the §8 rule from W1c arriving again —
  a test only gets a platform decorator when the mechanism it exercises is the platform's, and
  atomicity is not. Torn reads measured across every run of the spike and the suite: zero.
- **2026-09-16, W3a → §4.** `write_meta` leaked its temporary file on failure. The name is
  `meta.json.<pid>.tmp`, fixed for the life of the runner, so an abandoned write left a stale
  record in the session directory that the next write would land on top of. It is unlinked on
  the last try now. Found by asserting `os.listdir` in the give-up test rather than by
  anything going wrong.
- **2026-09-16, W3b → §4, §1's status.** A pseudoconsole asks the terminal what it is (`ESC[c`,
  DA1) before it lets the child's output through, and waits **3.04 seconds** for an answer that
  nothing here was giving. Measured on every shape of child and every run; 0.04s once
  `Terminal` replies `ESC[?1;0c`; 0.22s on the old WinPTY backend, which does not ask. It is
  three of the 6.2 seconds W0a measured to a link and three of the forty-five the phone waits
  in W4e, and it is not a slow machine — it is a handshake with one side missing. Answered only
  after the query has been seen: sent before it, the reply is input like any other and reaches
  the child; sent after, the console consumes it.
- **2026-09-16, W3b → §4.** The plan said an exec failure "lands on the pty and therefore in
  `pty.log`". That is a property of the Mac's fork, not of spawning: on Windows
  `CreateProcess` fails synchronously, there is no child to write anything, and pywinpty
  raises with the terminal still empty. `spawn` raises `OSError` and `Runner.run` catches it
  around the spawn, so the session is `failed` with the reason in the record. The alternative
  found by writing it down — hand back a terminal holding a fake error line — needs a pid for
  a child that does not exist, and `terminate(0)` is not a thing to go near.
- **2026-09-16, W3b → §4.** pywinpty takes the program and its *arguments* separately and
  prepends the program itself, quoted (verified against an appname containing a space), so
  `spawn` passes `list2cmdline(argv[1:])`. Passing the whole argv gives the child its own path
  as `argv[1]` — measured, and it would have read as Claude Code refusing its own flags.
- **2026-09-16, W3b → §8.** W3a's `test_a_reader_never_sees_a_partial_record` guarded itself
  with `assertTrue(written)`, and on Windows that guard was luck: its reader holds the file so
  continuously that 0–8 of 150 replaces land, and **3 of 32 measured runs landed none** — a
  one-in-ten flake that passed W3a only because nothing was loading the box. It failed here
  under W3b's ConPTY tests. One millisecond of pause between reads takes it to 38–42 landing,
  every run, busy or idle. The rule this is a case of: a test whose *guard* is timing-dependent
  fails for the one reason it cannot teach anybody anything about, and the fix is to make the
  interesting thing happen reliably, not to relax what is asserted about it.
- **2026-09-16, W2b → §9 ritual, §3.** `requirements-win.txt` exists, with `pywin32==312` and
  the `pywinpty==3.0.5` that has been in `.venv` since W0a. The Windows suite is run from the
  venv from here (`.venv\Scripts\python -m unittest -q`); under the system interpreter
  everything still imports — `win32security` is imported inside the check, not at module
  level — but no config file loads, 41 tests fail, and that refusal is the correct answer to
  "I cannot read the permissions" rather than a bug to route around.
