# centrion on Windows — port plan

The bot as it stands is a macOS program. `bot.py` and `session.py` both fail on this box at
import time with `ModuleNotFoundError: No module named 'fcntl'`, and that is the shallowest of
the problems: every process and terminal mechanism SPEC.md leans on — `openpty`, `fork`,
`setsid`, `TIOCSCTTY`, `killpg`, `SIGTERM`, `select` on an fd, `lockf` on fd 9, launchd — has no
Windows equivalent. What *is* portable is the shape: three processes, files as the only
protocol, a runner that outlives its launcher, a scraper that finds one URL in a terminal
stream. This document is the plan for keeping that shape and replacing the mechanisms under it.

Status: **W4d done (2026-09-20); W4e next — end to end from the phone, and it is unblocked:
`bot.py --serve` is a runnable command on this box for the first time, and a second copy is
refused in 0.11s with status 0 (§9).** The suite
runs natively on Windows since W1c: 624
tests, 549 pass, 75 skipped as the Mac's (each skip names its reason or the slice that
un-gates it), and since W3h **no expected failure at all** — the one there had been was W3h's
own. One unidentified error seen once in 35 runs remains, which W3c's row records rather than
explains and which has not recurred since. Since W2b it is run from the venv —
`.venv\Scripts\python -m unittest -q` — because `config.py`'s secrecy check needs pywin32;
`requirements-win.txt` exists as of that slice. The go/no-go question is answered *go*: under
a 200x50 ConPTY, `claude.exe --remote-control` printed its link 6.2 seconds after spawn, as
one contiguous run, and today's `Scrape` finds it unmodified at every chunk size
(`tests/fixtures/rc_startup_win.log`) — and three of those 6.2 seconds were the pseudoconsole
waiting to be told what terminal it had, which W3b now answers (§4). W3g measured what is left
by running the whole runner and found the link reaching the record 6.7 seconds after spawn,
4.5 of them `Scrape` holding a complete URL back for one more byte; **W3i took that apart and
it is a rare frame rather than a standing cost** — the link is in the record in 1.9–2.2s today
with or without the fix, because ConPTY keeps writing past the URL, and the fix caps the case
where it does not at one `TICK` instead of one redraw (§9). Two Ctrl-C bytes on the ConPTY
input ended it in 1.7 seconds with exit status 0. A child of a scheduled task survives the
task being stopped, with no breakaway flag — which is refused there anyway. W3h adds one fact
about the same stream and one about what is in it: ConPTY's reads are small (median 21 bytes)
but never cut an escape sequence in half, and §9.3's trust dialog no longer appears for a new
directory under `projects_root` at all — only outside it — so `Trust` is insurance here rather
than a mechanism anything currently exercises (§4). W4a opens the listener's half — §4's
pid-reuse guard has both platforms' answers now — and found that Windows never *refuses* to
date a process (0 of 208, including the 109 whose owner psutil cannot read) but answers `0.0`
for pids 0 and 4, so a corrupt record naming pid 4 reads as a live runner here exactly as one
naming pid 1 does on the Mac (§6). **W4b closes the listener's other half**: a `stop` is *ask,
wait, force* on both platforms now, the ask being W3f's marker, and the A/B it ran against a
real session says the hard kill it replaces was **not** leaving processes behind — the job and
the pseudoconsole take all three pids in 0.00s — it was skipping the session's own ending,
which is claude's clean exit and a `meta.json` that does not say `live` for something already
gone (§6). The price is the Mac's: `SIGTERM` is the fallback there now rather than the first
act. **W4c then found that SPEC.md §2's one non-negotiable — the runner outlives the listener
— was, on this platform, asserted by nothing**: `test_procs_win.py` carried its own copy of
the detach flags, so two mutations that broke the property outright were caught by 0 of 611
tests. The mechanism was right all along and the hand-run says so — a real listener broken
with Ctrl-C leaves the runner, the ConPTY child and the process under it all in `tasklist`,
and a second listener reads the session back as `live` with the same pids — but the tests are
what changed, and the rule the ritual gained is that *"the test already exists" and "the test
would fail" are different claims, separated only by a mutation* (§9). **W4d is the last of the
seam's `NotImplementedError`s and the first `--serve` this platform has ever run**: the lock is
a named mutex, the second copy is refused in 0.11s with status 0 before it touches Telegram,
and the first one — killed by a control event with no unwinding at all — leaves nothing behind,
the next copy having the lock inside 0.1s (§6). Everything below that is not in §11's
table is still plan, and the claims about Windows behaviour in it are what the API documents
until a slice turns them into facts.

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
child_env(env) -> env                              # the platform's half of §6's environment (W3g)
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
| Process tree | `setsid` + `killpg` | **Job Object with `KILL_ON_JOB_CLOSE`** (`pywin32`'s `win32job`) | Windows has no process groups worth the name. A job is the only thing that reliably ends a dev server the session started. *W3e, measured both ways:* the rest of this row was right about the outcome and wrong about the mechanism. Kill the runner and claude does die with it — but that is the **pseudoconsole** closing, within half a second, and it reaches only the one process claude was. What the flag adds is the tree: without it a detached grandchild outlives the runner, the session, and the reconciliation that comes after. |
| Graceful stop | `SIGTERM` to claude | **write `\x03` to the ConPTY, wait `GRACE`, then `TerminateJobObject`** | *W3e corrects this:* ConPTY does **not** turn a 0x03 on its input into a `CTRL_C_EVENT`. It is delivered to whatever is *reading* the console, like any other key — so it reaches claude, which reads it, and nothing else. `cmd`, `ping` and a python process in `time.sleep` each ignore two of them (measured, all three). Claude Code asks for a second Ctrl-C to confirm exit, so send it twice with a short gap. The job kill is therefore not the fallback for a stubborn claude but the **only** thing that reaches the rest of the session, in the ordinary case as much as the bad one. |
| Listener → runner stop | `SIGTERM` to runner | **a `stop` marker file in the session directory**, polled every `TICK` | Windows cannot deliver a catchable signal to another process. §2 already has the two processes talking only through files, so this is the design extended rather than a new channel. Fallback after `STOP_GRACE`: `TerminateProcess` on the runner, which takes claude *and* its tree — the first through the pseudoconsole and the second only because of `KILL_ON_JOB_CLOSE`. See the Process tree row for which half does what. |
| Liveness | `kill(pid, 0)` + `/bin/ps lstart` | **`psutil.pid_exists` + `psutil.Process(pid).create_time()`** | Both halves of §4's pid-reuse guard in two calls, no `ps` to parse. `psutil` is a compiled wheel, available for 3.12. |
| Detach | `os.setsid()` in the runner | **`Popen(creationflags=DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP)`** in the listener — and **not** `CREATE_BREAKAWAY_FROM_JOB` | *W0c, verified 2026-09-14:* Task Scheduler does put the task in a job, with `LimitFlags = 0` — so `BREAKAWAY` is refused with "Access is denied" and the runner would never start. It is also unnecessary: `Stop-ScheduledTask` terminated the parent and left both a flagless child and a detached child running. The runner survives the listener on Windows by default; the two remaining flags give it no shared console and no inherited Ctrl-C. |
| Lock | `lockf` on fd 9 across an `exec` | **a named mutex, `Local\centrion-<sha1 of bot.LOCK's path>`**, taken in `bot.py --serve` before the first `getUpdates` | The kernel releases a mutex when its owner dies, so like `lockf` there is no stale lock to clear. No shell, no inherited fd, and so no equivalent of §8's "the runner must close fd 9" trap — child processes do not inherit a mutex handle unless asked to. *W4d, 2026-09-20: this row said "sha1 of config path" and the seam had already decided otherwise — `Lock(path)` takes `bot.LOCK` (`var\.bot.lock`), the file the Mac's `lockf` is on, which is what §6 and `bot.py`'s comment both say. Same one-per-checkout property, one path spelled once. What the kernel reclaims is the object, when the last handle to it closes; measured at ≤0.1s after a listener killed outright.* |
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

*W3c, 2026-09-17 — the first bullet above is confirmed, and it was half the story.* The round
trip is exact: a prompt carrying `"`, `^`, `%`, `&`, `|`, `<`, `>` and a trailing backslash
reaches the child as one argument, byte for byte, and so do an empty argument, a bare quote and
a non-ASCII one. No code changed. The half the bullet missed is that the same flattening
happens one process earlier, in `Sessions.start`'s `Popen` — see §6.

**`pump()`** — no `select`. `pywinpty` reads are `pty.read(length, blocking=False)`, which
returns an empty string when nothing is ready, so the loop becomes: read; if empty, sleep
`TICK`; check `stop_requested()`; check `pty.isalive()`. The `Trust` and prompt timers run on
the same rhythm as today. One consequence to test: `pywinpty` decodes to `str` internally, so
`Scrape.feed` gets text rather than bytes — it already accepts both — and `Transcript.write`
re-encodes to UTF-8. Prefer the raw-bytes API if the installed version exposes one, so the
transcript is what the terminal produced and not a re-encoding of it.

*W3d, 2026-09-18 — the sleep and the poll are not in `pump` at all; they are in `read`.* The
plan had `pump` doing the waiting and branching on the platform's answer. What it does instead
is call `terminal.read(TICK)` and let each `Terminal` wait its own way — `select` on the Mac,
a poll here — so the loop names four calls (`read`, `write`, `alive`, `close`) and nothing
else, and there is one copy of it rather than two. `session_posix.Terminal` is the other half
of W3b's class and `spawn` returns one on both platforms; `Terminal` is on the seam's surface
list beside the functions.

**`read` and `alive` answer two questions that used to be one, and they do not mean the same
thing on the two platforms.** On a pty they arrive together: EIO off the master is the end of
the output *and* the end of the session, and the old loop broke on either. ConPTY separates
them — the child is gone while the last of its output is still buffered — so the shared loop
needs them separated on the Mac too. The consequence to carry into W3e and W4a is that
**`Terminal.alive()` is about the terminal on the Mac and about the process on Windows**:
`session_posix` sets its flag when the pty hangs up, `session_win` asks `pty.isalive()`. Both
answer *is more output coming*, which is the only question `pump` asks; neither is a
substitute for `_reaped()` or for `procs.alive(pid)`, which are about a pid.

**One Mac-side behaviour change, deliberate and small.** An interrupted `select` used to be a
`continue`, which skipped that tick's `Trust` and prompt timers; it is now an empty read, which
runs them. That is the same thing an idle tick has always done — `chunk` was `b""` there too —
so the timers see nothing new, and a signal no longer has the side effect of skipping a check
it has no business skipping. The signal still ends the loop by the route it always did, which
is `stopping` being read at the top.

**The drain after the terminal finishes goes through the scraper, not just the transcript.**
An empty read against a finished terminal costs one more read before `pump` gives up (§4.6:
that last part is the error text a `failed` session's tail is made of). The first way to write
that is to append it to `pty.log` and leave — which loses a *link* arriving in the same chunk,
and on Windows that chunk is reachable, because output outlives the child here. So both places
call one `Runner.absorb`, and `test_a_link_in_the_tail_still_goes_live` is the case.

**A session's end costs two `TICK`s.** Measured on a `cmd /c echo`: 0.432s of `pump`, of which
0.401s is the two empty reads that end it — one ordinary poll, then the drain — against 0.031s
of actually reading the child. It is paid after the child is gone and after `live` has been
recorded, so nothing the phone waits for moves except the error tail on a `failed` session.
Left at `TICK` deliberately: shortening the drain trades 0.2s at the end of a session against
the chance of losing the text §4.6 exists to deliver.

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

*W3h, 2026-09-19 — and the sentence above is right about the defect and wrong about who was
exposed to it.* The reads ConPTY really hands the runner are **small**: over a live session,
26 chunks, 3 bytes at the smallest, median 21, and three quarters of them 64 bytes or under —
well inside the range where the replayed fixture loses the dialog. But **none of them ended
inside an escape sequence**: 0 of 26, because ConPTY flushes whole renders, and a boundary
between two complete sequences is exactly the boundary that costs nothing. So the pre-W3h
`Trust` answered the *live* dialog too, in 2.84s against the fixed one's 2.71s — the bug is
reachable by anything that cuts the stream on a buffer of its own (a fixed-size read, a pipe,
a replay, a fixture at 64), and was not being reached by the one reader this program has. It
is fixed anyway, and the reason to keep it fixed is that nothing in `Trust` or `pump` promises
that boundary and a version of `pywinpty` or ConPTY that buffers differently would not be a
visible change.

*W3h, 2026-09-19 — and "the first thing every session meets" is no longer true where the bot
starts its sessions.* Four shapes of brand-new directory under the configured `projects_root`
(`~\Projects\tomtomtomdev`) — empty, one file, `git init` plus a file, and a
`.claude\settings.json` — all came straight up to a link with no dialog at all, on the same
binary W0a met it with, and Claude Code wrote each one a `projects` entry saying
`hasTrustDialogAccepted: false` without ever asking. The identical directory under `%TEMP%`
raises the panel every time, immediately, and `Trust` answers it. So the variable is *where*
the directory is and not what is in it, and the place that suppresses it is the one place
`new` ever creates a directory. Consequences, none of them a code change: `Trust` is
insurance on this box rather than a live mechanism, so it cannot be confirmed by watching a
`new` from the phone (W4e's checklist item is amended to say so); the emptiness rule in
`Runner.run` stays, because it is the half of the argument that makes answering honest and
costs nothing when nothing asks; and the dialog is not gone, so neither is the code. What is
*not* known is the rule Claude Code is applying — a sibling of trusted projects, a known
parent, something in `githubRepoPaths` — and W4e should re-check it rather than assume it
holds, because a session that meets an unanswered panel is a session the phone waits 45s for.

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

*W3e: done.* The order above is what shipped and the three steps are unchanged, but two of the
sentences around them were wrong and the slice is mostly those two.

**A Ctrl-C here is a keystroke and not a signal** (§3's table above, corrected). Nothing turns it
into a `CTRL_C_EVENT`; the console hands it to whatever is reading, so it reaches claude and
reaches nothing else — `cmd`, `ping` and a sleeping python all take two of them and carry on.
W0b's 1.71s is still real, because claude is the one program the runner ever starts, but the
polite step is a message to *one process* and never to a tree. Which is why what `terminate` waits
on is **whether the job is empty**, not whether `pid` has gone: the ordinary session — claude
takes the Ctrl-C, exits 0, and the dev server it started is still running — is exactly the case a
pid-shaped wait answers True to while the phone is told a session is over that is not. That is
§9.10's failure in Windows dress and it is what the job is for.

**`terminate` therefore takes the terminal, and the seam is one argument wider than the Mac
needed.** Neither half of a Windows stop is reachable from a pid: the Ctrl-C is a write to the
terminal, and the job has been held by that same object since before `spawn` returned.
`session_posix.terminate` accepts the argument and ignores it, which is the whole of the Mac's
answer to it. `bot.py` is the caller with a pid and nothing else, and it gets a single hard kill
and a line in the log saying that is what it got — §6's `stop` writes the marker first precisely
so that the runner's own `terminate`, which has the terminal, is what ends the tree.

**And `KILL_ON_JOB_CLOSE` covers the ending nothing else can: the runner dying without reaching
any of this.** Measured in both directions. The pseudoconsole closing with the runner ends claude
in under half a second on its own — so the flag is not what saves the session from an accidentally
dropped handle, and the reason this was first written without the flag does not hold. What the
flag adds is everything claude started, which otherwise outlives the runner with nothing left
anywhere that can reach it.

**`_catch_signals()`** — a stop marker instead. `Runner.pump` checks
`os.path.exists(os.path.join(self.dir, "stop"))` each tick; finding it sets `self.stopping`,
which is the flag today's signal handler sets. `SIGINT` from a hand-run `--foreground` console
still works, because `signal.signal(SIGINT)` exists on Windows — keep that one, drop `SIGTERM`
and `SIGHUP`. Also handle `CTRL_BREAK_EVENT` the same way for tidiness; it costs one line.

*W3f: done, as written, with two things the plan said in passing and one it had backwards.*

**The marker is checked on every pass of the loop and not only on an idle one**, and those
are not the same thing — "each tick" above reads like the second. A session running a build
answers every read with a chunk, so a check that only fires when a read came back empty stops
every session except the busy ones, and the busy one is what somebody reaches for `stop`
about. The cost of doing it properly was the open question and it is answered: the `stat` is
**5.7µs**, about a fifth of a pass of `pump` over a fake terminal that does no other work and
a far smaller share of a real one, which also reads a pseudoconsole and writes to disk. An
idle session pays it five times a second. Nothing needs throttling, and
`test_a_session_that_never_goes_quiet_is_still_stoppable` is the case that says so.

**`SIGTERM` is dropped for a stronger reason than "Windows does not have it".** It does have
it — `signal.SIGTERM` exists and `signal.signal` accepts it — which is what makes it a trap:
the handler registers and then is never run, because there is no `kill(2)` here and
`os.kill(pid, SIGTERM)` is `TerminateProcess`, which ends the target without running anything
in it. A handler for it would be a promise nothing can call in, and *that* is the reason
`stop` is a file rather than a signal in the first place. `SIGHUP` is the plain case: it does
not exist on this platform at all. What is left is `SIGINT` and `SIGBREAK`, and they are for
the runner's other life — `--foreground` in a console somebody is watching, which is W3g's
acceptance run and every hand-debug after it. In service, `DETACHED_PROCESS` (§6) means the
runner has no console to be interrupted from, so it installs two handlers that are never
called and the marker is the whole of how it is stopped.

**POSIX honours the marker too**, which the slice text had as a convenience for the tests and
is really the point of it: the runner then has **one** place where a stop is heard rather than
one per platform, and `TestTheStopMarker` runs on both boxes. The Mac keeps `SIGTERM` as well,
because it is what `bot.py` sends there and because a runner that is anywhere other than
`pump` hears only that.

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

*W3g built it, and split it where the seam already was.* The hazard filter is a decision about
Claude Code and is the same on any platform, so it stays in `session.py`; what a child needs in
order to *be* a child is the platform's, and `procs.child_env(env)` is a twelfth name on §2's
surface. Three amendments to the paragraph above. **`TMP` and `PATHEXT` join the list**: nothing
sets one temp variable without the other, and `PATHEXT` is what makes `claude` mean
`claude.exe` for every shell the session opens, `CreateProcess` not being the only thing that
resolves a program here. **The list is a backfill, not an allowlist** — it fills a name in from
`os.environ` only when the base handed in has none, which is the exact twin of the Mac's
`HOME or expanduser("~")`, and the comparison is case-insensitive because `os.environ`
upper-cases its keys on this platform and `environment_block` would otherwise write
`SystemRoot=` and `SYSTEMROOT=` into one block with no rule about which the child reads.
**`TERM` is not invented, but an inherited one is not taken away** either: this is a filter and
`TERM` is not a hazard, so one arriving from a Git Bash travels on like anything else.

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

*W4a, 2026-09-19 — `AccessDenied` is the case that does not happen, and the one that does
answers `0.0`.* Of the 208 processes on this box, **none** refused `create_time` — including
the 109 whose `username()` psutil could not read — because it needs only
`PROCESS_QUERY_LIMITED_INFORMATION`, which every account has for everything. The handler stays
as insurance (a release that tightens the check would otherwise turn a log line into a
traceback), but §4's `began is None` branch is, on this platform, reached only by a pid that
died between the two calls. What the two genuinely unopenable pids do instead is return **the
epoch**: `started(0)` and `started(4)` are `0.0`, not the boot time and not an error, so they
arrive at `Sessions.alive` as a real timestamp older than any record and pid 4 reads as a live
runner for as long as a corrupt record names it. Not patched, because the Mac reaches the same
place for pid 1 honestly — launchd's start time really is boot — and its own test asserts that
pid 1 is alive on purpose. The pid in a record is one the listener wrote from its own `Popen`;
this is a property of corrupt records, and it is the same property on both platforms.

**`Sessions.stop()`** — write the `stop` marker, then wait up to `STOP_GRACE` for `alive()` to go
false. Only then `psutil.Process(pid).terminate()` on the runner — which is `TerminateProcess` and
catches nothing, so the runner never reaches its own `terminate`. What saves that case is the two
handles dying with it: the pseudoconsole takes claude and the job's `KILL_ON_JOB_CLOSE` takes the
tree (W3e, measured). Without the flag this line would have left a dev server running under a
session the phone was told had stopped. §9.11's nesting rule carries over unchanged: the
listener's grace must exceed the runner's `GRACE` plus the two Ctrl-C settles, or the listener
kills a runner that was halfway through ending claude cleanly.

*W4b, 2026-09-19 — done as written, and the fallback turns out not to be the leak this
paragraph implies.* Both halves of the sentence above were measured against a real session on
the same box, one after the other: the marker path ends a live session in **5.70s** with
`meta.json` at `ended`, and the fallback — `stop` with the wait skipped, which is exactly what
this function did before the slice — returns in **0.00s** and takes the runner, the ConPTY's
child and the `ping` under it **just as completely**. So the flag and the pseudoconsole really
do cover the hard kill, as the sentence above says, and the cost of taking it is not a stray
process. **What the hard kill loses is the ending, not the tree**: nothing sends claude the two
Ctrl-Cs it exits cleanly on (W0b: status 0 in 1.71s), and `meta.json` is left saying `live` by
the one process that knew otherwise — the listener's `halt` writes `ended` a moment later
through `finish`, so the phone is told the truth either way, and `stop` on its own is not. The
5.70s is the runner spending its whole `GRACE` on a child that ignores a Ctrl-C, which is the
`ping` standing in for a build; a real session is W0b's 1.71s.

**The ask is portable and the Mac's timing changed for it.** `stop` is now *ask, wait, force* on
both platforms — the marker, `STOP_GRACE` of polling, then `session.terminate` — which moves the
Mac's `SIGTERM` from the first act to the second. It is still sent (it is the first half of
`session_posix.terminate`), and a runner in `pump` never notices the difference, because it hears
the marker within a tick either way. What pays is a Mac runner that is *anywhere else* — inside
`spawn`, inside the trust dialog — which used to be reached at once by a signal and now waits out
`STOP_GRACE` first. Taken deliberately: the alternative is a `stop` that means two different
things on the two platforms, and the process that knows what the session was doing is the one
that should end it. Unverified on the Mac, like everything since W1a.

**`Sessions.start()`** — `Popen(argv, stdin=DEVNULL, cwd=HERE, close_fds=True,
creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`. No `CREATE_BREAKAWAY_FROM_JOB`
and no retry logic: W0c showed the scheduler's job refuses breakaway outright, and showed the
runner does not need it — a child of a scheduled task survives the task being stopped. The
runner therefore stays *inside* the scheduler's job for its life. That is harmless today
(`LimitFlags = 0`, no kill-on-close) and is the assumption W5c re-verifies with a real
session: if a Windows update ever gives that job `KILL_ON_JOB_CLOSE`, every session dies with
the listener, and the fix at that point is to spawn the runner through a second scheduled task
or WMI rather than `Popen`. `reap()` stays: `Popen.poll()` works on Windows.

*W3c, 2026-09-17 — this `Popen` is a quoting hop, and the plan had not noticed.* §4 says
ConPTY takes a command line and that the quoting there has to be the library's. It is just as
true one process earlier and this section never said so: `Popen` has no `execve` to hand a list
to on Windows, so it joins `argv` with `subprocess.list2cmdline` itself and the runner's own C
runtime splits it apart again before `argparse` sees anything. The prompt therefore makes the
round trip **twice** on Windows — once here and once into ConPTY — where on the Mac it makes it
zero times. `bot.py`'s comment claimed "there are no quoting rules to get wrong when there is
nothing to quote for", which is a true sentence about `execve` and a false one here; it now
says which platform it is describing. The defence itself is unchanged and still sound — no
shell is in the chain either way, so nothing expands a `%VAR%` or acts on a `>` — but "no
quoting to get wrong" and "quoting that has to be exactly right" are different guarantees, and
only the second one needs a test. `TestSpawningForRealOnWindows` is that test.

**The lock** — moves from `bot.sh` into `serve()`, before `Telegram()` is constructed, so a
second copy is refused before its first `getUpdates` (§7's 409 is mutual). On the Mac the lock
stays in `bot.sh`, and `serve()` calls `procs.Lock(...).take()`, which is a no-op on posix
returning `True`. A refused copy exits 0 with the same message `bot.sh` prints today, and the
wrapper loop in §7 sleeps ten seconds and tries again — the same "say so every ten seconds
until the hand-run copy exits" behaviour `bot.sh`'s header describes.

*W4d, 2026-09-20 — done as written, and the two things it did not say are the interesting
ones.* `Lock.take()` is `CreateMutexW` on `Local\centrion-<sha1 of the lock path>`, refusing
when it comes back `ERROR_ALREADY_EXISTS`. **It is ctypes and not pywin32, and that is not a
taste**: `win32event.CreateMutex` returns a `PyHANDLE` that closes itself when it is
collected, and `serve()` says `procs.Lock(LOCK).take()` without keeping the `Lock` — so the
pywin32 spelling releases the mutex on the line that takes it, measured (drop the handle,
create again, no `ERROR_ALREADY_EXISTS`). A raw handle in a module-level list is closed by
nothing, which is the lifetime this wants. The second thing: **a refused copy has to close the
handle it opened**, because `CreateMutexW` hands back a valid one either way, and a second
listener that keeps it holds the object alive after the *first* one exits — the refused copy
would lock out its own successor. Both are one line, and each is caught by exactly one test.
**And the release really is free of everything else**: the hand-run's first listener was ended
by a console control event, with no handler, no `finally` and no unwinding — status
3221225786, `STATUS_CONTROL_C_EXIT` — and the next copy had the lock 0.1s later, which is the
whole of §3's "no stale lock to clear" demonstrated at the worst end. A refusal costs 0.11s
and exits 0, before `deleteWebhook` and before the first `getUpdates`.

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
   pass before its code exists has the wrong tests — *unless the slice's own text predicted
   it*, which W3a's `rotate` ordering and W3c's quoting both did, because some slices exist to
   find out whether a mechanism is already right. Those do not skip to step 3. They break the
   thing under test on purpose and show the tests catching it (W3a swapped `rotate`'s two
   statements; W3c replaced `list2cmdline` with `" ".join`), and the row in §11 records which
   tests did *not* fail under the mutation, because that is the half that says whether it was
   aimed at the right thing.
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

*W3c: done.* "Usually nothing" was right about the code and wrong about the slice. `spawn`
needed no change — the round trip through `list2cmdline` and the child's own parser is exact
for quotes, for backslashes before quotes, for empty arguments and for non-ASCII — so the
eight new tests were checked the only way a green-before-the-code test can be, by mutation:
with `list2cmdline` swapped for `" ".join`, six of the eight fail. The two that survive are
the two that do not route through it, which is the answer that says the check worked.

What the slice found instead is **a second quoting hop the plan had not counted**. §4 knew
ConPTY takes a command line; §6 did not say that `Sessions.start`'s `Popen` takes one too,
because on the Mac it does not — `execve` is handed the list. So a prompt off a phone is
flattened and re-split **twice** on Windows, and the only place that was written down was a
comment in `bot.py` asserting the opposite ("no quoting rules to get wrong when there is
nothing to quote for"). §6 above carries the correction and `bot.py`'s comment now names the
platform it is describing. `tests/test_bot.py::TestSpawningForRealOnWindows` is the Windows
twin of `TestSpawningForReal`'s prompt test — same claim, other alphabet: `;` and backticks
there, `&` `|` `>` `^` `%VAR%` here, and in both the assertion that the redirect did not
happen is the one that matters.

The case worth naming is the trailing backslash, because its failure is not mangled text but
a **shorter argv**: `--prompt "…C:\dir\"` re-read as `…C:\dir" --trust` loses the flag §9.3
decides trust with. The hand-run shows the rule doing its work — `C:\dir\` leaves as
`"…C:\dir\\"`, the backslash doubled before the closing quote — and the test asserts the argv
is still fifteen elements with `--trust` last.

**W3d — `pump` without `select`.**
- Red: portable `test_session.py::test_pump_with_fake_terminal` drives `Runner.pump` with a
  fake `Terminal` yielding the W0a fixture in chunks, asserts state goes `LIVE` with the URL
  and the transcript equals the input. Windows `test_pump_real_echo` spawns
  `cmd /c echo https://claude.ai/code/session_w3d_test` and asserts `LIVE`.
- Green: `pump` takes a `Terminal`; posix `Terminal` wraps the master fd with `select`
  inside `read(timeout)` so the loop body is one implementation on both platforms.
- Run: the W0a fixture through `Scrape` again — it should still pass; it is the same code.
- Test: both platforms; the Mac `--foreground` hand-run again.

*W3d: done.* The green went exactly as the line above says — `Terminal.read(timeout)` on both
sides, `select` inside the Mac's — and §4 above carries the three things the slice found that
the plan had not: `alive()` means the terminal on one platform and the process on the other,
the drain has to go past the scraper and not only into the transcript, and a session's end
costs two `TICK`s. `READ_SIZE` moved to `session_posix.py` with the `os.read` that wants it;
`session.py` no longer imports `select` or `errno`. The test move is worth naming because it
is coverage rather than tidying: `test_session.py`'s `drain()` helper is now the same two calls
`pump` makes, so the Mac's eight real-pty tests exercise `Terminal` on their way to asking
about the pty behind it, and `trust_panel()` went to module level so the panel `TestTheTrustDialog`
proves things about is the one `TestPumpOverATerminal` sends through the pump.

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

*W3e: done.* The green is the line above almost exactly — with one word changed, and the word is
what the slice was worth. `terminate` waits on **the job being empty**, not on `alive()`, because
the Ctrl-C is a keystroke rather than a signal and only ever reaches the one process that is
reading the console. §4 above carries that correction and the two it brings with it.

The slice also **put back a flag it had first left out**, and the way that happened is the part
worth keeping. `_create_job` shipped without `KILL_ON_JOB_CLOSE`, reasoning that dropping a handle
should not be a way to end a session by accident. The measurement retired the reason rather than
the conclusion: a runner killed outright loses claude anyway — in under half a second, through the
pseudoconsole — so there was no accident left to protect against, and the only thing the missing
flag bought was a detached grandchild that outlived the runner, the session and the reconciliation
after it. Both directions measured before a line changed;
`test_a_runner_that_dies_without_warning_leaves_nothing_behind` is the pair kept as one test, and
its two assertions are the two mechanisms.

The general shape, and the fourth time this port has hit it: **a sentence that is true of the
Mac's mechanism survives as a claim about the design.** §3's table said the job gives "kill the
runner and claude dies with it" for free. It does — the outcome was never wrong — but for a reason
that had nothing to do with the job, and believing the stated reason is what made the flag look
optional.

**W3f — the stop marker.**
- Red: portable `test_stop_marker_ends_pump` — fake terminal that never ends; a thread
  writes the marker after 0.3s; `pump` returns and the runner writes `ended`. Windows
  `test_request_stop_is_atomic_and_idempotent`.
- Green: `request_stop`/`stop_requested` on both platforms (posix keeps `SIGTERM` *and*
  honours the marker, which costs nothing and makes the tests one set); `pump` checks it each
  tick; `_catch_signals` on win32 handles `SIGINT` and `CTRL_BREAK_EVENT` only.
- Run: none.
- Test: both platforms.

*W3f: done.* The green is the line above, and the marker half of it was already sitting in
`session_win.py` — `request_stop` and `stop_requested` shipped as two lines in W1c, so five of
the thirteen new tests were green before the slice and were checked the way W3a's and W3c's
were, by mutation. Disabling either function fails nine of the ten stop tests; the tenth
asserts a *negative* (one session's marker does not stop another) and is right to survive.
Swapping the append for `os.O_CREAT | os.O_EXCL` — which is what `bot.claim()` does one file
away, so it is the plausible mistake rather than an invented one — fails the two idempotence
tests. **One mutation survives everything: `"ab"` → `"wb"`.** Nothing pins the append, because
the marker is empty and nothing ever writes to it, so truncating and creating are the same
act; it is left as an append against the day something does, and this sentence is the record
that the choice is unguarded.

The slice's own named test had to be repaired before it meant anything, and the repair is the
part worth keeping. `FakeTerminal(forever=True)` gives up after `PATIENCE` so that a `pump`
which never sees the marker *fails* rather than wedging the suite — and that safety valve is
also a way to pass: with the marker disabled, `test_stop_marker_ends_pump` still reached
`ended`, five seconds late, by way of the fake finishing on its own. Every assertion it made
was true of that run. `assertTrue(term.alive())` after `run()` returns is what makes it a test
about the stop, and the mutation is the only thing that would have found that.

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

*W3g: done.* `child_env` split at the seam (§4 above carries the three amendments), and the
acceptance run did what it was asked: a link, `pty.log`, exit status 0, `meta.json` at `ended`,
nothing left running. Two things it found that the plan did not predict.

**The suite could not have caught the bug this slice exists to fix.** Every test in
`test_session_win.py` builds its own environment — `dict(os.environ)`, because they are about
ConPTY and not about `child_env` — so `test_spawn_reports_size` was green on `cmd /c mode con`
throughout the week that the same command answered `'mode' is not recognized` in W3b's hand-run.
The only difference between the two was the one thing neither tested. `test_the_environment_
the_runner_really_passes_can_find_a_program` is that command asked a third time, through
`session.child_env()`, and it is the cheapest guard there is against a `PATH` regression the
rest of the file is constructed not to notice.

**The stop is CTRL_BREAK and not CTRL_C, for a reason worth writing down.** `CTRL_C_EVENT` can
only be addressed to process group 0 — every process sharing the console, the driver included —
so no script can send one to a child alone; `CTRL_BREAK_EVENT` can, and `catch_signals` installs
the same handler on `SIGINT` and `SIGBREAK`, so the path under test is the one a keystroke takes.
The keystroke itself stays a hand check.

**W3h — `Trust` carries a partial escape, like `Scrape`.** Portable; found by W0a.
- Red: already written. `test_session.py::TestTheWindowsTrustDialog::
  test_the_answer_does_not_depend_on_chunking` is decorated `@unittest.expectedFailure`
  and fails at chunk sizes 64, 16 and 1. Remove the decorator; it is now the red test. Add the
  same sweep against the Mac fixture in `TestTheTrustDialog` for the question half.
- Green: give `Trust.feed` the carry `Scrape.feed` has — hold a trailing `\x1b...` fragment
  (bounded by `CARRY_LIMIT`) and prepend it to the next chunk before squeezing. Same for a
  split multi-byte character: use an incremental decoder as `Scrape` does, or squeeze bytes
  through one shared helper. *Done as the shared helper:* `session.Stripper` is `strip` over a
  stream, `Scrape` and `Trust` each hold one, and neither carries its own copy of the rule.
- Run: none. *Taken anyway, and it is the half of this slice worth reading* — "none" was
  written when the bug looked like a property of a fixture. What ConPTY actually delivers, and
  whether the dialog is still there to answer, are both facts, and both are in §4 above and in
  the row: the chunks are tiny but never split an escape, and under `projects_root` there is no
  dialog any more.
- Test: both platforms.

**W3i — the link is held for a byte that is not coming.** Portable; found by W3g's acceptance
run, and it is the largest thing left in the time to a link.
- The fact: `Scrape.feed` returns a URL only when `found.end() < len(self.tail)` — one more
  character has to arrive, so that a URL still being written is never reported half-formed.
  Measured through the real runner: **the link is complete in the tail at 1.96s and `Scrape`
  hands it over at 6.43s**, because ConPTY emits on screen change and the screen does not
  change again for four and a half seconds. The Mac has never shown this: its renderer keeps
  drawing, so the next byte is always along in milliseconds. *Amended by W3i:* the condition
  is real and rare, not standing. Eight runs on this box, four with the pre-slice code, put
  the link in the record at 1.9–2.2s with **no hold at all** — 404 characters follow the URL
  inside the same ConPTY read, five runs out of five — so W3g caught a frame that ended at the
  link and this slice cannot reproduce one. What the hold costs when it does happen is the
  measurement below.
- Red: portable `test_session.py::test_a_link_at_the_very_end_of_the_output_is_not_held_for
  ever` — feed the W0a fixture truncated to end exactly at the URL, then feed nothing, and
  assert the link is reported within a bounded number of idle ticks. The Windows twin is the
  measurement above turned into an assertion over `tests/fixtures/rc_startup_win.log`.
- Green: the holdback needs a way to end other than more output. The shape to try first is an
  idle flush — `pump` already knows when a read came back empty, so `Scrape` can be told
  "nothing more is coming this tick" and release a URL that has been stable for one tick. Keep
  the guard for the chunked case; it is doing real work (`CARRY_LIMIT` and W3h are the same
  family of bug).
- Run: the acceptance run again, and the time to link has to come down by about four seconds.
  *Amended by W3i:* it did — 6.7s to 2.3s — and **none of it is this slice's doing**, because
  the same code with the fix disabled is just as fast today. A prediction written from one
  run, where the run was the rare case. The measurement that does answer the slice provokes
  W3g's condition on a live ConPTY instead of waiting for it: cut the read at the URL's last
  byte and withhold the rest for 5s, which is what "the screen does not change again" is.
  Three runs each way: **held 5.00s without the fix and 0.20s — one `TICK` — with it**.
- Test: both platforms — this is `Scrape`, which is the most portable code in the program.

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

*W4a, 2026-09-19 — two thirds of this slice's red and green were already on disk, and the
un-gate is a word this entry should not have used.* The portable pid-reuse test it names
shipped in **W1b** as `test_a_pid_that_started_after_the_record_is_somebody_else`, and `psutil`
entered `requirements-win.txt` in **W3e**, which needed it for `terminate`'s tree. What was
actually missing was narrower and in a different place: of the eight properties
`TestWhetherARunnerIsStillThere` asserts, three had fake-`procs` twins and three are the
platform's, but **two had neither** — a record with no usable `started`, and a pid the platform
will not date. Both are `bot.Sessions.alive` branches that return `True`, both were untested on
Windows, and a mutation of each is caught by exactly one test in the whole suite: the one W4a
added. Nothing is un-gated: `TestWhetherARunnerIsStillThere` and `TestSpawningForReal` stay
`@posix_only` because their mechanisms are `/bin/sleep`, `EPERM`, fd 9 and `getsid`, and a
platform test is not a gate to be lifted. The pattern this entry wanted is the one W3c already
built for `TestSpawningForRealOnWindows` — a twin beside it, not a decorator removed — and
W4b–W4c should read it that way.

**W4b — `Sessions.stop` on Windows.**
- Red: `test_bot.py::test_stop_writes_marker_then_waits` (portable, fake `procs`);
  `test_bot_win.py::test_stop_real_runner` starts the real `session.py` against
  `cmd /c ping -n 60 localhost` as the argv (an `--argv` debug flag, or a test hook), calls
  `stop`, asserts `ended` in `meta.json` and no pids left.
- Green: `stop` = `request_stop`, wait `STOP_GRACE` on `alive`, then `terminate` runner.
- Run: none beyond the tests.
- Test: both platforms.

*W4b, 2026-09-19 — the green is three lines and the shape around them is four branches, and
the test hook this entry offered a choice about did not need to be built.* `Runner.__init__`
has taken an `argv` since slice 6, so the real-runner test hands it
`cmd /c echo <link> & ping -n 60 127.0.0.1` through a twelve-line harness of its own and
`session.py` grows no debug flag — **no `--argv` on the shipped program**, which is the right
answer for a launcher whose whole defence is that the argv is built and never accepted (§10).
The test lives in `test_bot.py` as `TestStoppingARealRunnerOnWindows`, beside the portable
class rather than in a `test_bot_win.py`, which is W3c's twin-beside-it pattern and what W4a
said this slice should copy. `echo` is in that command line because a `stop` is only ever aimed
at a **live** session, and `live` is a state the runner reaches by scraping a link — the
substitute has to produce one or the test is about `failed`, which is a different path. The
entry's "asserts `ended` in `meta.json`" is the assertion that carries the slice, and for a
reason it does not give: a session that says `ended` was ended *by its own runner*, so the
marker was heard. A kill leaves the word `live` on disk, which is what the red run showed.

What the entry does not mention, and what the code needed, is the **three ways the ask cannot
be made**: a record with no usable `sid` (no directory, so no marker), an `OSError` writing it
(a full disk, a directory deleted under the listener), and the session that is already gone.
The first two skip the wait and go straight to the force — waiting out fifteen seconds for an
answer to a question nobody was asked is the bug — and the third asks nothing and kills
nothing. Four of the slice's six tests are those branches and the fifth is the fallback's
grace; all six were mutation-checked (§11).

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

*Noted from W4b, 2026-09-19 — read this before starting: **W4c's red and green are both
already on disk**, and what is left of the slice is its run step.* The portable red is
`test_bot.py::test_start_passes_the_platform_flags_to_popen`, which shipped in **W1b**; the
Windows one is `test_procs_win.py::test_breakaway_is_not_among_the_flags`, which shipped in
**W0c** under a name this entry did not predict; `session_win.spawn_flags()` returns the two
flags and `Sessions.start` already spreads it into `Popen`. This is the third slice in a row
(W4a, and W4b's `--argv`) whose stated work was partly done by an earlier one — the pattern is
that W1b built the seam's *portable* half wholesale, so every W4 entry that names a portable
fake-`procs` test is naming something that exists. W4c should therefore start by running its
hand-run, and its row should say what that showed and which tests it found already green.

*Amended by W4c, 2026-09-20 — **the paragraph above is half wrong, and the wrong half is the
Windows one**.* The portable red is real and catches what it claims. The Windows one was not
a red test at all: `test_procs_win.py` opened with **its own copy** of the two `getattr`
lines, so `test_breakaway_is_not_among_the_flags` asserted about a constant in the test file
and never read `session_win`. Mutating `session_win.DETACH_FLAGS` to add breakaway back, and
mutating `spawn_flags()` to return `{}`, left **all 611 tests green** — the one property this
slice exists to hold was, on this platform, held by nothing. The fix is one line
(`DETACH_FLAGS = session_win.DETACH_FLAGS`), which also repoints the real-process survival
test at the program's flags, plus two tests: `test_spawn_flags_hands_popen_the_detach_flags`
for the function nothing looked at, and `test_both_detach_flags_are_actually_set` for the
vacuity — `flags & BREAKAWAY` is falsey for empty flags too, so the breakaway assertion alone
is satisfied by a constant that has lost everything. **The rule this slice adds to the
ritual's step 1: "the test already exists" is not the same claim as "the test would fail",
and only a mutation can tell the two apart.** W4a and W4b both found work hiding under
"already on disk"; this one found the opposite — a test on disk that was not testing anything.

*Also from W4c: the run step's first words, `python bot.py --serve`, cannot be obeyed on
Windows yet.* `serve()`'s first act is `procs.Lock(LOCK).take()`, and that is still
**W4d**'s stub, so the command dies with `NotImplementedError: session_win: WINDOWS.md W4d
has not been built` before it reaches `Listener`. W4c's hand-run therefore ran `serve()`
minus that one line, in a process of its own (`scratch\w4c_listener.py`). **W4d is what makes
`--serve` a runnable command on this platform, and W4e's from-the-phone checklist depends on
it** — neither entry said so.

**W4d — the mutex.**
- Red: `test_procs_win.py::test_second_lock_refused`; `test_lock_released_on_owner_death`
  (a subprocess takes it and is killed; retake succeeds within 1s);
  `test_bot.py::test_serve_exits_zero_when_lock_refused` (portable, fake `Lock`).
- Green: `Lock` via `CreateMutexW` (`pywin32` or `ctypes`); `serve()` takes it before
  constructing `Telegram`.
- Run: two consoles both running `bot.py --serve`; the second prints the refusal and exits 0.
- Test: both platforms.

*Noted from W4b, 2026-09-19 —* the third red here,
`test_bot.py::test_serve_exits_zero_when_lock_refused`, also shipped in **W1b**, as
`test_serve_takes_the_lock_before_it_touches_telegram`. The other two and the green are real
work: `session_win.Lock.take()` is still the `_later("W4d")` stub, so this is the last
`NotImplementedError` left in the seam.

*Noted from W4c, 2026-09-20 —* that stub is also **the only thing standing between this box
and a runnable `bot.py --serve`**, which is bigger than "the last NotImplementedError" makes
it sound: `take()` is the first statement in `serve()`, so today the listener cannot be
started by its own entry point at all, and W4c had to hand-run a `serve()` with that line
removed. Two consequences for this entry. The run step here — two consoles, the second
refused — is the *first* time `--serve` will have run on Windows, so expect to find whatever
else that path has never executed; and **W4e cannot begin until this lands**, because every
line of its checklist starts with a listener. Verify the `NotImplementedError` is gone by
running `--serve` for real, not only by the fake-`Lock` test.

*W4d, 2026-09-20 — done, and the third red was covered while the case beside it was not.*
`test_serve_exits_zero_when_lock_refused` needed nothing: W1b's
`test_serve_takes_the_lock_before_it_touches_telegram` catches all three ways that line can be
wrong — no lock at all, the answer ignored, and `return 1` instead of `return 0` — each by
itself and by nothing else in the suite. What no test held is **the grant**:
`if not procs.Lock(LOCK).take() or True`, a listener that refuses itself, was caught by **0 of
623**, and that is a bot which exits 0 every ten seconds and never answers the phone. On the
Mac `take()` is `return True` and cannot be wrong; here it can, so the slice's portable test is
`test_serve_runs_the_listener_when_the_lock_is_granted` rather than the one the entry named.
The two Windows tests are as written and both were red on the stub. The run step is in §11.
The third item in the list above — "Test: both platforms" — is Mac debt like every slice since
W1a; the green is Windows-only code, but `test_bot.py` gained a test that must run there.

**W4e — end to end from the phone.** No new code expected.

*Noted from W4d, 2026-09-20 —* the block is gone: `.venv\Scripts\python bot.py --serve` runs
here now and logs `centrion listening · 1 allowed chat(s) · root … · max_sessions 2 · offset
None` within a second, so W4e's listener is the shipped entry point rather than W4c's
`scratch\w4c_listener.py`. Two practicalities from that run. The listener has no handler for a
console control event — `catch_signals` is the *runner*'s — so Ctrl-C ends it with
`STATUS_CONTROL_C_EXIT` (3221225786) and no unwinding; that is fine for the lock (the kernel
releases it either way) and it is what §7's loop restarts on. And `.telegram.json` on this box
still carries the placeholder token from W3g, so `--serve` sits in `getUpdates: HTTP 401 …
retrying` until a real one is put there; that swap is W4e's first act.
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
| — baseline before any slice | — | 2026-09-13 | 7a09f8d | 163 ran, 23 F, 18 E · not run | Windows: `test_bot` and `test_session` fail to import (`fcntl`); `test_config`'s 0600 tests and `test_projects` fail. Mac suite not run from this desk. |
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
| W3b `Terminal` and `spawn` on ConPTY | done · Mac pending | 2026-09-16 | b80f4f1 | **531 ran: 462 pass, 68 skip, 1 xfail**, 11.5s · **not run** | **Three seconds of every session start were the pseudoconsole waiting for an answer nobody was giving it.** A fresh ConPTY sends `ESC[c` — DA1, *what terminal are you* — and holds the child's output for 3.04s before giving up: measured on every shape of child, every run, and 0.04s once `Terminal` replies `ESC[?1;0c`. The old WinPTY backend has no such wait (0.22s), which is what pinned it on ConPTY rather than on pywinpty. The reply is sent only after the query has been seen, because before it the bytes would be ordinary input and would reach the child; after it, the console consumes them — an interactive `cmd` driven through this never sees them. This is three of W0a's 6.2 seconds to a link, and three of the phone's forty-five. Two more corrections to §4: **pywinpty takes the program and its arguments separately** and prepends the program itself (quoted — verified against an appname with a space), so `spawn` passes `list2cmdline(argv[1:])`; passing all of argv gives the child its own path as `argv[1]`, silently. And **an exec failure does not land on the pty**, because `CreateProcess` fails before there is a child to write it: pywinpty raises, nothing reaches the terminal, so `spawn` raises `OSError` and `Runner.run` catches it around the spawn — without that a missing binary is a traceback over a record still saying `starting`. `Terminal.close()` ends the child by itself (the pseudoconsole closes with its last reference, process gone in under half a second), which is a second reason for §4's close-last order. Run step: `cmd /c echo hello` came back in 0.05s; `cmd /c mode con` answered `'mode' is not recognized`, which is `child_env()` still handing out a POSIX `PATH` — W3g's, now visible instead of predicted. One repair found on the way: **W3a's torn-read test guards itself with `assertTrue(written)`, and on Windows that was a coin toss** — its reader held the file so continuously that 0–8 of 150 replaces landed and 3 of 32 measured runs landed none. It failed exactly that way once here, under the load of these ConPTY tests. A millisecond of pause between reads takes it to 38–42 landing, every run, busy or idle — so the Windows half now exercises the property instead of asserting nothing, and the reader is still open across the whole of every `json.loads`. Torn reads: still zero. +13 tests, no skip count change here (the new file skips whole on the Mac). Mac: not run — see Pending. |
| W3c command-line quoting | done · Mac pending | 2026-09-17 | 8e8f032 | **539 ran: 470 pass, 68 skip, 1 xfail**, 22.7s · **not run** | **The quoting was already right; the count of places doing it was wrong.** `spawn` needed no change — a prompt carrying `"`, `^`, `%`, `&`, `\|`, `<`, `>` and a trailing backslash comes back byte-for-byte, and so do an empty argument, a bare quote, doubled backslashes and a non-ASCII string. So the eight new tests were checked by mutation instead of by a green run: with `subprocess.list2cmdline` replaced by `" ".join`, **six of the eight fail**, and the two survivors are the two that do not route through it — `test_a_program_path_with_a_space_is_still_one_program` (that quoting is pywinpty's, not ours) and `test_no_prompt_means_no_prompt_flag` (no argument with a space in it). §9's ritual and §11's rule are amended: a slice that is green at step 1 mutates rather than skipping ahead, and the row says which tests did *not* fail. **The find is a second quoting hop nobody had written down.** §4 had ConPTY; `Sessions.start`'s `Popen` does exactly the same flattening on Windows, because there is no `execve` to hand a list to — so a prompt off a phone is joined and re-split *twice* here against zero times on the Mac, and `bot.py`'s comment at that spawn asserted the opposite in as many words ("no quoting rules to get wrong when there is nothing to quote for"). True of `execve`, false of `CreateProcess`; §6 and the comment now say which. Nothing was broken and no behaviour changed — `list2cmdline` is what `Popen` already used — but the hop had no test on this platform and the documentation pointed the wrong way. Hand-run (the slice said "Run: none"; the two claims in that comment were worth seeing): `C:\dir\` leaves hop 1 as `"…C:\dir\\"`, backslash doubled before the closing quote, and both a plain child and a ConPTY child parse back the identical string — that doubling is the whole reason `--trust` still exists at the far end, which is the case the twin test pins at fifteen argv elements. Suite time doubled, 11.5s → 23.3s: the four new ConPTY tests spawn real interpreters, and an interpreter's first byte under a fresh ConPTY is the slowest thing in this suite. **One open item, honestly unresolved:** one run in the first four errored (`errors=1`) and the name was lost — that command kept only the last three lines of output. It has not come back in **34 consecutive runs since, 24 idle and 10 under four spinners**, which is the load W3b's flake needed. Every run since keeps its whole output (`scratch\w3c_flake.sh`, `scratch\w3c_flake_loaded.sh`, beside `w3c_mutate.py` and `w3c_handrun.py`), so the next occurrence names itself; until then this is an unidentified ~3% error, not a green suite, and W3d should re-read this row before trusting a single clean run. +8 tests, no skip count change. Mac: not run — see Pending. |
| W3d `pump` without `select` | done · Mac pending | 2026-09-18 | 0937950 | **559 ran: 484 pass, 74 skip, 1 xfail**, 21.8s · **not run** | **The loop went where the plan said; what moved with it was a question that used to have one answer.** `pump` now calls `terminal.read(TICK)` and nothing platform-shaped: the `select` that was its first statement is the first statement of `session_posix.Terminal.read`, `spawn` returns a `Terminal` on both sides, and `Terminal` joined the seam's `SURFACE`. **`read` and `alive` are two questions now, and they do not mean the same thing on the two platforms** — on a pty EIO is the end of the output *and* of the session, so the old loop broke on either; ConPTY separates them, so `session_posix.alive()` is about the *terminal* (the flag set when the pty hangs up) and `session_win.alive()` is about the *process* (`pty.isalive()`). Both answer the only question `pump` asks, and **neither is a substitute for `_reaped()` or `procs.alive(pid)` — W3e and W4a should read that sentence before reaching for `alive`.** Second find: **the drain after the terminal finishes has to go past the scraper, not just into the transcript.** The obvious version appends the dead child's last chunk to `pty.log` and leaves, which loses a *link* arriving in it — reachable on Windows, where output outlives the child (W3b) — so both callers go through one `Runner.absorb` and `test_a_link_in_the_tail_still_goes_live` is the case. Third, measured rather than assumed: **a session's end costs two `TICK`s** — 0.401s of a 0.432s `pump` over `cmd /c echo`, one ordinary poll then the drain, against 0.031s of reading the child (`scratch\w3d_exitcost.py`). Paid after `live` is recorded, so only a `failed` session's tail waits on it; left at `TICK` because shortening it trades 0.2s against the text §4.6 exists to deliver. Run step: the W0a capture gives the same single link at chunk sizes 1, 7, 64, 512, 4096 and whole — unchanged, as predicted — and the new pump over a real ConPTY reached `live` with the URL in `meta.json` and the link in `pty.log` (`scratch\w3d_handrun.py`). `READ_SIZE` moved to `session_posix.py` with the `os.read` that wants it; `session.py` no longer imports `select` or `errno`. **W3c's open ~3% error did not recur**: 20 consecutive clean runs of the full suite after the green, plus the red and green runs themselves — it stays open and unidentified, and `scratch\w3d_flake.sh` keeps the whole output of any run that is not OK. +20 tests (12 portable over a fake `Terminal`, 6 posix over a real pty, 3 Windows over a real ConPTY), skips 68 → 74 — the six new posix ones, which is the whole of the change. Mac: not run — see Pending. |
| W3e Job Object and `terminate` | done · Mac pending | 2026-09-19 | c00371b | **572 ran: 497 pass, 74 skip, 1 xfail**, 31.8s · **not run** | **Two sentences in the plan were true about the outcome and wrong about the mechanism, and one of them made a flag look optional.** The Ctrl-C is not a `CTRL_C_EVENT` — nothing turns it into one; it goes to whatever reads the console, so it reaches claude and reaches nothing else (`cmd`, `ping`, a sleeping python: two each, all three carry on). So `terminate` waits on the **job being empty** and not on the pid, because the ordinary session — claude exits 0 in a second, its dev server does not — is precisely what a pid-shaped wait calls finished. And `KILL_ON_JOB_CLOSE`, first left out on the grounds that a dropped handle should not end a session by accident, went back in once measured: a hard-killed runner loses claude within 0.5s regardless (the pseudoconsole, not the job), so the accident was already unavoidable and the flag's only actual effect was the grandchild it was leaving alive. `terminate` gained a `terminal` argument on both platforms — the Mac ignores it — because neither the Ctrl-C nor the job is reachable from a pid. Eleven tests in `TestEndingTheSessionAndItsTree`, all against real trees; two portable ones in `test_session.py` for the seam and for `run`'s ordering. `psutil` arrives a slice early (§`requirements-win.txt` says why). |
| W3f stop marker | done · Mac pending | 2026-09-19 | 0c7514e | **586 ran: 511 pass, 74 skip, 1 xfail**, 35.3s · **not run** | **The plan's "each tick" was the sentence to get right, and `SIGTERM` is dropped for the opposite of the obvious reason.** A check that only looks for the marker on an *idle* tick stops every session except the ones producing output, which is the session `stop` is for; so it is checked on every pass, and the cost that made that look expensive is 5.7µs of `stat` against a 23.6µs pass of `pump` over a fake terminal doing nothing else — five a second on an idle session, and a smaller share of any real one. No throttle. `SIGTERM` is not absent on Windows, it is *undeliverable*: `signal.signal(SIGTERM)` is accepted and `os.kill(pid, SIGTERM)` is `TerminateProcess`, so a handler registers and can never run — which is the actual reason `stop` is a file. Left: `SIGINT` and `SIGBREAK`, for `--foreground` only; in service `DETACHED_PROCESS` means there is no console to interrupt from. POSIX honours the marker now as well, so the runner has one place that hears a stop and the tests are one set. Mutation, for the five tests green before the slice (`request_stop`/`stop_requested` shipped in W1c): disabling either fails 9 of the 10 stop tests, the survivor being the one asserting a negative; `O_CREAT\|O_EXCL` instead of the append fails the two idempotence tests; **`"ab"` → `"wb"` is caught by nothing** — the marker is empty, so nothing pins the append. And the mutation found a real hole in the slice's own named test: `FakeTerminal(forever=True)`'s patience let `test_stop_marker_ends_pump` reach `ended` five seconds late with the marker disabled entirely. `assertTrue(term.alive())` is the assertion that makes it about the stop. |
| W3g `child_env`, runner acceptance run | done · Mac pending | 2026-09-19 | 66d93b2 | **592 ran: 516 pass, 75 skip, 1 xfail**, 30.3s · **not run** | **The runner works end to end on this box, and four and a half of its seven seconds to a link are ours.** Acceptance run, twice: `session.py --foreground` produced a link, a 3.3 KB `pty.log`, exit status 0 1.8s after the console control event, `meta.json` at `ended`, and no claude left behind — but **time to link 6.7s**, which is W0a's 6.2 with three seconds supposedly removed by W3b. Both halves of that turned out to be true. W3b's saving is real and re-measured here against the same binary minutes apart: **2.0s with the DA1 answer, 5.0s with it suppressed**, twice each. The rest is `Scrape`: instrumented through the real `Runner`, **the link is complete in the tail at 1.96s and `Scrape` returns it at 6.43s**, because `feed` holds a URL until one more character arrives and ConPTY emits only on screen change — the screen does not change again for four and a half seconds. The Mac has never shown it: its renderer keeps drawing. New slice **W3i**. The other finding is about the suite, not the code: every test in `test_session_win.py` builds its own `dict(os.environ)`, so `test_spawn_reports_size` was green on `cmd /c mode con` for the whole week that the same command answered `'mode' is not recognized` through `child_env` — the new `test_the_environment_the_runner_really_passes_can_find_a_program` closes that, and fails with the Mac's `PATH` put back (mutation-checked). `mode con` now answers, and answers `Lines: 50 / Columns: 200`, which is W3b's size claim confirmed by the tool rather than by pywinpty. `cmd /c set` read back through the ConPTY shows all nine essentials present once each, no duplicate spelling, no `CLAUDE*`, no `AI_AGENT`, `COLUMNS`/`LINES` intact. The config this box had never needed until now: `.telegram.json` with a placeholder token, `projects_root` the parent of this checkout, and the W2b DACL check passed it unmodified. +6 tests, +1 skip (the Mac's shell variables). Mac: not run — see Pending. |
| W3h `Trust` carries a partial escape | done · Mac pending | 2026-09-19 | 0c8350e | **593 ran: 518 pass, 75 skip, 0 xfail**, 35.1s · **not run** | **The bug is real, the sizes that trigger it are real, and the live path was never hitting it — and the slice only knows that because it took a run step it was excused from.** `Stripper` is the green: `strip` over a stream, holding a trailing partial escape bounded by `CARRY_LIMIT` and decoding incrementally, and both `Scrape` and `Trust` now hold one instead of `Scrape` owning the only copy of the rule. The red was wider than the row it replaces said: not "64, 16, 1" but **127 of the first 199 chunk sizes on the Mac panel and 116 of the first 299 on the Windows capture** — the passing sizes in between are the cuts that happen to miss an escape, which is why sampling three sizes made it look like a threshold. After: **every size from 1 to 399 answers the dialog, on both fixtures**, and `Scrape` returns the same link at 1, 7, 64, 512, 4096 and whole. Run step, and it is two findings. **One:** ConPTY's real reads are small — 26 chunks, min 3 bytes, median 21, three quarters ≤64 — but **0 of 26 ended inside an escape sequence**, because it flushes whole renders, so the pre-W3h `Trust` answered the live panel too (2.84s against 2.71s, both reaching `done` and a link). The plan had measured the input's size and reasoned about where it was cut; those are different measurements and only one of them was taken. **Two, and it is the one that changes a later slice:** §9.3's dialog **no longer appears at all** for a new directory under `projects_root` — empty, one file, a git repo and a `.claude\settings.json` all came straight up to a link, each getting a `projects` entry saying `hasTrustDialogAccepted: false` without being asked — while the same directory under `%TEMP%` raises it every time and `Trust` answers it. Where, not what. So `Trust` is unexercised on the only path that turns it on, W4e cannot confirm `new+trust` by watching, and §4 now says both. No code changed for either finding; the honest response to "the mechanism is currently unreachable" is not to delete the code that handles it. Test count +1 and **the suite's one expected failure is gone** — 592/1 xfail becomes 593/0, the first slice since W0a where that line reads clean. Two consecutive full runs, 35.0s and 35.1s; **W3c's unidentified ~3% error did not recur** (it stays open, now ~22 clean runs on from W3d's twenty). Mac: not run — see Pending. |
| W3i the link is held for a byte that is not coming | done · Mac pending | 2026-09-19 | d57ac3f | **605 ran: 530 pass, 75 skip, 0 xfail**, 35.9s · **not run** | **The hold is real, the fix is one tick, and the four and a half seconds it was sized from are not there any more.** `Scrape.idle()` is the green: `feed` is what the stream says and `idle` is what its absence says, and the loop may only say it after a read has come back empty — a whole `TICK` of nothing — or against a finished terminal, where the claim is stronger still. The guard `feed` keeps is untouched, because a match at the end of a *chunk* may be half a link with the rest in flight and a match at the end of a *silence* may not. **But the live path is not taking it.** Eight sessions, four of them with `Runner.idle` stubbed back out to the pre-slice code, all reached the record in 1.9–2.2s with **zero hold**; the read that completes the URL carried **404 more characters after it, five runs out of five**, and reads kept arriving every ~0.1s after. So W3g caught a frame that happened to end at the link, and this box will not produce one to order — the same shape as W3h's finding one slice earlier, and the second time running that a number measured once has been read as a constant. Both amendments are in §9. What answers the slice is the condition provoked on a live ConPTY rather than waited for: cut the read at the URL's last byte and withhold the rest for 5s, which is exactly "the screen does not change again". Three runs each way — **held 5.00s without the fix, 0.20s with it**, which is one `TICK` and is the bound the tests assert. The acceptance run came down from W3g's 6.7s to **2.3s**, exit 0 1.7s after the console control event, `pty.log` 4.1 KB, `meta.json` at `ended`, nothing left behind — and **none of those four seconds are this slice's**, which the A/B is the only reason anyone knows. +12 tests, **no skip count change** — every one runs here; on the Mac the two fixture-gated Windows ones will skip. W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4a `alive`/`started` via psutil | done · Mac pending | 2026-09-19 | d131849 | **614 ran: 539 pass, 75 skip, 0 xfail**, 35.3s · **not run** | **Two thirds of the slice was already on disk, and the third that was not is two branches nobody had tested on this platform.** The entry's portable red test shipped in **W1b** as `test_a_pid_that_started_after_the_record_is_somebody_else`, and `psutil` entered `requirements-win.txt` in **W3e**; the green is therefore two functions and nine tests. `alive` is `psutil.pid_exists` — the same line `session_posix` draws with EPERM, since it asks whether the process has *exited* rather than whether it is ours — and `started` is `Process(pid).create_time()`, epoch seconds on the same clock the record's `started` is written from. What was actually missing: of the eight properties `TestWhetherARunnerIsStillThere` asserts, three had fake-`procs` twins and three are the platform's, and **two had neither** — a record with no usable `started`, and a pid the platform will not date. Both are `Sessions.alive` branches returning `True`, both untested here, and since both new tests were green before any code changed (the ritual's step-1 rule), each was mutated: `return True` → `return False` in either branch is caught by **exactly one test in the whole 614**, the one this slice added. **Run step, and §6's prediction is wrong in both directions.** `AccessDenied` from `create_time` **does not happen**: 0 of the 208 processes on this box refused, including the **109 whose `username()` psutil cannot read**, because it needs only `PROCESS_QUERY_LIMITED_INFORMATION` and every account has that for everything — so §4's `began is None` branch is reachable here only by a pid that dies between the two calls, and the handler is insurance (kept, and now with a test that would notice if Windows tightened the check). What the two genuinely unopenable pids answer instead is **`0.0`** — the epoch, not the boot time and not an error — so pid 4 arrives at `Sessions.alive` as a timestamp older than every record that could exist and **reads as a live runner** for as long as a corrupt record names it. Not patched: the Mac reaches the same place for pid 1 by an honest route (launchd really did start at boot) and its own test asserts pid 1 alive on purpose, `Sessions.alive` cannot tell the two apart, and a record's pid is one the listener wrote from its own `Popen`. §6 and §9's W4a are amended. **Nothing was un-gated, and that word is the entry's other mistake**: `TestWhetherARunnerIsStillThere` and `TestSpawningForReal` stay `@posix_only` because `/bin/sleep`, EPERM, fd 9 and `getsid` are the Mac's mechanisms — the shape that works is W3c's twin-beside-it, not a decorator removed, and W4b–W4c should copy that. `test_procs_win.pid_alive` also stays on `tasklist` rather than moving to psutil now that it could: those tests ask whether a detached child survived, and answering that with the library under test in the same file is not an answer. +9 tests (7 Windows real-process, 2 portable), **no skip count change** — every one runs here; on the Mac the 7 will skip. Two consecutive full runs, 35.3s both; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4b `Sessions.stop` | done · Mac pending | 2026-09-19 | 3ee62a7 | **619 ran: 544 pass, 75 skip, 0 xfail**, 37.1s · **not run** | **The listener asks with a file now, on both platforms — and the kill it replaces was not leaking anything, which is not what §6 led this slice to expect.** `stop` is *ask, wait, force*: `procs.request_stop`, then `STOP_GRACE` of polling `alive` at `STOP_POLL`, then `session.terminate` for the runner that did not answer. **Run step, and it is an A/B on one box** (`scratch\w4b_handrun.py`, a real `Runner` over a real ConPTY with a `ping` under it): the marker path ends the session in **5.70s** with `meta.json` at `ended` and all three pids gone; the path it replaces — the same call with the wait skipped, which is exactly the old body — returns in **0.00s** and takes **all three pids just as completely**. So W3e's `KILL_ON_JOB_CLOSE` and the pseudoconsole really do cover a hard kill, and §6's worry about what the fallback leaves behind is answered: nothing. **What the old path lost was the ending, not the tree** — nobody sends claude the two Ctrl-Cs it exits 0 on (W0b, 1.71s), and `meta.json` is left saying `live` by the only process that knew better, which is precisely what the red run printed (`'live' != 'ended'`). On the shipped path `Listener.halt` writes `ended` through `finish` a moment later, so the phone was told the truth before this slice too; `stop` on its own was not, and the 5.70s is the runner spending its whole `GRACE` on a child that ignores a Ctrl-C. **The Mac's timing changes and it is the cost of the slice**: `SIGTERM` moves from the first act to the fallback, so a runner anywhere but `pump` — inside `spawn`, inside the trust dialog — now waits out `STOP_GRACE` before anything it can hear arrives. Taken on purpose (§6), and it is the one thing in this slice the Mac has to check. Three things the entry did not name and the code needed: a record with **no usable `sid`** has no directory and so no marker, an `OSError` writing it is an ask that was not made, and both skip the wait rather than paying fifteen seconds for an answer to a question nobody heard. No `--argv` was added to `session.py`: `Runner` has taken an `argv` since slice 6, so the real-runner test builds its own harness and the launcher keeps the property that its argv is built and never accepted (§10). **Mutation, for the four tests green before the green step:** seven mutations, every one caught — no ask at all (3 tests), waiting although nothing was asked (2), no early return when the runner goes (2), no corpse guard (**1, and only the test this slice added**), the ask aimed at the root instead of the session directory (2), the runner given its own `GRACE` instead of the nested one (2), and `STOP_GRACE = GRACE` (3). +5 tests net (6 new, 1 rewritten away), **no skip count change** — the one Windows test runs here and will skip on the Mac. Two consecutive full runs, 37.9s and 37.1s against W4a's 35.3s: the real-runner test is ~6s of that and is the only end-to-end stop this suite has. W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4c runner outlives listener | done · Mac pending | 2026-09-20 | 1adcb45 | **621 ran: 546 pass, 75 skip, 0 xfail**, 42.3s · **not run** | **The slice had no red test on this platform, and nobody could have known without mutating.** §9's note said W4c's red and green were both already on disk; the green is (`spawn_flags()` since W1c, `**procs.spawn_flags()` in `Sessions.start` since W1b) and the *portable* red is, but the Windows one was reading a copy: `test_procs_win.py` opened by respelling `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` as its own module-level `DETACH_FLAGS`, so `test_breakaway_is_not_among_the_flags` — and the real-process survival test, which spawns `PARENT % DETACH_FLAGS` — were evidence about the test file. **Mutation, before: `session_win.DETACH_FLAGS` gaining `CREATE_BREAKAWAY_FROM_JOB` back, and `spawn_flags()` returning `{}`, were each caught by 0 of 611.** After: one line (`DETACH_FLAGS = session_win.DETACH_FLAGS`) and two tests, and **four mutations are caught by four distinct tests, one each** — breakaway back → `test_breakaway_is_not_among_the_flags`; `spawn_flags()` → `{}` → `test_spawn_flags_hands_popen_the_detach_flags`; `DETACH_FLAGS = 0` → `test_both_detach_flags_are_actually_set` (the vacuity case: `flags & BREAKAWAY` is falsey for empty flags, so the breakaway assertion alone accepts a constant that has lost both); `Sessions.start` dropping the seam → `test_start_passes_the_platform_flags_to_popen`. **Run step, and the mechanism is sound — it was only ever the tests that were not.** `python bot.py --serve` could not be used: `serve()`'s first statement is `procs.Lock(LOCK).take()`, still W4d's stub, so it dies with `NotImplementedError: WINDOWS.md W4d has not been built` — **§9's W4c and W4d amended, and W4e is blocked on W4d for the same reason**. The hand-run is that `serve()` minus the lock, in its own process group (`scratch\w4c_listener.py`), Telegram as two files: listener A takes `claude beacon`, a real runner over a real ConPTY reaches `live` **0.6s** after the message; `CTRL_BREAK_EVENT` (a driver cannot send `CTRL_C_EVENT` to one child) ends A in **0.78s** with status 130; `tasklist` — not `procs.alive`, per W4a — then says all three of runner 6980, ConPTY child 6136 and the `ping` under it 8540 are **still there**; listener B against the same sessions root answers `ls` with the session, `2s` old, link intact, `meta.json` still `live` and **the same two pids**. Unplanned, and carried here because it was found on the way: `test_request_stop_is_atomic_and_idempotent` was failing about one run in five, and it is the *test* that is wrong, not `stop_requested` — four threads appending to one list record append order while the assertion is about evaluation order. Stamped before the call, the answers are in order **0 times out of 80 rounds** across two measurements (9 in 40 and 6 in 40 out of order by append); one list per poller, then 25 runs of the class with no failure. +3 tests (2 Windows, 1 is the flake fix rewritten), **no skip count change here** — the 2 new ones run on this box; on the Mac they will skip. Two consecutive full runs, 42.3s both; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4d mutex | done · Mac pending | 2026-09-20 | 17d8ba0 | **624 ran: 549 pass, 75 skip, 0 xfail**, 36.8s · **not run** | **The seam's last `NotImplementedError` is gone and `--serve` ran on Windows for the first time; the slice's own third red needed nothing, and the case standing next to it was held by nothing at all.** The green is `CreateMutexW` on `Local\centrion-<sha1 of bot.LOCK's path>`, refusing on `ERROR_ALREADY_EXISTS`. **ctypes and not pywin32, and it is not a taste**: `win32event.CreateMutex` returns a `PyHANDLE` that closes itself when collected, and `serve()` says `procs.Lock(LOCK).take()` keeping no reference — so the pywin32 spelling releases the lock on the line that takes it (measured: drop the handle, create again, no `ERROR_ALREADY_EXISTS`). **Red, and the entry's third test was already covered**: W1b's `test_serve_takes_the_lock_before_it_touches_telegram` catches all three ways that line goes wrong — no lock (1 test), the answer ignored (1), `return 1` (1) — each by itself and by nothing else in 623. What nothing held was **the grant**: `if not procs.Lock(LOCK).take() or True`, a listener that refuses itself and exits 0 every ten seconds forever, was caught by **0 of 623**, so the portable test this slice ships is `test_serve_runs_the_listener_when_the_lock_is_granted` rather than the `test_serve_exits_zero_when_lock_refused` §9 named. **Mutation of the green, four ways:** a refusal that leaks its handle — the refused copy would lock out its own successor — caught by `test_lock_released_on_owner_death` alone; one name for every checkout, by `test_second_lock_refused` alone; never refusing, by both; and **dropping `_HELD.append` is caught by 0 of 624 and cannot be caught**, because a raw ctypes `HANDLE` is an integer no collector will close — the list is the lifetime written down rather than left to that, and the comment now says so instead of claiming work it does not do. **Run step, `scratch\w4d_handrun.py`, and it is the first `bot.py --serve` this platform has ever run.** A starts and logs `centrion listening · 1 allowed chat(s) · root ~\Projects\tomtomtomdev · max_sessions 2 · offset None`, then the placeholder token's 401s with backoff — nothing else on that path was hiding a second stub. **B is refused in 0.11s with status 0**, its only output the refusal line, before `deleteWebhook` and before any `getUpdates`: §7's mutual 409 never gets the chance. A is then ended by `CTRL_BREAK_EVENT` with **no handler, no `finally`, no unwinding** — status **3221225786**, `STATUS_CONTROL_C_EXIT`, where W4c saw 130 because *its* scratch listener installed a `SIGBREAK` handler that `bot.py` does not have — and **the lock was taken again 0.100s later, on the first probe**, which is §3's "no stale lock to clear" demonstrated at the worst end rather than argued. A fourth `--serve` then starts normally. **One test bug, found the slow way and worth the line**: `assertEqual(x, y, "…%s" % holder.stderr.read())` evaluates its message whether or not it fails, and reading a live child's stderr waits for the child to exit — sixty seconds, after which the holder was gone, its mutex released, and the test failed saying "another listener was let in" about a process that no longer existed. The mechanism was right the whole time; the assertion was describing the aftermath of its own message. **Amended**: §3's Lock row said "sha1 of config path" where the seam had long since taken `bot.LOCK` (§6 and `bot.py` both say so); §6 gains the ctypes reason, the close-on-refusal rule and the two figures; §9's W4d records the covered red and §9's W4e that it is unblocked, with the two practicalities (no control-event handler in the listener, and a `.telegram.json` still holding W3g's placeholder token). `_later()` is deleted with its last caller. +3 tests (2 Windows real-process, 1 portable), **no skip count change here** — both new Windows ones run on this box. Two consecutive full runs, 36.9s and 36.8s against W4c's 42.3s; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4e end to end from the phone | todo | | | | checklist: link ≤45s · ls · stop · new+trust (W3h: no dialog appears under `projects_root` on this box — check whether that still holds, do not assume `Trust` ran) · cap · failed tail |
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
| W3c | `/usr/bin/python3 -m compileall -q .` | clean — 3.9. The only executable change in the slice is a comment in `bot.py`, but the two new test classes are compiled there too. |
| W3c | `/usr/bin/python3 -m unittest -q` | green, and **eight newly skipped, in two files**: `TestTheCommandLineTheChildParsesBack` (4) is `skipUnless(WIN)` and `TestSpawningForRealOnWindows` (4) is `skipIf(POSIX)`. Every one of the eight is the Windows half of a pair whose Mac half already exists and must still *run* — `TestSpawningForReal`'s four in particular. A skip in `TestSpawningForReal` means the new class was written over the old one rather than beside it. The count is W3b's plus 8. |
| W3c | `TestSpawningForReal::test_a_prompt_is_one_argument_and_never_a_command_line`, watched | unchanged, and it is the load-bearing one here: the Mac keeps the `;`/backtick/`$()` hostile prompt and the Windows twin keeps the `&`/`|`/`>`/`%VAR%` one, because the two platforms are dangerous in different alphabets. If the Mac's version has quietly acquired Windows characters, the twin was made by copying rather than by writing. |
| W3c | nothing to hand-run | `bot.py`'s changed comment is the whole of the Mac-visible change, and it changes no behaviour there. Named here so the row is not mistaken for an omission. |
| W3d | `/usr/bin/python3 -m compileall -q .` | clean — 3.9. `session_posix.Terminal` is a plain class and `Runner.absorb` a plain method, but neither has been near a 3.9 interpreter. |
| W3d | `/usr/bin/python3 -m unittest -q` | green, and **this is the first slice since W1a where the Mac run is the only thing that tests the code that changed**. `session_posix.spawn` returns a `Terminal` and the Mac's read path goes through it; Windows exercises none of that. The count is W3c's plus 20, of which the Mac runs the 12 portable ones and the 6 in `TestThePosixTerminal`, and skips the 3 in `tests/test_session_win.py::TestThePumpOverAConpty`. **`TestThePosixTerminal` must run, all six** — it is the new code's only test anywhere, and a skip there means `posix_only` landed on the wrong class. |
| W3d | `TestTheTerminalSize`, watched | all eight still pass, and that is the regression guard for the return-type change: they call `session.spawn` and now hold a `Terminal` instead of an fd, with `drain()` rewritten onto `read`/`alive`. They are also the only place the real pty's EIO meets the new `_finished` flag. A failure here is `Terminal`, not the pty — the pty half of each of them was passing before this slice. |
| W3d | `test_closing_the_pty_hangs_up_the_child_once_it_owns_the_terminal`, watched | SIGHUP, as before. It now closes through `Terminal.close()` rather than `os.close(master)`, and `TestThePosixTerminal::test_closing_it_hangs_up_the_child` makes the same claim about the same call — if one passes and the other does not, `close()` is not closing the fd it thinks it is. |
| W3d | `python3 session.py --foreground --cwd <project> --name w3d`, then Ctrl-C | a link, and `meta.json` at `ended`. The whole point of the hand-run this time is the **exit**: `run()`'s `finally` now calls `terminal.close()` where it called `os.close(master)`, still last, after `terminate(pid)`. What must not appear is a session that ends without `ended` being written, or a runner that does not come back from `pump` at all — the loop's only way out on the Mac is now `alive()` going false off the `_finished` flag, where it used to be a `break` inside the read. |
| W3d | the same run, timed | the link should arrive no later than it did before W3a's hand-run. `read(TICK)` waits exactly as the old `select(…, TICK)` did, so there is nothing here that should have slowed down; if it has, the `max(timeout, 0.0)` or the poll ordering is wrong in a way Windows cannot show, because Windows never had a `select` to compare against. |
| W3e | `.venv\Scripts\python -m compileall -q .` | clean. Nothing here is 3.10+, but the Mac is the only interpreter that can say so and it has not run. |
| W3e | the runner-death measurement, twice | The plan said "none beyond the tests", and this is the one that earned its place: a stub runner holding a real ConPTY and a real job, hard-killed with `psutil.Process.kill()`. **Without `KILL_ON_JOB_CLOSE`:** runner gone, claude gone within 0.5s, grandchild alive at +5s. **With it:** all three gone by +0.5s. The first half is why the flag was thought unnecessary and the second is why it is not, and neither was knowable from the API docs. Kept as a test. |
| W3e | `.venv\Scripts\python -m unittest -q` | **572 ran: 497 pass, 74 skip, 1 xfail**, 31.8s. Twelve more than W3d and no change to the skip count — every new test runs on this platform. The Mac's number is W3d's until somebody runs it. |
| W3e | not run on the Mac | `session.py` and `session_posix.py` both changed: `terminate` takes a fourth argument there too and ignores it. The portable `test_terminate_forwards_the_terminal_the_session_was_read_through` and `test_the_terminal_is_handed_to_terminate_and_closed_after_it` are what the Mac run has to answer, plus the hand-run W3d already asks for. Recorded as debt, like every slice since W1a. |
| W3f | `/usr/bin/python3 -m compileall -q .` | clean — 3.9. Nothing in the slice reaches past it (a `for` over a dict, a `getattr` with a default), and the Mac is still the only interpreter that can say so. |
| W3f | `/usr/bin/python3 -m unittest -q` | green, and **seven newly skipped against seven newly run**. `TestTheStopMarker` (7) is portable and must run there — it is the whole of the loop half of this slice, and the marker exists on the Mac precisely so that it does. `TestTheStopMarkerOnWindows` (3) and `TestTheSignalsThatAreLeftOnWindows` (4) are `skipUnless(WIN)`. The count is W3e's plus 14. |
| W3f | `TestThePlatformSeam::test_the_posix_no_ops_answer_as_the_mac_needs`, watched | it now asserts the **opposite** of what W1a wrote there: `request_stop` makes the marker and `stop_requested` finds it. That line is the only Mac-side assertion this slice inverts, and it is the one to read if the Mac run is not green. |
| W3f | `python3 session.py --foreground --cwd <project> --name w3f`, then `touch var/sessions/<sid>/stop` from another terminal | the session ends within a tick and `meta.json` says `ended` — the Mac's first stop that is not a signal. Then the same run again ended with Ctrl-C, which must still work: `session_posix.catch_signals` is untouched and `SIGTERM`/`SIGHUP` are still caught there. |
| W3f | nothing changed in `bot.py` | the Mac's `Sessions.stop` still sends `SIGTERM` and never writes a marker; W4b is where the listener learns to ask the other way. Named here so the row is not mistaken for an omission — the marker is reachable on the Mac today only by hand. |
| W3g | `/usr/bin/python3 -m compileall -q .` | clean — 3.9. `session_posix.child_env` is today's `env.update` moved behind the seam and nothing in it is new syntax, but the Mac is still the only interpreter that can say so. |
| W3g | `/usr/bin/python3 -m unittest -q` | green, and **three newly skipped against three newly run**. The three Windows twins in `TestTheChildEnvironment` are `skipUnless(not POSIX)`; what must *run* there is `test_the_mac_shell_variables_are_set` (the `TERM`/`LANG`/`PATH` assertions lifted out of `test_the_required_variables_are_set`, which is now the portable half) and `TestThePlatformSeam::test_child_env_is_filtered_here_and_finished_by_the_platform`. The count is W3f's plus 6. |
| W3g | `TestTheChildEnvironment`, watched | **this is the one that matters on the Mac**: `child_env` is the only function in the program whose body moved *out* of `session.py` in this slice, and the Mac's half of it is the `PATH` the version-pinned `claude` depends on (§9.8). Every one of the class's ten must pass there unchanged. If `test_claude_is_first_on_the_path` fails, the move dropped a line. |
| W3g | `python3 session.py --foreground --cwd <project> --name w3g`, then Ctrl-C | a link, `pty.log`, `meta.json` at `ended`. Same run W3d and W3f already ask for, and this time the thing to read is the **time to the link**: Windows measures 6.7s of which 4.5s is `Scrape` holding a complete URL for one more byte (W3i). The Mac's number is the control — if it is also seconds rather than milliseconds, W3i is not a Windows fix at all and its red test belongs on both boxes before the green. |
| W3h | `/usr/bin/python3 -m unittest -q` | green, and **one expected failure fewer**: `test_the_answer_does_not_depend_on_chunking` was an `expectedFailure` on both platforms and is now an ordinary test in both `TestTheWindowsTrustDialog` (fixture-gated, so it runs there too) and `TestTheTrustDialog`. The count is W3g's plus 1, with `expectedFailures=0` — if the Mac still reports one, the decorator was removed on a class the Mac skips. |
| W3h | `TestTheTrustDialog`, whole class | **the portable half of the slice is the whole of it**: `Stripper` is in `session.py`, so the Mac's `Trust` and `Scrape` changed too, and this class is where a carry that eats a character instead of holding it would show. `test_it_does_not_hold_the_whole_session_in_memory` is the one to watch — the carry is a second buffer and `CARRY_LIMIT` is the only thing bounding it. |
| W3h | a fresh empty directory, `python3 session.py --foreground --cwd <it> --name w3h --trust` | **does the Mac still get the dialog at all?** Windows stopped raising it under `projects_root` (§4), which would be a property of Claude Code rather than of the platform — so the Mac is the control, and the answer decides whether W4e's `new+trust` check can be observed anywhere. A link with `Trust` never leaving `waiting` is the no. |
| W3i | `/usr/bin/python3 -m compileall -q .` | clean — 3.9. `Scrape.idle` and `Runner.idle`/`went_live` are plain methods and nothing in them is new syntax, but the Mac is still the only interpreter that can say so. |
| W3i | `/usr/bin/python3 -m unittest -q` | green, and **two newly skipped against ten newly run**. `TestTheLinkAtTheEndOfTheWindowsCapture` (2) is gated on `rc_startup_win.log`, which is not on that box; everything else in `TestALinkAtTheVeryEndOfTheOutput` (6) and the four new ones in `TestPumpOverATerminal` are portable and must *run* there — this is `Scrape`, the most portable code in the program. The count is W3h's plus 12. |
| W3i | `TestALinkAtTheVeryEndOfTheOutput`, watched | **the Mac is the control for the claim that it has never needed this.** `test_a_link_at_the_very_end_of_the_output_is_not_held_forever` feeds the *Mac* fixture truncated at the URL, so if `idle()` answers something other than `CAPTURED` there the shared code is wrong and not just unexercised. |
| W3i | `python3 session.py --foreground --cwd <project> --name w3i`, then Ctrl-C | a link, `pty.log`, `meta.json` at `ended` — and **the time to link is the thing to read**: this box now says 2.3s with the hold absent. The Mac's renderer keeps drawing, so its number should be unchanged by this slice in either direction; a Mac that got *faster* would mean it had been taking the hold all along, which §9's W3i says it never does. |
| W3i | nothing else changed on the Mac | `session.py` only, and `absorb`'s body moved into `went_live` without changing what it does. Named here so the row is not mistaken for an omission. |
| W4a | `/usr/bin/python3 -m unittest -q` | green, and **seven newly skipped against two newly run**. `TestWhetherARunnerIsStillThereOnWindows` (7) is `skipUnless(WIN)` — real processes and psutil. The two portable ones in `TestTheListenerUsesThePlatformSeam` must *run* there and pass: `test_a_record_with_no_start_time_is_trusted_to_the_pid_alone` and `test_a_pid_the_platform_cannot_date_is_left_alive`. The count is W3i's plus 9. |
| W4a | `TestWhetherARunnerIsStillThere`, whole class | **unchanged, and that is the claim**: no posix code moved in this slice, so its eight must pass exactly as before. The two new portable tests are twins of two of them, so a Mac failure in the pair is the twin being wrong about a branch the Mac has always exercised — read `test_a_record_with_no_start_time_falls_back_to_the_pid_alone` beside it. |
| W4a | `python3 -c "import session, os; print(session.procs.started(1))"` | **the control for the 0.0 finding.** Windows answers `0.0` — the epoch — for pids 0 and 4, which makes a corrupt record naming pid 4 read as live. The Mac's pid 1 should answer launchd's real start time, i.e. approximately boot and nowhere near zero. If it is 0.0 there too, the property is shared for one reason rather than two and §6's paragraph should say so instead of calling the Mac's route honest. |
| W4a | nothing to hand-run | `session_win.py` and two test files are the whole change; no posix or shared code moved. Named here so the row is not mistaken for an omission. |
| W4b | `/usr/bin/python3 -m unittest -q` | green, and **one newly skipped against five newly run**. `TestStoppingARealRunnerOnWindows` (1) is `skipIf(POSIX)`. The five in `TestTheListenerUsesThePlatformSeam` are portable and must *run* there — they are the whole of the slice's logic, and `test_stop_asks_through_the_marker_before_it_reaches_for_a_kill` is the one that asserts the Mac now asks with a file too. The count is W4a's plus 5, skips plus 1 (76). |
| W4b | **`python3 bot.py` with a real session, stopped from the phone** | **the one run step this slice cannot take here, and the one that matters.** `Sessions.stop` is shared code and the Mac's `SIGTERM` is now the *fallback* rather than the first act. An ordinary `stop` must still come back in about the time it did before — the runner hears the marker within a `TICK` either way — and the thing to watch for is a stop that takes fifteen seconds, which means `pump` is not honouring the marker there and every stop is now paying `STOP_GRACE` before the signal it used to get at once. W3f's hand-run says the marker works on the Mac; this is the listener half of the same claim. |
| W4b | `TestStopping` and `TestTheFleetIsListed`, watched | unchanged, and that is the claim: `halt` and the listing did not move, and their fake `Sessions.stop` never reaches the new code. A failure there is the fake's signature drifting from the real one. |
| W4c | `/usr/bin/python3 -m unittest -q` | green, and **two newly skipped against nothing newly run**. Both additions are in `TestADetachedChildOutlivesItsParent`, which is `skipUnless(WIN)`. The count is W4b's plus 2, skips plus 2 (78). The third change is a rewrite of `test_request_stop_is_atomic_and_idempotent`, which is in `test_session_win.py` and skips there already. |
| W4c | **`import session_win` at the top of `test_procs_win.py`** | **the one line in this slice that can break the Mac**, and it is a module-level import in a file whose classes all skip there. `session_win`'s top level is stdlib-only and the win32 calls are inside their functions, so it should import on 3.9 as it does on 3.12 — but nothing had ever imported it *on the Mac* before this slice, and a collection error in that file would look like the whole file vanishing rather than like a failure. If it errors, the fix is to move the import inside the WIN guard and take `DETACH_FLAGS` from it there. |
| W4c | `/usr/bin/python3 -m compileall -q .` | clean — 3.9. Only test files changed and nothing in them is new syntax, but the Mac is the only interpreter that can say so. |
| W4c | nothing to hand-run | no posix, shared or program code moved — three test changes and nothing else. The detach flags are Windows-only by construction (`session_posix.spawn_flags()` returns `{}`, and `test_session.py` has always asserted that). Named here so the row is not mistaken for an omission. |
| W4d | `/usr/bin/python3 -m unittest -q` | green, and **two newly skipped against one newly run**. `TestTheSingleInstanceMutex` (2) is `skipUnless(WIN)` — real processes and a named mutex. The one that must *run* there is `test_bot.py::test_serve_runs_the_listener_when_the_lock_is_granted`, and on the Mac it is the assertion that `session_posix.Lock.take()`'s `return True` still lets `serve()` through to `Telegram` and `Listener.run`. The count is W4c's plus 3, skips plus 2 (80). |
| W4d | `/usr/bin/python3 -m compileall -q .` | clean — 3.9. `session_win.py` is the only program file that changed and none of it is new syntax, but `test_procs_win.py` imports that module at its top level on both platforms (W4c), so 3.9 is the only interpreter that can say the `hashlib`/ctypes additions parse there. |
| W4d | `sh launchd/bot.sh`, then a second `sh launchd/bot.sh` | **unchanged, and that is the claim.** No posix or shared program code moved in this slice: the Mac's lock is still `bot.sh`'s `lockf` and `session_posix.Lock.take()` is still `return True`. The second copy must still be refused *by the shell*, not by python, with bot.sh's own message. A refusal that now comes from python would mean the Windows `Lock` had been wired in on the wrong platform. |
| W4b | a runner deliberately wedged outside `pump` | **the cost of the slice, if anyone wants to measure it.** Start a session, `kill -STOP` the runner, then `stop` it from the phone: this used to be `SIGTERM` immediately and `SIGKILL` at `STOP_GRACE`; it is now fifteen seconds of marker-polling *before* the `SIGTERM`. Nothing is broken by it — the session still ends — but the number is the honest price of one `stop` meaning one thing on both platforms, and §6 says it is paid on purpose. |

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
- **2026-09-17, W3c → §6, `bot.py`.** The plan counted one place where an argv is flattened
  into a command line on Windows, and there are two. §4 had `spawn`; nobody wrote down that
  `Sessions.start`'s `Popen` does the same thing, because on the Mac it does not — `execve`
  takes the list as a list. `bot.py` said so in as many words at the spawn: "there are no
  quoting rules to get wrong when there is nothing to quote for", which is a true sentence
  about `execve` and a false one about `CreateProcess`. Nothing was broken and nothing changed
  in the code — `list2cmdline` is what `Popen` already uses — but the hop had no test on this
  platform, and the comment was documentation pointing the wrong way. Both fixed. The general
  shape, and the third time this port has hit it: a claim that is true of the Mac's *mechanism*
  reads as a claim about the design, and survives the port unexamined because it is still a
  true-sounding sentence.
- **2026-09-17, W3c → §9 ritual.** Two slices now (W3a's `rotate`, W3c's quoting) have had
  tests that passed before any code changed, which the ritual's step 1 says is the mark of the
  wrong tests. It is not, when the slice's own text predicts it — but it does mean the tests
  have proved nothing yet, so both were checked by mutation instead, and that is the rule from
  here: **a slice whose tests are green at step 1 does not skip to step 3; it breaks the thing
  under test on purpose and shows the tests catching it.** W3c's mutation is
  `subprocess.list2cmdline = " ".join`, and it fails six of the eight. The two survivors are
  named in §11's row, because "which tests did *not* fail" is the part that says whether the
  mutation was aimed at the right thing.
- **2026-09-19, W3e → §3, §4.** The Ctrl-C is a **keystroke**, not a signal. §3's table said
  ConPTY turns a 0x03 on its input into a `CTRL_C_EVENT` for the attached console; nothing does.
  It is handed to whatever is *reading* the console, like any other key, so it reaches claude and
  reaches nothing else — `cmd`, `ping` and a python process in `time.sleep` were each sent two
  and each carried on. W0b's 1.71s was never wrong; it was a measurement of claude, and it got
  read as a measurement of the mechanism. The consequence is not in `terminate`'s steps but in
  what it *waits on*: the job being empty, never the pid, because the ordinary session — claude
  takes the interrupt and exits 0, the dev server it started does not — is exactly the shape a
  pid-shaped wait reports as finished.
- **2026-09-19, W3e → §3, §6.** `KILL_ON_JOB_CLOSE` was dropped from `_create_job` and then put
  back, and the round trip is the useful part. The reason for dropping it — a dropped handle
  should not end a session by accident — sounds like a safety argument and is not one, because
  the runner's death already ends the session by a different route: the pseudoconsole closes
  with it and claude is gone in under half a second (measured; the same thing W3b saw from
  `Terminal.close()`). So there was no accident to prevent, and what the missing flag actually
  bought was a detached grandchild still running at +5s with nothing left anywhere that could
  reach it. **The rule: before defending a mechanism against a hazard, measure whether the
  hazard is already unavoidable by another path** — if it is, the defence is not a trade-off,
  it is just the cost. Both directions were measured before a line changed, and the pair is
  kept as `test_a_runner_that_dies_without_warning_leaves_nothing_behind`.
- **2026-09-19, W3e → §9 ritual.** Fourth instance of the port's recurring shape, and the first
  where the sentence was *true*. §3 said the job gives "kill the runner and claude dies with it"
  for free. It does. But the stated reason was the job handle closing, and the real reason is
  the pseudoconsole — so the sentence was checkable, checked out, and still hid a hole, because
  a correct outcome with the wrong mechanism behind it makes everything downstream of the
  mechanism look settled. The three earlier instances (W3b's argv, W3c's quoting, W3d's `alive`)
  were all sentences that were *false* off the Mac. This one was not, and it was the more
  expensive kind.
- **2026-09-16, W2b → §9 ritual, §3.** `requirements-win.txt` exists, with `pywin32==312` and
  the `pywinpty==3.0.5` that has been in `.venv` since W0a. The Windows suite is run from the
  venv from here (`.venv\Scripts\python -m unittest -q`); under the system interpreter
  everything still imports — `win32security` is imported inside the check, not at module
  level — but no config file loads, 41 tests fail, and that refusal is the correct answer to
  "I cannot read the permissions" rather than a bug to route around.
- **2026-09-19, W3h → §4, W4e.** Two of them, from a run the slice said it did not need.
  The carry bug is real, the chunk sizes it needs are real — ConPTY's median read is 21 bytes
  — and the live path was still never broken, because 0 of 26 real chunks ended inside an
  escape sequence. **A measurement of the input's *size* is not a measurement of where it is
  cut**, and the size was the one the plan had written down. The second: §9.3's dialog no
  longer appears for a new directory under `projects_root`, only outside it, so the mechanism
  `Trust` exists for is currently unreachable on the path that uses it — found only because
  the run step was taken on a slice marked "Run: none", which is the argument for the ritual's
  step 4 having no exemption.
- **2026-09-19, W4a → §6, §9's W4a.** Two corrections and they point opposite ways. §6 said
  `process_started` answers `None` on `NoSuchProcess` or `AccessDenied`; **`AccessDenied` never
  happens** — 0 of 208 processes, including the 109 whose owner psutil cannot read — so the
  branch §4 built for it is reachable only by a pid that dies mid-check, and the case §6 did
  not predict is the one that bites: pids 0 and 4 answer **`0.0`**, a real timestamp older
  than every record, so a corrupt record naming pid 4 reads as a live runner. Left alone,
  because the Mac has the same property for pid 1 by a route that is not a bug. And §9's W4a
  asked to "un-gate" two posix classes whose mechanisms are `/bin/sleep`, EPERM, fd 9 and
  `getsid`: a platform test is not a gate, the shape that works is the twin beside it that W3c
  already built, and what the entry was really pointing at was two untested `Sessions.alive`
  branches — which is a smaller and more useful slice than the one it described.
- **2026-09-19, W4b → §6, §9's W4b, W4c and W4d.** §6 worried, twice, about what the fallback
  kill leaves behind — "without the flag this line would have left a dev server running". It
  leaves nothing: killing the runner outright took the ConPTY's child *and* the `ping` under
  it in the same A/B that measured the marker path, so W3e's job flag covers the hard kill as
  well as the accidental death it was measured against. **What the hard kill actually loses is
  the ending** — the two Ctrl-Cs claude exits 0 on, and a `meta.json` left saying `live` by the
  process that knew better — and that is what this slice buys, at 5.70s against 0.00s on a
  child that ignores the polite byte. The same paragraph now also records what the portable
  ask costs the Mac: `SIGTERM` is the fallback rather than the first act, so a runner outside
  `pump` waits `STOP_GRACE` before anything it can hear arrives. And W4c and W4d each carry a
  note that a red test they name shipped in **W1b** — for W4c, so did the green, which leaves
  that slice with nothing but its run step.

- **2026-09-20, W4c → §9's W4c and W4d, §11's baseline row, and the ritual's step 1.** The
  entry promised a slice with nothing left but a run step, on the grounds that its red and
  green were both on disk. The green was; the portable red was; **the Windows red was a test
  reading a copy of the constant it was checking**, and `test_procs_win.py` had opened that
  way since W0c, so every real-process survival result in that file was also evidence about
  the wrong value. Nothing here was broken and the run step confirms it — which is exactly
  why this needed a mutation to find, and why *"already on disk" is a claim about existence
  and step 1 wants a claim about failure*. The ritual already says to mutate when the tests
  pass before the code; what W4c adds is that the same applies when the tests were **inherited
  from an earlier slice**, which is the case W4a, W4b and W4c have now all hit in a row. Also
  recorded: `bot.py --serve` has never run on Windows and cannot until W4d, because the mutex
  is `serve()`'s first statement — so W4d is a dependency of W4e and not merely the slice
  before it. And §11's baseline row was dated a day after its commit (`7a09f8d`, 2026-09-13);
  corrected.
- **2026-09-20, W4d → §3's Lock row, §6, §9's W4d.** Three corrections from one slice. §9 offered
  the mutex as "`pywin32` or `ctypes`", as if it were a spelling: **pywin32 cannot hold this
  lock**, because its `PyHANDLE` closes on collection and `serve()` keeps no reference to the
  `Lock` — the mutex would be released on the line that takes it, which was measured rather
  than reasoned about. §3's Lock row named the mutex after "the config path" where `bot.py`
  and §6 had long since agreed it is named after `bot.LOCK`; the row is now what the code
  does. And the entry's third red test was already on disk since W1b and covered — while the
  *grant* of the lock, which nothing named, was asserted by 0 of 623 tests. Every slice in W4
  has now found its stated red partly done and something beside it undone, which is four in a
  row: the step that finds it is the mutation, not the reading.
- **2026-09-19, W3i → §9's W3i.** The hold was measured once, in W3g, and sized as a standing cost: 4.5 of 6.7 seconds. It is a rare frame, not a property. Eight sessions here — four with the pre-slice code — hold for **0.00s**, because the read that completes the URL carries 404 more characters after it every time, and the acceptance run came down to 2.3s **without the fix contributing any of it**. The fix is still right and its own measurement says so under the condition provoked: 5.00s of hold becomes 0.20s. **Two slices running, a number taken once has been read as a constant** (W3h was the other), and both times the correction cost a run step rather than a rewrite — so the rule the ritual is missing is not "take the run step", which it already says, but *take it twice, or say in the row that you did not*.
