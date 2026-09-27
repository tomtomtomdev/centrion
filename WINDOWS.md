# centrion on Windows — port plan

The bot as it stands is a macOS program. `bot.py` and `session.py` both fail on this box at
import time with `ModuleNotFoundError: No module named 'fcntl'`, and that is the shallowest of
the problems: every process and terminal mechanism SPEC.md leans on — `openpty`, `fork`,
`setsid`, `TIOCSCTTY`, `killpg`, `SIGTERM`, `select` on an fd, `lockf` on fd 9, launchd — has no
Windows equivalent. What *is* portable is the shape: three processes, files as the only
protocol, a runner that outlives its launcher, a scraper that finds one URL in a terminal
stream. This document is the plan for keeping that shape and replacing the mechanisms under it.

Status: **The port is done (2026-09-27). W0 through W7 are all taken; W5d is `skipped` —
refused, not deferred, and it is the only one. This document stopped being a plan at W6 and
is now two other things: the record of why the bot on this box is shaped the way it is, which
§4 through §7 are, and the place two kinds of unfinished business are kept honest.** There is
no open slice in §9. The two are §11's time items, which happen rather than get done, and the
second half of §11's "Pending on the Mac" — which W7 cut from 97 rows to 24 by answering the
rest on a CI runner, and which is still real in every row it kept: the hand-runs, the phone,
launchd, a real `~/Projects`, an installed Claude Code and a working token are owed on the
user's own machine, and a runner stands in for none of them. Nothing in §9 should be started
without reading the ritual first.
W6 built the CI matrix and its finding is that the risk was on the wrong side: the two
`macos-latest` jobs passed on the first push and the `windows-latest` job failed four tests,
every one of them a test this desk cannot run — ten symlink cases the account has no
privilege for, a `realpath` premise that is only true where `%TEMP%` is an 8.3 alias, and two
assertions whose truth was this machine's speed and this machine's installed software. The
best single fact in the slice is that `/usr/bin/python3` on a GitHub macOS runner is
**3.9.6**, the interpreter the whole Mac guard is written against, so the ritual's step 3 is
answered by a push from now on. The worst is that §11's Mac skip predictions have been ten
out since W1a, because each row added to *this* box's count. **W7 measured how wrong the
method was rather than how wrong the number was: the desk skips 98 tests, the Mac skips 90,
and the two sets share exactly one.** They were never addable, and no arithmetic on the
Windows number could have produced the Mac's.
W5d went to try the not-logged-on case and could not register the task to do it, for two
reasons that are about this account rather than about Windows: it has **no password**, so it
has no batch logon at all (`schtasks` answers ERROR_ACCOUNT_RESTRICTION, and `LogonUser` with
an empty credential answers **1327 and never 1326** for `BATCH`, `SERVICE`, `NETWORK` and
`INTERACTIVE` alike, which is what proves the blankness rather than a wrong guess), and both
`S4U` and a `<BootTrigger>` are "Access is denied" unelevated. The finding worth keeping is
the third one, which cost nothing to find and would have cost a wrong conclusion: **the
recipe in §9's W5d was wrong.** `centrion.xml`'s only trigger is a `LogonTrigger`, so
"switch the logon type, reboot, do not log in" leaves nothing to start the task — the run
would have measured a bot that was never triggered. The logon type and the trigger are one
decision in two elements and `test_the_logon_type_and_the_trigger_agree_about_when_the_bot_runs`
now holds the pair; it is the only test in the repository that notices half the move, and 39
of `test_layout`'s 40 pass with a `BootTrigger` bolted on under `InteractiveToken`. What was
measurable is the control this document lacked: under `/Run` all three of the task's
processes report **session 11**, this desk's interactive logon session, so "the bot exists
only while this user is logged on" is a process fact and not a policy note. Claude Code's
login is a plain 509-byte JSON file in `%USERPROFILE%\.claude`, which supports half of §7's
optimism; **ConPTY in a non-interactive session is untested and untestable from here**, and
§11 carries it beside the logoff figure W5b could not take. The registration is unchanged —
`InteractiveToken`, one `LogonTrigger`, `<Priority>5</Priority>`, `Ready` — because nothing
was switched, so nothing needed reverting.
W5e took the decision W5c left it and gave the runner a log of its own. `bot.cmd` opens
`var\bot.log` with a `cmd` redirection, which denies other writers; the listener inherits
that handle and used to hand a duplicate to every runner, so a live session pinned the log
for its whole life and the `KeepAlive` loop could not restart a listener that died while a
session was running. The two fixes on the table were "the runner logs somewhere else" and "a
writer process behind a pipe in `bot.cmd`", and the pipe loses on measurements rather than
taste: it costs a process and a file per iteration of the loop whose job is reliability, it
puts a new single point of failure in front of the only log there is, and **a pipeline's
`%errorlevel%` is its last command's** — so `=== listener exited N ===`, one of the two lines
§7's log is made of, would have reported the writer. So `procs.runner_output` yields `{}` on
POSIX, where an inherited append fd costs nothing, and `var\sessions\<sid>\runner.log` here;
SPEC.md §14's first diagnostic is two files on Windows and `bot.cmd`'s header says so.
Measured against W5c's 5m10s, same scenario under the registered task: listener killed with a
session live, exit line in the log **0.02s** later, restart line **10.03s** after that,
listening 0.11s after that, the session untouched and adopted by the new listener at the same
runner pid. What is left is the `/End` orphan, bounded rather than removed: the `/Run` after
a `/End` still cannot open the log, but it now throttles at **0.00s of CPU** and takes the
log 4.67s after the orphan dies — and an orphaned listener is still answering the phone, so
that state is a bot that is up. The `||` half of W5e's entry is decided *against*: an
unredirected listener under the scheduler is up and permanently blind and holds the mutex, so
the loop never replaces it.
W5c, before this, took the restart-survival checklist against the registered task and two of its four items
came back as §7 says: `schtasks /End` kills the `cmd.exe` and **nothing else** — the runner,
the `claude.exe` under it and the orphaned listener all live on, and that listener went on
answering `ls` while `schtasks` reported the task `Ready` — and a listener restarted against
a session it never spawned adopted the record, rendered it at the **same runner pid**, and
stopped it in 2.3s. The third item could not be taken against the task at all. **`schtasks
/Run` starts a `bot.cmd` that cannot open `var\bot.log` while anything else holds it, and
instead of failing it spins**: `cmd` opens a redirection target denying other writers, a
failed redirection leaves `ERRORLEVEL` at 0 so `if errorlevel 1 ping` never fires, and the
`timeout` it guards was itself skipped because that line redirected to the same log — 13% of
a core, no python, no log line, no symptom but a fan. One line of `bot.cmd` fixes the
throttle. Behind it is the finding that is **W5e**: the runner inherits the log handle, so a
live session pins `var\bot.log` and the `KeepAlive` loop cannot restart a listener that died
while a session was running — measured at 5m10s of a wrapper that could do nothing, and
recovery 1.1s after the runner exited.
W5b, before this, wrote `windows\centrion.xml`, taught `install.ps1` to render and register
it, and found
two things this document had wrong. Task Scheduler defaults a task to priority 7, which is
`BELOW_NORMAL_PRIORITY_CLASS` and is inherited by every session the listener spawns — the
plist's loudest warning, `ProcessType Standard, NOT Background`, with a different name and no
row in §7's table until now; measured, the whole tree runs `BelowNormal` without a
`<Priority>` and `Normal` with one. And `windows\install.ps1` **as W5a committed it did not
parse at all**: nine errors from `powershell -File`, because Windows PowerShell 5.1 reads a
BOM-less `.ps1` as cp1252 and a UTF-8 em dash terminates a string there — a run step measures
the bytes that were run, and the commit is a different set of bytes unless something checks.
Everything under `windows\` is now ASCII and a portable test holds it. The task is registered
and works: `schtasks /Run` to `centrion listening` in **0.37s** and to the first poll in
**0.92s**, the whole tree at `Normal`, the KeepAlive loop restarting a killed listener 9.36s
later — and the console question W5a left open is answered *yes*, the action has one, so
`timeout` is what throttles under the scheduler and the `ping` fallback never fires.
**The logon leg of "logon to first poll" could not be measured from this desk**, because
logging off ends the session driving the slice; §11 carries it as a time item with the one
elevated command it needs. W5a, before this, found §7's restart throttle silently did nothing
without a console and put a `ping` fallback behind it.
**What is still owed is the literal run from a phone**: `.telegram.json` here holds W3g's
placeholder token and every Telegram call answers `HTTP 401 Unauthorized`, so no phone can
reach this listener until a real token is put in that file — a user's decision, not a slice's,
and §11 carries it as a named debt on both machines rather than as a tick. W4e, before this,
drove the whole acceptance checklist against the shipped `serve()` and five of its six items
behaved exactly as specified; the sixth found that W3h's trust finding is half wrong (§4), and
the run found a double reply in shared code that §4.6 forbids and nothing tested (§6, fixed,
three portable tests). The suite
runs natively on Windows since W1c: 699
tests, 605 pass, 94 skipped as the Mac's (each skip names its reason or the slice that
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

*W4f, 2026-09-27 — the Mac's `Terminal` has two methods this one does not, and `pump` is still
those four calls.* The merge `5174808` gave `session_posix.Terminal` a `size()` and a
`resize()` for `attach.py`, a macOS-only viewer that connects to a running session over a Unix
socket and resizes the pty to the window it is shown in. Neither is reached from shared code
here: `Runner._serve_viewers` returns `None` on win32 *before* it imports `attach`, and
`went_live` only opens a window when it has a viewer server, so `pump` is unchanged and nothing
on Windows asks for a name that is missing. But **the seam's `Terminal` is asymmetric now**, and
`tests/test_session.py`'s `SURFACE` check would not notice — it asserts module-level names, not
a class's methods. If a Windows viewer is ever wanted, `size`/`resize` on `session_win.Terminal`
(pywinpty's `PTY.set_size`) are what it needs and they are not there. The flag that reaches the
viewer, `session.py --attach <sid>`, is an unguarded `import attach` on this platform and dies
with `ModuleNotFoundError: No module named 'fcntl'`; the import is lazy, so `session.py` itself
still imports and runs exactly as W1a left it, and a Windows `--attach` is a traceback rather
than a refusal. Recorded, not fixed: nothing in the port needs it, and W5 is where a Windows
viewer would be decided.

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

*W4e, 2026-09-27 — re-checked from the listener, and the sentence above that has to go is
"the variable is where the directory is and not what is in it".* Both halves were tested in
one sitting, in the same `projects_root`, minutes apart. **A directory this bot had just
created** — `new w4e-trust-a1`, empty — came straight up to a link in **2.30s** with no
dialog and got its `projects` entry saying `hasTrustDialogAccepted: false` without being
asked, exactly as W3h describes. **An ordinary project already sitting in that root** —
`claude Lab`, real content, no entry in `~\.claude.json` — raised the panel **every time**,
sat on it for the full 45s, and the phone got the timeout reply with the panel as its tail.
Same root, opposite answers, so *where* is not the variable; and `Lab` was left with **no
`projects` entry at all** afterwards, so the entry is what Claude Code writes when it decides
not to ask, not the reason it decides. What the box does agree on is the list: the four
directories under this root that come straight up (`Beacon`, `centrion`, `Peerscape`,
`vgchartzer`) are the four with `hasTrustDialogAccepted: true`, and they are also the four in
`githubRepoPaths`. The precise rule Claude Code applies to a directory it has never seen is
still not known, and W4e is not going to guess it either.

**The consequence is sharper than W3h's, and it is the finding of that slice.** `Trust` is
armed only by `new` *and* an empty directory (§9.3's two halves, the listener's and the
runner's), and that is precisely the case the dialog never appears for. The case it does
appear for — `claude <a project this box has not trusted>` — never arms it. **The mechanism
and the case are disjoint**, so the panel is unanswerable in the shipped program and every
such `claude` costs the phone 45 seconds and a wall of dialog text. `Trust` itself is not the
problem and was measured against the live panel this run captured: fed the 1622 bytes of it
at chunk sizes 1, 7, 64, 512, 4096 and whole, it reaches `asked` and emits Down at every one,
which is what `trust_dialog_win.log`'s test asserts about a capture taken under `%TEMP%` in
W0a. Nothing is changed here: widening `Trust`'s arming to `claude` would be answering a
trust question about somebody else's repository, which is the one thing §9.3 argues it may
never do. It is written down so that the next person to see a 45s timeout looks at the
project's trust record rather than at ConPTY.

*W4e, also — the tail of a panel arrives with its spaces gone, and that is ConPTY.* §4.6
promises the phone the last of the transcript, and for a TUI panel what it gets reads
`Quicksafetycheck:Isthisaprojectyoucreatedoroneyoutrust?`. Claude Code's renderer does not
emit the spaces: it positions each word with `ESC[<n>G` (CHA), so `strip` — which is right to
remove the escape and has nowhere to put a space — leaves the words butted together. An
ordinary error keeps its spacing (W4e's `failed` run got `unknown option --remote-control`
and its usage text intact), because that is plain stdout and not a render. **Not fixed, and
the reason is `Scrape` rather than effort**: turning a CHA into whitespace would put spaces
wherever the renderer happens to break a line, and the one thing on that stream that must
never acquire one is the `session_` URL. `Trust` is unaffected either way — `squeeze` removes
whitespace before it matches, which is why TRUST_QUESTION is spelled without any.

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

*W6, 2026-09-27, and it took a CI runner to see it because this account cannot make a
symlink:* **`realpath` does not canonicalise a link whose target is not there.** For a
dangling reparse point `ntpath` cannot ask the OS, falls back to `_readlink_deep`, and
returns **the spelling stored in the link, verbatim**. A GitHub runner's `%TEMP%` is
`C:\Users\RUNNER~1\...`, an 8.3 alias, so a link stored against that spelling resolves to a
dirname the long-form root does not equal — and `test_a_dangling_symlink_is_refused`, never
executed anywhere until that run, came back refused by **check 3** with the containment
message instead of by check 4 with "no project by that name exists". The boundary is intact
and this is not §10.4 bending: to reach check 4 at all the stored target must already name a
direct child of the resolved root, and a dangling one of those does not exist, so a dangling
link is refused on every path through. What moves is only which sentence the phone gets. Not
corrected in `config.py`, deliberately: making `_child` see through an unresolved reparse
target is a change to the security boundary's one function, and this document does not make
that call inside an optional CI slice — W7 carries it. The test now spells its target through
`realpath(root)`, which is what it always meant.

*W7, 2026-09-27 — **decided: `_child` does not canonicalise it, and the reason is that there
is nothing to repair.*** The choice was between one sentence and another on a phone, and the
evidence points the same way as §5.1's own rule. **What it would cost.** The fix is a second
resolution pass — re-`realpath` the dirname of a path that has *already failed check 3* and
judge it again — placed inside the one function §10.4 is a promise about. A retry after a
refusal is the shape a boundary bug takes, and the string it would retry on is the one
`_readlink_deep` handed back: text out of a reparse point, which check 1 never saw and which
the OS itself declined to resolve. §5.1 inverted the DACL list from a deny-list to an
allow-list on the argument that `config.py` fails closed; "the resolution did not complete,
so refuse" is the closed answer here and "resolve it again, differently" is not. **What it
would buy.** Nothing on the Mac at all — `posixpath.realpath` walks every component that
exists and appends the one that does not, so the same fixture reaches check 4 there without
help — so it is a Windows-only branch in the one function both platforms share, where §5 has
otherwise pushed every platform difference into the *rule* (`splitdrive`) and never into a
second code path. And on Windows it buys the case of a **hand-made broken symlink inside
`projects_root`**, which no listing can produce: `projects()` filters through `resolve()`, so
the name is not in the reply to bare `claude` either way, and the phone user who typed it
gets a refusal plus "send `claude` on its own for the list" on both routes. **And check 3 is
allowed to catch it.** Check 3 is not "you escaped"; it is "this did not resolve to a direct
child of the root", and a name whose resolution could not be completed is exactly a name the
boundary cannot vouch for. Three tests hold the decision in
`tests/test_projects.TestAnUnresolvableReparseTarget` — the portable one says a dangling link
is refused whichever spelling of the root it stores, the Windows one pins check 3, the posix
one pins check 4 — and **none of the three can run at this desk**, so the guard was proved by
breaking `_child` on purpose and pushing it: exactly one test went red, and it named the
substitution ("no project by that name exists" where "root" was wanted). §11's W7 row has the
run.

**5.4 — `max_sessions`.** The default of 2 is annotated "8 GB on this box". This box is a
different box; leave the default and set it in `.telegram.json`.

**5.5 — `terminal_window`, and it is refused here.** *Added by W4f, 2026-09-27, out of the Mac
merge `5174808` rather than out of a Windows slice.* `config.py` has a sixth key:
`terminal_window`, which opens a Warp — or Terminal.app — window onto every session once it is
live (`attach.py`, §4's note). `DEFAULT_TERMINAL_WINDOW` is `sys.platform == "darwin"`, so it
is **off by default here**, and `_window()` raises `ConfigError` for `terminal_window: true`
anywhere but darwin: a Windows config that asks for a window is refused by name rather than
ignored, which is the fail-closed rule §5.1 inverted the DACL list for. Nothing else in this
section moves — the key is in `KNOWN_KEYS` so it does not trip the unknown-key check, and
`Config` gained a slot with a default, which is the only reason a `Config(...)` built by hand
in a test still works. §5.1 through §5.4 are unaffected, and this line is here so the next
reader does not have to re-establish that.

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
**procs.runner_output(dir), creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`.
The `runner_output` half is W5e's and is the second name on this seam that answers
differently on the two platforms for a reason neither side chose; see §7 and W5e. No
`CREATE_BREAKAWAY_FROM_JOB`
and no retry logic: W0c showed the scheduler's job refuses breakaway outright, and showed the
runner does not need it — a child of a scheduled task survives the task being stopped. The
runner therefore stays *inside* the scheduler's job for its life. That is harmless today
(`LimitFlags = 0`, no kill-on-close) and **W5c re-verified it with a real session**: with a
Claude Code session live under a task-started listener, `schtasks /End` took the `cmd.exe`
and left the venv launcher, the listener, the runner launcher, the runner, the
`OpenConsole.exe` and `claude.exe` — six processes — all running. The assumption it re-checks: if a Windows update ever gives that job `KILL_ON_JOB_CLOSE`, every session dies with
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

*W4f, 2026-09-27 — the merge's listener changes are platform-neutral, and this paragraph exists
so the next reader does not have to prove that again.* `5174808` brought SPEC.md slice 13's
project keyboard into `bot.py` (`tappable`, `menu_names`, `keyboard`, `Listener.buttons` and
`Listener.menu`, and `say` taking a markup) and two additions to `telegram.py`
(`send_message`'s `reply_markup`, and `set_commands`, which `Listener.run` calls once after
`delete_webhook`). Every line of it is `config.projects` plus string handling plus one more
field in a JSON body — no process, no path, no handle, nothing this section is about — and
W4f's run step exercised the whole of it on this box (§11). Two things worth naming anyway.
**`tappable` refuses a project whose directory name contains a space**, because the button
`claude My Project` parses back as project `My` with the prompt `Project`; a space in a
directory name is a more ordinary shape on Windows than on the Mac, and the reply says so in
words when it happens — this checkout's root has 32 directories and none of them has one.
And **`ls` carries the keyboard although its text says nothing about projects**, which is
`menu()`'s deliberate choice and the reply the run step read back.

*W4e, 2026-09-27 — the listener sent the link twice, and the marker that was supposed to stop
it was being written and then ignored.* `Listener.waited` claims before it sends — the comment
above the line says so and says why — but it threw `claim()`'s answer away, so a session the
tick had already announced through §9.12's `arrived()` was announced a second time by its own
waiter. `claim()`'s docstring calls itself "the arbitration between §4.6's waiter thread and
§4's tick"; it was an arbitration only one side listened to. The fix is that `waited` returns
when the claim is refused, with a line in the log saying which side won — three lines, all
portable, and **the Mac has the same bug today**. The window is the 0.25s between two polls of
`meta.json`, and on the wire a `getUpdates` returns into it only by luck — call it half a
percent per session, plus every message that arrives while one is coming up. W4e saw it
because its driver's fake long poll returns in 0.2s instead of 50, which turned half a percent
into a coin flip: **two replies on the first `claude` of the run, and again on the first
`new`** — `▶ Beacon · Beacon-ebe5 came up after 2s` followed 28ms later by
`▶ Beacon · Beacon-ebe5 … 1 of 2 sessions`. The cadence is the amplifier and the ignored
return value is the defect, and the same three lines spend the `END_SENT` marker the same way
for a session that ends before its waiter looks. Three tests, all portable
(`TestALateLinkIsStillAnnounced`): the two directions the suite had only one of, plus the
vacuity guard, because a waiter that answered nothing at all would have passed both.

*W4e, and it costs nothing but it is not what the log says —* **every runner on this box is
two processes.** `.venv\Scripts\python.exe` here is a 274 KB `venvlauncher` and not a copy of
the 105 KB base interpreter: it spawns `…\Python312\python.exe` as a child and waits on it. So
`Sessions.start`'s `Popen` holds the *launcher*, and `meta.json`'s `runner_pid` — written by
`session.py` out of its own `os.getpid()` — is the grandchild. Nothing is wrong: `reap()`
polls the direct child, which is the right one to reap, and `alive()`, `stop()` and §4's
reconciliation all read `runner_pid`, which is the right one to ask about. What it does mean
is that §14's `runner pid 2308` and the record's `runner_pid: 8028` name two different
processes on Windows and always will, so a `tasklist` started from the log line finds the
launcher and not the runner. Both die together on `stop` (measured, every session in W4e's
run), and W5a's `install.ps1` will make this the shape under the scheduled task's job too.

*W5e, 2026-09-27 — an inherited handle is a resource on one platform and a lock on the other,
and this seam now has a name for the difference.* Every version of this document has said
that `Sessions.start` inherits stdout and stderr so the runner's lines land in `var/bot.log`
beside the listener's, and on the Mac that sentence costs nothing: `bot.sh` appends with `>>`,
an append `open(2)` denies nobody, and two processes writing one file with `O_APPEND` is the
ordinary arrangement. On Windows the same words describe a lock. `cmd` opens a redirection
target with `FILE_SHARE_READ` alone, the listener inherits *that* handle, and a runner
holding a duplicate of it outlives the listener by design — so `bot.cmd` could not reopen
`var\bot.log` while a session was live, and the `KeepAlive` loop was unavailable for as long
as the session lasted (§7; 5m10s, measured). **`procs.runner_output(directory)`** is where
that now diverges: a context manager yielding `Popen` kwargs, `{}` on POSIX — no file, no
process, no open, `Popen` called exactly as before — and `{"stdout": fh, "stderr": fh}` on a
`var\sessions\<sid>\runner.log` here, closed by the listener the moment `CreateProcess` has
duplicated it. It is a context manager rather than a function because the parent must not
keep the handle: a fix for who holds a file that leaks its own has moved the lock rather
than removed it, and that is one of the four mutations §11's row names.

Three things the shape costs, all of them on this platform only. **SPEC.md §14's first
diagnostic is two files here** — `var\bot.log` still carries the listener's side of a session
(started, live, stopped) and `var\sessions\<sid>\runner.log` carries the runner's four lines
(`spawned pid`, `live:`, `stop requested`, `ended`), which is what W5e's decision traded away
and `windows\bot.cmd`'s header now says in words. **The listener creates the session
directory**, a beat before `Runner.begin` would have; `read_meta` already returns `None` for a
directory with no record and §4's walk already skips it, and the consolation is that a
directory holding nothing but a `runner.log` is a runner that died before it wrote `starting`,
which is the case that used to leave nothing at all. And **`runner.log` is not appendable from
a second process** either, because a `cmd` `>>` fails against *any* existing writer whatever
that writer permitted — the difference that matters is not its share mode but that `bot.cmd`
never opens it. Reading it while the session runs works, which is what §14 asks for.

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
if errorlevel 1 ping -n 11 127.0.0.1 > nul
goto loop
```

Ten seconds is launchd's default `ThrottleInterval`, and the loop restarts on *every* exit,
which is the `KeepAlive true, unconditional` the plist header insists on — a listener that has
exited has stopped listening, and there is no successful version of that.

**The throttle is two lines because one was not enough** (W5a, measured). `timeout` needs a
console: with stdin anything else — which is what a process started without one inherits — it
prints `ERROR: Input redirection is not supported, exiting the process immediately`, sets
`errorlevel` 1 and returns in **0.02s** instead of 10. The throttle is the only thing between a
listener that cannot start at all and a loop writing two log lines every twenty milliseconds
until the disk is full, so it does not get to depend on how the file was launched. `ping` needs
no console and no venv, and eleven echoes a second apart is ten seconds of gaps. Measured both
ways: 10.09s with the fallback, 0.02s with the original line alone. On a console the fallback
never fires (`ping.exe` count 0 across a full hand-run), so it costs nothing where `timeout`
works. Whether Task Scheduler gives its action a console was a W5b question and this made it
stop mattering. **W5b's answer: it does.** A task action is `cmd.exe /c`, and `cmd.exe`
allocates a console even under `Hidden` — a restart driven by the scheduler waited **9.36s**
between the exit line and the next start line, with no `Input redirection is not supported`
anywhere in `var\bot.log` and 0 `ping.exe` processes. So the fallback never fires on either
of the two ways this file is actually launched, which does not make it wrong: it is now the
only thing standing behind a launcher that is neither (a service wrapper, `nssm`, a
`CreateProcess` from something that closed its handles) and it costs nothing where `timeout`
works.

**Neither wait may redirect to `var\bot.log`** (W5c, measured), and until W5c the `timeout`
carried a `2>> var\bot.log` so that its complaint would land in the log with everything else.
That redirection is what turned a locked log into a hot loop. `cmd` opens a redirection
target denying other writers, so **any** process still holding `var\bot.log` open for writing
makes every `>>` in this file fail — and the next paragraph is about how normal that is. Two
measured facts then compound. A failed redirection prints
`The process cannot access the file because it is being used by another process.` to stderr,
does not run the command, and **leaves `ERRORLEVEL` at 0** — in both spellings, `echo x >> f`
and `>> f echo x`, checked with the level reset to 0 before each — so `if errorlevel 1 ping`,
the whole of the belt above, never fires. And the command the belt guards was itself one of
the ones not run, because *its* redirection was to the same log. Measured under the
registered task: **13% of a core, four failing redirections an iteration, nothing in
`var\bot.log`, nothing in the task's last result, and no symptom but a fan.** `> nul` cannot
fail, so a wait redirected only there always runs. The cost is that `timeout`'s complaint no
longer reaches the log, and it only ever could in the case where the log opens — which is the
case where the throttle was never in danger. `||` *does* see a failed redirection even though
`ERRORLEVEL` does not, and that is the hook the fix in W5e will need. **Wrong on the last
clause: W5e needed no hook here at all** — it took the *writer* off the log instead of
teaching this file to cope with a log it cannot open, and `bot.cmd` is unchanged below the
header. The `||` half is not built and is decided against rather than deferred; see W5e.
W5e also sharpens the ERRORLEVEL sentence, which is true of a batch file and not of a
process: `cmd /c echo x >> <a locked file>` **exits 1**, and it is only the in-script
`ERRORLEVEL` that stays 0, because a command that did not run sets nothing. That is the same
fact `||` sees from the other side, and it is why a wrapper *around* `cmd` can tell what a
line *inside* `cmd` cannot.

**The log handle outlives the wrapper, and that is the bigger half of W5c.** `cmd` opens
`var\bot.log` for the `>>`, the listener inherits it, and until W5e `Sessions.start` handed
it on to the runner as well — stdout and stderr were inherited on purpose, so the runner's
lines landed in `var\bot.log` beside the listener's (SPEC.md §14). The handle was
therefore held by every live session, and is still held by any listener a `schtasks /End`
has orphaned — `/End` kills the `cmd.exe` and nothing under it. So a second `bot.cmd`, started
by a `/Run` or by a logon, cannot open the log at all; and worse, **the loop in the *first*
`bot.cmd` could not either, from the moment a session started.** Measured: with one session
live and its listener killed, the wrapper sat for **5m10s** without starting anything, and
opened the log **1.1s** after the runner exited — the `KeepAlive` this whole file exists for
was not available in exactly the state that most needs it. The throttle fix above bounds the
damage (a silent 10s retry instead of a spin) and does not remove it.

**W5e removed it, at the runner's end and not at this file's.** `session_win.runner_output`
gives the runner `var\sessions\<sid>\runner.log` and the listener closes its own copy of that
handle the moment `CreateProcess` has duplicated it, so nothing but the listener itself holds
`var\bot.log` any more. Re-measured under the registered task, same scenario as the 5m10s:
listener killed with a session live, `=== listener exited 15 ===` in the log **0.02s** later,
`=== centrion listener starting ===` **10.03s** after that, `centrion listening` 0.11s after
that, and the session untouched — `ls` from the restarted listener rendered it at the same
runner pid with its link. **What is left is the orphan case, and it is bounded rather than
gone**: after a `/End` the orphaned listener still holds the log, so the `/Run` after it
starts a `cmd.exe` that can do nothing but throttle (measured: 25s, `timeout.exe` in the
tree, **0.00s of CPU**, not one byte written) until that orphan dies — and then takes the log
**4.67s** later and has a listener up 0.02s after that. That is the acceptable half of the
same shape, because an orphaned listener *is still answering the phone*: the state W5e had to
remove was the one where nothing was listening and nothing could start, and after a `/End`
something is.

Nothing in this file is substituted at install time, and that is the one place the Windows
side is simpler than the Mac's. `%~dp0` is the file's own directory, so the committed `bot.cmd`
is already correct in every clone — where the plist has to carry a `__CHECKOUT__` placeholder
that `install.sh` renders, because launchd expands nothing. The installer here has one fewer
thing to get wrong and the XML below is the only thing left that needs rendering.

**`windows\centrion.xml`** — a Task Scheduler task definition. **Not** imported with
`schtasks /Create /TN centrion /XML windows\centrion.xml`, which is what this line said
until W5b and cannot work: the committed file holds placeholders, and `install.ps1` renders
it to a temp file and imports *that*. The settings that matter:

| Setting | Value | Why |
|---|---|---|
| Trigger | `LogonTrigger` for this user | `RunAtLoad` |
| Action | `%SystemRoot%\system32\cmd.exe /c "<repo>\windows\bot.cmd"` | the scheduler expands the variable; `cmd.exe` because `CreateProcess` cannot run a `.cmd` |
| `Priority` | `5` | **the default is 7, which is `BELOW_NORMAL_PRIORITY_CLASS`, and every session inherits it** — see below |
| `ExecutionTimeLimit` | `PT0S` | the default is 3 days, after which the task is killed |
| `MultipleInstancesPolicy` | `IgnoreNew` | belt for the mutex's braces |
| `DisallowStartIfOnBatteries`, `StopIfGoingOnBatteries` | `false` | this is a laptop |
| `StartWhenAvailable` | `true` | a missed logon trigger still fires — **doubted, W5d**, see below |
| `AllowStartOnDemand` | `true` | W5c's checklist is `schtasks /End` then `schtasks /Run` |
| `RestartOnFailure` | `PT1M`, count 3 | for the wrapper itself dying; the loop handles the listener |
| `Hidden` | `true` | no console window on the desktop |
| `LogonType` | `InteractiveToken` | run as the logged-on user, no stored password; **W5d: the alternative cannot be registered on this box**, and it moves with the trigger |

**`Priority` is this file's `ProcessType`** (W5b, measured). The plist's loudest warning is
that `ProcessType Background` throttles every session the listener spawns, because a child
inherits it, and the symptom is "Remote Control feels slow" days later with nothing in any
log. Task Scheduler has exactly the same trap under a different name and defaults *into* it:
a task registered with no `<Priority>` gets 7, which is `BELOW_NORMAL_PRIORITY_CLASS`.
Measured on this box with the whole tree cleared between runs — **no element: `cmd.exe`, the
venv launcher and the interpreter all `BelowNormal`; `<Priority>5</Priority>`: all `Normal`**.
4, 5 and 6 are all `NORMAL_PRIORITY_CLASS`. This row did not exist in the table above before
W5b and is the one thing in it that is wrong by default.

**Two placeholders, not one.** §9's W5b said `install.ps1` had one `-replace` to do because
`bot.cmd` needs none; it has two. `__CHECKOUT__` is the plist's reason. `__USER__` is a
reason the Mac does not have: a LaunchAgent is this user's because of the directory it is
installed into, where a task lives in one machine-global store, so both the `LogonTrigger`
and the `Principal` name a `UserId` — and a `LogonTrigger` with none means *any* user's
logon, which `schtasks /Create` refuses from an unelevated shell with **"ERROR: Access is
denied"** and no mention of the trigger or the element (measured; it is the principal's
`UserId` that may be omitted, not the trigger's).

**The XML has no environment, and cannot have one.** The plist carries `PYTHONUNBUFFERED`
and a `PATH`; this schema has no element for either — `<EnvironmentVariables>` and
`<Environment>`, in `Settings` and in `Exec`, are all "ERROR: The task XML contains an
unexpected node". So `PYTHONIOENCODING`, which §9's W5b wanted put here, could only ever
have been a `set` in `bot.cmd`; W5b decided against it and wrote the decision into that file.

**Encoding: `<?xml version="1.0"?>`, ASCII, no byte order mark**, and every other spelling
fails as `ERROR: The task XML is malformed` with a column number. Measured: `schtasks` refuses
`encoding="UTF-8"` in the prolog outright ("unable to switch the encoding") and refuses a
UTF-8 BOM ("incorrect document syntax" at (1,2)); it accepts `encoding="UTF-16"` over ASCII
bytes and UTF-16LE-with-BOM over UTF-16 ones, but Python's expat then will not read the
committed file, and UTF-16 in a repository is a blob git diffs as binary. A bare prolog is
the one spelling both accept. PowerShell 5.1's `Out-File -Encoding utf8` writes a BOM, which
makes the natural way to write the rendered file the broken one.

`InteractiveToken` means the bot exists only while this user is logged on. That is the same
place launchd's GUI domain leaves the Mac (SPEC.md §14: "a login after a cold boot is still
unwatched"). Moving to `Password` logon type and "run whether user is logged on or not" would
close that gap but runs the bot in a non-interactive session; ConPTY does not care, and
Claude Code's login lives in `%USERPROFILE%\.claude` rather than in an interactive keychain, so
it may well just work — but it is a W5 verification, not an assumption.

**W5d went to take that verification and could not register the task at all, which is a
different answer from the one this paragraph was waiting for.** Three findings, none of them
about being logged on, and the first two stop the switch before the question is reached.
*(1) This account has no password*, and a task that runs whether the user is logged on or not
needs a batch logon. `schtasks /Create /XML` with `<LogonType>Password</LogonType>` answers
**ERROR_ACCOUNT_RESTRICTION** — "Account restrictions are preventing this user from signing
in. For example: blank passwords aren't allowed" — and `LogonUser` with an empty password
answers the same **1327** for `BATCH`, `SERVICE`, `NETWORK` *and* `INTERACTIVE`, never 1326.
1326 would have meant the account has a password and an empty one was wrong; 1327 means the
empty one was accepted as the credential and policy refused the logon. So it is the blankness
and not a guess: no batch logon of this user exists until the account has a password, which is
the user's decision about their own machine and not a slice's. *(2) The two ways round a
stored password are both refused unelevated*: `<LogonType>S4U</LogonType>` is **"ERROR: Access
is denied"**, and so is any `<BootTrigger>`, with either logon type. *(3) And a `LogonTrigger`
would not have fired anyway.* This is the finding worth keeping, because it is free to get
wrong and nothing would report it: the only trigger in `centrion.xml` is a `LogonTrigger`, so
a principal that *could* run with nobody logged on would still never be started — W5d's own
run step in §9 (switch the logon type, reboot, do not log in, message the bot) would have
measured a bot that was never triggered and recorded it as a bot that does not work. The
logon type and the trigger are one decision in two elements, and
`test_the_logon_type_and_the_trigger_agree_about_when_the_bot_runs` now holds the pair.

What W5d *could* take supports the optimistic half of this paragraph rather than settling it.
Claude Code's login is `%USERPROFILE%\.claude\.credentials.json`, 509 bytes of plain UTF-8
JSON with one key, `claudeAiOauth` — a file, not DPAPI and not a keychain, so any logon of
this user that can read the profile can read the login. And the interactive half is now
measured rather than inferred: under `/Run`, all three of the task's processes (`cmd.exe`,
the venv launcher, the interpreter) report **session 11**, this desk's own interactive logon
session, at `Normal` priority — the bot is inside the session a logoff destroys, which is
what `InteractiveToken` costs, stated in processes. **ConPTY in a non-interactive session
remains untested and untestable from here**, because nothing unelevated on this box can
create such a session; §11 carries it.

One row of the table above is *doubted* by this slice and not corrected, because doubt is all
the evidence supports: `StartWhenAvailable`'s "a missed logon trigger still fires". Microsoft
documents that setting as applying to time-based triggers, and a logon trigger is not one.
Nothing here can distinguish the two without a logoff, so it stays as it is with this sentence
against it, and §11's logoff time item names the observation that would settle it.

The paths in the XML
are absolute and specific to this checkout, exactly as the plist's are; the install script
writes them, and the user with them.

**`schtasks /Query /TN centrion /XML` is not the committed file with the placeholders filled
in** (W5d). The scheduler normalises what it stores: the `Principal`'s `UserId` comes back as
a **SID**, the trigger's as `DOMAIN\user`, `<RunLevel>LeastPrivilege</RunLevel>` and the
trigger's `<Enabled>true</Enabled>` are dropped because they are the defaults, and the prolog
is UTF-16. It is still the right place to *read* a registration, as the line above says; it is
not a file to diff against `windows\centrion.xml`, and a diff that shows those four
differences shows nothing wrong.

**`windows\install.ps1`** — creates `.venv`, installs `requirements-win.txt`, makes `var\`,
checks `.telegram.json`'s permissions, and (W5b) renders the XML and registers the task with
`schtasks /Create /TN centrion /XML <temp> /F`. `-Print` writes the rendered XML to stdout
and does nothing else, which is `install.sh --print`; `-NoTask` installs everything but the
task, for a clone that only exists to run the suite. Idempotent; re-running reuses the venv,
re-installs the pinned wheels (a no-op) and re-registers (`/F` replaces, with no `bootout`
and no wait loop — the scheduler replaces a registration synchronously where `launchctl
bootout` returns before the job is gone). Measured: **10.1s on a fresh clone and 1.7s on a
re-run** (W5a), **2.1s on a re-run with the task** (W5b), and a clone it has installed runs
the suite from its own venv.

**W5b found that this file did not run at all.** As W5a committed it, `powershell
-ExecutionPolicy Bypass -File windows\install.ps1` — the invocation in its own header —
answered **nine parse errors** and executed nothing. Windows PowerShell 5.1 reads a BOM-less
`.ps1` in the ANSI code page, not UTF-8; a U+2014 em dash is `E2 80 94` there, and `0x94` in
cp1252 is a curly closing quote, which PowerShell honours as a string terminator, so one em
dash inside one `throw "…"` unbalances every quote after it. There is no `pwsh` on this box,
and `pwsh` is the only version that would have read the file as UTF-8. The rule is now one
line per directory — **everything under `windows\` is ASCII** — and `test_layout.py`'s
`test_nothing_under_windows_is_anything_but_ascii` is what holds it, on both platforms,
because it is a property of the bytes and the Mac can read bytes.

Three things it does differently from the sentence above, the first two W5a's findings and
the third W5b's:

- It **does not set** the token's DACL, it *asks*. A `.telegram.json` created under the
  checkout — or under `%TEMP%`, also measured — already inherits owner/SYSTEM/Administrators,
  which is exactly what W2b's check wants, so setting it unasked would be ceremony. The script
  runs `config.load()` the way the listener will and prints whatever `config.py` says; when
  that is a refusal it carries the `icacls` line built from the principals actually on the
  file, which is the only version of that line W2b found to work. A checkout with no
  `.telegram.json` at all — the fresh-clone case — gets told so in words rather than leaving
  it to the first silent listener.
- Finding the *base* interpreter is worth the twenty lines it takes. `py -3` is tried first
  because a stock python.org install leaves only the launcher on `PATH` ("Add python.exe to
  PATH" is off by default), and no candidate is accepted until it has answered `--version`,
  because Windows 11 ships a `python.exe` app-execution alias that opens the Store and runs
  nothing. Probing with `--version` rather than `-c` is deliberate: a probe that can open a
  REPL is a probe that can hang an installer.
- It writes the rendered XML with `[System.IO.File]::WriteAllText(…, UTF8Encoding($false))`
  and not with `Out-File`. The latter is the natural PowerShell spelling and, in 5.1, the
  broken one: `-Encoding utf8` there means UTF-8 *with* a byte order mark, which `schtasks`
  rejects. The temp file is removed in a `finally`; the registered task is then readable with
  `schtasks /Query /TN centrion /XML`, which is where to look rather than on disk.

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
   will never see. *Since W6 the Mac half is the `macos` CI job and has never been anything
   else; a slice names the run id and that is the whole of it.*
4. **Run.** The hand-run named in the slice, on the real thing, and read the output. A slice
   with no runnable surface says so.
5. **Test.** The whole suite: `.venv\Scripts\python -m unittest -q` on Windows — from the
   venv since W2b, where `config.py` grew a check that needs pywin32; the system
   interpreter still *imports* everything and fails 41 config tests. For slices that touch
   posix code or shared code, also on the Mac — and *this is the half W6 changed and left
   half-written, which W7 finished.* Since W6 "on the Mac" means the `macos` CI job, so it
   happens **after the commit and before the merge**, which is the exact inversion of what
   this step said for five slices. That is the price of not having the machine, and the
   shape it takes is: work on a `w<n>-…` branch, push, let the run answer, amend the branch
   rather than pile fix-ups on it, and merge `--ff-only` only what came back green. Since
   W7 the job runs `-m unittest -v`, not `-q`, because a count cannot check a row that
   predicts *which* tests skip — and twenty rows of §11 predicted exactly that.
6. **Commit.** One commit per slice, message `W<n>: <what>`, body naming the tests added.
   Nothing half-done gets committed as "WIP"; if a slice is too big to finish in a sitting,
   split it in this document first.
7. **Progress.** Fill in the row in §11: date, commit hash, what the run step showed, and
   anything learned that changes a later slice. Amend the later slice's text in the same
   commit or the next — the plan is the record, not the chat.

The Mac suite is the regression guard for everything that moves in W1. **Since W6 there is a
CI matrix, and it changes what half of this sentence means.** `.github/workflows/test.yml`
runs the suite on `macos-latest`, where `/usr/bin/python3` is **3.9.6** — the same 3.9 this
whole guard is written against, because it is the Command Line Tools python and not anything
in the toolcache. So step 3, and the suite half of step 5, are now answered by a push: a
slice may write "green on the Mac (CI), run &lt;id&gt;" and mean it. **Steps 3 and 5 above now
say so in their own words — W6 rewrote this paragraph and left the two steps it contradicts
untouched, and W7 finished it.** The one thing W6 could not have written is in step 5 too:
the job prints `-v` since W7, so what a slice gets back is the list of tests that skipped
and why, which is the only form in which the rows below can be checked at all.

What CI is *not* is the Mac. It is a third environment with the right interpreter and the
right source, and it has no `~/Projects`, no Claude Code, no `.telegram.json`, no phone and
no launchd session — so **every hand-run in step 4 still means somebody ran it there**, and
so does every row of §11's "Pending on the Mac" that is one. Two different sentences now, and
a slice has to say which of them it is claiming.

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

*Amended by W4f, 2026-09-27 — one of W4a's nine tests asserts a promise neither side of the
seam makes, and it took a year and somebody else's process to show it.*
`test_a_pid_that_is_not_one_is_answered_rather_than_raised` listed `"4242"` first among the
pids `started` must answer `None` for. It does not and it must not: both guards are `int(pid)`
— `session_posix` spells it out, `session_win` writes it inside the `psutil.Process(...)` call
— and `int("4242")` is 4242, so **a numeric string is a pid to both platforms**, and what comes
back is whatever the platform says about that pid. The case passed for a year because 4242
happened to be free on this box, and went red the day `EACefSubProcess.exe` took it: an
assertion about the machine wearing the clothes of an assertion about the code. The property
that was meant is a pid the seam cannot read *at all* — `"nope"` raises `ValueError` inside the
guard on both sides — and that is what the loop carries now; `None`, `2 ** 62` and `-1` were
always sound and still assert `None`. **Nothing above the seam was relying on it**, which is
the other half of why it sat unnoticed: `session.py` writes `"runner_pid": os.getpid()`, an int
through json, so bot.py cannot *produce* a record with a string pid; and if one arrived by hand
or by corruption, `Sessions.alive`'s guard is `isinstance(pid, int)`, which returns `False`
before `procs.alive` or `procs.started` is called at all — and `Sessions.stop` gates on that
same `alive` before it reaches `session.terminate`. There is no path in the program that hands
this function a string. It is a seam-parity test and only that, and parity is about what the
two sides *promise*, not about what one box's pid table happens to be holding this afternoon.

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

**W4f — the Mac merge lands on Windows.** *Out of order on purpose, and the ordering above is
the honest one: this entry was written after W4e's and it is a **prerequisite** to it.* W4e
assumes a green suite to run a from-the-phone checklist against; commit `5174808` merged about
eighteen commits of Mac-side work into this tree — SPEC.md slice 13's project keyboard, a new
macOS-only `attach.py`, launchd and cleanup scripts — and the Windows suite, green since W1c,
went red. So the sequence is W4d, **W4f**, W4e.
- Red: the merge wrote it, which is the ritual's step 1 satisfied by somebody else's commit.
  `test_bot.py::TestTheKeyboard::test_the_text_is_capped_and_the_keyboard_still_arrives` errors
  with `[WinError 206] The filename or extension is too long`, and
  `test_procs_win.py::TestWhetherARunnerIsStillThereOnWindows::
  test_a_pid_that_is_not_one_is_answered_rather_than_raised` fails. For each, the rewritten
  test must be shown failing against the unfixed condition before it passes.
- Green: **both are the test and neither is the program.** The first makes 30 directories of
  203 characters, which clears macOS and exceeds MAX_PATH here; the property it is aimed at
  (§7's 4096 caps the text, the markup is a separate field and must survive truncation) is
  real and portable, so it is bought with the *count* of names rather than their width and
  keeps running on both platforms — a decorator is the last resort in this repo, not the first
  (W4a). The second asserts something neither `session_posix` nor `session_win` promises; see
  W4a's amendment above, and it is the finding of this slice.
- Also: read what the merge did to *shared* code — `session.py`, `session_posix.py`, `bot.py`,
  `config.py`, `telegram.py` — and record whether any of it changes a claim a W-slice made or a
  paragraph of §4/§5/§6. "Checked and it did not" is the finding that stops the next person
  re-reading it.
- Run: `bot.py --serve`, which has been runnable since W4d, answering `/ls` — the merged Mac
  feature shown working on Windows once, and not merely unit-tested.
- Test: both platforms.

*W4f, 2026-09-27 — done, and the slice that was about two red tests found that one of them had
never been about the code.* The keyboard test is a portability fix and nothing more: 80 names
of 63 characters in place of 30 of 203, plus an `assertIn("characters elided", …)` first,
because "the text is under the cap" and "the markup survived" are both true of a reply that was
never truncated — the guard is what keeps the other two assertions from passing vacuously on a
day the projects root is small. The pid test is the finding, and the amendment is in W4a above.
**The shared-code read found three things and amended three sections** — §4 (the posix
`Terminal` gained `size`/`resize` for `attach.py`, so the seam's `Terminal` is asymmetric and
`session.py --attach` is a `ModuleNotFoundError` here), §5 (a new `terminal_window` key, off by
default on Windows and *refused* by name if asked for), §6 (the keyboard and the two
`telegram.py` calls are platform-neutral, checked rather than assumed). Nothing in the merge
contradicts a W-slice's claim. The `Terminal` asymmetry is the one thing carried forward, and
it is carried as a note rather than as work, because nothing in the port needs a viewer.

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

*W4e, 2026-09-27 — five of the six ran and none of them is the finding; the sixth cannot be
taken here at all.* **The block is the token and it is the user's to lift.** `.telegram.json`
on this box still carries W3g's placeholder, so the shipped `bot.py --serve` answers
`deleteWebhook: HTTP 401 Unauthorized: invalid token specified`, then the same for
`setMyCommands`, then `getUpdates: … retrying in 1s`, forever — run for 14 seconds and read,
so that the block is a measurement and not an assumption. No phone can reach this listener
until a real bot token is put in that file, and inventing one is not a thing a slice may do.
**What was run instead** is the whole checklist against the shipped `bot.serve()` with
`telegram.Telegram._open` replaced — W4f's seam, one level below `Telegram`, so `_call`,
`_best_effort`, the JSON body, `delete_webhook`, `set_commands` and `send_message`'s
`reply_markup` are all the program — over W4d's real mutex, real `Popen` runners, real
ConPTY, real `claude.exe` and the real `projects_root`. The one thing it does not exercise is
the hop to api.telegram.org and the phone on the other end of it, and that is named in §11 as
debt rather than counted as done. **What it found:** the double reply (§6, fixed here, three
portable tests), that W3h's trust finding is half wrong (§4), that a TUI panel's tail loses
its spaces (§4), and that every runner here is two processes (§6).
- **Still owed, and it is the three words in this slice's title:** the six items from an actual
  phone, against an actual token. Everything else about them is measured in §11, and the row
  says `from the phone pending` for as long as that is true. It is one sitting's work the day
  a token exists — send six messages and read six replies — and it needs no code.

### W5 — startup

**W5a — `windows\bot.cmd` and `install.ps1`.** Done; see §11.
- Red: `test_layout.py`: `windows/bot.cmd`, `windows/install.ps1`, `requirements-win.txt`
  exist; `.gitignore` covers `.venv/` and `scratch/`; `bot.cmd` contains `--serve` and
  `goto loop`. **Three of the twelve pass before the code exists** and the entry should have
  said so: `requirements-win.txt` arrived with W2b, `.gitignore` has named `.venv/` and
  `scratch/` since slice 0 — its comment there says "W5a's install.ps1 creates it" — and the
  tracked check answers vacuously while the files are absent, because `git check-ignore`
  matches patterns and not files. Those three are mutated instead (§11).
- Green: §7's two files.
- Run: `install.ps1` on a clean clone in `%TEMP%`; `windows\bot.cmd` from a console, then
  kill the Python process — the loop restarts it after 10s and `var\bot.log` shows both lines.
- Test: both platforms (layout tests run everywhere; all twelve are portable and undecorated).
- **Amended by W5b: the `install.ps1` that was committed here did not parse.** The run step
  measured a script that then had its prose polished, and nine UTF-8 em dashes went into a
  BOM-less `.ps1` that Windows PowerShell 5.1 reads as cp1252, where `0x94` is a closing
  curly quote and terminates a string (§7). Every one of W5a's twelve tests read the file as
  text and none of them ran it, which is the whole of the lesson: **a run step measures the
  bytes that were run, and the commit is a different set of bytes unless something checks.**
  W5b's `test_nothing_under_windows_is_anything_but_ascii` is that something.

**W5b — the scheduled task.** Done; see §11. Three things W5a left on this entry's desk, and
the answer to each:
- **Does the task's action get a console?** `timeout /t 10` needs one and silently does not
  throttle without it (§7). W5a made that survivable with a `ping` fallback, so this is now a
  fact to record rather than a risk to carry — read `var\bot.log` after a task-driven restart
  and see whether the `ERROR: Input redirection is not supported` line is in it.
  **Answered: it does.** The action is `cmd.exe /c`, which allocates a console even under
  `Hidden`; 9.36s between the log's exit line and its next start line, no complaint, 0
  `ping.exe`. §7 amended.
- **The task's environment.** The plist sets `PYTHONUNBUFFERED=1` and a `PATH`; `bot.cmd` sets
  nothing, and neither is needed — `bot.log()` writes to stderr and flushes every line itself,
  and Windows has a usable `PATH` in every session. What is *not* set anywhere is
  `PYTHONIOENCODING`, so `var\bot.log`'s encoding is the ACP (cp1252 on this box) and a
  project name outside it reaches the log backslash-escaped rather than as itself. Harmless —
  `sys.stderr` uses `backslashreplace`, so nothing raises — but if the XML is going to carry
  an environment at all, this is the one line worth putting in it.
  **Answered: the XML cannot carry one.** There is no element for an environment in this
  schema at all — `<EnvironmentVariables>` and `<Environment>`, in `Settings` and in `Exec`,
  are each "ERROR: The task XML contains an unexpected node". So the only place it could go
  is a `set` in `bot.cmd`, which is the place this entry said it did not belong. **Decided,
  not inherited: it stays unset**, and the reasoning is written into `bot.cmd` above the loop
  — `type var\bot.log` is the instruction §14 gives, ASCII escapes stay readable in a console
  and UTF-8 would not, and nothing raises either way.
- `bot.cmd` needs no path substitution (§7), so the XML is the only rendered file and
  `install.ps1` has one `-replace` to do, not two. **Wrong: two.** `__CHECKOUT__` for the
  plist's reason and `__USER__` for one the Mac does not have — a task lives in a
  machine-global store and its `LogonTrigger` must name a `UserId`, or it means every user's
  logon and `schtasks /Create` answers "Access is denied" (§7).
- Red: `test_layout.py::test_task_xml_settings` parses `windows/centrion.xml` and asserts
  the §7 table: `PT0S`, `IgnoreNew`, batteries `false`, `StartWhenAvailable true`,
  `InteractiveToken`, a `LogonTrigger`; and the win32 counterparts of the three
  `TestTheLaunchdInstall` checks W1c gated as `this_mac_checkout` — every path in the XML
  absolute and present, the working directory is this checkout, the log is `var\bot.log`.
  The third has no counterpart to compare against, because **Task Scheduler has no
  `StandardOutPath`**; what the test asserts instead is the join — the action is `bot.cmd`,
  and `bot.cmd` is the only thing that appends `var\bot.log`.
- Green: the XML template; `install.ps1` substitutes the path and runs `schtasks /Create`.
- Run: register; log off and on; `var\bot.log` shows the listener up within a minute and a
  phone `ls` answers. Record the seconds. **The logoff/logon half could not be taken from
  this desk** — logging off ends the session driving the slice — so the figure in §11 is the
  leg that could be: scheduler start to first poll. The logon leg is a named debt.
- Test: both platforms.

**W5c — restart survival, the launchd §12 slice 9 test.** Done; see §11. **"No new code
expected" was wrong, and the code it needed was one line of `windows\bot.cmd`**: the
throttle redirected to `var\bot.log`, a log the second `bot.cmd` cannot open while anything
else holds it, and a failed redirection in `cmd` leaves `ERRORLEVEL` at 0 — so the `timeout`
never ran, `if errorlevel 1 ping` never fired, and the `/Run` half of this entry's own
checklist spun at 13% of a core with nothing written anywhere (§7). Behind that is the
finding this slice is really about: **the runner inherits the log handle, so no `cmd.exe` can
reopen `var\bot.log` while a session is live**, and the `KeepAlive` loop is therefore
unavailable in exactly the state that needs it. That is W5e. Unblocked by
W5b; the task is registered and `AllowStartOnDemand` is on, which is what `schtasks /Run`
needs. Two things W5b leaves on this entry's desk:
- **The listener is three processes under the task, not two.** `cmd.exe` (the action), the
  venv launcher `python.exe`, and the real interpreter. W4e's "two processes" is the
  console case; the scheduler adds the wrapper, and it is the wrapper that the loop lives in.
- **`schtasks /End` kills the action and leaves the pythons**, observed while measuring the
  priority counterfactual — which is W0c's finding arriving from the other direction, and is
  exactly the property this slice exists to check. Do not read "the task is not running" as
  "nothing is listening": the next `/Run` starts a `bot.cmd` whose listener is then refused
  by W4d's mutex in about a tenth of a second and restarted every ten thereafter.
- Red: checklist into §11 first: start a session; `schtasks /End /TN centrion`; `tasklist`
  shows runner and claude alive; `schtasks /Run`; `ls` shows the session live with the same
  runner pid; `stop 1` ends it. **Taken, and the third item could not be taken against the
  task's own wrapper** — `schtasks /Run` starts a `bot.cmd` that cannot open the log while
  the session it is asking about is alive, so the restarted listener that answered `ls` was
  started the way `bot.cmd` will start one once W5e lands: same venv interpreter, same
  `--serve`, same mutex, same records. Which half is which is written into the §11 row.
- Green: one line of `bot.cmd`, held by one portable test in `tests/test_layout.py`,
  `test_the_throttle_does_not_redirect_to_the_log_it_cannot_always_open`.
- Run: the checklist, against the registered task, with `telegram.Telegram._open` replaced —
  W4f's seam — by a `.pth` dropped into the venv, because the scheduler runs `bot.cmd` and
  there is nowhere else to stand. `.telegram.json` still holds W3g's placeholder token.
- Commit: the fix, the test, §7, this entry, W5e, and the §11 row.

**W5e — the runner must not pin `var\bot.log`.** Done; see §11. Found by W5c (§7).
- The problem: `cmd`'s `>>` opens the log denying other writers; the listener inherits that
  handle and `Sessions.start` passes it to the runner, which keeps it for the life of the
  session. So `bot.cmd`'s own loop cannot restart a listener that died while a session was
  running, and a `/Run` after a `/End` cannot start one at all. Measured: 5m10s of a wrapper
  that could do nothing, and recovery 1.1s after the runner exited.
- The shape of the fix is a decision, not a detail, and it is why this is its own slice.
  The runner's stderr is where `session.py` writes `stop requested`, `ended` and its own
  diagnostics, and §14 says to read them in `var\bot.log`. Moving them to the session
  directory keeps the handle out of `cmd`'s way and splits §14's first diagnostic in two;
  keeping them where they are means the wrapper must stop using `cmd` redirection — a writer
  process behind a pipe, which costs a process and a file. The Mac needs neither and must
  not pay for either, so whatever this is, it goes behind the `procs` seam (§6).
  **Decided: the runner logs somewhere else**, and the evidence decided it rather than a
  preference. Three things against the pipe. It costs a process and a file *per iteration of
  a loop whose whole job is to be reliable*, and the process is a new single point of failure
  in front of the only log there is. It breaks `%errorlevel%` after the listener — a
  pipeline's level is its last command's, so `=== listener exited N ===`, one of the two
  lines §7's log is made of, would report the writer's status and not the listener's. And it
  puts new untested machinery in the path W5a and W5c have each already had to repair, which
  is the shape W5c named: *a fallback that shares a dependency with the thing it is backing
  up is not a fallback.* Against that, "the runner logs somewhere else" is one seam function,
  `{}` on the Mac, and it is the Mac that settles it — W5c's own Pending row asks the Mac to
  confirm that an inherited append fd costs POSIX nothing, which is an argument for changing
  nothing there and everything for changing the side that pays. The price is §14's first
  diagnostic in two files on this platform, and that price is paid in `bot.cmd`'s header.
- A third, cheaper half that stands on its own: `bot.cmd` can *detect* the failure even
  though `ERRORLEVEL` cannot — `||` fires on a failed redirection (measured, W5c) — so the
  loop can run the listener unredirected rather than not at all, and a listener that is up
  and blind beats a bot that is down. Whether that is wanted is part of the same decision.
  **Decided against, and not deferred.** It is wanted only in a state that the decision above
  removes: after the fix the only thing that can still pin the log is a listener, and a
  listener holding the log is a listener *answering the phone*. What the `||` branch would
  buy in that state is a second listener that the mutex refuses; what it would cost is the
  case where it succeeds — an unredirected listener under the scheduler writes to a console
  nobody has, so §14's log stops with no line saying it did, and because that listener holds
  the mutex the loop never replaces it. A bot that is up and permanently blind is worse than
  a ten-second retry that heals itself, which is what the measurement showed it doing.
- Red: a test that a live session does not stop a second `bot.cmd` from opening the log;
  it needs a real runner, so it belongs with `TestSpawningForRealOnWindows`. **Written, and
  three things it needed that this line did not say.** `subprocess` with `stdout=None` passes
  `GetStdHandle(STD_OUTPUT_HANDLE)` to `CreateProcess` and not fd 1, so a test that wants to
  reproduce an inherited `cmd` redirection has to `SetStdHandle` — `dup2` changes the wrong
  thing and the test would pass against the bug. The probe has to be `cmd` itself and it has
  to be passed as one string, because `list2cmdline` escapes a quote as `\"` and `cmd` then
  fails on the path rather than on the sharing. And the property test says *that* the log
  opens, never *where* the runner's lines went, so the mechanism needs its own three in
  `test_session_win.py` and the wiring one portable test in `test_bot.py`.

**W5d — the not-logged-on case.** Optional. Taken, and **refused rather than deferred**; see
§11 and §7. `Optional` is what sent it to the back of W5 — W5e was taken first for that one
reason — and it turns out to be the right label for a different reason than the one intended:
the switch this entry describes cannot be made on this box at all.
- Run: switch the task to `LogonType Password` and "run whether user is logged on or not";
  reboot; do not log in; message the bot. Record what happens. Revert if it does not work.
  **Nothing after the semicolon was reachable.** `schtasks /Create /XML` refuses
  `<LogonType>Password</LogonType>` with ERROR_ACCOUNT_RESTRICTION (this account has no
  password, so it has no batch logon — `LogonUser` answers 1327 and not 1326 for every logon
  type), and refuses both `S4U` and a `<BootTrigger>` with "Access is denied" unelevated. No
  revert was needed because no switch was made, and the box's registration is unchanged.
- **And the recipe above is wrong in a way that would have produced a false negative**, which
  is the finding this slice is worth keeping for. The only trigger in `centrion.xml` is a
  `LogonTrigger`. "Switch the logon type, reboot, do not log in" leaves nothing to start the
  task, so the measurement would have been of a bot that was never triggered, not of a bot
  that cannot run without a session. The move is two elements, `Password`-or-`S4U` **and** a
  `BootTrigger`, and whoever takes this next must make both.
- Green: no program code. One portable test in `tests/test_layout.py`,
  `test_the_logon_type_and_the_trigger_agree_about_when_the_bot_runs`, which holds the pair so
  that half the move fails loudly instead of silently; the comments in `windows\centrion.xml`
  and §7 carry the measurements.
- Red: the entry named no tests, so the new one passed the moment it was written — the ritual's
  mutation case. Two mutations of the committed XML, both caught; §11's row says which tests
  did *not* fail under each.
- What is still owed, and it needs a keyboard and a decision rather than a slice: a password on
  this account (or an elevated shell), then the pair of elements above, then a reboot with no
  logon. Until then **ConPTY in a non-interactive session is untested** — §7's "ConPTY does not
  care" is still an assumption, and the only part of that paragraph W5d could confirm is that
  Claude Code's login is a plain JSON file in the profile. §11 carries all of it.

### W6 — CI matrix. Optional.
- Red: none meaningful; the workflow is the test.
- Green: `.github/workflows/test.yml`, `windows-latest` and `macos-latest`, `python -m
  unittest -q`, Windows job installs `requirements-win.txt`.
- Run: push; both jobs green.
- *Taken 2026-09-27, and three things in those four lines were wrong.* **(1) "Both jobs" is
  three.** `macos-latest` runs twice, once on `/usr/bin/python3` and once on a toolcache
  3.12, because a single macOS job cannot tell a 3.10+ construct (the only thing step 3
  exists for) from a posix mechanism that has rotted, and the pair can. **(2) The green one
  was the Mac.** The entry is written as though the macOS job is the risky half; it passed
  on the first push and the *Windows* job failed four tests, all of them tests this desk
  does not run. **(3) `macos-latest`'s `/usr/bin/python3` is 3.9.6**, so the matrix is a
  better instrument than §8's "the only way the Mac side stays green" hoped — but it is
  still not the user's Mac, and §11's "Pending on the Mac" says exactly which half it
  answers. Five tests were added with it (`test_layout.TestTheCiMatrix`), because a
  workflow's failures are as silent as a startup file's.

### W7 — what "Pending on the Mac" still means, and one question about the boundary

Opened by W6, which made most of that table answerable and answered none of it in place.

- Red: none yet; the first half is a documentation reconciliation and the second half needs
  a test that only a runner can execute.
- Green, part one: go through §11's "Pending on the Mac" row by row against a green macOS CI
  run and split it in two — the rows CI answers (every `compileall`, and every "green, and N
  newly skipped" whose numbers a `-v` run can now be checked against) move to `done` naming
  the run, and the rows only that machine can answer (the hand-runs, the phone, `launchctl`,
  a real `~/Projects`, an installed Claude Code) stay, with the table's preamble saying which
  kind each is. The reconciliation is not clerical: **the table's skip predictions are wrong
  by ten**, because every row since W1a computed "the Mac's count" by adding to *this box's*
  number, and the two platforms skip different sets. The measured Mac number is 89 against
  this box's 95.
- Green, part two: decide whether `config._child` should canonicalise a reparse point whose
  target does not exist (§5.3, W6). Today a dangling symlink is refused by check 3 with the
  containment message rather than by check 4 with "no project by that name exists" whenever
  the link's stored target is spelled through an alias of the root. Nothing is unsafe — it is
  refused either way — so this is a decision about which sentence the phone gets and about
  whether check 3 should be reachable by a name that never escaped anything, not a repair.
  Whatever it decides needs a test that creates a symlink, so it is a test **this desk can
  never run** and CI always will; write it as such.
- Run: a push, and a `-v` macOS job for part one.
- *Taken 2026-09-27, and the entry was right about both halves and wrong about the size of
  one of them.* **(1) The table split 73 / 24, not roughly in half.** Of 97 rows, 73 are
  answered by one green macOS run and 24 are not — and the 24 are the whole of what was ever
  worth carrying there: hand-runs, the phone, launchd, a real `~/Projects`, an installed
  Claude Code, a live session. **(2) "Wrong by ten" understates it, because it is not an
  error in a number.** The desk and the Mac skip 98 and 90 tests and **share exactly one**,
  so the two counts were never addable and no correction to the arithmetic would have
  helped: the method was wrong, not the sum. **(3) The `-v` the Run step asks for had to
  become permanent.** A `-q` line prints a count and twenty rows predict *which* tests skip;
  all three jobs now run `-m unittest -v` and `test_layout.TestTheCiMatrix` holds it.
  **(4) `_child` is unchanged**, by decision — §5.3 carries the reasoning, and the guard was
  proved by pushing the mutation *before* pushing the fix, because no test at this desk can
  see either.

### Time items

Not slices; §11 gets a row when each happens. The first day of §10.7's retention on NTFS
(`shutil.rmtree` against a directory whose `pty.log` something may still hold); the first
sleep/wake with a session open; the first Claude Code self-update under a live runner; the
first real logoff and logon (W5b's missing half, and after W5d also the only way to see what
`InteractiveToken` does to a listener that is inside the session being destroyed).

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
| W4a `alive`/`started` via psutil | done · Mac pending | 2026-09-19 | d131849 | **614 ran: 539 pass, 75 skip, 0 xfail**, 35.3s · **not run** | **Two thirds of the slice was already on disk, and the third that was not is two branches nobody had tested on this platform.** The entry's portable red test shipped in **W1b** as `test_a_pid_that_started_after_the_record_is_somebody_else`, and `psutil` entered `requirements-win.txt` in **W3e**; the green is therefore two functions and nine tests. `alive` is `psutil.pid_exists` — the same line `session_posix` draws with EPERM, since it asks whether the process has *exited* rather than whether it is ours — and `started` is `Process(pid).create_time()`, epoch seconds on the same clock the record's `started` is written from. What was actually missing: of the eight properties `TestWhetherARunnerIsStillThere` asserts, three had fake-`procs` twins and three are the platform's, and **two had neither** — a record with no usable `started`, and a pid the platform will not date. Both are `Sessions.alive` branches returning `True`, both untested here, and since both new tests were green before any code changed (the ritual's step-1 rule), each was mutated: `return True` → `return False` in either branch is caught by **exactly one test in the whole 614**, the one this slice added. **Run step, and §6's prediction is wrong in both directions.** `AccessDenied` from `create_time` **does not happen**: 0 of the 208 processes on this box refused, including the **109 whose `username()` psutil cannot read**, because it needs only `PROCESS_QUERY_LIMITED_INFORMATION` and every account has that for everything — so §4's `began is None` branch is reachable here only by a pid that dies between the two calls, and the handler is insurance (kept, and now with a test that would notice if Windows tightened the check). What the two genuinely unopenable pids answer instead is **`0.0`** — the epoch, not the boot time and not an error — so pid 4 arrives at `Sessions.alive` as a timestamp older than every record that could exist and **reads as a live runner** for as long as a corrupt record names it. Not patched: the Mac reaches the same place for pid 1 by an honest route (launchd really did start at boot) and its own test asserts pid 1 alive on purpose, `Sessions.alive` cannot tell the two apart, and a record's pid is one the listener wrote from its own `Popen`. §6 and §9's W4a are amended. **Nothing was un-gated, and that word is the entry's other mistake**: `TestWhetherARunnerIsStillThere` and `TestSpawningForReal` stay `@posix_only` because `/bin/sleep`, EPERM, fd 9 and `getsid` are the Mac's mechanisms — the shape that works is W3c's twin-beside-it, not a decorator removed, and W4b–W4c should copy that. `test_procs_win.pid_alive` also stays on `tasklist` rather than moving to psutil now that it could: those tests ask whether a detached child survived, and answering that with the library under test in the same file is not an answer. +9 tests (7 Windows real-process, 2 portable), **no skip count change** — every one runs here; on the Mac the 7 will skip. Two consecutive full runs, 35.3s both; W3c's unidentified ~3% error did not recur. *Amended by W4f, 2026-09-27: one of the nine, `test_a_pid_that_is_not_one_is_answered_rather_than_raised`, listed `"4242"` among the pids `started` must answer `None` for, and **neither side of the seam promises that** — both guards are `int(pid)` and `int("4242")` is a pid, so the answer is whatever the platform says about 4242. It was green for a year because that pid was free on this box and went red when `EACefSubProcess.exe` took it. The case is now `"nope"`, which is unreadable as a pid on both platforms; `None`, `2 ** 62` and `-1` were always right. Nothing above the seam was relying on it either: `session.py` writes `os.getpid()`, so bot.py cannot produce a string pid, and `Sessions.alive`'s `isinstance(pid, int)` would stop one before `procs` — which is precisely why nine tests and a year of runs never caught the claim.* Mac: not run — see Pending. |
| W4b `Sessions.stop` | done · Mac pending | 2026-09-19 | 3ee62a7 | **619 ran: 544 pass, 75 skip, 0 xfail**, 37.1s · **not run** | **The listener asks with a file now, on both platforms — and the kill it replaces was not leaking anything, which is not what §6 led this slice to expect.** `stop` is *ask, wait, force*: `procs.request_stop`, then `STOP_GRACE` of polling `alive` at `STOP_POLL`, then `session.terminate` for the runner that did not answer. **Run step, and it is an A/B on one box** (`scratch\w4b_handrun.py`, a real `Runner` over a real ConPTY with a `ping` under it): the marker path ends the session in **5.70s** with `meta.json` at `ended` and all three pids gone; the path it replaces — the same call with the wait skipped, which is exactly the old body — returns in **0.00s** and takes **all three pids just as completely**. So W3e's `KILL_ON_JOB_CLOSE` and the pseudoconsole really do cover a hard kill, and §6's worry about what the fallback leaves behind is answered: nothing. **What the old path lost was the ending, not the tree** — nobody sends claude the two Ctrl-Cs it exits 0 on (W0b, 1.71s), and `meta.json` is left saying `live` by the only process that knew better, which is precisely what the red run printed (`'live' != 'ended'`). On the shipped path `Listener.halt` writes `ended` through `finish` a moment later, so the phone was told the truth before this slice too; `stop` on its own was not, and the 5.70s is the runner spending its whole `GRACE` on a child that ignores a Ctrl-C. **The Mac's timing changes and it is the cost of the slice**: `SIGTERM` moves from the first act to the fallback, so a runner anywhere but `pump` — inside `spawn`, inside the trust dialog — now waits out `STOP_GRACE` before anything it can hear arrives. Taken on purpose (§6), and it is the one thing in this slice the Mac has to check. Three things the entry did not name and the code needed: a record with **no usable `sid`** has no directory and so no marker, an `OSError` writing it is an ask that was not made, and both skip the wait rather than paying fifteen seconds for an answer to a question nobody heard. No `--argv` was added to `session.py`: `Runner` has taken an `argv` since slice 6, so the real-runner test builds its own harness and the launcher keeps the property that its argv is built and never accepted (§10). **Mutation, for the four tests green before the green step:** seven mutations, every one caught — no ask at all (3 tests), waiting although nothing was asked (2), no early return when the runner goes (2), no corpse guard (**1, and only the test this slice added**), the ask aimed at the root instead of the session directory (2), the runner given its own `GRACE` instead of the nested one (2), and `STOP_GRACE = GRACE` (3). +5 tests net (6 new, 1 rewritten away), **no skip count change** — the one Windows test runs here and will skip on the Mac. Two consecutive full runs, 37.9s and 37.1s against W4a's 35.3s: the real-runner test is ~6s of that and is the only end-to-end stop this suite has. W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4c runner outlives listener | done · Mac pending | 2026-09-20 | 1adcb45 | **621 ran: 546 pass, 75 skip, 0 xfail**, 42.3s · **not run** | **The slice had no red test on this platform, and nobody could have known without mutating.** §9's note said W4c's red and green were both already on disk; the green is (`spawn_flags()` since W1c, `**procs.spawn_flags()` in `Sessions.start` since W1b) and the *portable* red is, but the Windows one was reading a copy: `test_procs_win.py` opened by respelling `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` as its own module-level `DETACH_FLAGS`, so `test_breakaway_is_not_among_the_flags` — and the real-process survival test, which spawns `PARENT % DETACH_FLAGS` — were evidence about the test file. **Mutation, before: `session_win.DETACH_FLAGS` gaining `CREATE_BREAKAWAY_FROM_JOB` back, and `spawn_flags()` returning `{}`, were each caught by 0 of 611.** After: one line (`DETACH_FLAGS = session_win.DETACH_FLAGS`) and two tests, and **four mutations are caught by four distinct tests, one each** — breakaway back → `test_breakaway_is_not_among_the_flags`; `spawn_flags()` → `{}` → `test_spawn_flags_hands_popen_the_detach_flags`; `DETACH_FLAGS = 0` → `test_both_detach_flags_are_actually_set` (the vacuity case: `flags & BREAKAWAY` is falsey for empty flags, so the breakaway assertion alone accepts a constant that has lost both); `Sessions.start` dropping the seam → `test_start_passes_the_platform_flags_to_popen`. **Run step, and the mechanism is sound — it was only ever the tests that were not.** `python bot.py --serve` could not be used: `serve()`'s first statement is `procs.Lock(LOCK).take()`, still W4d's stub, so it dies with `NotImplementedError: WINDOWS.md W4d has not been built` — **§9's W4c and W4d amended, and W4e is blocked on W4d for the same reason**. The hand-run is that `serve()` minus the lock, in its own process group (`scratch\w4c_listener.py`), Telegram as two files: listener A takes `claude beacon`, a real runner over a real ConPTY reaches `live` **0.6s** after the message; `CTRL_BREAK_EVENT` (a driver cannot send `CTRL_C_EVENT` to one child) ends A in **0.78s** with status 130; `tasklist` — not `procs.alive`, per W4a — then says all three of runner 6980, ConPTY child 6136 and the `ping` under it 8540 are **still there**; listener B against the same sessions root answers `ls` with the session, `2s` old, link intact, `meta.json` still `live` and **the same two pids**. Unplanned, and carried here because it was found on the way: `test_request_stop_is_atomic_and_idempotent` was failing about one run in five, and it is the *test* that is wrong, not `stop_requested` — four threads appending to one list record append order while the assertion is about evaluation order. Stamped before the call, the answers are in order **0 times out of 80 rounds** across two measurements (9 in 40 and 6 in 40 out of order by append); one list per poller, then 25 runs of the class with no failure. +3 tests (2 Windows, 1 is the flake fix rewritten), **no skip count change here** — the 2 new ones run on this box; on the Mac they will skip. Two consecutive full runs, 42.3s both; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4d mutex | done · Mac pending | 2026-09-20 | 17d8ba0 | **624 ran: 549 pass, 75 skip, 0 xfail**, 36.8s · **not run** | **The seam's last `NotImplementedError` is gone and `--serve` ran on Windows for the first time; the slice's own third red needed nothing, and the case standing next to it was held by nothing at all.** The green is `CreateMutexW` on `Local\centrion-<sha1 of bot.LOCK's path>`, refusing on `ERROR_ALREADY_EXISTS`. **ctypes and not pywin32, and it is not a taste**: `win32event.CreateMutex` returns a `PyHANDLE` that closes itself when collected, and `serve()` says `procs.Lock(LOCK).take()` keeping no reference — so the pywin32 spelling releases the lock on the line that takes it (measured: drop the handle, create again, no `ERROR_ALREADY_EXISTS`). **Red, and the entry's third test was already covered**: W1b's `test_serve_takes_the_lock_before_it_touches_telegram` catches all three ways that line goes wrong — no lock (1 test), the answer ignored (1), `return 1` (1) — each by itself and by nothing else in 623. What nothing held was **the grant**: `if not procs.Lock(LOCK).take() or True`, a listener that refuses itself and exits 0 every ten seconds forever, was caught by **0 of 623**, so the portable test this slice ships is `test_serve_runs_the_listener_when_the_lock_is_granted` rather than the `test_serve_exits_zero_when_lock_refused` §9 named. **Mutation of the green, four ways:** a refusal that leaks its handle — the refused copy would lock out its own successor — caught by `test_lock_released_on_owner_death` alone; one name for every checkout, by `test_second_lock_refused` alone; never refusing, by both; and **dropping `_HELD.append` is caught by 0 of 624 and cannot be caught**, because a raw ctypes `HANDLE` is an integer no collector will close — the list is the lifetime written down rather than left to that, and the comment now says so instead of claiming work it does not do. **Run step, `scratch\w4d_handrun.py`, and it is the first `bot.py --serve` this platform has ever run.** A starts and logs `centrion listening · 1 allowed chat(s) · root ~\Projects\tomtomtomdev · max_sessions 2 · offset None`, then the placeholder token's 401s with backoff — nothing else on that path was hiding a second stub. **B is refused in 0.11s with status 0**, its only output the refusal line, before `deleteWebhook` and before any `getUpdates`: §7's mutual 409 never gets the chance. A is then ended by `CTRL_BREAK_EVENT` with **no handler, no `finally`, no unwinding** — status **3221225786**, `STATUS_CONTROL_C_EXIT`, where W4c saw 130 because *its* scratch listener installed a `SIGBREAK` handler that `bot.py` does not have — and **the lock was taken again 0.100s later, on the first probe**, which is §3's "no stale lock to clear" demonstrated at the worst end rather than argued. A fourth `--serve` then starts normally. **One test bug, found the slow way and worth the line**: `assertEqual(x, y, "…%s" % holder.stderr.read())` evaluates its message whether or not it fails, and reading a live child's stderr waits for the child to exit — sixty seconds, after which the holder was gone, its mutex released, and the test failed saying "another listener was let in" about a process that no longer existed. The mechanism was right the whole time; the assertion was describing the aftermath of its own message. **Amended**: §3's Lock row said "sha1 of config path" where the seam had long since taken `bot.LOCK` (§6 and `bot.py` both say so); §6 gains the ctypes reason, the close-on-refusal rule and the two figures; §9's W4d records the covered red and §9's W4e that it is unblocked, with the two practicalities (no control-event handler in the listener, and a `.telegram.json` still holding W3g's placeholder token). `_later()` is deleted with its last caller. +3 tests (2 Windows real-process, 1 portable), **no skip count change here** — both new Windows ones run on this box. Two consecutive full runs, 36.9s and 36.8s against W4c's 42.3s; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4f the Mac merge lands on Windows | done · Mac pending | 2026-09-27 | 28698b9 | **668 ran: 574 pass, 94 skip, 0 xfail**, 41.7s · **not run** | **A merge nobody had run here broke two tests, and only one of them was a break — the other was a test that had never been about the code.** `5174808` brought ~18 Mac commits into this tree (slice 13's project keyboard, a macOS-only `attach.py`, launchd and cleanup scripts) and the suite went to `668 ran — FAILED (failures=1, errors=1)` against W4d's 624/0/0. **(1)** `test_the_text_is_capped_and_the_keyboard_still_arrives` had never run on Windows: 30 directories of 203 characters under a temp root clears macOS and dies inside `makedirs` with `[WinError 206] The filename or extension is too long`. The property is real and portable — §7's 4096 caps the *text*, the markup is a separate field — so it is bought with the count instead of the width (80 names of 63 characters, ~5.3 KB of list) and **still runs on both platforms**; no decorator, per W4a's rule that only a mechanism that genuinely is the platform's earns one. A third assertion went in first, `assertIn("characters elided", …)`, because the other two are both true of a reply nothing happened to. **Mutation: dropping the markup in `say` fails it on `markups[0] is None`; sending `text` instead of `fit(text)` fails it on the elision guard.** **(2)** `test_a_pid_that_is_not_one_is_answered_rather_than_raised` is **not a regression and not new code** — it asserted `started("4242") is None`, which neither platform promises, because both guards are `int(pid)`. It passed for a year on a free pid and failed the day `EACefSubProcess.exe` took 4242: an assertion about this box's pid table. Now `"nope"`, which is unreadable as a pid on both sides; **mutation: deleting `session_win.started`'s `except (TypeError, ValueError, OverflowError)` makes it error with `ValueError: invalid literal for int()` on exactly that case.** **`bot.py` cannot produce a string pid at all** — `session.py` writes `os.getpid()`, and `Sessions.alive`'s `isinstance(pid, int)` stops one before `procs.alive`, `procs.started` and (through `alive`) `session.terminate` — which is why nothing above the seam ever noticed. §9's W4a and its row are amended; the correction is the finding. **The merge's shared-code diffs were read rather than assumed** (`session.py` +50, `session_posix.py` +21, `bot.py` +123, `config.py`, `telegram.py`), and three sections are amended: **§4** — the posix `Terminal` gained `size`/`resize` for `attach.py`, so the seam's `Terminal` is asymmetric and `test_session.py`'s `SURFACE` check cannot see it (it asserts module names, not class methods), and `session.py --attach` is an unguarded `import attach` → `ModuleNotFoundError: fcntl` here; `pump` still names its four calls and `_serve_viewers` returns `None` on win32 before the import, so nothing shared is broken. **§5** — a sixth config key, `terminal_window`, off by default here and *refused by name* off darwin. **§6** — the keyboard and `telegram.py`'s `reply_markup`/`set_commands` are platform-neutral; checked, and the line is there so nobody re-reads it. **Nothing in the merge contradicts a W-slice's claim.** **Run step, `scratch\w4f_handrun.py` — the merged Mac feature working on Windows, once.** The shipped `bot.serve()` with only `telegram.Telegram._open` replaced (the seam `telegram.py`'s own docstring names), so the real `config.load()`, W4d's real mutex, `Listener.run`, `reconcile` and the real JSON body all run: `centrion listening · 1 allowed chat(s) · root ~\Projects\tomtomtomdev · max_sessions 2 · offset None`, then `setMyCommands` with all five verbs before the first poll, then **`/ls` answered `No live sessions.` carrying a `reply_markup` of 12 `claude <project>` buttons and an `["ls","help"]` row**, `resize_keyboard` and `is_persistent` both set — and bare `claude` and `help` the same keyboard, with `The keyboard holds the first 12. ` in the text because the root holds **32** projects, all 32 `tappable` (none has a space in its name). Three replies in 0.9s; `CTRL_BREAK_EVENT` ended it 130 (this harness installs a `SIGBREAK` handler, where bare `--serve` is W4d's 3221225786). Reconciliation also swept five session directories left from W4d's runs, 7d old — §10.7 running for real. **Suite 624 → 668 is the merge, not this slice**: +44 tests, and skips 75 → 94 are +18 `tests/test_attach.py` (macOS-only, correctly gated) and one more in `test_bot.py`, where `TestTheLaunchdInstall` is now `skipUnless(POSIX)` with a reason that already names W5. This slice changed two existing tests and added none. Two consecutive full runs, 41.9s and 41.7s; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending. |
| W4e end to end from the phone | done · from the phone pending · Mac pending | 2026-09-27 |  8ccf7ad | **671 ran: 577 pass, 94 skip, 0 xfail**, 41.9s · **not run** | **Five of the six checklist items behaved exactly as written, the sixth rewrote a paragraph of §4, and the slice that expected no red found a bug in shared code that sends the phone the same link twice.** First, the part that cannot be done here: `.telegram.json` carries W3g's placeholder token, so the shipped `bot.py --serve` answers `deleteWebhook: HTTP 401 Unauthorized: invalid token specified`, then the same for `setMyCommands`, then `getUpdates: … retrying in 1s` — run for 14s and read, so the block is measured rather than assumed. **No phone reached this listener and the row says so**; the checklist was driven instead through the shipped `bot.serve()` with `telegram.Telegram._open` replaced (W4f's seam), over W4d's real mutex, real `Popen` runners, real ConPTY, real `claude.exe`, the real `projects_root`. **1 — the link: 2.44s and 12.92s on two runs, against a 45s deadline**, `▶ Beacon · Beacon-06a6` and the `session_` URL on its own line. **2 — `ls`: 0.03–0.23s**, numbered from 1, project · name · uptime, link on its own line, `starting…` for the one that had none, and W4f's 13-row keyboard on every one of them (`claude Allstocks` first, `ls`/`help` last). **3 — `stop 1`: 1.14s, 1.56s, 1.70s, 0.96s across the runs and `stop all` 3.45s for two** — all far under W4b's 5.70s, which was a `ping` ignoring its Ctrl-C and not a session; `ls` renumbered behind each one and then said `No live sessions.`, `meta.json` said `ended`, and **every runner and every ConPTY child was gone from `tasklist`**. **5 — the cap: 0.15s and 0.20s**, `2 of 2 sessions already running.`, and the third `claude` spawned nothing and resolved nothing. **6 — the failed tail: 0.83s** to `✗ Beacon · the session did not start.` carrying `unknown option --remote-control` and the real usage text, tilde-collapsed by `scrub` — provoked by pointing `claude_bin` at `python.exe` for one run, with `.telegram.json` written back byte-for-byte after. **4 is the one that did not do as it was told, and it contradicts W3h.** `new w4e-trust-a1` — fresh, empty, under `projects_root` — came up in **2.30s with no dialog**, `📁 … created` in the reply, and an entry written for it saying `hasTrustDialogAccepted: false` without anyone being asked: W3h's finding, still true. But `claude Lab` — an ordinary project **in that same root**, never trusted — **raised the panel every time**, on both runs, and the phone waited the full 45s for `… Lab · no link after 45s`. So W3h's "the variable is *where* the directory is and not what is in it" is **false**, and §4 now says so. `Lab` was left with no `~\.claude.json` entry at all, so the entry is the effect of not being asked rather than the cause; the four directories here that come straight up are the four with `hasTrustDialogAccepted: true`, which are also the four in `githubRepoPaths`, and the rule for a directory Claude Code has never seen is still unknown. **The consequence is the finding: `Trust` is armed only by `new` *and* an empty directory, which is exactly the case that never sees the panel, while the case that does never arms it.** The mechanism is sound — fed the 1622 bytes of the live panel this run captured, `Trust` reaches `asked` and emits Down at chunk sizes 1, 7, 64, 512, 4096 and whole — and it is simply never reached. `new Lab` showed the other half live for the first time: the listener arms it and the runner stands down, `Lab is not empty — leaving §9.3's dialog alone`. **The red nobody predicted.** The first `claude` of the run was answered **twice**, 28ms apart — §9.12's `arrived()` and then the session's own waiter — and so was the first `new`. `Listener.waited` claims before it sends and **ignores the answer**, so `claim()`, whose docstring calls itself the arbitration between the waiter and the tick, was an arbitration only one side listened to; §4.6 promises exactly one reply per session. Shared code, so **the Mac has it too**. Green is three lines: return when the claim is refused, and log which side won. **+3 tests, all portable, all red first** — `test_the_waiter_says_nothing_when_the_tick_announced_the_link_first` and `…_the_ending_first` failed 2 != 1 on the unfixed code, and `test_a_waiter_that_claims_the_session_does_answer` is the vacuity guard, **green before and after**, because a waiter that answered nothing would have passed the other two. Mutating the green the other way — `claimed = False`, a waiter silent even when nothing claimed it — is caught by three tests that were already there (`test_a_live_record_with_no_link_is_not_reported_as_a_link`, `test_a_session_that_never_comes_up_is_reported_rather_than_forgotten`, `test_giving_up_does_not_claim_the_session_is_gone`), so the timeout half was held all along. **Honest about the amplifier:** the driver's fake long poll returns in 0.2s rather than 50, which turns a window of about half a percent per session into a coin flip. The cadence is why it was seen; the discarded return value is the defect. **Two more, carried as notes and not as work.** A TUI panel's tail reaches the phone with its spaces gone — `Quicksafetycheck:Isthisaprojectyoucreated…` — because ConPTY positions each word with `ESC[<n>G` rather than writing a space, and turning those into whitespace would risk putting one inside the `session_` URL (§4; an ordinary error's tail, which is plain stdout, keeps its spacing). And **every runner here is two processes**: `.venv\Scripts\python.exe` is a 274 KB `venvlauncher`, not a copy of the 105 KB base, so the `Popen` pid §14 logs and the `runner_pid` in `meta.json` name the launcher and the runner respectively, forever (§6). Nothing reads the wrong one. **The run step's own bug, for the next person who scripts one:** a detached runner inherits the driver's stdout, so a hand-run piped into `tail` never sees EOF and hangs until the session is stopped — redirect to a file. +3 tests and **no skip count change** — all three are portable and must run on the Mac. Two consecutive full runs, 41.9s and 42.0s against W4f's 41.7s; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending, which gains four rows here. |
| W5a `bot.cmd`, `install.ps1` | done · Mac pending | 2026-09-27 | 178b5fa | **683 ran: 589 pass, 94 skip, 0 xfail**, 37.2s · **not run** | **The startup loop's throttle, written down in §7 and copied out of it verbatim, does not throttle unless the file happens to have been launched with a console — and the first test of it was caught by nothing.** `timeout /t 10 /nobreak` with stdin anything but a console prints `ERROR: Input redirection is not supported, exiting the process immediately`, sets `errorlevel` 1 and returns in **0.02s**; measured against 10.09s for the `ping -n 11 127.0.0.1` fallback that now sits behind it, and 0 `ping.exe` processes across a whole console hand-run, so the belt costs nothing where the braces hold. §7 amended with both figures. It matters more than a tidy loop: the throttle is the only thing between a listener that cannot start at all — a missing venv, a refused config — and two log lines every twenty milliseconds until the disk is full, and the failure is *silent*, because a hot loop and a healthy listener look identical from outside. **Red: 9 of the 12 new tests failed and 3 passed before the code existed, which the entry did not predict** — `requirements-win.txt` is W2b's, `.gitignore` has named `.venv/` and `scratch/` since slice 0 (its comment there already says "W5a's install.ps1 creates it"), and `test_the_startup_files_are_tracked` passed *vacuously*, because `git check-ignore --no-index` matches patterns and answers "not ignored" for a file that is not there. All three were mutated rather than waved through: dropping the two `.gitignore` lines, adding `windows/` to it, and moving `requirements-win.txt` aside were each caught by exactly one test and by nothing else in 683. §9's W5a amended to say which three and why. **Mutation of the green, eleven ways, ten caught one-for-one and the eleventh caught by nobody.** `--serve` removed, `goto loop` removed, the `goto` put behind an errorlevel, `>>` turned into `>`, `2>&1` dropped, `cd /d "%~dp0.."` replaced by `%CD%`, the venv python replaced by the system one, and the installer's three (hard-coding this checkout, dropping the lockfile, dropping `icacls`) — each caught by one test and only one. **Removing the throttle was caught by 0 of 683**, because the first `test_bot_cmd_throttles_the_restart_by_ten_seconds` accepted either wait and the conditional `ping` line still reads as a wait — while in fact, with the unconditional `timeout` gone, `errorlevel` after an `echo` is 0 and the `ping` never runs. The test now requires one wait that is not behind an `if`, and the mutation is caught. W4c's rule again, on a slice that thought it was writing two files: *"the test exists" and "the test would fail" are different claims.* **Run step, two parts.** `install.ps1` on a `git clone` into `%TEMP%`: **10.1s** fresh (venv plus three wheels), **1.7s** on a re-run, exit 0 both times, and the clone it left runs 73 of its own tests green from its own venv — which is the real claim, pywin32 being the thing the suite cannot start without (W2b). It also found a bug in its own first draft: `@(...) | Select-Object -Skip 1` returns a **scalar** when one element is left, and **splatting a scalar String in PowerShell 5.1 passes nothing at all** — measured, `$e = "-3"; & py @e -c "print(3)"` opens an interactive REPL — so the `py -3` probe answered nothing, `py` was skipped without a word, and the fall-through to `python` hid it on this box. On a stock python.org box, where "Add python.exe to PATH" is off by default and the launcher is all there is, that is `no python found on PATH` with `py.exe` sitting in front of it. The probe is now `--version` against a `[string[]]`, and the `2>$null` is gone with it: under `$ErrorActionPreference = "Stop"` redirecting a native command's stderr turns each line into a terminating error, so the Store alias's complaint would have been caught as a crash rather than read as a no. **Part two, `windows\bot.cmd` in a real console (`scratch\w5a_handrun.ps1`).** Listener up and logging at 11:53:43.01, mutex taken, then W3g's placeholder token's 401s exactly as W4e recorded them; `taskkill /F /T` on the venv launcher at 11:53:49, `=== listener exited 1 ===` **0.08s** later, `=== centrion listener starting ===` again at 11:53:59.15 — **10.07s after the exit line, 10.19s after the kill** — and the second listener reached `centrion listening` with no stale mutex to clear, which is W4d's 0.100s seen again from the loop's side. Both `===` lines in `var\bot.log`, exactly as the entry asks. Killing the `cmd.exe` tree left 0 listeners. **Two notes carried rather than built.** The listener is two processes like every runner (W4e): `.venv\Scripts\python.exe` is the launcher, its child is the real interpreter, and the loop restarts whichever of them dies. And **nothing sets `PYTHONIOENCODING`**, so `var\bot.log` is written in the ACP — cp1252 here — and a project name outside it reaches §14's one diagnostic backslash-escaped; nothing raises, `sys.stderr` being `backslashreplace`, and the fix if it is wanted belongs in W5b's XML environment and not in a batch file. §9's W5b carries both. **§7 amended three ways**: the throttle above; the note that `bot.cmd` needs no substitution at all, where the plist needs `__CHECKOUT__` rendered, so the XML is the only rendered file left; and the `install.ps1` paragraph, which claimed the script registers the task (W5b's half) and sets the token's DACL (it deliberately does not — a file created under the checkout, or under `%TEMP%`, already inherits owner/SYSTEM/Administrators, so the script runs `config.load()` and prints whatever `config.py` says, which is the only `icacls` line W2b found to work). **+12 tests, all portable, none decorated, no skip count change.** Two consecutive full runs, 37.3s and 37.2s against W4e's 41.9s; W3c's unidentified ~3% error did not recur. Mac: not run — see Pending, which gains four rows here. |
| W5b scheduled task | done · Mac pending | 2026-09-27 | a33eccb | **699 ran: 605 pass, 94 skip, 0 xfail**, 37.2s · **not run** | **The task defaults into the plist's loudest bug under a different name, and the file W5a committed one slice ago does not parse at all.** **(1) `Priority`.** A task registered with no `<Priority>` gets **7**, which is `BELOW_NORMAL_PRIORITY_CLASS`, and a child inherits its parent's priority class — so every Claude Code session started from the phone would run below normal for as long as it lived, with the symptom "Remote Control feels slow" days later and nothing in any log. That is word for word the plist's `ProcessType Standard, NOT Background` warning, and §7's table did not have the row. Measured with the whole tree cleared between registrations: **no element → `cmd.exe`, the venv launcher and the interpreter all `BelowNormal`; `<Priority>5</Priority>` → all three `Normal`.** **(2) `windows\install.ps1`, as `178b5fa` committed it, answered nine parse errors and ran nothing.** Windows PowerShell 5.1 reads a BOM-less `.ps1` in the ANSI code page; a U+2014 em dash is `E2 80 94`, `0x94` in cp1252 is a curly closing quote, PowerShell honours it as a string terminator, and one of them inside one `throw "…"` unbalances every quote after it. There is no `pwsh` here and `pwsh` is the only version that would have read it as UTF-8. W5a's twelve tests all read that file as text and none of them ran it — **a run step measures the bytes that were run, and the commit is a different set of bytes unless something checks.** The rule is now one per directory, everything under `windows\` is ASCII, and `test_nothing_under_windows_is_anything_but_ascii` is portable so the Mac holds it too. **Three of this entry's own predictions were wrong and §7 and §9 are amended in place.** `install.ps1` has **two** `-replace`s, not one: a task lives in a machine-global store, so its `LogonTrigger` must name a `UserId` or it means *any* user's logon, which `schtasks /Create` refuses from an unelevated shell with **"ERROR: Access is denied"** and no mention of the trigger (the *principal's* `UserId` may be omitted; the trigger's may not). The XML **cannot** carry `PYTHONIOENCODING` or anything else, because the schema has no environment element at all — `<EnvironmentVariables>` and `<Environment>`, in `Settings` and in `Exec`, are each "ERROR: The task XML contains an unexpected node" — so the only place left is the `set` in `bot.cmd` this entry said it did not belong in; **decided rather than inherited, it stays unset**, with the reasoning written above the loop (`type var\bot.log` is §14's instruction, ASCII escapes stay readable there and UTF-8 would not, and `sys.stderr`'s `backslashreplace` means nothing raises either way). And the third `this_mac_checkout` counterpart has nothing to compare against: **Task Scheduler has no `StandardOutPath`**, so the test asserts the join instead — the action is `bot.cmd` and `bot.cmd` is the only thing that appends `var\bot.log`. **Encoding, measured five ways, because every wrong answer is the same "ERROR: The task XML is malformed" with a column number**: `schtasks` refuses `encoding="UTF-8"` in the prolog outright ("unable to switch the encoding") and refuses a UTF-8 BOM ("incorrect document syntax" at (1,2)); it accepts `encoding="UTF-16"` over ASCII bytes and UTF-16LE-with-BOM over UTF-16 ones, but expat then will not read the committed file and UTF-16 is a blob git diffs as binary. `<?xml version="1.0"?>`, ASCII, no BOM is the one spelling both accept — and PowerShell 5.1's `Out-File -Encoding utf8` writes a BOM, so the natural way to write the rendered file is the broken one. **Red: the new class errored in `setUpClass` on the missing file (its 13 blocked) plus one failure, count and skips unchanged at 683/94** — and **one existing test passed before the code existed**, `test_the_startup_files_are_tracked`, vacuously and for W5a's exact reason, so it is now written as a pair with the presence check and the comment says why. **Mutation of the green, twenty-two ways, twenty caught one-for-one and two by nobody at first.** One of those two was the mutation script's own whitespace bug. The other was real: `test_install_registers_the_task_from_the_rendered_xml` stayed green through a mutation that deleted the `schtasks /Create` call, because the header block *documents* that command and the script's own `throw "schtasks /Create failed …"` names it — comments **and** string literals now come out before the assertion, and the mutation is caught. W4c's rule for the third slice running. **Run step, three parts, `scratch\w5b_handrun.ps1`, `w5b_restart.ps1`, `w5b_priority2.ps1`.** `install.ps1` end to end, exit 0 in **2.1s** on a re-run with the registration (W5a's 1.7s without it), `config.load()` still green on the token, task `Ready`. Then `schtasks /Run`: **`centrion listening` 0.37s after the scheduler was asked, first Telegram call at 0.92s**, three processes (`cmd.exe` → venv launcher → interpreter, all `Normal`), and `schtasks /Query /TN centrion /XML` shows `PT0S`, `IgnoreNew`, both battery settings `false`, `StartWhenAvailable`, `Hidden`, `InteractiveToken`, `Priority 5` and `RestartOnFailure PT1M`/3 all surviving the round trip (the trigger's `UserId` comes back as a SID, the principal's as the name). Then `taskkill /F /T` on the venv launcher: exit line **0.16s** later, restart line **9.36s** after that — **and the console question is answered, `timeout` did not complain and 0 `ping.exe` ran, so the scheduler's action has a console after all** (§7 amended; the fallback is kept for the launchers that are neither). **`schtasks /End` kills the action and leaves the pythons** — W0c from the other direction, noted on W5c. **What could not be proved here**: the logon leg of "logon to first poll". Logging off ends the session driving the slice, the Task Scheduler operational log is disabled on this box and enabling it needs elevation, so **the measured figure is 0.92s from the scheduler starting the action to the first `getUpdates`** and the logon-to-action leg is a named debt below. The Telegram call was HTTP 401 as it has been since W3g — placeholder token, a standing debt, not a slice failure. **+16 tests, 13 of them portable and undecorated and 3 gated `this_windows_checkout` — the counterparts of the `this_mac_checkout` three, because "absolute, and present" about `__CHECKOUT__\windows\bot.cmd` is a question only this platform can answer. No skip count change, because a `skipUnless(win32)` runs here.** Two consecutive full runs, 37.7s and 37.2s against W5a's 37.2s. Mac: not run — see Pending, which gains four rows here. |
| W5c restart survival | done · Mac pending | 2026-09-27 | 67fc632 | **700 ran: 606 pass, 94 skip, 0 xfail**, 37.7s · **not run** | **Two of the four checklist items came back exactly as §7 says, and the third could not be taken against the task at all, because `schtasks /Run` starts a `bot.cmd` that cannot open `var\bot.log` while the session it is asking about is alive — and the wrapper does not fail, it spins.** **The checklist, written into this row before the run (step 1):** (1) `/Run`, then a session through that listener; (2) `/End` — runner and `claude.exe` still alive, record still `live`; (3) `/Run` — the new listener adopts the record and `ls` shows the same runner pid; (4) `stop 1` ends it. Predicted: all four hold and no new code. **Item 1, and it is the first Claude Code session ever started by a Task Scheduler listener.** `/Run` at 12:53:53.74 → `centrion listener starting` at 12:53:53.89 → `centrion listening` at 12:53:54, and the tree is **three processes plus a `conhost.exe`** — `cmd.exe` 13772 ← the scheduler, the venv launcher 8528, the interpreter 952, all `Normal` (W5b's `<Priority>5</Priority>` holding), and the conhost is the console W5b inferred from `timeout` not complaining, now visible as a process. `claude Beacon` at 12:54:14.13 → `state: live` with a scraped link at **12:54:17**, and the session is itself **five more processes**: a runner launcher 9312, the runner 8112 (which is what `meta.json` calls `runner_pid` — `Sessions.start` returns the *launcher's* pid, 9312, and the two are not the same number), an `OpenConsole.exe`, a `conhost.exe` and `claude.exe` 2240. **Item 2, `/End` at 12:54:46.25:** `cmd.exe` 13772 is the only thing that dies. The launcher, the interpreter, the runner, OpenConsole and `claude.exe` are all alive two seconds later, the record still says `live`, and **the orphaned listener is still serving** — an `ls` sent at 12:54:56, with `schtasks` reporting `Status: Ready`, was answered by pid 952 with the session at 41s old. W5b's `/End` finding confirmed from the other side, and the entry's warning — do not read "the task is not running" as "nothing is listening" — is now a measured sentence rather than an inference. **Then item 3 did not happen, and that is the slice.** `/Run` at 12:55:06.76 started `cmd.exe` 2016 which, 80 seconds later, had **no python child, no `ping.exe`, not one byte in `var\bot.log`, and 8.9s of CPU** — 13% of a core, measured over a 6.8s window, with the task showing `Running` and last result `267009`. **`cmd` opens a redirection target denying other writers, so a process holding `var\bot.log` open for writing makes every `>>` in `bot.cmd` fail** — and after `/End` there is always one, because the orphaned listener keeps the handle its dead `cmd.exe` created (verified directly: `open_files()` on both leftover pythons names `var\bot.log`). **A failed redirection leaves `ERRORLEVEL` at 0** — measured in both spellings with the level reset to 0 before each — so `if errorlevel 1 ping`, W5a's entire belt, never fires; and the command it guards was itself skipped, because `timeout /t 10 /nobreak > nul 2>> var\bot.log` redirects to the same log. Two failures that each look survivable compose into the one §7 exists to prevent: **a hot loop, and this time a silent one, with not even the two log lines a second that would have named it.** W5a's rule for the third time — the throttle does not get to depend on how the file was launched — extended: it does not get to depend on the log either. **Green: one line, `2>> var\bot.log` off the `timeout`**, held by `test_the_throttle_does_not_redirect_to_the_log_it_cannot_always_open`, portable and undecorated because it is a property of the committed bytes. Re-measured after the fix: the same `/Run` against the same locked log used **0.00s of CPU over 24s** with `timeout.exe` visible in the tree, and an ordinary restart under the task (no session, log free) was an exit line **0.04s** after the kill and a start line **9.66s** later with 0 `ping.exe` — the console path is untouched, against W5b's 9.36s. **The bigger finding, which the fix bounds and does not remove: the runner holds the log too.** `Sessions.start` inherits stdout and stderr so the runner's lines land in `var\bot.log` (§6), which on Windows means a live session pins the file for its whole life — `open_files()` on runner 8112 and its launcher 9312 both named it, and the runner's own `stop requested` and `ended` lines reached the log at 13:05:10 through a handle whose listener had been dead for four minutes. So the `KeepAlive` loop cannot restart a listener that died while a session was running: `cmd.exe` 552 sat from 13:00:03 doing nothing but throttling, and opened the log **1.1s** after the runner exited at 13:05:12. That is the loop being unavailable in the one state it exists for, and it is **W5e**, added to §9 — the shape of the fix is a decision (the runner's stderr moves to the session directory, or the wrapper stops using `cmd` redirection) and it goes behind the `procs` seam because the Mac has no such problem. `||` *does* see a failed redirection although `ERRORLEVEL` does not, measured, which is the hook W5e will want. **Items 3 and 4 taken against a listener started the way `bot.cmd` will start one after W5e** — same venv interpreter, same `--serve`, same mutex, same records, stderr to a file of its own — because the task's wrapper could not start one while the session was live. It came up at 13:04:41 with `offset 1003` carried across, **announced no ending** and did not touch the record (mtime still 12:54:17), and `ls` at 13:04:56 rendered `Beacon-4c93 · 10m` with the link and **runner pid 8112 unchanged, started 12:54:15, `claude.exe` 2240 still under it** — a listener holding a record whose runner it never spawned, which is SPEC.md §12 slice 9's real claim and the reason that slice was about reconciliation more than about launchd. `stop 1`: **2.3s** from the message file appearing to the runner being gone, all five pids gone, record `ended`, reply `■ Beacon · Beacon-4c93 stopped.` (W4b's 5.70s was a session with a `ping` under it ignoring its Ctrl-C). **And the loop recovered on its own, which is the last thing this slice has to say.** The moment the runner exited, `cmd.exe` 552 — blocked for 5m10s — opened the log at 13:05:13.15, started a listener that W4d's mutex refused in **0.14s** with status 0, and tried again **9.87s** later; when the hand listener was killed at 13:05:43.03 the task's own listener was up **0.12s** later with the offset again carried across. That is this entry's own prediction, "refused by the mutex in about a tenth of a second and restarted every ten thereafter", measured — and it only reads that way because the throttle was fixed first. **Red, and W5c predicted green, so the ritual's second clause applies: four mutations, one per checklist item, each caught.** `DETACH_FLAGS = 0` (the runner not detached from its listener) → 1, `test_both_detach_flags_are_actually_set`, W4c's vacuity case again and still the only one. `alive()` trusting `kill(pid, 0)` alone with no start-time check → 3. `Sessions.stop` reporting success without asking or waiting → 5. `AllowStartOnDemand` false → 1, `test_the_task_can_be_started_on_demand`, which W5b wrote for this slice. **Nothing in the suite held the thing the run actually broke**, which is the fifth slice running where the mutation aimed at the entry's predictions and the run found the defect somewhere else. **The run needed the wire, and the wire could not be a script.** `.telegram.json` holds W3g's placeholder token, so every real Telegram call is a 401 and no message can reach a task-started listener; W4f's answer — replace `telegram.Telegram._open` and nothing above it — could not be used as written, because the scheduler runs `bot.cmd` and there is no script to wrap. The substitution is a `.pth` dropped into `.venv\Lib\site-packages` that arms on a marker file (`scratch\w5c_wire.py`), so the registered task, `bot.cmd`, `centrion.xml` and `bot.py` are all the committed bytes. Worth writing down for whoever needs it next: `site.addpackage` execs a `.pth` line with **site's** globals and its own locals, so a module written the ordinary way sees none of its own names from inside a function — it needs a namespace passed to `exec`. **+1 test, portable, no skip count change.** Two consecutive full runs, 37.7s and 37.8s against W5b's 37.2s. Mac: not run — see Pending, which gains four rows here. |
| W5d not-logged-on | skipped (refused, not deferred) · Mac n/a | 2026-09-27 | 313c218 | **708 ran: 613 pass, 95 skip, 0 xfail**, 38.2s · **not run** | **The switch this slice exists to try cannot be made on this box, and the recipe for trying it would have given a false negative anyway.** §7 offered `LogonType Password` plus "run whether user is logged on or not" as the way to close the gap, and W5d's own run step was "switch it, reboot, do not log in, message the bot". Nothing after the first comma was reachable, and the wall is not the logoff. **(1) This account has no password.** `schtasks /Create /XML` with `<LogonType>Password</LogonType>` (and with `InteractiveTokenOrPassword`) answers **"ERROR: Account restrictions are preventing this user from signing in. For example: blank passwords aren't allowed"** — ERROR_ACCOUNT_RESTRICTION, **1327**. Proved to be the blankness rather than a wrong guess without guessing anything: `LogonUser` with an empty password answers **1327 for `BATCH`, `SERVICE`, `NETWORK` and `INTERACTIVE` alike**, never 1326, so the empty string was *accepted* as the credential and policy refused the logon type; `Get-LocalUser tommy` confirms `PasswordRequired False`, `PasswordLastSet` empty. A task that runs whether the user is logged on or not needs a batch logon, and this user has none until the account has a password — a decision about the user's own machine, in the same class as the phone token, not something a slice may take. **(2) Both ways round a stored password need elevation.** `<LogonType>S4U</LogonType>` — the no-password spelling of the same principal — is **"ERROR: Access is denied"** unelevated, and so is a `<BootTrigger>` under either logon type. Three registration attempts, three refusals, and no throwaway task was left behind (`schtasks /Query /TN centrion-w5d`: "The system cannot find the file specified"). **(3) The recipe was wrong, and this is the half worth keeping.** `centrion.xml`'s only trigger is a `LogonTrigger`, which fires on a logon and nothing else. "Switch the logon type, reboot, do not log in" leaves *nothing to start the task*, so the run would have measured a bot that was never triggered and written it down as a bot that cannot run without a session. The logon type and the trigger are one decision in two elements and must move together; **`test_the_logon_type_and_the_trigger_agree_about_when_the_bot_runs`** now holds the pair in both directions (InteractiveToken ⇒ the only trigger is the logon one; anything else ⇒ a `BootTrigger` must be there). **What *was* measurable, and it is the control this document did not have:** under `schtasks /Run`, all three of the task's processes — `cmd.exe`, the venv launcher, the interpreter — report **session 11**, this desk's own interactive logon session, all at `Normal` (W5b's `<Priority>5</Priority>` still holding). "The bot exists only while this user is logged on" is therefore not a policy statement but a process fact: the listener lives *inside* the session a logoff destroys. `/End` again left **2 orphaned pythons** with the task `Ready` (W5c, third sighting); both killed, task left `Ready` with nothing running. Half of §7's optimism confirmed: Claude Code's login is `%USERPROFILE%\.claude\.credentials.json`, **509 bytes of plain UTF-8 JSON**, one key `claudeAiOauth` — a file in the profile, not DPAPI and not a keychain, so any logon of this user that can read the profile can read it. **The other half, "ConPTY does not care", is untested and untestable from here**: nothing unelevated can create a non-interactive session to try it in. **Also found: `schtasks /Query /TN centrion /XML` is not the committed file with its placeholders filled in** — the scheduler normalises the `Principal`'s `UserId` to a **SID**, the trigger's to `DOMAIN\user`, drops `<RunLevel>LeastPrivilege</RunLevel>` and the trigger's `<Enabled>true</Enabled>` as defaults, and emits UTF-16; §7 amended, because "readable with `schtasks /Query /XML`" reads as "diffable against the file" and it is not. **Red, the mutation case** (the entry named no tests, so the new one passed on arrival): mutation 1, `InteractiveToken` → `Password` in the committed XML, failed 2 of 40 in `test_layout` — the new test and `test_it_runs_as_the_logged_on_user_with_no_stored_password`; **`test_the_trigger_is_this_users_logon` did not fail**, correctly, the trigger being untouched. Mutation 2, a `<BootTrigger>` added beside the `LogonTrigger` with the logon type left alone, failed **only** the new test — **39 of 40 passed, including both of the tests that already read the principal and the trigger**, which is the whole point of it: nothing in this repository could see half of this move before. **One row of §7's table is doubted and not corrected**: `StartWhenAvailable` "a missed logon trigger still fires" is documented by Microsoft as a time-based-trigger setting, and a logon trigger is not one — nothing here can distinguish the two without a logoff, so it stands with the doubt written against it and the logoff time item names what would settle it. **Registration unchanged**: `InteractiveToken`, `LeastPrivilege`, one `LogonTrigger`, `<Priority>5</Priority>`, `Ready`. Nothing was switched, so nothing was reverted. |
| W5e runner pins `var\bot.log` | done · Mac pending | 2026-09-27 | 1d8c1df | **707 ran: 612 pass, 95 skip, 0 xfail**, 37.9s · **not run** | **The decision went to the runner and not to the wrapper, and the thing that decided it is the Mac.** W5e's entry offered two fixes — the runner logs somewhere else, or `bot.cmd` stops using `cmd` redirection and puts a writer process behind a pipe — and the pipe loses on three counts, none of them taste. It costs a process and a file on every iteration of the loop whose entire job is to be reliable, and that process is a new single point of failure standing in front of the only log there is. It **breaks `%errorlevel%` after the listener**: a pipeline's level is its last command's, so `=== listener exited N ===`, one of the two lines §7's whole log is made of, would report the writer's status instead of the listener's. And it adds untested machinery to the path W5a and W5c have each already had to repair — which is W5c's own rule, *a fallback that shares a dependency with the thing it is backing up is not a fallback*, pointed at the fallback. The other side needs one seam function, and **W5c's Pending row is the argument for it**: `bot.sh` appends with `>>`, the Mac's runner inherits the same fd, and on POSIX that costs nothing — so the platform that pays is the platform that changes, and `session_posix.runner_output` yields `{}` with `Popen` called exactly as before. The price is that **SPEC.md §14's first diagnostic is two files on Windows**: `var\bot.log` keeps the listener's side of a session and `var\sessions\<sid>\runner.log` takes the runner's four lines. Paid in `windows\bot.cmd`'s header, which now names both. **The third half of the entry — `\|\|` to run the listener unredirected — is decided against rather than deferred.** It is only wanted in a state the fix removes: afterwards the only thing that can still pin the log is a *listener*, and a listener holding the log is a listener answering the phone. Where it would fire it buys a copy the mutex refuses; where it would succeed it costs §14 entirely — an unredirected listener under the scheduler writes to a console nobody has, and it holds the mutex, so the loop never replaces it. Up and permanently blind is worse than a ten-second retry that heals. **Run step, against the registered task, `scratch\w5e_run.py` and `scratch\w5e_probe.py` with W5c's `.pth` wire re-armed as `w5e_wire.pth`.** `/Run` at 13:30:49.35 → wrapper 10292, venv launcher 8124, interpreter 18888, `centrion listening` at 13:30:49; `claude Beacon` → session `203b0b` **live with a scraped link at 13:31:24**, runner 10084 (record) under launcher 10928 (the W4e split again, and the log line still names the launcher). **The measurement that is the slice**: `open_files()` with the session live names `var\bot.log` on the three listener processes **and nothing else** — runner 10084 and its launcher 10928 name `var\sessions\203b0b\runner.log` instead, where under W5c there were five holders. Listener killed at 13:32:16.12 with the session still live: `=== listener exited 15 ===` reached the log **0.02s** later — under W5c it reached nothing at all — `=== centrion listener starting ===` **10.03s** after that, `centrion listening` **0.11s** after that, **against W5c's 5m10s of a wrapper that could do nothing and a recovery 1.1s after the runner exited.** The throttle's own figures are untouched (W5c: exit +0.04s, restart +9.66s). The restarted listener adopted the record and `ls` rendered `Beacon-203b · 2m` with the link at the same runner pid; `stop 1` ended it in ~2s, record `ended`, and the runner's `spawned pid 1516`, `live: <url>`, `stop requested` and `ended` are all in `runner.log` — the four lines that used to be §14's. **The `/End` case is bounded, not removed, and that is deliberate.** `/End` took cmd.exe 10292 and left the pythons holding the log (W5c confirmed from a third side); the `/Run` after it started cmd.exe 15892 which for 25s wrote nothing, started nothing, kept `timeout.exe` in its tree and used **0.00s of CPU** — W5c's throttle fix doing exactly its job — and took the log **4.67s** after the orphan was killed, with a listener up 0.02s later. An orphaned listener is still serving, so that state is a bot that is up; the state W5e existed to remove was a bot that was down and could not start. **Red: 6 of the 7 new tests ran here and all 6 were red, the seventh is posix-only and skipped, and one existing test changed colour on purpose** — `test_session_exposes_the_chosen_module_as_procs`, because `TestThePlatformSeam.SURFACE` gaining `runner_output` *is* part of the red. 707/605/95 before the code, nothing else moved. **Mutation of the green, four ways, all caught and three one-for-one.** `runner_output` yielding `{}` on Windows (the fix undone at the platform) → 5, including the property test; the `with` dropped from `Sessions.start` → exactly 2, the property test and the wiring test; the `finally: handle.close()` dropped, so the listener keeps the runner's log → exactly 1, `test_the_listener_does_not_keep_the_handle_it_hands_over`; `"ab"` → `"wb"` → exactly 1, `test_it_appends_and_never_truncates`. **The fifth mutation is the one this box cannot run and it is the important one**: making `session_posix.runner_output` yield the Windows shape — the Mac paying for a Windows file-sharing rule — is caught by **0 of 707 here**, because `session_posix` will not even import without `fcntl`. `test_the_runner_still_inherits_the_listeners_log_on_the_mac` is that guard and it is a Mac row below. **Three things the entry's Red line did not say and the test needed.** `subprocess` with `stdout=None` hands `CreateProcess` the value of `GetStdHandle(STD_OUTPUT_HANDLE)` and **not fd 1**, so reproducing an inherited `cmd` redirection needs `SetStdHandle`; a `dup2` test would have passed against the bug. The `cmd` probe must be one string, because `list2cmdline` escapes an embedded quote as `\"` and `cmd` then fails on the *path* — which is how this test first went green-for-the-wrong-reason with the message `The filename, directory name, or volume label syntax is incorrect.` instead of `The process cannot access the file because it is being used by another process.` And a fourth, found by a test that failed: **a `cmd` `>>` fails against any existing writer, however permissive that writer's own share mode** — Python's `open` restricts nothing and `runner.log` is still not appendable from a second process. What makes it safe is not its sharing but that `bot.cmd` never opens it; what §14 needs is a *reader*, and `type` works while the session runs. **§7 amended three ways and §6 gains a W5e note.** §7's promise that `\|\|` "is the hook the fix in W5e will need" is **wrong** — the fix took the writer off the log instead, and `bot.cmd` is unchanged below its header. §7's ERRORLEVEL sentence is sharpened rather than corrected: `cmd /c echo x >> <locked>` **exits 1**, and it is only the in-script `ERRORLEVEL` that stays 0, because a command that did not run sets nothing — the same fact `\|\|` sees from the other side, and the reason a wrapper around `cmd` can tell what a line inside `cmd` cannot. And §7's log-handle paragraph now carries the after figures and the orphan residual. **+7 tests: 5 win32 (1 in `TestSpawningForRealOnWindows`, 4 in a new `TestWhereTheRunnersLinesGoOnWindows`), 1 portable and undecorated in `TestTheListenerUsesThePlatformSeam`, 1 posix-only — so skips go 94 → 95, and that one skip is the Mac's row below.** Two consecutive full runs, 37.9s and 37.9s against W5c's 37.7s; W3c's unidentified ~3% error did not recur. Box left as W5b and W5c left it: task registered and `Ready`, nothing running, no process holding `var\bot.log`, the `.pth` disarmed and removed. Mac: not run — see Pending, which gains four rows here. |
| W6 CI matrix | done · Mac still pending | 2026-09-27 | 166c861 | **713 ran: 618 pass, 95 skip, 0 xfail**, 42.9s · **CI macOS 3.9.6: 713 ran, 624 pass, 89 skip, 0 xfail, 14.1s — green, and it is not the user's Mac** | **The matrix went up, the two macOS jobs passed on the first push, and the `windows-latest` job failed four tests — every one of them a test this desk has never run. The Mac was not the risk; the desk's blind spots were.** **What CI is.** `.github/workflows/test.yml`, three jobs, `on: push`/`pull_request`/`workflow_dispatch`, `timeout-minutes: 20`, no secrets and no `.telegram.json`: `windows-latest` at 3.12 installing `requirements-win.txt` (without it W2b's DACL check has no `win32security` and 41 config tests *fail* rather than skip — the runner has no venv to forget), `macos-latest` at `/usr/bin/python3`, and `macos-latest` at 3.12. **The interpreter decision, which the plan left open: `/usr/bin/python3` on a macos-latest runner is Python 3.9.6** — the Command Line Tools build, not the toolcache — which is the same 3.9 the whole Mac guard and the ritual's step 3 are written against. So the macOS job runs the literal command every "Pending on the Mac" row names, with no `setup-python` at all, and the 3.12 twin exists only as a control: the pair separates "a 3.10+ construct" from "a posix mechanism that rotted", which one job cannot. §9's W6 entry and the ritual's closing paragraph are amended; §9 gains **W7**. **Wall-clock.** Run 1 (`36306902010`): macOS 3.9 **25s**, macOS 3.12 **29s**, Windows **1m18s**, Windows red. Run 2 (`36307406677`): macOS 3.9 **21s** (suite 14.1s), macOS 3.12 **22s** (suite 15.2s), Windows **1m11s** (suite 45.1s), all green. Run 3 (`36307691457`) is the merge itself, the commit this row names, on `main`: macOS 3.9 **24s** (suite 14.9s, 713 ran, 89 skip), macOS 3.12 **21s** (suite 14.7s, 89 skip), Windows **1m14s** (suite 49.3s, 85 skip), **all green — a branch-green workflow is green on `main` too, which is the formality this row is allowed to call a formality only because it was checked.** **Three pushes, two of which changed anything.** The first was the workflow as written; the second carried the four test fixes below and bumped `checkout@v4`/`setup-python@v5` to `@v5`/`@v6`, which is what the runner's Node 20 deprecation annotation was asking for; the third is the merge. The first two went to a `w6-ci` branch, because a workflow proves itself only by running and an unproven one does not belong on `main`; the history shows one commit because the branch was amended rather than piled on, so **the number two lives here and nowhere else in the repository**. **The four Windows failures, and none is a port bug.** *(1)* `test_a_dangling_symlink_is_refused` — **the first execution of this test anywhere**, because this account cannot make symlinks; refused at check 3, not check 4. `ntpath.realpath` cannot canonicalise a link whose target is absent and returns the reparse point's stored spelling verbatim, and a runner's `%TEMP%` is the 8.3 alias `C:\Users\RUNNER~1\...`, so its dirname is not the long-form root. Boundary intact — a dangling link is refused on every path through — so the fixture now spells its target through `realpath(root)`, which is what it always meant, and §5.3 carries the finding. Whether `_child` should see through an unresolved reparse target is **W7**, not a repair to make inside an optional slice. *(2)* `test_the_default_on_windows_is_what_is_on_path` errored: **the only test in the suite that needed a program installed to pass.** Claude Code is not on a runner, so `DEFAULT_CLAUDE_BIN` is `None` and `load()` raises instead of returning something to compare. Gated on the installation; the `None` case was already covered portably by `test_a_default_that_is_not_on_path_is_refused_in_its_own_words`, so no coverage moved. *(3)* `test_the_system_process_is_there_and_dates_from_before_every_record` — **W4a's "psutil answers 0.0 for pids 0 and 4" is this box's answer, not the platform's.** The runner dates pid 4 for real, and **1.78s after `psutil.boot_time()`**, because `boot_time` comes off the tick count and `create_time` off `GetProcessTimes`: two clocks, and the assertion meant "before every record". The yardstick is now this process's own `create_time` — same clock, and strictly tighter by however long the box has been up. *(4)* `test_reaped_says_whether_the_pid_is_gone` — a race this desk won every time. `terminate` promises the *job* is empty and `TerminateJobObject` does not wait, so `_reaped(pid)` the instant it returns is an assertion against a race; it goes through `gone()` now, like every other dead-pid assertion in that file. **What the skip counts say, and one of them refutes the plan.** Desk **95**, CI Windows **85**, CI macOS **89**. CI Windows runs ten the desk skips (all `needs_symlinks`) plus `test_projects`' "this box's temp dir is its own realpath" — the `RUNNER~1` alias makes the Mac's `/var`→`/private/var` premise true on Windows, which is also the corroboration for failure (1) — and skips one the desk runs (the `claude` gate). **W5e predicted the Mac at 94 → 99 skips. It is 89.** The prediction was not a little out, and the reason is structural: every row since W1a computed the Mac's count by adding to *this box's* number, and the two platforms skip different sets. The table has been carrying a Windows number under a Mac heading for twenty slices. **The never-run Mac test ran.** `test_the_runner_still_inherits_the_listeners_log_on_the_mac` — the one W5e's row calls "the single most important Mac row in this table, because a mutation of it is caught by 0 of 707 here" — executed for the first time and **passed**: `session_posix.runner_output` yields `{}`, `Popen` is called as it was before W5e, and nothing under the seam makes a session directory. Both macOS jobs agree, so it is not a 3.9-vs-3.12 accident. **Red, the ritual's mutation case** (the entry says "none meaningful; the workflow is the test", so the five new tests were written after the file): five mutations, each caught by **exactly one** test — the macOS jobs deleted (`test_both_platforms_are_in_the_matrix`), the pip install removed (`..._installs_the_lockfile`), `/usr/bin/python3` swapped for the toolcache (`..._the_interpreter_the_ritual_names`), `secrets.TG` wired in (`test_no_credential_reaches_ci`) — and the file deleted, caught by four. **The one that stays silent under deletion is the credential test**, because an absent file names no secret; that is why it is paired with the presence check and why the pair is written the way W5a learned to write it. **Not done and not CI's to do:** no token, no chat id and no repository secret exists — CI runs the suite, it never runs the bot — and the phone run on a real token is still the user's decision. |
| W7 Pending-on-the-Mac, and the dangling-link decision | done | 2026-09-27 |  | **717 ran: 619 pass, 98 skip, 0 xfail**, 42.4s · **CI macOS 3.9.6: 717 ran, 627 pass, 90 skip, 0 xfail, 14.6s — green, and it is still not the user's Mac** | **The reconciliation found a broken method, not ten bad numbers, and the decision about `_child` is to leave it alone and say why.** **Part one — the table split 73 / 24.** 97 rows went in; 73 came out `done` against one green macOS run and 24 stayed. The 73 are every `compileall`, every suite run, every named test or class, and every "nothing moved here" claim — all properties of the source and the interpreter. The 24 are the hand-runs, the phone checklist, `launchctl`, `sh launchd/*.sh`, a real `~/Projects`, an installed Claude Code and the live-session checks, and **three of those 24 are marked apart**: `config.resolve('C:foo')`, `procs.started(1)` and `procs.started('nope')` are one-liners a runner could answer and nothing runs, which is a smaller debt than the rest and should not be filed with it. **The ten was the wrong shape of error.** W6 recorded the Mac skip prediction as ten out (94 → 99 predicted, 89 measured). Measured properly with `-v`: **this desk skips 98, the macOS runner 90, CI Windows 86 — and the desk's set and the Mac's set have exactly one member in common**, `test_the_default_on_windows_is_what_is_on_path`, which skips on the Mac for being a Windows twin and on a runner for needing Claude Code installed, i.e. for two unrelated reasons. Two nearly disjoint sets were being added to each other for twenty slices, so the ten absolute counts (W1a, W2b, W3a, W3c, W3d, W3h, W3i, W4c, W4f, W5b) are void **as a class**; correcting them one at a time would have re-stated the same mistake with better arithmetic. They are left in place with that said over them, because a quietly fixed number is what this plan forbids. **Two predictions are refuted outright, and both are corrected in their rows.** *(1)* W3i said `TestTheLinkAtTheEndOfTheWindowsCapture` would be "gated on `rc_startup_win.log`, which is not on that box" — the fixture is **committed**, so it is on every checkout, and both tests ran and passed on 3.9.6: the row is *nothing newly skipped against twelve newly run*. *(2)* W5b's headline said "sixteen newly run, nothing newly skipped" while its own body listed three that must skip; three did, so the headline should read *thirteen newly run, three newly skipped* and has contradicted its own body for four slices. Everything else checked out, including every "skips whole" claim. **One row is misfiled rather than wrong, and it is the base error in a single cell:** W3e's two rows name `.venv\Scripts\python` and print **572 ran, 497 pass, 74 skip, 1 xfail** — this desk's own W3e figures — in a table of things to run on the Mac. **Part two — `config._child` does not canonicalise a dangling reparse target, decided, not deferred.** §5.3 carries the argument and the code carries a fourteen-line version of it. The short of it: the fix is a second resolution pass that re-judges a path which has **already failed check 3**, inside the one function §10.4 is a promise about, and it would run `realpath` over a string `_readlink_deep` read out of a reparse point — text check 1 never saw and the OS itself declined to resolve. §5.1 inverted the DACL list on the argument that `config.py` fails closed; "the resolution did not complete, so refuse" is the closed answer. It buys nothing on the Mac (`posixpath.realpath` resolves every component that exists and appends the one that does not, so the same fixture reaches check 4 there unaided), so it would be a Windows-only branch in shared code where §5 has always put platform differences in the *rule* and never in a second path; and on Windows it buys only a nicer sentence for a hand-made broken symlink inside `projects_root`, a name `projects()` never lists because the listing is filtered through `resolve()` itself. **And check 3 is entitled to catch it**: it does not mean "you escaped", it means "this did not resolve to a direct child of the root", which is exactly true of a name whose resolution could not be completed. **Red, and the honest version of it: this desk never saw any of it.** All three new tests in `TestAnUnresolvableReparseTarget` need `os.symlink`, so all three **skip here** — which is the ritual's step 1 outcome "skipped for a missing fixture", and also means a mutation of `_child` is caught by **0 of 717** tests at this desk, exactly as W5e's row was of `runner_output`. So the mutation was pushed *before* the fix: run `36308573533` carried the canonicalisation deliberately, and **exactly one test went red** — `test_an_unresolved_reparse_target_is_not_canonicalised`, naming the substitution it was written to catch ("no project by that name exists" where "root" was wanted). The other two passed under the mutation, correctly: one asserts the invariant (refused either way) and one is the Mac's half. **I have never watched any of these three pass. CI has.** That asymmetry is ordinary in this repository now and the report says so rather than rounding it off. **The workflow changed, and it is a decision rather than a tidy-up.** All three jobs now run `-m unittest -v`. `-q` prints a count; twenty rows of the table above predict *which* tests skip, and not one of them could be checked until the runner started naming them. `TestTheCiMatrix` gained `test_every_job_runs_the_suite_verbosely` (asserting no job says `-q` and all three say `-v`, `assertFalse` rather than `assertNotIn` so a failure does not print the whole file), and `test_the_mac_job_runs_the_interpreter_the_ritual_names` dropped the flag from its assertion and kept the interpreter, which was always its subject. A local mutation of one job back to `-q` fails the new test and nothing else. W6's no-path-filter observation stands and was deliberately left alone: a doc-only commit still runs all three jobs, and the two minutes are worth more than the risk of a filter that is wrong once. **The plan was wrong in two more places and both are amended in this commit.** *(a)* The ritual's steps 3 and 5 still said the Mac half happens "before the commit, not after" — W6 rewrote the closing paragraph to say CI answers them and left the two steps it contradicts untouched. They now say what actually happens: branch, push, read the run, amend, merge `--ff-only`, which is after the commit and before the merge. *(b)* The `Status:` line called W7 the last open slice and the table "still real after W6"; there is no open slice now and the table is half the size. **Four pushes, three of them on the branch.** `w7-pending-and-the-dangling-link`, amended rather than piled on, so `main` gets one commit and the number two survives only here: the first push was the mutation (Windows red by exactly one test, both macOS jobs green and the `-v` log that part one is built from), the second the decision plus the documents and its own all-green run `36309399515` (Windows 717 ran, 86 skip, **0 failures** — which is the two symlink tests passing against an unchanged `_child`), the third naming that run in this row, and the fourth the merge. **Not discharged and not CI's to discharge:** the user's Mac has still never run this suite, and the phone run is still owed on a real token. |
| retention day 1 on NTFS | — | | | | |
| first sleep/wake with a session open | — | | | | |
| first self-update under a live runner | — | | | | |
| first real logoff/logon after W5b | — | | | | **the half of W5b's figure this desk could not take.** Logging off ends the session driving a slice, so only the scheduler-start→first-poll leg is measured (0.92s). To get the other leg: one elevated `wevtutil sl "Microsoft-Windows-TaskScheduler/Operational" /e:true` (it is disabled on this box and enabling it is refused unelevated), then after the next logon compare that log's task-start event with the Security log's 4624, and add 0.92s. What must also be true and is not yet observed: the listener comes up **without** anyone asking, and `var\bot.log` gains a `centrion listener starting` line with a logon timestamp on it. **W5c could not discharge this either** and did not try to fake it: the same desk, the same session, the same disabled operational log. One thing W5c adds to what the logon should be checked for, and it is cheap: the first thing the returning `bot.cmd` does is open `var\bot.log`, so the `=== centrion listener starting ===` line is also the proof that nothing from the last login is still holding it (§7, W5e). No line, no listener, and the task will say `Running` while it spins. **W5d adds three things and discharges none.** (a) It is now known *why* the logoff is fatal to the measurement rather than merely awkward: the task's three processes all run in the interactive logon session (measured, session 11), so a logoff does not interrupt the slice, it destroys the thing being measured along with it — the returning listener at the next logon is a different process tree and the figure wanted is across the two. (b) The doubt to settle while at the keyboard, which costs nothing extra: **does `StartWhenAvailable` do anything for a `LogonTrigger`?** Microsoft documents it for time-based triggers only. The observation: with the machine booted and *nobody* logged on for a few minutes (a reboot to the lock screen, no sign-in), check afterwards whether `var\bot.log` gained a `centrion listener starting` line before the logon timestamp. A line there means the setting fires a logon trigger without a logon and §7's row is right; no line means the row is wrong and should say so. (c) The not-logged-on case itself, which is **not** a time item because it will not happen by waiting — it needs two things nobody at this desk may do alone: a **password on this account** (it has none; every non-console logon is refused 1327, so no batch logon exists) *or* an elevated shell, and then **two** XML edits rather than one — `<LogonType>` to `Password` or `S4U` *and* a `<BootTrigger>` beside the `LogonTrigger`, because a logon trigger cannot fire when nobody logs on. With those made and registered: reboot, do not sign in, and read `var\bot.log` from another machine or after signing in later — a `centrion listener starting` line dated before the sign-in is the whole answer, and `schtasks /Query /TN centrion /V /FO LIST` should say `Logon Mode: Interactive/Background` instead of today's `Interactive only`. Then the question §7 cannot otherwise reach: start a session and see whether **ConPTY works in a non-interactive session**, which is the one assumption in that paragraph W5d could not touch. |

### Pending on the Mac

The ritual's step 5 says shared and posix changes are tested on the Mac *before* the commit.
This work is being done from the Windows box, so that step is a debt. **W7, 2026-09-27, split
the debt in two and paid the larger half**, which is what the two tables below are.

**The first table is answered, and by one thing: CI run `36308573533`, the `macos-latest`
job on `/usr/bin/python3`, which is Python 3.9.6 — 717 ran, 627 pass, 90 skip, 0 xfail,
14.6s, green.** Its twin at 3.12 agrees test for test (717 ran, 90 skip, 17.0s). That run’s *Windows* job was deliberately red — it carried W7’s mutation, see the row — and the all-green one on the same tree is `36309399515` (macOS 3.9.6 717/90, macOS 3.12 717/90, Windows 717/86). Every row
there is a `compileall`, a suite run, a named test or class, or a claim that nothing moved,
and all four kinds are properties of the source and the interpreter. Since W7 the job runs
`-m unittest -v`, which is why they could be checked at all: a `-q` line prints a count, and
these rows predict *which* tests skip.

**The second table is not answered and cannot be by any runner.** A GitHub macOS runner has
no `~/Projects` with `beacon` in it, no Claude Code installed, no `.telegram.json`, no phone,
no launchd session, no `bot.sh` that has ever been run and no Warp. The hand-runs, the phone
checklist, `launchctl`, the timing comparisons and the live-session checks mean what they
have always meant: **somebody has to run them on the user's own Mac, which has still never
run this suite.** Three rows in it are marked differently — a runner *could* answer them and
nothing does, because nobody wrote a step or a test for them. That is a smaller debt than the
rest and it should not be filed with them.

**What the reconciliation found, which is not a corrected number but a broken method.**
Twenty-three rows below carry a number. Every absolute one was computed by adding that
slice's new tests to *this box's* skip count, and the measurement says that could never have
worked: **this desk skips 98 tests, the macOS runner skips 90, CI Windows skips 86, and the
desk's set and the Mac's set have exactly one member in common** —
`test_config.TestClaudeBinary.test_the_default_on_windows_is_what_is_on_path`, which skips on
the Mac for being the Windows half of a pair and on a runner for needing Claude Code
installed, i.e. for two unrelated reasons. Two nearly disjoint sets were being added to each
other for twenty slices. So W5e's "94 → 99 on the Mac" was not ten out by accident, and
**no arithmetic on the Windows number would have produced the Mac's**; the absolute counts in
rows W1a, W2b, W3a, W3c, W3d, W3h, W3i, W4c, W4f and W5b are void as a class rather than
individually wrong, and are left in place with that said rather than quietly restated.

**Two predictions are refuted outright by the run, and both are corrected in their rows.**
W3i said `TestTheLinkAtTheEndOfTheWindowsCapture` would skip on the Mac for want of
`rc_startup_win.log`; the fixture is committed, so it is on every checkout and both tests ran
there. W5b's headline said "nothing newly skipped" while its own body said three would skip;
three did. Every other class-level claim in the table checked out against the `-v` log,
including every "skips whole" it names: `test_session_win.py`'s eight classes (42 tests, 42
skipped there), `TestCheck1OnWindows`, `TestTheWindowsDacl`,
`TestADetachedChildOutlivesItsParent`, `TestTheSingleInstanceMutex` and
`TestWhetherARunnerIsStillThereOnWindows`. One further row is not wrong but misfiled:
**W3e's two rows name `.venv\Scripts\python` and print this desk's own counts**, in a table
of things to run on the Mac.

The two standing debts are **unchanged by W7, and W7 does not get to discharge them**: the
user's Mac has still never run this suite, and the phone run is owed on a real token.

#### Answered by CI — 73 rows, all `done`

| Slice | What to run on the Mac | Verdict, then the row as it was written |
|---|---|---|
| W1a | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9 syntax; no 3.9 on the Windows box to check with |
| W1c | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — `tests/support.py` and the decorators are 3.9 syntax, but nobody has compiled them with 3.9 |
| W2a | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — nothing here reaches for 3.10 syntax, but the ritual's step 3 is the only thing that knows that |
| W2b | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9 again; nothing new reaches past it, and nothing on this box can check that |
| W3a | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9; the retry loop is plain `for`/`try`, but nothing here has compiled it with 3.9 |
| W3b | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9 again. `tests/test_session_win.py` is compiled there even though every test in it skips, and it is the first file in the port written without a 3.9 interpreter anywhere near it. |
| W3c | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. The only executable change in the slice is a comment in `bot.py`, but the two new test classes are compiled there too. |
| W3d | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `session_posix.Terminal` is a plain class and `Runner.absorb` a plain method, but neither has been near a 3.9 interpreter. |
| W3e | `.venv\Scripts\python -m compileall -q .` | **done — CI run `36308573533`, and the row was misfiled.** It names `.venv\Scripts\python`, which is *this desk's* interpreter, in a table of things to run on the Mac. Read as `/usr/bin/python3 -m compileall -q .`, which is what W3e meant and what the run did. clean. Nothing here is 3.10+, but the Mac is the only interpreter that can say so and it has not run. |
| W3f | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. Nothing in the slice reaches past it (a `for` over a dict, a `getattr` with a default), and the Mac is still the only interpreter that can say so. |
| W3g | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `session_posix.child_env` is today's `env.update` moved behind the seam and nothing in it is new syntax, but the Mac is still the only interpreter that can say so. |
| W3i | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `Scrape.idle` and `Runner.idle`/`went_live` are plain methods and nothing in them is new syntax, but the Mac is still the only interpreter that can say so. |
| W4c | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. Only test files changed and nothing in them is new syntax, but the Mac is the only interpreter that can say so. |
| W4d | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `session_win.py` is the only program file that changed and none of it is new syntax, but `test_procs_win.py` imports that module at its top level on both platforms (W4c), so 3.9 is the only interpreter that can say the `hashlib`/ctypes additions parse there. |
| W4f | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. Two test files changed and nothing in them is new syntax, but the Mac is the only interpreter on this project that can say so. |
| W4e | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `bot.py` gained an assignment, an `if` and a `return`; nothing in them is new syntax, but the Mac is still the only interpreter that can say so. |
| W5a | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `tests/test_layout.py` is the only Python file this slice touches and the only new syntax in it is an `import re` and a class attribute, but the Mac is still the only interpreter that can say so. |
| W5b | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `tests/test_layout.py` is the only Python file this slice touches; the new syntax in it is an `import xml.etree.ElementTree`, a `@classmethod` used as a helper and an f-string-free `%` format, none of it past 3.9 — but the Mac is still the only interpreter that can say so. |
| W5c | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. `tests/test_layout.py` is the only Python file this slice touches and the only new thing in it is one `for` loop over strings; the Mac is still the only interpreter that can say so. |
| W5e | `/usr/bin/python3 -m compileall -q .` | **done — CI run `36308573533`, macOS 3.9.6.** clean — 3.9. Three Python files move in this slice (`bot.py`, `session_posix.py`, `session_win.py`) and the new syntax is `@contextlib.contextmanager`, a `yield` in a generator, and **two `**` unpackings in one call** (`**out, **procs.spawn_flags()`), which is 3.5+ and therefore fine — but `session_win.py` is the file the Mac never runs and `bot.py` is one it runs constantly, and only that interpreter can say so. |
| W1a | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** The absolute count this row predicts is a pre-W0 Windows count and is void for the reason in the preamble; the suite is green on 3.9.6. green; the count is the pre-W0 count plus the new `TestThePlatformSeam` (6) and fixture tests (7, one xfail) |
| W1b | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestTheListenerUsesThePlatformSeam` is seventeen tests now and the Mac runs all of them. green; `TestTheListenerUsesThePlatformSeam` (10) added; the real-process tests at the end of `test_bot.py` still pass through the `process_started` alias |
| W2a | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** Measured: `TestCheck1OnWindows` skips whole (4/4) and `TestClaudeBinary` skips two on the Mac and runs five, so the `skipUnless(POSIX)` pair is running there and not leftover. green, and **nothing newly skipped**: of the four new tests the Mac runs, three are `skipIf(POSIX)` win32 twins it skips by design and one — the `DEFAULT_CLAUDE_BIN = None` case — is portable and must pass there. `TestCheck1OnWindows` skips whole. The two `skipUnless(POSIX)` tests in `TestClaudeBinary` must still *run* and pass: they are the posix half of the pair now, not leftovers. |
| W1c | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`, and the symlink half is now a measured fact rather than an assumption: every `needs_symlinks` test ran on the macOS runner.** Nothing in `TestEverythingImportsHere` skips there. green, and **nothing newly skipped**: every W1c decorator is `skipUnless(POSIX)` or `needs_symlinks`, and the Mac can create symlinks. The count is W1b's plus `TestEverythingImportsHere` (2). A skip on the Mac means a decorator landed on the wrong test. |
| W2b | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestPermissions` runs there, all four (and skips all four here, which is the pair working). "The count is W2a's plus 15" is a Windows count; see the preamble. green, and **`TestPermissions` must still run there, all four**. It is the Mac's half of the secrecy pair, not a leftover: the whole of W2b is downstream of the fact that its 0600 check cannot be ported, so a skip on the Mac means `_secret` was bound to the wrong function. `TestTheWindowsDacl` (13) and the `_on_windows` creation twin skip whole, by design. The count is W2a's plus 15. |
| W3a | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `test_a_reader_never_sees_a_partial_record` runs on the Mac and passes, so its `denied == []` / `lost == 0` assertions held there. green, and **one fewer skip than W2b**: `test_a_reader_never_sees_a_partial_record` is portable now and must *run* there. The count is W2b's plus 4. |
| W3b | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `tests/test_session_win.py` is 42 tests now and **all 42 skip on the Mac**, so "`skipUnless(WIN)` whole" is still true four slices of additions later; `test_a_terminal_that_cannot_be_started_is_failed_not_a_traceback` ran and passed. green, and **twelve newly skipped, all in one file**: `tests/test_session_win.py` is `skipUnless(WIN)` whole — it drives a real ConPTY. The thirteenth new test is the portable one, `TestTheRunner::test_a_terminal_that_cannot_be_started_is_failed_not_a_traceback`, and it must *run* and pass there: it patches `session.spawn` to raise `OSError` and asserts the runner records `failed` rather than dying. A skip on that one means the `try` around the spawn landed behind a platform check it has no business being behind. |
| W3c | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** Both class claims hold, and one has drifted: `TestSpawningForRealOnWindows` is **five** tests today rather than four — W5e added `test_a_live_session_does_not_pin_the_listeners_log` — and all five skip on the Mac, as do all four of `TestTheCommandLineTheChildParsesBack`. `TestSpawningForReal`'s seven run there. green, and **eight newly skipped, in two files**: `TestTheCommandLineTheChildParsesBack` (4) is `skipUnless(WIN)` and `TestSpawningForRealOnWindows` (4) is `skipIf(POSIX)`. Every one of the eight is the Windows half of a pair whose Mac half already exists and must still *run* — `TestSpawningForReal`'s four in particular. A skip in `TestSpawningForReal` means the new class was written over the old one rather than beside it. The count is W3b's plus 8. |
| W3d | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestThePosixTerminal`'s six run on the Mac and skip here; "the count is W3c's plus 20" is a Windows count, see the preamble. green, and **this is the first slice since W1a where the Mac run is the only thing that tests the code that changed**. `session_posix.spawn` returns a `Terminal` and the Mac's read path goes through it; Windows exercises none of that. The count is W3c's plus 20, of which the Mac runs the 12 portable ones and the 6 in `TestThePosixTerminal`, and skips the 3 in `tests/test_session_win.py::TestThePumpOverAConpty`. **`TestThePosixTerminal` must run, all six** — it is the new code's only test anywhere, and a skip there means `posix_only` landed on the wrong class. |
| W3e | `.venv\Scripts\python -m unittest -q` | **done — CI run `36308573533`, and the row was misfiled twice over.** The command is this desk's, and the numbers in it (**572 ran, 497 pass, 74 skip, 1 xfail**) are this desk's own W3e figures printed under a Mac heading — which is the base error of this whole table in one cell. The Mac's W3e figure was never taken and is now superseded by the measurement in the preamble. **572 ran: 497 pass, 74 skip, 1 xfail**, 31.8s. Twelve more than W3d and no change to the skip count — every new test runs on this platform. The Mac's number is W3d's until somebody runs it. |
| W3f | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestTheStopMarker`'s seven run on the Mac and the seven Windows twins skip. green, and **seven newly skipped against seven newly run**. `TestTheStopMarker` (7) is portable and must run there — it is the whole of the loop half of this slice, and the marker exists on the Mac precisely so that it does. `TestTheStopMarkerOnWindows` (3) and `TestTheSignalsThatAreLeftOnWindows` (4) are `skipUnless(WIN)`. The count is W3e's plus 14. |
| W3g | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestTheChildEnvironment` is thirteen tests, of which the Mac skips exactly three and Windows skips two. green, and **three newly skipped against three newly run**. The three Windows twins in `TestTheChildEnvironment` are `skipUnless(not POSIX)`; what must *run* there is `test_the_mac_shell_variables_are_set` (the `TERM`/`LANG`/`PATH` assertions lifted out of `test_the_required_variables_are_set`, which is now the portable half) and `TestThePlatformSeam::test_child_env_is_filtered_here_and_finished_by_the_platform`. The count is W3f's plus 6. |
| W3h | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `expectedFailures=0` on both macOS interpreters and here. green, and **one expected failure fewer**: `test_the_answer_does_not_depend_on_chunking` was an `expectedFailure` on both platforms and is now an ordinary test in both `TestTheWindowsTrustDialog` (fixture-gated, so it runs there too) and `TestTheTrustDialog`. The count is W3g's plus 1, with `expectedFailures=0` — if the Mac still reports one, the decorator was removed on a class the Mac skips. |
| W3i | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533` — and the prediction is refuted.** `rc_startup_win.log` is committed (`git ls-files tests/fixtures` lists all three fixtures), so it is on every checkout including a Mac's, and both tests in `TestTheLinkAtTheEndOfTheWindowsCapture` **ran** on macOS 3.9.6 and passed. The row should read *nothing newly skipped against twelve newly run*. green, and **two newly skipped against ten newly run**. `TestTheLinkAtTheEndOfTheWindowsCapture` (2) is gated on `rc_startup_win.log`, which is not on that box; everything else in `TestALinkAtTheVeryEndOfTheOutput` (6) and the four new ones in `TestPumpOverATerminal` are portable and must *run* there — this is `Scrape`, the most portable code in the program. The count is W3h's plus 12. |
| W4a | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestWhetherARunnerIsStillThereOnWindows` skips whole (7/7) there; `TestWhetherARunnerIsStillThere`'s eight run. green, and **seven newly skipped against two newly run**. `TestWhetherARunnerIsStillThereOnWindows` (7) is `skipUnless(WIN)` — real processes and psutil. The two portable ones in `TestTheListenerUsesThePlatformSeam` must *run* there and pass: `test_a_record_with_no_start_time_is_trusted_to_the_pid_alone` and `test_a_pid_the_platform_cannot_date_is_left_alive`. The count is W3i's plus 9. |
| W4b | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestStoppingARealRunnerOnWindows` skips there (1); the seventeen in `TestTheListenerUsesThePlatformSeam` all run. green, and **one newly skipped against five newly run**. `TestStoppingARealRunnerOnWindows` (1) is `skipIf(POSIX)`. The five in `TestTheListenerUsesThePlatformSeam` are portable and must *run* there — they are the whole of the slice's logic, and `test_stop_asks_through_the_marker_before_it_reaches_for_a_kill` is the one that asserts the Mac now asks with a file too. The count is W4a's plus 5, skips plus 1 (76). |
| W4c | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533` — and "(78)" was never a Mac number.** It is this box's skip count, and no arithmetic on it produces the Mac's (see the preamble). The class claim holds: `TestADetachedChildOutlivesItsParent` is five tests today and all five skip there. green, and **two newly skipped against nothing newly run**. Both additions are in `TestADetachedChildOutlivesItsParent`, which is `skipUnless(WIN)`. The count is W4b's plus 2, skips plus 2 (78). The third change is a rewrite of `test_request_stop_is_atomic_and_idempotent`, which is in `test_session_win.py` and skips there already. |
| W4d | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestTheSingleInstanceMutex` skips whole (2/2) there. green, and **two newly skipped against one newly run**. `TestTheSingleInstanceMutex` (2) is `skipUnless(WIN)` — real processes and a named mutex. The one that must *run* there is `test_bot.py::test_serve_runs_the_listener_when_the_lock_is_granted`, and on the Mac it is the assertion that `session_posix.Lock.take()`'s `return True` still lets `serve()` through to `Telegram` and `Listener.run`. The count is W4c's plus 3, skips plus 2 (80). |
| W4f | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** Nothing in `TestTheKeyboard` skips on either platform. green, and **nothing newly run and nothing newly skipped** — this slice changes two existing tests and adds none, so the count is whatever the merge `5174808` left there. The one that must still *run* on the Mac, and pass, is `test_bot.py::TestTheKeyboard::test_the_text_is_capped_and_the_keyboard_still_arrives`: it is **not** newly decorated, and if it comes back skipped there something has gone wrong with this slice rather than with the Mac. |
| W4e | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestALateLinkIsStillAnnounced` is thirteen tests and none of them skips anywhere. green, and **three newly run, nothing newly skipped**. All three additions to `TestALateLinkIsStillAnnounced` are portable and must run there: the two that pin the waiter standing down when the tick claimed first, and `test_a_waiter_that_claims_the_session_does_answer`. The count is W4f's plus 3. |
| W5a | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `TestTheWindowsStartup` is fourteen tests today and the Mac runs every one. green, and **twelve newly run, nothing newly skipped**. Every one of `TestTheWindowsStartup` is portable and undecorated on purpose: `windows\bot.cmd` and `windows\install.ps1` are in this repository on both boxes, and a merge that dropped the directory, or a `.gitignore` rule that swallowed it, should fail on the machine that cannot otherwise notice. The count is W4e's plus 12. Note `test_the_startup_files_are_tracked` and `test_the_venv_and_the_scratch_directory_are_ignored` shell out to `git check-ignore`; they have run on the Mac since slice 0 in the same file, so the only new thing there is the paths. |
| W5b | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533` — and the headline is wrong while the body is right.** Measured: three of `TestTheScheduledTask` skip on the Mac, and they are exactly the three the body names. "Sixteen newly run, nothing newly skipped" should read *thirteen newly run, three newly skipped*; the body's "plus 3 there" already said so and the headline contradicted it for four slices. green, and **sixteen newly run, nothing newly skipped**. Thirteen of the new tests are portable and undecorated for `TestTheWindowsStartup`'s reason — `windows\centrion.xml` is in this repository on both boxes, and a merge that drops it, or an ignore rule that swallows it, should fail on the machine that cannot otherwise notice. The three `this_windows_checkout` ones (`test_every_path_in_the_task_is_absolute_and_present`, `test_it_runs_this_checkout`, `test_install_renders_the_task_xml_these_checks_read`) must come back **skipped** there, with the same reason `this_mac_checkout` gives in the mirror: a rendered `__CHECKOUT__\windows\bot.cmd` is not a path on a Mac. The count is W5a's plus 16, skips unchanged at 94 here and plus 3 there. |
| W5c | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** green, and **one newly run, nothing newly skipped**. `test_the_throttle_does_not_redirect_to_the_log_it_cannot_always_open` is portable and undecorated for the same reason as the rest of `TestTheWindowsStartup`: it is a property of the committed bytes of `windows\bot.cmd`, a file that is in this repository on both boxes, and the Mac is as good a place as this one to notice that somebody has put a log redirection back on the throttle. The count is W5b's plus 1, skips unchanged at 94 here and there. |
| W5e | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`, and see the row below, which is the one that mattered.** green, and **two newly run, one newly skipped**. `TestThePlatformSeam.SURFACE` gains `runner_output`, so both surface tests assert it there; `test_the_runner_still_inherits_the_listeners_log_on_the_mac` is `@needs_session_posix` and is the one new test the Mac runs and this box cannot. The five `win32` ones come back skipped there and `test_start_sends_the_runners_output_where_the_platform_says` is portable and undecorated, because the three claims it makes — the seam is asked, asked about this session's directory, and what it yields reaches `Popen` — are true of `Sessions.start` on both platforms whatever each side answers. The count is W5c's plus 7, skips 94 → 95 here and 94 → 99 there (four `win32` unit tests plus the real-runner one; the posix-only one un-skips). |
| W5d | `/usr/bin/python3 -m unittest -q` | **done — CI run `36308573533`.** `test_the_logon_type_and_the_trigger_agree_about_when_the_bot_runs` runs on the Mac; it is not one of `TestTheScheduledTask`'s three skips. green, and **one newly run, nothing newly skipped**. `test_the_logon_type_and_the_trigger_agree_about_when_the_bot_runs` is portable and undecorated for `TestTheScheduledTask`'s standing reason — `windows\centrion.xml` is in this repository on both boxes. The count is W5e's plus 1 (708 here). A skip of it on the Mac means a decorator landed on the wrong test. |
| W3a | the same test, watched | **done — CI run `36308573533`, macOS 3.9.6.** on the Mac it must reach the end with `denied == []` and `lost == 0` — the two assertions that only run under `POSIX`. A refusal there would mean rename-over-an-open-file is not what this has always assumed it is. |
| W3b | `test_a_reader_never_sees_a_partial_record`, watched | **done — CI run `36308573533`, macOS 3.9.6.** unchanged from W3a: `denied == []`, `lost == 0`, and now `written == 150`. The `time.sleep(0.001)` added to its reader is for the Windows half, where it takes the number of replaces that land from 0–8 to 38–42; on the Mac every replace lands either way, so if this run shows a refusal or a loss the pause has changed something it was not supposed to touch. |
| W3c | `TestSpawningForReal::test_a_prompt_is_one_argument_and_never_a_command_line`, watched | **done — CI run `36308573533`, macOS 3.9.6.** unchanged, and it is the load-bearing one here: the Mac keeps the `;`/backtick/`$()` hostile prompt and the Windows twin keeps the `&`/` |
| W3d | `TestTheTerminalSize`, watched | **done — CI run `36308573533`, macOS 3.9.6.** all eight still pass, and that is the regression guard for the return-type change: they call `session.spawn` and now hold a `Terminal` instead of an fd, with `drain()` rewritten onto `read`/`alive`. They are also the only place the real pty's EIO meets the new `_finished` flag. A failure here is `Terminal`, not the pty — the pty half of each of them was passing before this slice. |
| W3d | `test_closing_the_pty_hangs_up_the_child_once_it_owns_the_terminal`, watched | **done — CI run `36308573533`, macOS 3.9.6.** SIGHUP, as before. It now closes through `Terminal.close()` rather than `os.close(master)`, and `TestThePosixTerminal::test_closing_it_hangs_up_the_child` makes the same claim about the same call — if one passes and the other does not, `close()` is not closing the fd it thinks it is. |
| W3f | `TestThePlatformSeam::test_the_posix_no_ops_answer_as_the_mac_needs`, watched | **done — CI run `36308573533`, macOS 3.9.6.** it now asserts the **opposite** of what W1a wrote there: `request_stop` makes the marker and `stop_requested` finds it. That line is the only Mac-side assertion this slice inverts, and it is the one to read if the Mac run is not green. |
| W3g | `TestTheChildEnvironment`, watched | **done — CI run `36308573533`, macOS 3.9.6.** **this is the one that matters on the Mac**: `child_env` is the only function in the program whose body moved *out* of `session.py` in this slice, and the Mac's half of it is the `PATH` the version-pinned `claude` depends on (§9.8). Every one of the class's ten must pass there unchanged. If `test_claude_is_first_on_the_path` fails, the move dropped a line. |
| W3h | `TestTheTrustDialog`, whole class | **done — CI run `36308573533`, macOS 3.9.6.** **the portable half of the slice is the whole of it**: `Stripper` is in `session.py`, so the Mac's `Trust` and `Scrape` changed too, and this class is where a carry that eats a character instead of holding it would show. `test_it_does_not_hold_the_whole_session_in_memory` is the one to watch — the carry is a second buffer and `CARRY_LIMIT` is the only thing bounding it. |
| W3i | `TestALinkAtTheVeryEndOfTheOutput`, watched | **done — CI run `36308573533`, macOS 3.9.6.** **the Mac is the control for the claim that it has never needed this.** `test_a_link_at_the_very_end_of_the_output_is_not_held_forever` feeds the *Mac* fixture truncated at the URL, so if `idle()` answers something other than `CAPTURED` there the shared code is wrong and not just unexercised. |
| W4a | `TestWhetherARunnerIsStillThere`, whole class | **done — CI run `36308573533`, macOS 3.9.6.** **unchanged, and that is the claim**: no posix code moved in this slice, so its eight must pass exactly as before. The two new portable tests are twins of two of them, so a Mac failure in the pair is the twin being wrong about a branch the Mac has always exercised — read `test_a_record_with_no_start_time_falls_back_to_the_pid_alone` beside it. |
| W4b | `TestStopping` and `TestTheFleetIsListed`, watched | **done — CI run `36308573533`, macOS 3.9.6.** unchanged, and that is the claim: `halt` and the listing did not move, and their fake `Sessions.stop` never reaches the new code. A failure there is the fake's signature drifting from the real one. |
| W4f | `tests/test_bot.py::TestTheKeyboard`, whole class | **done — CI run `36308573533`, macOS 3.9.6.** **the Mac's half of the portability fix, and the one thing here that can fail there.** The rewritten cap test now makes 80 directories of 63 characters instead of 30 of 203 — a strictly shorter path, so a name length the Mac was already fine with stays fine. What could fail is the new first assertion, `assertIn("characters elided", …)`: the reply has to exceed 4096 characters for `fit` to cut it at all, and every line of `Projects in <root>:` carries two spaces and a name, so the margin is ~5.3 KB against 4096 and is not close. If it *does* fail there, the fix is more names, never a smaller assertion — the guard is what stops the other two passing vacuously. The other fourteen tests in the class did not move. |
| W5b | `tests/test_layout.py::TestTheWindowsStartup::test_nothing_under_windows_is_anything_but_ascii` | **done — CI run `36308573533`, macOS 3.9.6.** **the one new test that matters most on the Mac, and the only one that would have caught W5a's break.** It is a property of the bytes, so the Mac can hold it, and the Mac is the likelier place for a UTF-8 em dash to get typed back into `windows\install.ps1` — an editor there has no reason to think twice about it, and no Mac will ever run the file and notice. If this fails on the Mac and passes here, somebody's checkout has re-smartened the punctuation. |
| W2b | `/usr/bin/python3 -c "import config"` with pywin32 absent (it is) | **done — CI run `36308573533`, macOS 3.9.6.** no error. The import is inside `_win32security()`, reached only on win32; if the Mac ever raises ImportError from `config`, the platform split leaked out of the function. |
| W4c | **`import session_win` at the top of `test_procs_win.py`** | **done — CI run `36308573533`, macOS 3.9.6.** **the one line in this slice that can break the Mac**, and it is a module-level import in a file whose classes all skip there. `session_win`'s top level is stdlib-only and the win32 calls are inside their functions, so it should import on 3.9 as it does on 3.12 — but nothing had ever imported it *on the Mac* before this slice, and a collection error in that file would look like the whole file vanishing rather than like a failure. If it errors, the fix is to move the import inside the WIN guard and take `DETACH_FLAGS` from it there. |
| W3c | nothing to hand-run | **done — CI run `36308573533`, macOS 3.9.6.** `bot.py`'s changed comment is the whole of the Mac-visible change, and it changes no behaviour there. Named here so the row is not mistaken for an omission. |
| W3e | not run on the Mac | **done — CI run `36308573533`, macOS 3.9.6.** `session.py` and `session_posix.py` both changed: `terminate` takes a fourth argument there too and ignores it. The portable `test_terminate_forwards_the_terminal_the_session_was_read_through` and `test_the_terminal_is_handed_to_terminate_and_closed_after_it` are what the Mac run has to answer, plus the hand-run W3d already asks for. Recorded as debt, like every slice since W1a. |
| W3f | nothing changed in `bot.py` | **done — CI run `36308573533`, macOS 3.9.6.** the Mac's `Sessions.stop` still sends `SIGTERM` and never writes a marker; W4b is where the listener learns to ask the other way. Named here so the row is not mistaken for an omission — the marker is reachable on the Mac today only by hand. |
| W3i | nothing else changed on the Mac | **done — CI run `36308573533`, macOS 3.9.6.** `session.py` only, and `absorb`'s body moved into `went_live` without changing what it does. Named here so the row is not mistaken for an omission. |
| W4a | nothing to hand-run | **done — CI run `36308573533`, macOS 3.9.6.** `session_win.py` and two test files are the whole change; no posix or shared code moved. Named here so the row is not mistaken for an omission. |
| W4c | nothing to hand-run | **done — CI run `36308573533`, macOS 3.9.6.** no posix, shared or program code moved — three test changes and nothing else. The detach flags are Windows-only by construction (`session_posix.spawn_flags()` returns `{}`, and `test_session.py` has always asserted that). Named here so the row is not mistaken for an omission. |
| W4f | nothing to hand-run | **done — CI run `36308573533`, macOS 3.9.6.** **no program code changed at all** — two test files and this document. The run step here was `bot.py --serve` answering `/ls`, and its object was to show the merged keyboard working on *Windows*; the Mac has been running that keyboard since slice 13 and has nothing to re-confirm. Named so the row is not mistaken for an omission. |
| W5a | **nothing to hand-run, and nothing installed** | **done — CI run `36308573533`, macOS 3.9.6.** **named so the row is not mistaken for an omission.** No posix, shared or program code moved: the change is `tests/test_layout.py`, two new files under `windows\`, and this document. `windows\bot.cmd` is a batch file and `windows\install.ps1` needs `py`, `pip` and a `.venv\Scripts\` — neither can run on the Mac at all, and neither is meant to. The Mac's startup is still `launchd/install.sh`, `launchd/bot.sh` and the plist, all three untouched, and the check that they still are is simply that `TestTheLaunchdInstall` passes there as it always has. |
| W5b | **nothing to hand-run, and nothing installed** | **done — CI run `36308573533`, macOS 3.9.6.** **named so the row is not mistaken for an omission**, the second time W5 has had to say it. No posix, shared or program code moved: the change is `tests/test_layout.py`, one new file and two edited files under `windows\`, and this document. `windows\centrion.xml` is a Task Scheduler definition and `schtasks` does not exist on macOS; the Mac's startup is still `launchd/install.sh`, `launchd/bot.sh` and the plist, all three untouched again, and the check that they still are is that `TestTheLaunchdInstall` passes there as it always has. The two `windows\` files this slice *edited* were edited for reasons that cannot reach the Mac either: ASCII (a PowerShell 5.1 parser) and a `rem` block recording why `PYTHONIOENCODING` stays unset (a Windows ACP). |
| W5c | **nothing to hand-run, and nothing installed** | **done — CI run `36308573533`, macOS 3.9.6.** **named so the row is not mistaken for an omission**, the third time W5 has had to say it. No posix, shared or program code moved: the change is one line of `windows\bot.cmd` with the `rem` block above it, one test, and this document. The line is a `cmd` redirection and the bug behind it is a Windows file-sharing rule, so there is nothing on a Mac for it to be right or wrong about. The Mac's startup is still `launchd/install.sh`, `launchd/bot.sh` and the plist, all three untouched for the third slice running, and the check that they still are is that `TestTheLaunchdInstall` passes there as it always has. |
| W5d | **nothing to hand-run, and nothing installed** | **done — CI run `36308573533`, macOS 3.9.6.** **named so the row is not mistaken for an omission**, the fifth time W5 has had to say it — and this time it is the plain truth. No posix, shared or program code moved at all: the change is one test in `tests/test_layout.py`, comments in `windows\centrion.xml`, and this document. The test reads the committed XML's bytes, so the Mac runs it and must pass it like every other `TestTheScheduledTask` check. |
| W4e | **`bot.py`'s three-line change in `waited`, on the Mac** | **done — CI run `36308573533`, macOS 3.9.6.** **the one thing in this slice that is not a Windows finding at all.** The double reply is in shared code and the Mac has been doing it since slice 8 — the tick's §9.12 announcement followed by the waiter's own, for any session whose link lands inside the 0.25s between two polls of `meta.json`. Nothing about it is platform-shaped, so the Mac needs no separate fix; what it needs is the suite run and the three new tests passing there. |
| W5e | ~~**`session_posix.runner_output` must yield `{}`, and this box cannot prove it**~~ **done — CI, 2026-09-27, run `36307406677`, on 3.9.6 and on 3.12** | **the single most important Mac row in this table, because a mutation of it is caught by 0 of 707 here.** `session_posix` will not import without `fcntl`, so every assertion about the Mac's half of W5e's seam is unrun on Windows — and the failure it guards against is not a crash but a silent tax: the cheap way to write this fix is in shared code, and a Mac runner that stops inheriting would put SPEC.md §14's diagnostics in a second file on a platform where the first one was never locked. `test_the_runner_still_inherits_the_listeners_log_on_the_mac` asserts the two halves that matter — the kwargs are empty, so `Popen` is called exactly as it was before W5e, and nothing under the seam creates a session directory, so `Runner.begin` is still the first thing to make one there. If it fails on the Mac, somebody has made the Mac pay for a Windows file-sharing rule. **It did not: W6's `macos-latest` job ran it for the first time and it passed on both interpreters. `runner_output` yields `{}` there, and the claim the mutation test could not reach from this desk is now a measured fact rather than a careful argument.** |

#### Only the user's Mac can answer these — 24 rows, all still owed

| Slice | What to run on the Mac | Verdict, then the row as it was written |
|---|---|---|
| W1a | `python3 session.py --foreground --cwd <project> --name w1a` | **still owed on the user's own Mac.** a link, and Ctrl-C leaves `meta.json` at `ended` — the signal path now goes through `session_posix.catch_signals` |
| W1b | `sh launchd/bot.sh`, then `ls` from the phone | **still owed on the user's own Mac.** the listener starts (the `Lock` no-op returns True under bot.sh's lockf), answers `ls`; a second `sh launchd/bot.sh` is still refused by bot.sh, not by python |
| W2a | `python3 -c "import config; print(config.load())"` | **still owed on the user's own Mac.** a Config whose `claude_bin` is still `~/.local/bin/claude` expanded and **not** resolved through the version symlink — §5.2's split must not have moved the Mac's default |
| W2a | `python3 -c "import config; print(config.resolve('C:foo', '<root>'))"` | **still owed — but a runner could answer this one and nothing does; it is here because no step or test runs it, not because it needs that machine.** `ProjectError` only if a directory of that name is absent; `posixpath.splitdrive` finds no drive, so on the Mac this is an ordinary name and check 4 is what refuses it. A check-1 refusal there means the rule was applied portably by mistake. |
| W2b | `python3 -c "import config; print(config.load())"` | **still owed on the user's own Mac.** loads, exactly as before — `_secret` is `_secret_by_mode` off win32 and the mode check moved into it *verbatim*, message included. A `chmod 644 .telegram.json` there must still say `mode is 0644, must be 0600` and name `chmod 600`, not `icacls`. |
| W3a | `python3 session.py --foreground --cwd <project> --name w3a`, then Ctrl-C | **still owed on the user's own Mac.** a link, and `meta.json` at `ended`. `write_meta` is on every state change, so this is the path the retry sits in; the Mac must never take the retry branch at all. |
| W3b | `python3 session.py --foreground --cwd <project> --name w3b`, then Ctrl-C | **still owed on the user's own Mac.** a link, and `meta.json` at `ended` — the same run W3a asks for, because `run()` now has a `try` around the spawn that the Mac takes the happy path of. What must *not* appear is `could not start the terminal` in the log. |
| W3d | `python3 session.py --foreground --cwd <project> --name w3d`, then Ctrl-C | **still owed on the user's own Mac.** a link, and `meta.json` at `ended`. The whole point of the hand-run this time is the **exit**: `run()`'s `finally` now calls `terminal.close()` where it called `os.close(master)`, still last, after `terminate(pid)`. What must not appear is a session that ends without `ended` being written, or a runner that does not come back from `pump` at all — the loop's only way out on the Mac is now `alive()` going false off the `_finished` flag, where it used to be a `break` inside the read. |
| W3d | the same run, timed | **still owed on the user's own Mac.** the link should arrive no later than it did before W3a's hand-run. `read(TICK)` waits exactly as the old `select(…, TICK)` did, so there is nothing here that should have slowed down; if it has, the `max(timeout, 0.0)` or the poll ordering is wrong in a way Windows cannot show, because Windows never had a `select` to compare against. |
| W3e | the runner-death measurement, twice | **still owed on the user's own Mac.** The plan said "none beyond the tests", and this is the one that earned its place: a stub runner holding a real ConPTY and a real job, hard-killed with `psutil.Process.kill()`. **Without `KILL_ON_JOB_CLOSE`:** runner gone, claude gone within 0.5s, grandchild alive at +5s. **With it:** all three gone by +0.5s. The first half is why the flag was thought unnecessary and the second is why it is not, and neither was knowable from the API docs. Kept as a test. |
| W3f | `python3 session.py --foreground --cwd <project> --name w3f`, then `touch var/sessions/<sid>/stop` from another terminal | **still owed on the user's own Mac.** the session ends within a tick and `meta.json` says `ended` — the Mac's first stop that is not a signal. Then the same run again ended with Ctrl-C, which must still work: `session_posix.catch_signals` is untouched and `SIGTERM`/`SIGHUP` are still caught there. |
| W3g | `python3 session.py --foreground --cwd <project> --name w3g`, then Ctrl-C | **still owed on the user's own Mac.** a link, `pty.log`, `meta.json` at `ended`. Same run W3d and W3f already ask for, and this time the thing to read is the **time to the link**: Windows measures 6.7s of which 4.5s is `Scrape` holding a complete URL for one more byte (W3i). The Mac's number is the control — if it is also seconds rather than milliseconds, W3i is not a Windows fix at all and its red test belongs on both boxes before the green. |
| W3h | a fresh empty directory, `python3 session.py --foreground --cwd <it> --name w3h --trust` | **still owed on the user's own Mac.** **does the Mac still get the dialog at all?** Windows stopped raising it under `projects_root` (§4), which would be a property of Claude Code rather than of the platform — so the Mac is the control, and the answer decides whether W4e's `new+trust` check can be observed anywhere. A link with `Trust` never leaving `waiting` is the no. |
| W3i | `python3 session.py --foreground --cwd <project> --name w3i`, then Ctrl-C | **still owed on the user's own Mac.** a link, `pty.log`, `meta.json` at `ended` — and **the time to link is the thing to read**: this box now says 2.3s with the hold absent. The Mac's renderer keeps drawing, so its number should be unchanged by this slice in either direction; a Mac that got *faster* would mean it had been taking the hold all along, which §9's W3i says it never does. |
| W4a | `python3 -c "import session, os; print(session.procs.started(1))"` | **still owed — but a runner could answer this one and nothing does; it is here because no step or test runs it, not because it needs that machine.** **the control for the 0.0 finding.** Windows answers `0.0` — the epoch — for pids 0 and 4, which makes a corrupt record naming pid 4 read as live. The Mac's pid 1 should answer launchd's real start time, i.e. approximately boot and nowhere near zero. If it is 0.0 there too, the property is shared for one reason rather than two and §6's paragraph should say so instead of calling the Mac's route honest. |
| W4b | **`python3 bot.py` with a real session, stopped from the phone** | **still owed on the user's own Mac.** **the one run step this slice cannot take here, and the one that matters.** `Sessions.stop` is shared code and the Mac's `SIGTERM` is now the *fallback* rather than the first act. An ordinary `stop` must still come back in about the time it did before — the runner hears the marker within a `TICK` either way — and the thing to watch for is a stop that takes fifteen seconds, which means `pump` is not honouring the marker there and every stop is now paying `STOP_GRACE` before the signal it used to get at once. W3f's hand-run says the marker works on the Mac; this is the listener half of the same claim. |
| W4d | `sh launchd/bot.sh`, then a second `sh launchd/bot.sh` | **still owed on the user's own Mac.** **unchanged, and that is the claim.** No posix or shared program code moved in this slice: the Mac's lock is still `bot.sh`'s `lockf` and `session_posix.Lock.take()` is still `return True`. The second copy must still be refused *by the shell*, not by python, with bot.sh's own message. A refusal that now comes from python would mean the Windows `Lock` had been wired in on the wrong platform. |
| W4f | `tests/test_procs_win.py` | **still owed — but a runner could answer this one and nothing does; it is here because no step or test runs it, not because it needs that machine.** **unchanged, and that is the claim**: every class in it is `skipUnless(WIN)`, so the `"4242"` → `"nope"` change cannot run on the Mac and the Mac cannot confirm it. What still has to happen there is the module-level `import session_win` W4c added (see its row) — a collection error in that file looks like the file vanishing rather than like a failure. The property the corrected case asserts *is* shared, though: `session_posix.started("nope")` must also be `None`, by the `int(pid)` guard at the top of it, and one `python3 -c "import session; print(session.procs.started('nope'), session.procs.started('4242'))"` on the Mac is the whole confirmation — the first `None`, the second **not** necessarily `None`, which is the point of the amendment. |
| W4e | **the six checklist items, from a phone, against a real token** | **still owed on the user's own Mac.** **the slice's own run step, and it is owed on *both* machines.** Windows could not take it — `.telegram.json` here holds W3g's placeholder and every call 401s — so the whole checklist was driven through the shipped `serve()` with `Telegram._open` replaced. The Mac is the box that has a working token, so the six items **from the phone** are cheapest to confirm there, and doing so would also be the first live test of the one-reply fix above. What it cannot confirm is anything in §4's trust paragraph: the dialog's behaviour is a property of Claude Code on this box and W3h's row already asks the Mac the control question. |
| W5a | `sh launchd/install.sh --print`, and `sh launchd/bot.sh` | **still owed on the user's own Mac.** **unchanged, and that is the claim** — the third time this table has made it, and the most load-bearing, because W5a is the slice most likely to have leaked. `windows\bot.cmd` was written *from* `launchd/bot.sh` and `windows\install.ps1` *from* `launchd/install.sh`, and the shapes deliberately differ in two places: the Windows loop is a `KeepAlive` the Mac gets from launchd, and the Windows file takes **no lock at all** because W4d's mutex is inside the listener. If a lock has appeared in `bot.sh` that python now also takes, or the rendered plist has changed by a byte, the porting went the wrong way. |
| W5c | **the `KeepAlive` question, asked of launchd** | **still owed on the user's own Mac.** **the counterpart nobody has to build, and the one thing a Mac can settle for W5e.** `bot.sh` appends to `var/bot.log` with `>>`, the listener inherits that fd and hands it to the runner exactly as it does here — and on POSIX that costs nothing, because an append open denies no one. So the Mac should be able to do what this box cannot: with a session live, kill the listener and watch launchd bring it back inside `ThrottleInterval`, with both `===`-equivalent lines in the log and the session untouched. If that is true there, it is the cleanest statement of what W5e is fixing: the same three-process design, the same inherited handle, and a platform rule that makes one of them a daemon that cannot restart itself. |
| W5e | **nothing to hand-run, and nothing installed** | **still owed on the user's own Mac.** **named so the row is not mistaken for an omission**, the fourth time W5 has had to say it — and the first time it is *not* quite the whole truth, because this slice moves shared code. `bot.py`'s `Sessions.start` is the Mac's line too: it now calls `procs.runner_output(self.directory(sid))` and unpacks the result into `Popen`. The claim is that this changes nothing there, and the way to see it rather than believe it is the ordinary one — start a session from the phone on the Mac and check that `var/bot.log` still carries the runner's `spawned pid`, `live:`, `stop requested` and `ended` lines, and that `var/sessions/<sid>/` has no `runner.log` in it. The Mac's startup is still `launchd/install.sh`, `launchd/bot.sh` and the plist, all four untouched now for four slices running. |
| W5d | **the not-logged-on question, asked of launchd** | **still owed on the user's own Mac.** **it does exist there, and the plan has already answered it — which is the row, rather than an errand.** The analogue of "run whether user is logged on or not" is not a plist setting but a *domain*: a `LaunchDaemon` in `/Library/LaunchDaemons` runs in the system domain from boot with nobody logged in, where this job is a `LaunchAgent` in the GUI domain on purpose. §10 rules it out ("it does not run as a Windows service … Task Scheduler as the user is the analogue of a LaunchAgent in the GUI domain, and that is what the Mac design is") and the plist's own header says why it is survivable ("a sleeping Mac means no session either way"). So there is nothing to try there and no Windows finding to mirror: the Mac's version of W5d's wall is `sudo`, not a blank password, and it was declined by design rather than refused by policy. What the Mac *can* confirm in one command, and should: `launchctl print system/com.tommy.centrion.bot` says the job is **not** loaded in the system domain, i.e. nothing has been quietly installed in both. |
| W4b | a runner deliberately wedged outside `pump` | **still owed on the user's own Mac.** **the cost of the slice, if anyone wants to measure it.** Start a session, `kill -STOP` the runner, then `stop` it from the phone: this used to be `SIGTERM` immediately and `SIGKILL` at `STOP_GRACE`; it is now fifteen seconds of marker-polling *before* the `SIGTERM`. Nothing is broken by it — the session still ends — but the number is the honest price of one `stop` meaning one thing on both platforms, and §6 says it is paid on purpose. |

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
  of the scheduler's job having no kill-on-close — **re-checked by W5c with a real session**:
  `schtasks /End` under a live Claude Code session left all six processes below the `cmd.exe`
  running, the record at `live`, and the orphaned listener still answering `ls`.
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
- **2026-09-27, W5a → §7.** The `KeepAlive` loop's throttle was written here as
  `timeout /t 10 /nobreak > nul`, and copied into `windows\bot.cmd` unchanged because the plan
  is the specification. It does not work unless the batch file was launched with a console:
  with stdin anything else, `timeout` prints `ERROR: Input redirection is not supported,
  exiting the process immediately`, sets `errorlevel` 1 and returns in **0.02s**. §7 now has
  a `ping -n 11 127.0.0.1` behind it — 10.09s measured, against 0.02s for the original line
  alone, and 0 `ping.exe` processes across a console hand-run, so it costs nothing where
  `timeout` works. The general lesson is not about batch files: **this document's code blocks
  have been specifications since W0, and this is the first one that was wrong.** A block that
  has never been run is a proposal, and the run step is what tells the two apart — which is
  also why §7's `install.ps1` paragraph turned out to describe a script that registers a task
  (W5b's half) and sets a DACL (it should not; the file already inherits the right one).
- **2026-09-27, W5a → §9's W5b.** Two facts the next slice would otherwise have had to
  rediscover. Whether Task Scheduler hands its action a console decides which half of §7's
  throttle runs, and the answer is now readable straight out of `var\bot.log`. And nothing
  anywhere sets `PYTHONIOENCODING`, so that log is written in the ACP — cp1252 here — which
  puts a project name outside it into §14's one diagnostic as backslash escapes. Neither is a
  defect today; both belong in the XML's environment if anything does.
- **2026-09-27, W5b → §7's table.** The table had no `Priority` row and should have had it
  first. Task Scheduler defaults a task to priority **7**, which is
  `BELOW_NORMAL_PRIORITY_CLASS`, and a child inherits its parent's priority class — so the
  plist's loudest warning, the one about `ProcessType Background` throttling every session
  the listener spawns, has an exact Windows twin that this document had walked straight past.
  Measured with the tree cleared between registrations: no element and all three processes
  are `BelowNormal`; `<Priority>5</Priority>` and all three are `Normal`. The porting rule
  the plan was missing: **when a setting on one platform is dangerous because of its default,
  look for the other platform's default, not for the same word.** "ProcessType" and
  "Priority" share nothing but the bug.
- **2026-09-27, W5b → §7, §9's W5a.** `windows\install.ps1` as committed in `178b5fa` did
  not parse: nine errors from `powershell -File`, the invocation in its own header. Windows
  PowerShell 5.1 reads a BOM-less `.ps1` as cp1252 and a UTF-8 em dash ends a string there.
  W5a's run step had measured a working script and its prose was polished afterwards; twelve
  tests read the file and none ran it. **A run step measures the bytes that were run, and the
  commit is a different set of bytes unless something checks.** The check is now
  `test_nothing_under_windows_is_anything_but_ascii`, portable, one rule for the directory.
- **2026-09-27, W5b → §7, §9's W5b.** Three of the entry's own predictions were wrong.
  `install.ps1` has two substitutions, not one, because a task lives in a machine-global
  store and its `LogonTrigger` must name a `UserId` — without one it means every user's logon
  and `schtasks /Create` says "Access is denied". The XML **cannot** carry `PYTHONIOENCODING`
  at all: this schema has no environment element, so the only candidate place was the batch
  file the entry ruled out, and the decision taken is to leave it unset with the reasoning in
  `bot.cmd`. And Task Scheduler has no `StandardOutPath`, so the third `this_mac_checkout`
  counterpart asserts a join between two files rather than one field. The one prediction that
  was a genuine open question — does the action get a console — came back **yes**, which
  retires the risk without retiring W5a's `ping` fallback.
- **2026-09-27, W5c → §7, §9's W5c.** "No new code expected" was wrong, and what it missed was
  not in the task or the XML but in a redirection. `cmd` opens a redirection target denying
  other writers, so any process holding `var\bot.log` makes every `>>` in `bot.cmd` fail; a
  failed redirection **leaves `ERRORLEVEL` at 0**, so `if errorlevel 1 ping` — W5a's belt —
  never fires; and the `timeout` that belt guards was skipped too, because it carried
  `2>> var\bot.log`. Three survivable-looking facts compose into the hot loop §7 exists to
  prevent, and a *silent* one: 13% of a core with not even the two log lines a second that
  would have named it. The throttle now redirects only to `nul`, which cannot fail. The rule
  W5a wrote — the throttle does not get to depend on how the file was launched — needed one
  more clause: **nor on anything it writes to.** The general form, which is what §7 gets
  wrong twice now: a fallback that shares a dependency with the thing it is backing up is
  not a fallback.
- **2026-09-27, W5c → §7, §9 (new W5e).** The document has said since W1 that the runner's
  log lines land in `var\bot.log` beside the listener's, and on the Mac that is free. On
  Windows it means a live session **pins** the log: `cmd` cannot reopen it, so `bot.cmd`'s
  `KeepAlive` loop cannot restart a listener that died while a session was running — 5m10s
  of a wrapper doing nothing, recovering 1.1s after the runner exited. §7 has the paragraph;
  W5e has the decision, because the fix is either "the runner logs somewhere else" or "the
  wrapper stops using `cmd` redirection" and neither is a detail. The porting rule underneath
  it is the same shape as W5b's `Priority` one: **an inherited handle is a resource on both
  platforms and a lock on only one**, and every place SPEC.md says "inherits" is a place to
  ask what Windows makes of it.
- **2026-09-27, W5e → §6, §7, §9's W5e.** The decision that entry framed is taken: **the
  runner logs somewhere else.** `procs.runner_output(directory)` yields `{}` on POSIX and a
  `var\sessions\<sid>\runner.log` here, and SPEC.md §14's first diagnostic is two files on
  this platform. Three measurements decided it against the pipe, and the third is the one
  worth keeping: a pipeline's `%errorlevel%` is its **last** command's, so a writer process
  behind `bot.py --serve` would have made `=== listener exited N ===` report the writer — and
  that line, with its twin, is the entire log §7 exists to produce. The general rule, which
  is W5c's turned around: *when a fallback and the thing it backs up cannot share a
  dependency, neither can a fix and the thing it fixes.* Two of §7's own sentences were
  wrong. `||` was named as "the hook the fix in W5e will need" and the fix needed no hook at
  all, because taking the writer off the log is cheaper than teaching a batch file to cope
  with a log it cannot open; `bot.cmd` is unchanged below its header. And the ERRORLEVEL
  finding is narrower than it reads: `cmd /c echo x >> <a locked file>` **exits 1**, and it
  is only the *in-script* `ERRORLEVEL` that stays 0, because a command that did not run sets
  nothing — which is why `||` sees what `if errorlevel 1` cannot, and why W5c's hot loop was
  invisible from inside and would not have been from outside.
- **2026-09-27, W5e → §9's W5e Red line.** A property test says *that* the log opens and
  never *where* the lines went, so "a test that a live session does not stop a second
  `bot.cmd` from opening the log" is one test of four. The other three are the mechanism, and
  the reason to write them is that the property test is expensive (a real runner, a real
  `cmd`) and says nothing about append-versus-truncate, about the listener closing its own
  copy, or about the file staying readable while the session runs. Two mechanics the next
  real-process test on this platform will want: `subprocess` with `stdout=None` passes
  `GetStdHandle(STD_OUTPUT_HANDLE)` to `CreateProcess` and **not fd 1**, so `dup2` cannot
  reproduce an inherited redirection and `SetStdHandle` must; and a `cmd` probe has to be
  passed as one string, because `list2cmdline` escapes an embedded quote as `\"` and `cmd`
  fails on the path instead of on the thing under test.
- **2026-09-27, W5d → §7 and §9's W5d.** The not-logged-on case was written as one edit —
  "switch the task to `LogonType Password`" — and it is two, in two different elements. A
  `LogonTrigger` fires on a logon and on nothing else, so a principal that can run with
  nobody logged on is still never started; the entry's own recipe (switch it, reboot, do not
  log in, message the bot) would have measured a bot that was never triggered and recorded it
  as a bot that cannot run without a session. §7's table gains nothing and its
  `InteractiveToken` paragraph gains the pairing;
  `test_the_logon_type_and_the_trigger_agree_about_when_the_bot_runs` is what stops half the
  move landing silently, and it is the only thing in the repository that can see it — 39 of
  `test_layout`'s 40 pass with a `BootTrigger` bolted on under `InteractiveToken`.
- **2026-09-27, W5d → §7's `StartWhenAvailable` row, doubted and *not* corrected.** "A missed
  logon trigger still fires" is documented by Microsoft as a property of time-based triggers,
  which a logon trigger is not. Nothing on this desk can tell the two apart without a logoff,
  so the row stands with the doubt written against it rather than being quietly reversed, and
  §11's logoff time item carries the one observation that settles it. Recorded here because
  the *absence* of a correction is also a decision the evidence forced.
- **2026-09-27, W5d → the shape of what this box can verify at all.** Two of W5's remaining
  questions are blocked by facts about the *account* rather than about Windows: this user has
  no password, so no batch logon of it exists (`LogonUser` answers 1327, never 1326, for
  `BATCH`, `SERVICE`, `NETWORK` and `INTERACTIVE` with an empty credential), and `S4U` and
  `BootTrigger` both need elevation. That puts "run whether user is logged on or not" in the
  same class as the phone token: a user's decision about their own machine, named in §11 and
  not taken by a slice.
- **2026-09-27, W6 → §9's ritual, last paragraph, rewritten.** "Until a CI matrix exists
  (W6), 'green on the Mac' means somebody ran it there" could be rewritten, and only halfway.
  `/usr/bin/python3` on a `macos-latest` runner is **3.9.6** — the Command Line Tools build,
  the same version the entire Mac guard is written against — so step 3 and the suite half of
  step 5 are answered by a push. Step 4 is not, and neither is anything else that needs *that
  machine*: no `~/Projects`, no Claude Code, no token, no phone, no launchd. The paragraph is
  now two sentences instead of one and a slice has to say which it is claiming.
- **2026-09-27, W6 → §11's Mac skip arithmetic, wrong by ten since W1a.** Every row since
  W1a wrote the Mac's expected skip count as "this box's number, plus or minus the slice",
  and W5c's "94 here and there" is the assumption stated outright. The two platforms skip
  different sets: measured, the Mac is **89** where this box is 95. W5e's explicit prediction
  of **99** is the one the matrix refutes. Not corrected row by row here — that is W7's first
  half, and correcting nineteen rows of arithmetic inside the commit that discovered the
  arithmetic was wrong is how a second wrong number gets written down.
- **2026-09-27, W6 → W4a's `0.0`, narrowed from a platform fact to a box fact.** §6 and W4a's
  row say psutil answers `0.0` for pids 0 and 4 on Windows. A GitHub runner answers a real
  `create_time` for pid 4. The *shape* W4a recorded survives — a pid nothing can date
  honestly still reads as older than every record — but "Windows answers 0.0" is this
  machine's answer and the document should not have generalised it. The test now measures
  against this process's own `create_time` rather than `psutil.boot_time()`, which were
  never the same clock.
- **2026-09-27, W6 → the suite's one non-hermetic test, found by a box without Claude Code.**
  `test_the_default_on_windows_is_what_is_on_path` compares `DEFAULT_CLAUDE_BIN` against
  `shutil.which("claude")` and cannot run where Claude Code is not installed — `load()`
  raises instead. Gated on the installation. Worth recording as a decision rather than a fix
  because the rule it establishes is new: **a test may depend on this box's *configuration*,
  never on this box's *software*,** and the only thing that can enforce that is a runner.
- **2026-09-27, W7 → §11's Mac skip arithmetic again, and this time the diagnosis rather
  than the number.** W6 filed it as "wrong by ten". It is not an error of ten; measured with
  `-v` on run `36308573533`, this desk skips **98**, the macOS runner **90**, CI Windows
  **86**, and the desk's set and the Mac's set have **exactly one member in common**. Two
  nearly disjoint sets were being added to each other for twenty slices, so every absolute
  Mac count in the table is void as a class and not as ten separate slips. They are left
  standing with that said over them: restating them one by one would repeat the mistake with
  better arithmetic, and a quietly corrected number is the thing this document exists to not
  do.
- **2026-09-27, W7 → W3i's row, refuted.** It predicted
  `TestTheLinkAtTheEndOfTheWindowsCapture` would skip on the Mac because `rc_startup_win.log`
  "is not on that box". The fixture is committed — `git ls-files tests/fixtures` lists all
  three — so it is on every checkout, and both tests ran and passed on 3.9.6. The row is
  *nothing newly skipped against twelve newly run*. Same pass, same run: W5b's headline
  ("sixteen newly run, nothing newly skipped") contradicted its own body ("plus 3 there") for
  four slices, and the body was right.
- **2026-09-27, W7 → §5.3, and it is a decision *not* to change code.** W6 left open whether
  `config._child` should canonicalise a dangling reparse point whose stored target is spelled
  through an alias of the root. It should not. Nothing is unsafe either way — a dangling link
  is refused on every path through — so the whole of the question is which sentence the phone
  gets, and against that the cost is a second resolution pass that re-judges a path which has
  already failed check 3, inside the one function §10.4 is a promise about, over a string the
  OS itself declined to resolve. §5.1's rule (`config.py` fails closed) settles it. Recorded
  here because a decision to leave code alone leaves no diff, and the next reader who finds
  check 3 firing on a name that never escaped anything will otherwise fix it.
- **2026-09-27, W7 → the ritual's steps 3 and 5, finishing W6's half-rewrite.** W6 rewrote
  the paragraph under the ritual to say CI answers the Mac half and left steps 3 and 5 saying
  the Mac suite runs "before the commit, not after". It does not and has not since W6: it
  runs on a push, which is after the commit and before the merge. The steps now describe the
  branch-push-amend-`--ff-only` shape that W6 and W7 both actually used.
- **2026-09-27, W7 → the workflow's verbosity.** All three CI jobs run `-m unittest -v`
  instead of `-q`. Not a preference: `-q` prints a count, twenty rows of §11 predict *which*
  tests skip, and the two cannot be compared.
  `test_layout.TestTheCiMatrix::test_every_job_runs_the_suite_verbosely` keeps it, because
  losing it would take the table's evidence away without taking anything red with it.
