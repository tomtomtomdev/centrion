# centrion — start a Remote Control Claude session from Telegram

A background LaunchAgent on this Mac long-polls a Telegram bot. Message it `claude` and it
starts `claude --remote-control <name> --dangerously-skip-permissions` in a project directory
and replies with the session link. Open the link (or find the session in the Claude app) and
you are driving a real session on this machine from your phone — your filesystem, your MCP
servers, your git checkouts, permissions already bypassed.

The bot is a **launcher**, not a bridge. It does not relay conversation. Once the link comes
back, Remote Control carries everything; Telegram's job is done.

Status: **slices 0-11 and 13 built, and every verb has now been driven from a phone —
the last one from a button.** `claude <project>` starts a session, `new <name>` creates the
project first and answers §9.3's trust dialog for it, `ls` lists them, `stop <n>` and `stop all`
end them, `max_sessions` refuses the one past the cap, and §4's reconciliation pass clears the
records a reboot orphaned and announces a link that arrives after its waiter gave up. Slice 10
bounded what all that leaves behind: the transcript is capped and a finished session's directory
goes after a day (§10.7). Slice 13 put the project list on the phone as a keyboard, which adds no
verb at all: a button says `claude <project>` in full and is read by the same parser as a typed
message.

**§9.13, which blocked the whole thing, stopped happening before slice 9 could diagnose it.**
For three hours on 2026-09-13 a LaunchAgent-started Claude Code hung at startup in any directory
containing a `.git` — every directory this bot can reach — and §8 was suspended behind it. By
13:07 the minimal repro answered 14 times out of 14 and the real runner scraped a link two
seconds after a launchd spawn. Nothing was changed and nothing was fixed; no reboot, no version
change. So the LaunchAgent is back and holding the lock, `launchctl kickstart -k` puts it back
in one second, and §9.13 is kept as a recognition guide rather than a diagnosis.

**The last run step closed on 2026-09-13 at 16:31, and it was a real logout.** `RunAtLoad`
brought the listener back 25 seconds after the login with nothing started by hand — and the
beacon session live since 13:02 came through as the *same* processes, because slice 6's
`TIOCSCTTY` leaves the runner a session leader with `ppid 1` and the gui teardown had nothing of
its to kill. So a brand-new listener process met a live record it had never spawned and left it
alone, which tested §8's reconciliation rather than launchd. A login after a cold *boot* is
still unwatched. See §12 slice 9 and §14.

**What is left is one planned slice and two things only time can close.** Slice 12 makes the 45s
deadline capture a stack, so §9.13 cannot come back unwitnessed a second time; §14 carries the
cold boot and the first real day under §10's retention. See §13.

Every claim marked *verified* was tested on this box against Claude Code v2.1.269 (2026-09-12)
or v2.1.270 (2026-09-13). **The box is on v2.1.263 as of 2026-09-23** — the homebrew build, older
than both, `~/.local/bin/claude` gone (§9.8) — and §14's two UI-coupled checks were re-run against
it and hold.

---

## 1. Why this shape

`/rc` inside a session, `claude --remote-control`, and `claude remote-control` (server mode) are
three doors to the same feature. The flag form is the one that matches "open a session with /rc
enabled", and it is what this spec builds on.

**Consider server mode before building any of this.** `claude remote-control --permission-mode
bypassPermissions`, run once per project as its own KeepAlive LaunchAgent, is a persistent
server that creates sessions on demand (up to 32) when you connect from the phone. No Telegram,
no PTY, no scraping — roughly 40 lines of plist against the ~600 lines below. If all you want is
"reach a Claude session on this Mac from my phone", stop here and do that instead.

The bot earns its keep on one thing server mode cannot do: **choosing the project from your
phone and having the link pushed to you**, without opening claude.ai and hunting a session list
first. If that is the point, continue.

---

## 2. Processes

Three, deliberately.

```
launchd (GUI domain, KeepAlive)
  └── bot.py            listener — long-polls Telegram, owns nothing else
        └── session.py  runner — one per session, detached (setsid), owns the PTY for its life
              └── claude --remote-control <name> --dangerously-skip-permissions
```

**The runner is detached on purpose.** launchd restarts the listener on every crash, every
logout, every `launchctl kickstart`. If the listener held the PTY master, each restart would
SIGHUP every live session and kill work mid-turn. Sessions must outlive their launcher, so the
runner calls `os.setsid()` and the listener keeps no handle on it.

*Verified in slice 7*, with the control that makes it a test rather than a hope: a throwaway
LaunchAgent forked two children, one that called `setsid()` and one that did not.
`launchctl kickstart -k` killed the one still in the job's process group and left the other
running. `setsid()` is the whole of the difference.

The runner cannot exit, though: closing the PTY master fd hangs up the child. It sits in a read
loop for the whole session life, appending to a transcript log. That is its only job.

**They communicate through files, not sockets.** A restarted listener re-reads `var/sessions/*/`
and knows exactly what is live — no reconnection protocol, no port, and the state is
inspectable with `cat` when something goes wrong.

---

## 3. Stack and layout

### Stack

Chosen, not defaulted into. Everything here is already on this box and verified working.

| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| Language | **Python 3.9.6**, `/usr/bin/python3` | Ships with macOS, so no `brew upgrade` can break the daemon at 3am. Same call `stock-watch-project` already makes (`PY=/usr/bin/python3`). Homebrew's 3.13 is present but is a moving target for a thing launchd must start on every login. |
| Dependencies | **stdlib only** — `urllib`, `json`, `pty`, `termios`, `fcntl`, `select`, `signal`, `os` | No venv, no lockfile, no supply chain. `python-telegram-bot` would add an async runtime and ~20 transitive packages to do one `GET` in a loop. `uv` is on this box for `beacon`, and is still not worth it here. |
| Telegram | **Bot API long polling**, hand-rolled on `urllib` | Outbound HTTPS only: no webhook, no public hostname, no inbound port, nothing to expose from a laptop. The client is a cut-down lift of `notify.py`, which already has the retry and token-redaction behaviour. |
| Session control | **`claude --remote-control` under a PTY** (`pty`/`termios`) | The flag form is what "a session with /rc enabled" means. Server mode is the alternative and §1 weighs it. |
| Supervision | **launchd LaunchAgent**, `KeepAlive` | The box's existing convention — five `com.tommy.*`/`com.beacon.*` agents already. No brew services, no pm2, no Docker (Docker could not reach the host filesystem or the host `claude` login, which is the entire point). |
| Locking | **`lockf(1)`** via `lock.sh` | Copied verbatim from `stock-watch-project`, whose header already documents why `shlock(1)` is wrong on macOS 25.6. |
| State | **JSON files under `var/`** | Survives a listener restart, inspectable with `cat`, no daemon-to-daemon protocol. SQLite would buy transactions this does not need. |
| Tests | **stdlib `unittest`**, no network | Matches `test_notify.py`/`test_pick.py`. pytest would be the first dependency in the tree. |
| Git | fresh repo, `git init` in slice 0 | `~/Projects/centrion` is not a repo yet. |

Not used, deliberately: node/bun/deno (none installed, and `claude` is the native build),
tmux (not installed; `pty` does the job without a dependency), Docker, any web framework.

### Layout

```
~/Projects/centrion/
  SPEC.md
  bot.py                    listener loop + command dispatch
  commands.py               message → intent, pure, no I/O
  config.py                 config load + project resolution (§3), shared by bot and runner
  session.py                PTY runner; also runnable by hand for debugging
  attach.py                 the terminal window onto a session: socket server + viewer
  telegram.py               Bot API client (lift from stock-watch-project/notify.py)
  .telegram.json            0600, gitignored — token, allowlist, root
  tests/
    test_*.py               stdlib unittest, no network
    fixtures/rc_startup.log real captured PTY transcript of an RC startup (§12 slice 6)
  launchd/
    com.tommy.centrion.bot.plist
    bot.sh                  the /bin/sh launchd actually execs
    lock.sh                 lockf single-instance guard (copy of stock-watch's)
  var/                      gitignored
    bot.log                 listener log (launchd stdout+stderr land here too)
    offset                  last processed getUpdates update_id
    .bot.lock               lockf target
    sessions/<sid>/meta.json
    sessions/<sid>/pty.log    full ANSI transcript of that session's terminal, capped (§10.7)
    sessions/<sid>/pty.log.1  the transcript before the last rotation; the tail reads both
    sessions/<sid>/tty.sock   0600 Unix socket a viewer attaches through (attach.py)
    sessions/<sid>/attach.command  what Warp (or Terminal) is handed to open that viewer
```

`/usr/bin/python3` (3.9.6, system), stdlib only — `urllib`, `json`, `pty`, `select`, `os`,
`signal`. No venv, no requirements file, nothing a `brew upgrade` can break at 3am. Same
reasoning as stock-watch-project's `PY=/usr/bin/python3`.

### `.telegram.json`

Extends the shape already in use in stock-watch-project, so `--whoami` and the client code carry
over.

```json
{
  "bot_token": "123456:ABC...",
  "allowed_chat_ids": [987654321],
  "projects_root": "/Users/tomtomtomtom/Projects",
  "claude_bin": "/Users/tomtomtomtom/.local/bin/claude",
  "max_sessions": 2,
  "terminal_window": true
}
```

**`terminal_window` opens a Warp window onto each session once it is live**, default on, Mac
only. Warp because it is what this Mac reaches for; Terminal.app when Warp is not installed,
which is the only app a Mac is guaranteed to have. Nothing but the app name changes between the
two — Warp declares itself a handler for `com.apple.terminal.shell-script`, so the same
`.command` runs in either. The runner still owns the pty (§2) and serves it on `tty.sock`; the window is a
viewer (`session.py --attach <sid>`) that mirrors the screen and types into it, resizes the pty
to itself, and can be closed — or left with Ctrl-] — without ending the session. It opens only
after the link is scraped, because until then the pty must stay at §6's size. `open` and a
`.command` file rather than AppleScript, so no Automation grant is ever asked of launchd.
`session.py --attach <sid>` from any terminal does the same by hand, with this set or not.

**`bot_token` is a bot of its own, not the stock-watch one.** Sharing would break in a way that
is hard to see: `notify.py --whoami` calls `getUpdates` (notify.py:171), and two consumers on one
token 409 each other — so a single `--whoami` run would knock centrion's poller off the air for
as long as it took, and centrion would do the same back.

*Corrected in slice 3:* this paragraph used to say `getUpdates` without an offset *consumes* the
queue. It does not. An update is confirmed only when `getUpdates` is called with an offset above
its `update_id`, which is why `--whoami` can be run twice and see the same message both times —
verified on this box. The 409 stands on its own and is structural; the decision does not change.

The security half matters more: one token would then
be both a stock notifier and shell access to this Mac, and the notifier is the half that gets
pasted around. Create a second bot in BotFather and keep the two `.telegram.json` files apart.

**`max_sessions: 2`, because this box has 8 GB.** Each session is a native Claude Code process
plus whatever it spawns — a simulator, a dev server, a python venv. Three is already swap
pressure. The cap is here to stop a held-down `claude` on a phone, not to ration deliberate work;
raise it after watching memory, not before.

**There is no `default_project`, deliberately.** See §5.

`allowed_chat_ids` empty or missing means **refuse everything**. Fail closed; an unconfigured
bot must not be an open shell.

**`projects_root` is the whole of the bot's reachable world, and it is `~/Projects`.** There is
no per-project map to maintain: `claude beacon` means the directory `beacon` directly inside
that root, so a new project becomes reachable by existing, and nothing outside the root is
expressible in any message. §9.2 is why the root is `~/Projects` and not `~`.

Resolution, in order — any failure is a `help` reply, never a path in the error:

1. Reject a name containing `/`, `\\`, a control character, or a leading `.`, or one that is
   `.` or `..`. *Control characters were added in slice 11*, when `new` made a project name
   something the bot **writes** to the filesystem rather than only looks up: a directory called
   `red<ESC>[31m` repaints `var/bot.log` around itself when §14 reads it with `tail -f`, and
   nothing here can delete it afterwards. Both verbs refuse them, through the same code.
2. Join to `projects_root` and `os.path.realpath` the result.
3. Require the realpath's parent to be exactly the realpath of `projects_root` — a direct child,
   so a symlink inside the root that points outside it fails here.
4. Require it to be a directory.

The listener resolves; the runner re-checks before `chdir`. Cheap, and it means a bug in
command parsing cannot become an arbitrary working directory.

### `meta.json`

```json
{
  "sid": "3f2a91",
  "state": "live",
  "project": "centrion",
  "cwd": "/Users/tomtomtomtom/Projects/centrion",
  "name": "centrion-3f2a",
  "runner_pid": 44213,
  "claude_pid": 44215,
  "url": "https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF",
  "started": 1789198400,
  "chat_id": 987654321
}
```

`state` ∈ `starting` | `live` | `failed` | `ended`. The runner writes it atomically (temp file +
`os.replace`) so a listener reading mid-write never sees half a record.

---

## 4. The launch sequence

1. Listener validates the sender, resolves the project name against the allowlist, checks the
   live-session count against `max_sessions`, and mints `sid` (6 hex).
2. It spawns `session.py --sid <sid> --cwd <dir> --name <name>` detached, and returns to
   polling immediately. **The listener never blocks on a session.**
3. Runner: `os.setsid()`, writes `meta.json` with `state:"starting"`, then `pty.fork()`.
4. Child: `chdir(cwd)`, builds a clean env (§6), `execve(claude, [...])`.
5. Parent: non-blocking read loop on the PTY master. Every chunk appends to `pty.log`. Until the
   URL is found, each chunk is also ANSI-stripped and matched against

   ```
   https://claude\.ai/code/session_[A-Za-z0-9_-]+
   ```

   On the first hit: write `meta.json` with `state:"live"` and the URL. Then keep reading
   forever — the loop is what holds the PTY open.
6. Listener polls `meta.json` for up to 45s (0.25s interval). On `live`, it replies with the
   link. On timeout or `failed`, it replies with the last 15 lines of `pty.log`, ANSI-stripped
   and scrubbed (§7), which is almost always the actual error.

### Reconciliation

The listener is otherwise purely reactive — it blocks in `getUpdates` and only acts on messages
— so on its own it would never notice a session ending, or a runner dying. **Every return from
`getUpdates`, message or not, is the tick**: at ≤50s cadence, walk `var/sessions/*/meta.json`
and for each record not `ended`:

- **Verify the runner pid with `os.kill(pid, 0)`.** A `live` record whose process is gone —
  reboot, force quit, OOM — is stale. Mark it `ended`. Without this check, stale records
  accumulate against `max_sessions` until the bot refuses to start anything and `ls` lists
  sessions that do not exist. A reboot alone produces this.
- A record that has newly reached `ended` gets one push to the chat that started it
  (`chat_id` is in the record for exactly this). This is the only message the bot sends
  unprompted.

pid reuse is theoretically possible between reboots; `started` is in the record, so compare it
against the process start time before trusting a pid that is alive.

*Verified:* a headless Python parent with no controlling terminal spawned
`claude --remote-control tg-probe --dangerously-skip-permissions` under `pty.fork()`, the header
showed `~/Projects/centrion · /rc connecting…` and `⏵⏵ bypass permissions on` with no prompt, and
`https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF` appeared in the stream. Budget 10–20s;
45s is the timeout because a cold start after a Claude Code update is slower.

If an initial prompt was given, the runner writes it to the PTY master after `live`, waits ~400ms
for the input box to settle, then writes `\r`.

---

## 5. Command surface

Parsing: strip one leading `/`, strip a trailing `@botname`, lowercase the verb. Both `claude`
and `/claude` work — the bare word is what was asked for, the slash form is what BotFather's
menu will send.

**Tier 1 — the point of the thing**

| Message | Effect |
|---|---|
| `claude` | replies with the directories in `~/Projects` and waits — it never picks a target for you |
| `claude beacon` | session in the `beacon` project |
| `claude beacon fix the failing probe test` | same, then types that prompt and hits Enter |
| `new scratchpad` | creates `~/Projects/scratchpad`, then starts a session there as above |

**Tier 2 — enough to not need a laptop to clean up**

| Message | Effect |
|---|---|
| `ls` | live sessions: index, project, name, uptime, link |
| `stop 2` / `stop all` | SIGTERM the runner, which SIGTERMs claude |
| `help` | the two tables above, and the directories currently in `~/Projects` |

Anything else: reply with `help`. The tier-1 and tier-2 verbs are registered as the phone's `/`
command menu by the bot itself (`setMyCommands`, at every startup) so they autocomplete there and
cannot drift from what the parser understands — no BotFather `/setcommands` step.

**Bare `claude` starts nothing.** It answers with the project list and waits for a second
message. The cost is one extra tap; what it buys is that no bypass-permissions session can ever
start in a repository you did not name. A "most recent project" default would be more
convenient and would make the target invisible at exactly the moment it matters — you are on a
phone, half-attending, and the first visible confirmation arrives after the session already
exists.

**Creating a project is its own verb, and that is the whole of its safety.** `claude beacn` is
a typo, and it stays one — the project list comes back and nothing is created. Folding creation
into `claude <name>` would mean every mistyped project name silently becomes an empty repository
with a bypass-permissions session sitting in it, which is the same mistake as a "most recent
project" default wearing different clothes: the target becomes invisible at exactly the moment
it matters. Saying `new` is the one extra word that keeps a directory from coming into existence
by accident. The name goes through §3's checks 1-3 unchanged — only the existence check (4) is
relaxed, and it is relaxed into its opposite, because `new` on a name that is already there is
not a new project. See §12 slice 11, and §9.3 for the part of it that is not a `mkdir`.

**A second session in a directory that already has one is allowed, and the reply says so.**
Refusing would be wrong — two sessions on one repo is a normal way to work — but they will
happily edit the same files underneath each other, which is the same hazard `claude
remote-control --spawn same-dir` carries. One line in the reply (`⚠ 2nd session in beacon`) is
the whole mitigation; anything more belongs to git, not to this bot.

Success reply — plain text, link on its own line so Telegram makes it tappable:

```
▶ beacon · beacon-3f2a
https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF
bypass permissions on · 2 of 2 sessions
```

---

## 6. Environment for the spawned session

Remote Control is fussy about its environment and launchd gives you almost none of it. Build the
child env explicitly rather than inheriting.

**Must be set**

- `PATH=/Users/tomtomtomtom/.local/bin:/Users/tomtomtomtom/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin` —
  `claude` lives at `~/.local/bin/claude`, and the session's own Bash tool needs brew on PATH to
  be useful. `~/.cargo/bin` because project hooks run under `/bin/sh -c` with this PATH and read
  no `.zshenv`: without it ttsecuritas's `tuntun ... --hook` fails with
  `/bin/sh: tuntun: command not found` in every session started from the phone.
- `HOME`, `USER`, `SHELL`, `LANG=en_US.UTF-8`.
- `TERM=xterm-256color`.
- `COLUMNS=200`, `LINES=50` — for the shell and for tools the session itself runs. **These do
  not size the terminal Claude Code renders into.** See below.

**Must be absent**

- `DISABLE_TELEMETRY`, `DO_NOT_TRACK`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`,
  `DISABLE_GROWTHBOOK` — each one disables the feature-flag evaluation Remote Control's
  availability depends on. The session starts fine and RC just never connects.
- `ANTHROPIC_BASE_URL` pointing anywhere but `api.anthropic.com`. Unset it entirely.
- `ANTHROPIC_API_KEY` — RC requires the claude.ai subscription login, not an API key.
- **Every `CLAUDE_CODE_*` and `CLAUDECODE`.** These leak in whenever the listener is started by
  hand from inside a Claude Code session and confuse the child about which session it is. Strip
  by prefix, not by name — the list grows between versions. (Hit during the probe.)

### Model and effort

**Pass no `--model` and no `--effort`.** Sessions inherit `~/.claude/settings.json` — today
`opus[1m]` with `effortLevel: xhigh` — so a session started from the phone is the same session
you would have started at the desk. That is the point: the bot is a launcher, and a launcher
that quietly downgrades the thing it launches is a trap, because the difference only shows up as
worse answers hours later.

The cost is real and worth naming: every message from the couch runs 1M-context Opus at the
highest effort. Two levers exist if that bites, neither of which needs a code change —
Remote Control lets you pick the model per session from the phone, and changing
`~/.claude/settings.json` changes every future spawn. Reach for those before adding a flag here.

### Terminal size is an ioctl, not an environment variable

The scrape depends on the URL landing on one line, so the width the renderer actually uses
matters. `COLUMNS` does not set it.

*Verified:* a `pty.fork()` terminal defaults to **0×0**, not 80×24 — `stty size` on a fresh one
prints `0 0` while `$COLUMNS` happily says 200. Claude Code asks the kernel, sees nothing usable,
and falls back to its own 80 columns. In the probe transcript the panel rules measure exactly 80
despite `COLUMNS=100` in the child's environment. **The URL fit on one line at 80 by luck, not by
design.**

The fix is `TIOCSWINSZ`:

```python
fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
```

*Verified:* with this, `stty size` in the child reports `50 200`.

Set it on the **slave fd before the exec**, which means `os.openpty()` + an explicit
`fork`/`setsid`/`dup2` rather than `pty.fork()`. Setting it on the master after `pty.fork()`
returns does work, but races the child's first render — and the failure it produces is an
occasional missed URL, which is the worst kind to debug.

---

## 7. Telegram transport

Long polling, outbound HTTPS only, no webhook and no inbound port:

```
GET https://api.telegram.org/bot<token>/getUpdates
    ?offset=<last+1>&timeout=50&allowed_updates=["message"]
```

- `urllib` socket timeout **60s**, above the server's 50s hold. Below it and every idle poll
  looks like a network failure.
- Call `deleteWebhook` once at startup. A webhook set at any point in this bot's past makes
  `getUpdates` return 409 forever.
- **409 Conflict means two consumers on one token.** `lockf` (§8) stops a second copy of this
  daemon; a second *bot* is on you. Log it loudly, back off 30s, retry.
- Persist `offset` to `var/offset` after each batch. Without it, a restart replays the batch and
  starts duplicate sessions.
- Belt and braces: **drop any message whose `date` predates daemon start by more than 120s.**
  Telegram holds undelivered updates for 24h. Come back from a weekend of downtime with `offset`
  lost and a naive loop spawns every `claude` you sent in the meantime, all at once.
- Network errors: exponential backoff 1→2→4→…→60s, then hold at 60. Never exit — launchd would
  restart, but a crash-loop against `ThrottleInterval` is a worse failure mode than a patient
  retry.

**Send error tails and `ls` output as plain text — no `parse_mode`.** `notify.py` uses HTML mode
and escapes for it, which is right for a composed report and wrong here: a PTY tail contains
arbitrary `<`, `&` and `_`, and one stray character turns the whole send into a 400 exactly when
something has already gone wrong. Keep HTML mode only for text this code composes itself.

**Truncate at Telegram's 4096-character cap**, head and tail with a marked elision — `ls` with
several sessions and a 15-line error tail can both exceed it. `notify.py` already carries
`LIMIT = 4096`; reuse it rather than rediscovering the 400.

Scrub anything outbound that came from the PTY: collapse `/Users/tomtomtomtom` to `~`, and drop
any token-shaped run of characters. §10 is why.

Outbound `sendMessage` is best-effort with 3 attempts, exactly as `notify.py` already does it.
Never put the token in an exception message: it is in the URL, and `HTTPError` carries the URL.
`notify.py` already raises its own error type for this reason — reuse that.

---

## 8. launchd

> **Suspended 2026-09-13 09:56, restored 13:15**, and the plist was never edited in between.
> §9.13 made a LaunchAgent unusable for about three hours: under launchd, Claude Code hung at
> startup in any directory containing a `.git`, which is every directory this bot can reach.
> The agent was booted out and the listener run by hand from `launchd/bot.sh` — which is what
> that script's header always said was the other supported way to start it, and which the lock
> makes safe to mix with a loaded agent.
>
> Slice 9 went to name the file that hang was blocked on and found nothing left to name: the
> minimal repro answered 14 times out of 14, and the real runner — pty, `--remote-control`, a
> real repository, under launchd — scraped a link two seconds after the spawn. So §8 is back as
> it was, on the evidence of it working rather than of the cause being fixed. §9.13 keeps the
> record and §14 keeps the standing check, because nothing here was explained.
>
> Starting it by hand remains supported and is still the workaround if §9.13 returns. Stop the
> agent first, or it holds the lock and the hand-run copy exits saying so — and in the other
> order the agent logs a refusal every ThrottleInterval seconds, which is `bot.sh` telling you
> exactly this rather than two pollers 409ing each other.

`~/Library/LaunchAgents/com.tommy.centrion.bot.plist`, matching the `com.tommy.*` convention.

```
Label            com.tommy.centrion.bot
ProgramArguments /bin/sh /Users/tomtomtomtom/Projects/centrion/launchd/bot.sh
WorkingDirectory /Users/tomtomtomtom/Projects/centrion
RunAtLoad        true
KeepAlive        true
ThrottleInterval 10
ProcessType      Standard
StandardOutPath  .../var/bot.log
StandardErrorPath .../var/bot.log
```

- **`ProcessType` must be `Standard`, not `Background`.** The other jobs on this box use
  `Background` correctly — they are short batch runs. Here the setting would be inherited by the
  spawned Claude sessions and throttle their CPU scheduling for hours.
- `KeepAlive true` (unconditional), not `SuccessfulExit false`. This daemon has no successful
  exit; if it stops for any reason it should come back.
- Absolute paths everywhere. launchd has no shell, no `cd`, and no useful PATH.
- `bot.sh` sources `lock.sh` and takes the `lockf` lock before `exec`ing python, so a stray
  hand-run and the launchd copy cannot both poll `getUpdates` (→ 409). Copy `lock.sh` verbatim
  from stock-watch-project; the reasoning in its header — flock on fd 9, no stale lock to clear,
  and why `shlock(1)` is the wrong tool on macOS 25.6 — applies unchanged.
- **The runner must close the inherited lock fd before `setsid()`.** `exec 9>>` sets no
  close-on-exec flag, so fd 9 survives the exec into python *and* is inherited by every detached
  runner. *Verified on this box:* a detached grandchild still held fd 9 and therefore still held
  the flock. The consequence is nasty and delayed — the listener exits, its runners keep the
  lock alive, and launchd's restarted listener can never acquire it again, so the bot goes
  permanently silent while its sessions look perfectly healthy. `os.close(9)` in the runner
  before `setsid()`, guarded with `try/except OSError` for the hand-run case where fd 9 was
  never opened.

`install.sh` sets up the rest of this Mac's schedule too, so a new Mac gets all of it from the one
command. `com.tommy.tt-lcmp-pull` runs the tuntun tooling's `~/.tuntun/bin/tt-lcmp-pull` at 09:00;
its plist says `__HOME__` where it needs the home directory, and install.sh skips it with a warning
when that program is not installed yet. `launchd/power.sh` then sets pmset's repeating events,
shutdown at 06:00 and wake-or-power-on at 08:45 every day. That is the one step that needs `sudo`,
so it runs only when `pmset -g sched` differs. A LaunchAgent cannot power a Mac on, so this part
is pmset's, not launchd's.

`mac-cleanup/` is this Mac's disk hygiene — Xcode build output, simulators, caches and stale
Claude Code scratchpads — and is installed separately, because it is not the bot's:
`sh mac-cleanup/install.sh` symlinks the script to `~/.local/bin/friday-cleanup.sh` and the
`/mac-cleanup` command into `~/.claude/commands`. Its unattended Friday `--apply` run deletes
things with nobody watching, so it is opt-in: `--schedule [HH:MM]` loads
`com.tommy.mac-cleanup`, `--unschedule` removes it.

```sh
install:  sh launchd/install.sh     # writes __CHECKOUT__ and __HOME__ in, bootstraps each agent, then power.sh
power:    sh launchd/power.sh --check   # does pmset's schedule match the committed one
lcmp:     launchctl print gui/$(id -u)/com.tommy.tt-lcmp-pull | head -30
restart:  launchctl kickstart -k gui/$(id -u)/com.tommy.centrion.bot
status:   launchctl print gui/$(id -u)/com.tommy.centrion.bot | head -30
remove:   launchctl bootout gui/$(id -u)/com.tommy.centrion.bot
logs:     tail -f var/bot.log
```

**A LaunchAgent lives in the GUI domain: it runs only while logged in, and stops when the Mac
sleeps.** The beacon jobs already proved this the hard way — nine days of an always-on scheduler
producing zero runs because 03:00–05:00 is asleep or logged out. Here it is survivable rather
than fatal: Remote Control needs this Mac awake regardless, so a sleeping Mac means no session
either way. Telegram holds the message and §7's staleness check decides whether to honour it on
wake. If the bot should answer with the lid shut, run it under `caffeinate -s` — and accept the
power cost.

---

## 9. Constraints discovered on this box

All verified 2026-09-12 unless noted.

1. **TTY is mandatory.** `--remote-control` starts an *interactive* session. launchd provides no
   controlling terminal, so the runner must allocate a PTY. `pty.fork()` works; `script -q
   /dev/null` also allocates one but makes reading the stream awkward.

2. **TCC blocks `~/Documents`, `~/Desktop`, `~/Downloads`.** A launchd-spawned process does not
   inherit the terminal's Full Disk Access grant and dies before its first line — exit 126,
   `Operation not permitted`. This box proved it on 2026-09-10 (beacon) and again on 2026-09-08
   (stockwatch); both plists carry the warning. **`~/Projects` is therefore the bot's entire
   scope, by decision and not merely by accident of TCC.** `~/Documents/Junction` and
   `~/Documents/ttsecuritas-2` are trusted by Claude Code and stay out of reach here; work in
   those from the terminal or from a session started by hand.

   There is a way around it — adding `/usr/bin/python3` to Full Disk Access — and this spec
   does not take it. It would hand the daemon, and every bypass-permissions session it spawns,
   all three protected folders for the life of the grant, in exchange for reaching two
   repositories. The narrower blast radius is worth more than the coverage. Revisit only by
   moving a repository into `~/Projects`.

3. **One-time dialogs are already cleared here.** `~/.claude.json` has `remoteDialogSeen: true`
   and `hasUsedRemoteControl: true`, so the "Enable Remote Control? (y/n)" gate will not fire.
   `~/.claude/settings.json` has `skipDangerousModePermissionPrompt: true`, which is why the
   probe came up straight into `⏵⏵ bypass permissions on`. Every `~/Projects/*` directory has
   `hasTrustDialogAccepted: true`.
   **A directory created in `~/Projects` later hangs at the trust dialog.** *Verified in slice
   11, where it had only been inferred before* — from the flag being set on all 46 project
   entries, never from watching a fresh directory come up. It hangs under
   `--dangerously-skip-permissions` too, which is the only way this bot ever starts one:

   ```
   Quick safety check: Is this a project you created or one you trust? …
   ❯ No, exit
     Yes, I trust this folder
   Enter to confirm · Esc to cancel
   ```

   Three things about that panel decide the shape of the answer.

   **The default selection is `No, exit`.** Pressing Enter — the obvious "just confirm it" —
   ends the session. The answer is Down, then Enter, and in between the check that makes it
   safe to send at all: that the marker actually moved onto `Yes, I trust this folder`. A UI
   that reorders the two options leaves the session hanging, which is the old behaviour and is
   honest, rather than confirming whatever is now second.

   **The renderer writes `CSI <n> G` cursor jumps between words instead of spaces**, so the
   stripped transcript is one unbroken run of letters — `yes,itrustthisfolder`. Anything
   matching against what a human sees on the screen matches nothing at all. The same shape of
   trap as §9.5's ULID: the obvious pattern is written against the wrong artefact.

   **Answering it is one-time.** Claude Code writes `hasTrustDialogAccepted` for that path into
   `~/.claude.json`, and a later `claude <name>` in the same directory comes straight up —
   verified: a second session in the answered directory reached `live` with no dialog at all.

   Pre-seeding that key from the daemon was the obvious fix and remains the wrong one: every
   live Claude Code process rewrites that file continuously, so it is a read-modify-write
   racing all of them over 46 projects' configuration to save one key. Answering on the PTY is
   self-contained and no more UI-coupled than the URL scrape already is (§14 covers both).

   **What makes the keystroke legitimate rather than merely convenient is narrower than "the
   bot needs it".** It is that the directory was created by this bot, empty, a second earlier,
   so there is nothing in it to trust. Both halves of that are enforced, in the two places that
   can see them: the listener passes `--trust` only for `new`, and the runner answers only if
   the directory is empty when it starts. `claude <project>` never answers it, and neither does
   a `new` on a name that was already there with anything in it — those hang at the dialog
   exactly as they did before, because that is what the dialog is for.

   **On 2026-09-23 the panel came back for `centrion` itself**, a project with nine days of
   sessions behind it and its flag long since written. `claude centrion` from the phone at 16:14
   parked on the dialog and spent the whole 45s deadline there (`var/sessions/75057a/pty.log`,
   marker still on `No, exit`); by 17:11 the same message came up in three seconds, and
   `~/.claude.json` carries `hasTrustDialogAccepted: true` for that path again with nothing in
   this repository having put it there. Two things follow, and the second is why the entry is
   being amended rather than annotated. **The flag is not permanent** — it is a key in a file
   every live Claude Code process rewrites, and it can go — so "answering it is one-time" is a
   fact about the answer and not about the state. And the split this entry ends on has a cost it
   had never had to pay: `claude <project>` never answers the dialog, so a project whose flag has
   gone is a 45-second panel on the phone *every time* until something else trusts it. The split
   is still right — the bot cannot vouch for a directory it did not create, and a keyboard that
   makes starting a session one tap (§12 slice 13) is an argument for that, not against it — but
   the reply owes the phone the reason, which is §12 slice 12's brief and not a new rule about
   trust. The count above is stale for the same reason: `~/Projects` holds one directory today,
   not 46.

4. **Killing the PTY leaves the remote session registered but offline** — it stays in the
   claude.ai/code list without the green dot. `claude --continue` in that directory reattaches
   within roughly four hours. `stop` should say so in its reply.

5. **Session IDs are ULID-shaped, not UUIDs**: `session_01HJK2Lh42N7JbfMGExJkpTF`. Do not write
   a UUID regex.

6. **No node, no bun, no deno, no tmux on this machine.** `claude` is the native build at
   `~/.local/bin/claude`. Nothing here needs any of them; do not introduce a dependency that
   does.

7. **Login expiry is the most likely failure after week one.** Remote Control needs the
   claude.ai subscription login, not an API key. When that lapses, the spawned session comes up
   into a `/login` prompt and sits there — no crash, no error, just a PTY waiting for a human
   who is not at the keyboard, until the 45s timeout. The error tail makes it obvious once you
   look; the point is that it will look like "the bot broke" and will not be the bot. Fixing it
   means `claude` and `/login` in a terminal here, then message the bot again.

8. **`claude` is a version-pinned symlink — never resolve it.** *Verified:*
   `~/.local/bin/claude` is a symlink to `~/.local/share/claude/versions/2.1.269`. Calling
   `realpath()` on it, which is the natural thing to do while validating a path, pins the daemon
   to whichever build was installed the day it last started. Claude Code then updates itself,
   that version directory goes away, and every spawn fails with `ENOENT` — on a listener that is
   otherwise healthy, has no reason to restart, and only re-reads config at startup. Following
   the symlink at exec time is the entire point of the symlink. `abspath`, never `realpath`, for
   `claude_bin`; `realpath` stays correct for `projects_root`, where §3 needs a resolved path to
   defeat a symlink escaping the root. Pinned by
   `test_config.TestClaudeBinary.test_a_symlinked_binary_is_not_resolved`.
   *Still true on 2026-09-23, about a different symlink:* `~/.local/bin/claude` is gone from this
   box and `.telegram.json` names `/opt/homebrew/bin/claude`, itself a symlink into
   `../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`. The rule is what survives the
   move — the path in config is a name to be followed at exec time, whoever installed it — and
   `DEFAULT_CLAUDE_BIN` is now a default that would fail closed on this machine, which is the
   right way round for a default to be wrong (§1).

9. **The volume is case-insensitive and `realpath()` is not.** *Verified:* `os.path.realpath`
   is a lexical resolver — it expands symlinks and normalises `..`, and it does not consult the
   directory listing for spelling. On APFS-default macOS, `claude BEACON` therefore passes all
   four §3 checks, `chdir`s into `~/Projects/beacon`, and starts a perfectly working session
   whose recorded `cwd` is `.../BEACON` — a path string that no directory on disk has. Every
   §3 check still holds (it is inside the root, it is a direct child, it is a directory), so
   this is not a boundary problem; it is a *comparison* problem, and §5 contains exactly one
   comparison: the `⚠ 2nd session in beacon` warning for a second session in a live directory.
   Started as `beacon` and `BEACON`, those two are in one directory and would compare unequal,
   so the warning silently never fires — which is the one case it exists for.

   Normalising inside `resolve()` was rejected: it would mean a directory listing on every
   resolve and a fifth check at the security boundary, to fix something that is not a security
   problem. **Slice 8 must compare session directories with `os.path.samefile`, not `==`.**
   Pinned by
   `test_projects.TestTheHappyCase.test_a_miscased_name_finds_the_directory_but_keeps_the_spelling_it_was_given`.

10. **The pty hangup kills the child, but only after it owns the terminal.** *Verified:*
    closing the master SIGHUPs the child in ~100ms, which is what §2 relies on and why the
    runner must sit in a read loop rather than spawn and return. The qualifier is the whole of
    this entry: SIGHUP travels to a process through its *controlling* terminal, and the child
    only has one once it has run `setsid()` and `TIOCSCTTY`. Close the master inside the window
    between the fork and that ioctl and there is nothing to hang up from — the child survives,
    orphaned, writing to a pty nobody holds, and `ls` never shows it because no record was ever
    written for it. A `stop` arriving moments after a spawn lands exactly in that window.

    **So `stop` must signal, not hang up** — and must signal the process *group*, since the
    child leads its own after `setsid()` and whatever the session started (a dev server, a
    simulator) is in there with it. `killpg(pid)` alone is not enough either: before `setsid()`
    there is no such group and it fails with `ESRCH`, which looks exactly like success. Both,
    every time. Pinned by
    `test_session.TestTheTerminalSize.test_a_child_orphaned_before_it_owns_the_terminal_is_still_killed`.

11. **`waitpid` cannot say whether a *detached* runner is alive, and `terminate()` believed it
    anyway.** *Found by hand in slice 7*, against a runner that had outlived the process which
    spawned it — which is to say against the ordinary case and not an edge of it. A runner is
    detached, so the moment its listener exits it is reparented to launchd and stops being
    anybody's child; `os.waitpid(pid, WNOHANG)` then raises `ECHILD`, which means *not mine*
    and reads exactly like *already gone*. `terminate()` returned True having signalled
    nothing at all — on precisely the pid `stop` is handed after the first `launchctl
    kickstart`. §9.10 again in a second disguise: the reply says the session was stopped, and
    the session is still there with permissions bypassed. Fixed by asking both, in order —
    `waitpid` first, because only it can clear a zombie, and `kill(pid, 0)` second, because
    only it speaks for a process that is not ours. `EPERM` is not death. Pinned by
    `test_session.TestTheTerminalSize.test_a_runner_that_is_no_longer_our_child_is_still_terminated`.

    **And the two grace periods nest.** Ending a runner is not one kill but two in sequence:
    the runner catches SIGTERM, then spends up to `GRACE` seconds ending claude — which takes
    longer than it looks. *Verified on this box:* a real Remote Control session was still
    winding down more than five seconds after its SIGTERM. A caller that allows the runner the
    same `GRACE` the runner allows claude therefore SIGKILLs the runner in the middle of that,
    orphaning the session and leaving meta.json saying `live` for a session that is gone.
    **Slice 8's `stop` must give the runner more time than the runner gives claude** — and the
    reconciliation pass in §4 is the only reason the stale record is survivable rather than
    permanent.

12. **Nothing bounds how long Remote Control takes to connect, and 45s is not it.** *Observed
    on the first real use from a phone, 2026-09-12:* two sessions started three minutes apart
    both sat at `~/Projects/beacon · /rc connecting…` past the deadline and were answered with
    the timeout tail. Both then completed **within one second of each other, 68 minutes later**.
    Memory was at 51% free, both processes were healthy, and the listener polled Telegram
    throughout. The §4 budget of 10–20s came from one probe on a quiet box: it is a typical
    case, not a bound. The single-second gap between two sessions three minutes apart says the
    trigger was external and shared — most likely the network, which is exactly what a laptop
    does between rooms — and not anything either session was doing.

    **The delay is not the defect. The silence is.** The listener had already given up, so
    `meta.json` went to `live` with a working link and *nobody was ever told*: the phone held
    two "no link after 45s" replies for two sessions that were running, reachable, and
    answering. Raising the timeout does not fix this — no deadline covers 68 minutes — so
    **slice 8's reconciliation pass must announce a record that reaches `live` after its waiter
    gave up**, exactly as it announces one that reaches `ended`. Same walk, same push, and
    `chat_id` is already in the record for it.

    One more thing this made hard to diagnose, worth fixing in slice 9: `telegram.py` returns
    from a socket timeout **silently**, which is right — it is ordinary under a 50s long poll
    and logging each one would be noise. But it means an hour of network outage and an hour of
    nobody messaging leave *identical* traces in `var/bot.log`, and §14 sends you to that log
    first.

    ***Corrected 2026-09-13.*** The guess above — "the trigger was external and shared, most
    likely the network" — is wrong, and §9.13 is what it actually was. Two sessions releasing
    within a second of each other is still unexplained, but the *stall* reproduces in
    twenty-five seconds and has nothing to do with the network.

13. **Claude Code hung at startup under launchd when — and only when — the working directory
    contained a `.git`.** *Reproduced 2026-09-13 10:03. Gone by 13:07, unexplained.* For the
    three hours it lasted it cost the bot its entire deliverable: every directory it can reach
    is a repository, the runner never saw a byte of output, so it never scraped a URL, and the
    phone was told the session had failed while the session was fine.

    **Read the rest of this entry as a description of a failure that is not currently
    happening.** It is kept in the present tense because it is a recognition guide: if links
    stop coming back under launchd, this is the shape to match against before diagnosing
    anything from scratch. What slice 9 established is at the bottom.

    The minimal repro needs neither the bot, nor Telegram, nor a pty, nor even a real
    repository — an empty directory named `.git` is enough:

    ```
    mkdir -p /tmp/x/.git
    launchctl submit -l p -- /bin/sh -c 'cd /tmp/x; ~/.local/bin/claude -p "say PONG"'   # hangs
    rmdir /tmp/x/.git
    launchctl submit -l q -- /bin/sh -c 'cd /tmp/x; ~/.local/bin/claude -p "say PONG"'   # PONG, 3.4s
    ```

    The same command from a shell answers in about a second in either directory. `sample` shows
    the main thread parked in `openat$NOCANCEL` for every sample it takes — one file open that
    never returns.

    **Ruled out, each by experiment rather than by reasoning.** Listed because every one of
    them looked obvious at the time and cost a probe to kill:

    - *The Claude Code version.* 2.1.270 landed at 09:04 the same morning. 2.1.269 hangs too,
      and both are fine from a shell. §14's standing check stands, but this is not it.
    - *The environment.* `child_env()` filters `os.environ`, so a terminal probe inherits far
      more than the daemon does. Rebuilt from the daemon's own 19 variables, it still comes up
      in 0.3s from a shell.
    - *The spawn path.* A detached runner forked through `Sessions.start()` reaches `live` in
      2.5s from a shell.
    - *Credentials.* They live in the login keychain (`Claude Code-credentials`; there is no
      `~/.claude/.credentials.json`) and a LaunchAgent has no `SECURITYSESSIONID` — which
      looked decisive. `security find-generic-password -w` returns 0 from inside a
      `launchctl submit` job anyway.
    - *File descriptors.* 256 under launchd against a shell's 1,048,576; a shell probe at
      `ulimit -n 256` is fine.
    - *Scheduling.* The stalled process shows `PRI 20` against a shell's `PRI 31`, which looked
      like an inherited QoS clamp. `taskpolicy -B` on the live pid moved neither the priority
      nor the process: it was never in `PRIO_DARWIN_BG`. PRI 20 is just what a blocked process
      looks like.
    - *TCC and the location.* §9.2 makes this the natural suspect, but a **non**-git directory
      inside `~/Projects` answers in 3.7s and a git directory in `/private/tmp` hangs. The
      location is irrelevant; the `.git` is everything.
    - *`git` itself.* `git --version` and `git rev-parse --show-toplevel` both return rc=0
      under launchd, in the very repository that hangs.
    - *Subprocess spawning in general.* Under launchd, in a non-git directory,
      `claude -p --dangerously-skip-permissions "run the bash command echo HELLO"` shells out
      and answers correctly in 4.8s. So it is not that Claude Code cannot spawn under launchd —
      it is something on the path it takes only when it has decided it is in a repository.

    **What is not yet known is which file.** The main thread blocks in `openat`, and naming the
    path needs `sudo fs_usage -w -f filesys claude` running *before* the open — the process is
    already parked by the time you can attach to it, so nothing new appears. That is the next
    step and it needs root.

    **The control, and for three hours it was the workaround.** *Verified 2026-09-13
    13:02:35–13:02:38*: the LaunchAgent booted out, the listener started from a shell by
    `launchd/bot.sh`, and otherwise nothing changed — same box, same checkout, same Claude Code
    2.1.270, same `~/Projects/beacon`, a repository like every other. The runner saw 3739 bytes
    where launchd gave it zero in eight minutes, the record reached `live` **two seconds** after
    the spawn, and the phone had the link one second after that. `ls` then rendered it.

    **Then it stopped happening, and that is where slice 9 found it.** Five minutes after the
    control above, with nothing changed in between and nothing fixed:

    - The minimal repro at the top of this entry — `launchctl submit`, `/tmp/x/.git`,
      `claude -p "say PONG"` — **answered 14 times out of 14**, every one in about three
      seconds. Ten of those were consecutive, to settle whether the first four were luck.
    - The same under launchd in `~/Projects/beacon` and `~/Projects/centrion`, the two real
      repositories the bot had failed in at 10:03 and 10:04: PONG, three seconds each.
    - **The real runner**, which is the test that decides it: `session.py` under
      `launchctl submit`, a pty, `--remote-control`, `~/Projects/beacon`. 3236 bytes of
      transcript and a scraped link, `state: live`, **two seconds** after the spawn — the
      thing this entry said launchd could not do.

    **The file was never named, because by then there was nothing left to catch.** `fs_usage`
    has to be running before an open that never returns, and there was no longer an open that
    never returned. The probe slice 9 was planned around cannot be run again until the hang
    comes back.

    **What did not change, each checked rather than assumed.** These matter because any one of
    them would have been an explanation:

    - *A reboot.* None. The box came up 2026-09-13 06:00:05 and both the failures and the
      recoveries are after it, on one boot.
    - *The Claude Code version.* 2.1.270 throughout — installed 09:04, an hour **before** the
      10:03 and 10:04 failures, and still installed for the 13:07 recoveries.
    - *The Claude desktop app.* Running since 09:04, also before the failures. Not it either.

    So this was outlived, not fixed, and §8 comes back on the evidence of the bot working rather
    than of the cause being understood. It could return, and §14 carries the standing check for
    recognising it in one step instead of a morning: no bytes on the pty, in a repository, under
    launchd, with a shell-run `launchd/bot.sh` unaffected.

---

## 10. Security

Be clear-eyed: **this bot is a remote code execution endpoint for this Mac, with permissions
bypassed.** Anyone who can message it can run anything as this user — and this user's home
directory holds Stockbit tokens, a Telegram bot token, and claude.ai OAuth credentials. The bot
token is the only thing standing in the way, and it travels in a URL query string to a third
party.

Non-negotiable:

1. **Allowlist by numeric chat id**, integer comparison, checked against both `message.chat.id`
   and `message.from.id`. Empty allowlist = refuse everything.
2. **Require `message.chat.type == "private"`.** Anyone can add a bot to a group; a group id is
   not an identity.
3. **Drop unknown senders silently** — log the id, send nothing. A reply confirms the bot exists
   and is worth attacking.
4. **Never accept a path.** `claude <project>` names a direct child of `projects_root`, run
   through the four checks in §3. No message can express a directory outside `~/Projects`, so
   `claude ../../etc` and `claude ~/Documents/Junction` are both just a `help` reply. **`new
   <name>` runs the same checks, from the same code** — checks 1-3 unchanged and check 4
   inverted (§12 slice 11) — and creates nothing unless every one of them passes, so a refused
   `new` cannot leave a directory behind on its way to being refused.
5. `.telegram.json` at `0600`, gitignored, and **never in the plist** — files in
   `~/Library/LaunchAgents` are world-readable `0644`.
6. **Cap concurrency.** `max_sessions` — §3 sets the value and §15 records why it is 2 on this
   box. Deliberately not restated here: this line used to carry its own copy of the number,
   which drifted to 4. Without a cap at all, a held-down `claude` fills RAM with Claude Code
   processes.

7. **Keep nothing for longer than it is useful.** `pty.log` is the complete transcript of a
   terminal that had permissions bypassed — every file the session printed, every prompt typed
   into it, and whatever credentials went past on the way. It is capped at 4 MiB with one
   predecessor kept, and the whole session directory is removed a day after the session ends.
   The cap is also the only thing that makes `var/` predictable: at most `max_sessions` × 2 ×
   4 MiB live, plus whatever a day of finished sessions left. Before slice 10 nothing removed
   any of it and nothing trimmed a transcript, so the bound on both was how long somebody
   happened to leave a session running — which is the one variable this bot exists to make
   large. A day, and not an hour, because the transcript is read when a session has just gone
   wrong (§14) and that is the same afternoon.

Worth knowing, in both directions:

- **The session URL is not a bearer token.** Remote Control sessions appear only in the account
  that started them; a leaked link gets a stranger a sign-in page. Losing the *bot token* is the
  real breach, not losing a link. Rotate via BotFather `/revoke` and restart the agent.
- **The Bot API is not end-to-end encrypted.** Telegram sees every message and every reply.
  Never let the bot echo file contents, env vars, or `pty.log` beyond the short error tail — and
  scrub that tail before sending.
- While Remote Control is connected, the session transcript is stored on Anthropic servers to
  keep devices in sync. Execution and file access stay local. This is ordinary RC behaviour, not
  something the bot adds, but it is worth knowing it applies to sessions started this way too.

---

## 11. How a slice runs

The work is sliced so that every slice is a day's worth at most, starts red, and ends on a
commit that could ship. No slice both adds a capability and leaves it unproven.

**Every slice, without exception:**

1. **Red.** Write the tests named in the slice first and watch them fail. A test that passes the
   moment you write it was testing something that already worked — delete it or fix it.
2. **Green.** Write the least code that turns them green. Resist the next slice's work; it has
   its own tests coming.
3. **Build.** `python3 -m compileall -q .` — the only build a stdlib project has, and it catches
   the 3.9-isms that `python3.13` on this box would otherwise hide (§3 pins `/usr/bin/python3`,
   which is 3.9.6; `match`, `X | Y` at runtime, and `dict | dict` are all syntax errors there).
4. **Run.** The slice's own manual step, listed under each slice. Running it is not optional —
   half these failures (launchd env, TCC, PTY width) cannot appear in a unit test.
5. **Test.** `python3 -m unittest discover -s tests -t . -v` — the whole suite, not just the new
   file.
6. **Commit.** One commit per slice, message `slice N: <what it now does>`, ending with

   ```
   Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
   Claude-Session: https://claude.ai/code/session_018Sws7H1zF9fSKM9soUk5g8
   ```
7. **Plan.** Tick the row in §13 and write one line in its Notes column — what surprised you,
   or `—`. That column is the point of the table; the ticks are bookkeeping.

Tests are stdlib `unittest`, no network, mocked at object boundaries — the same shape as
`test_notify.py`, including a `_open()` seam on the Telegram client so backoff is exercised
without patching `urllib` globally, and sentence-shaped test names
(`test_a_stale_message_does_not_start_a_session`).

---

## 12. The slices

### Slice 0 — repo, and a test that the token cannot leak

*Red:* `test_layout.py` — `.gitignore` matches `.telegram.json`, `var/`, and
`tests/fixtures/*.log` is *not* ignored; `git check-ignore` agrees with all three.
*Green:* `git init`, `.gitignore`, `var/` skeleton, empty modules.
*Run:* `touch .telegram.json && git status --short` shows nothing.

Ceremony this is not — §10.5 makes a committed bot token the single worst outcome in this
project, and this is the one test that can prevent it before the token exists.

### Slice 1 — config, failing closed

*Red:* `test_config.py` — missing file, absent `allowed_chat_ids`, `[]`, a string id where an
int belongs, absent `bot_token`, a `projects_root` that does not exist, and a `.telegram.json`
whose mode is wider than `0600`. Every one raises `ConfigError`; none returns a usable config.
*Green:* `config.py:load()`.
*Run:* `python3 -c "import config; config.load()"` against a deliberately broken file, read the
message, confirm it names the problem and never the token.

### Slice 2 — project resolution

*Red:* `test_projects.py` — the four §3 checks, one test each, plus `..`, `../../etc`,
`beacon/../../..`, an absolute path, a leading dot, the empty string, a symlink inside the root
pointing outside it, a regular file, and a name that does not exist. Then the one happy case.
*Green:* `config.py:resolve(name)`.
*Run:* `python3 -c "import config; print(config.resolve('beacon'))"` and the same with `../..`.

Slices 1 and 2 are separate because this one is the security boundary and deserves to fail on
its own terms.

### Slice 3 — Telegram client

*Red:* `test_telegram.py` — `getUpdates` advances the offset past the batch; a 409 backs off
rather than raising; a socket timeout is not treated as an error; `sendMessage` retries three
times then gives up quietly; and **no exception message or log line ever contains the bot
token** (assert on the string, with a token-shaped value in the fixture).
*Green:* `telegram.py`, lifted from `notify.py` and cut down.
*Run:* `python3 bot.py --whoami` — message the bot from your phone, get your chat id back, paste
it into `.telegram.json`. First real traffic, and the step that makes slice 5 possible.

### Slice 4 — command parsing

*Red:* `test_commands.py` — `claude`, `/claude`, `/claude@centrionbot`, `claude beacon`,
`claude beacon fix the probe test` (prompt preserved verbatim, including case), `ls`, `stop 2`,
`stop all`, `help`, `` (empty), and three kinds of garbage → `help`.
*Green:* `commands.py:parse(text)` returning an intent tuple. Pure, no I/O, no config.
*Run:* `python3 -c "import commands; print(commands.parse('claude beacon fix it'))"`.

### Slice 5 — the listener, echoing

*Red:* `test_bot.py` against a fake Telegram — an allowed private chat is answered; a
non-allowlisted id gets **no reply at all**; a group chat gets no reply; a message older than
start−120s is dropped; the offset survives a simulated restart; a send failure does not kill the
loop.
*Green:* `bot.py` loop, dispatching to an echo. No `session.py` yet — nothing dangerous is
reachable at this commit.
*Run:* install the plist, `launchctl bootstrap`, message it, get the echo. Then
`launchctl kickstart -k` mid-poll and confirm it comes back. Then start a second copy by hand
and confirm `lock.sh` refuses it rather than both hitting 409.

This is the first slice that touches launchd, and deliberately the last one that is harmless if
the allowlist is wrong.

### Slice 6 — PTY runner and the URL scrape

*Red:* `test_session.py` — `extract_url()` against **`tests/fixtures/rc_startup.log`, a real
captured transcript of an RC startup on this box**, finds
`https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF`; a chunk boundary splitting the URL in
half still resolves once both arrive; an ANSI escape split across two chunks does not corrupt the
strip (carry the tail, do not strip per-chunk); `meta.json` writes atomically and a reader never
sees a partial record. Plus the one that is not about parsing: **spawn `/bin/sh -c "stty size"`
through the real PTY setup and assert it prints `50 200`** — that is §6's ioctl, and it is the
difference between a scrape that works and one that works by luck.
*Green:* `session.py` — `openpty`, `TIOCSWINSZ` on the slave, `fork`, `setsid`, close the
inherited lock fd (§8), clean env (§6), read loop, meta writes.
*Run:* `python3 session.py --cwd ~/Projects/beacon --name test --foreground`, watch the URL
appear, open it on the phone, confirm the session answers. Then `kill` the runner and confirm
claude dies with it.

### Slice 7 — `claude` from Telegram, end to end

*Red:* extend `test_bot.py` with a fake runner — the listener replies with the link on `live`;
replies with the ANSI-stripped `pty.log` tail on `failed`; gives up at 45s; and **never blocks
the poll loop** (a second message is answered while the first session is still starting).
*Green:* wire the spawn and the meta poll into `bot.py`.
*Run:* message `claude` from the phone, open the link, type something, watch it execute here.
Then `launchctl kickstart -k` **while that session is live** and confirm the session survives —
the one architectural claim in §2 worth re-proving by hand.

**This slice is the deliverable.** Everything after is comfort.

### Slice 8 — fleet control and reconciliation

*Red:* `ls` renders index, project, uptime and link for two fakes and says so when there are
none; `stop 2` signals only that runner; `stop all` signals every one; `stop 9` is a clean
error; a spawn past `max_sessions` is refused with a count, not a crash. Then the §4
reconciliation pass: **a `live` record whose pid is gone is marked `ended` and stops counting
against `max_sessions`**; a record whose pid is alive is left alone; a newly `ended` record
produces exactly one push to its originating chat, and not a second one on the next tick; **a
record that reaches `live` after its waiter gave up produces exactly one push carrying the
link** (§9.12 — this one happened, on the first real use, to two sessions at once); an `ls`
longer than 4096 characters is truncated rather than 400ing.
*Green:* the tier-2 verbs, the cap, and the reconciliation tick.
*Run:* start two sessions from the phone and then a third — the third is refused, with a
count, which is the cap being real rather than configured. Then `ls`, `stop all`, and confirm
both are gone from claude.ai/code. Then the one that needs a reboot: leave a session live,
restart the Mac, and confirm the bot comes back reporting zero sessions rather than phantoms.
(This step used to say *three* sessions and predates §15 settling `max_sessions` at 2, which
is the same drift §10.6 records — a number restated in a second place and left behind.)

### Slice 9 — the link comes back, and the bot starts itself again

*The brief this slice was planned to, kept as written; what actually happened is under
**Outcome** at the end of it.*

**The bot does everything except the thing it is for.** Slice 8's verbs all work from a phone;
§9.13 means none of the sessions they control ever return a link, and §8 is suspended behind
it. Nothing below this line is worth building until `claude beacon` answers with a link and the
listener is still running tomorrow morning.

The slice starts by finding out which of two endings it has, because they are different work:

1. **Name the file.** `sudo fs_usage -w -f filesys claude`, started *before* the open — the
   process is parked by the time you can attach, so nothing new appears — against §9.13's
   two-line repro. If it turns out to be a path a LaunchAgent can be granted, §8 comes back
   exactly as it was and this slice is a plist change and a test.
2. **Or accept the hand-run listener and make it durable.** If the path cannot be granted, §8's
   job has to be done by something that is not a LaunchAgent — and on this box every candidate
   is a launchd job wearing a hat, login items included. The one that is not is a terminal
   application set to open at login with a tab running `launchd/bot.sh`. Ugly, visible, and
   the only thing here with evidence behind it — and since 13:02 on 2026-09-13 that evidence
   is the bot itself and not a probe: hand-run, `claude beacon` returned a link in three
   seconds.

*Red:* `child_env()` drops `CLAUDE_*` and `AI_AGENT` wholesale rather than only the
`CLAUDE_CODE*` prefix — a listener started from inside a Claude Code session must not hand its
own effort setting, and a `CLAUDE_PID` naming somebody else's process, to every session it
spawns (§14); whatever startup artefact replaces the plist names *this* checkout and carries no
token, asserted the way `TestTheLaunchdInstall` asserts the plist; and a second copy started by
any path finds the lock held and exits rather than 409ing the holder (§7), which is the one
property that must survive having two supported ways to start.
*Green:* the above, and whichever ending the probe chose.
*Run:* the one that cannot be faked — log out and back in, or reboot. Nobody starts anything by
hand; the bot answers `ls`, and `claude beacon` comes back with a link.

**Outcome: neither ending, because the blocker went away while the slice was being set up.**
Step 1 went looking for the file and found no hang left to catch — 14 launchd runs of the
minimal repro answered, and the real runner scraped a link two seconds after a launchd spawn
(§9.13 carries the numbers). So §8 came back exactly as it was, which is ending 1 without the
plist change that ending 1 assumed, and the terminal-at-login of ending 2 was never built.

Of the three red tests, the second dissolved with ending 2: nothing replaced the plist, so
`TestTheLaunchdInstall` is still the test that asserts the startup artefact and there was
nothing to write. The other two were written. Half the run step was done the same day — `launchctl kickstart
-k` restarts the listener in one second and the live session survives it — and the half that
needs a login stayed outstanding for seven hours.

*Run step: done, across a real logout, 2026-09-13 16:30-16:31.* Logged out at 16:30 with a
beacon session live since 13:02; logged back in at 16:31 and started nothing by hand. The
listener was back at 16:31:25 — pid 90043, parented to launchd, `var/.bot.lock` rewritten to
match, and the offset carried across at 80421003, so nothing queued was replayed and nothing
unread was acknowledged. The phone's `ls` answered 31 seconds after that start, which is inside
§8's up-to-50s first tick because `ls` reads the records rather than waiting for one.

**The live session came through as the same processes, not a restart** — pids 75050 and 75065,
started 13:02:35, three and a half hours elapsed, still writing `pty.log` afterwards. It
survived because the runner is its own session leader in its own process group with `ppid 1`,
so tearing down the gui domain had nothing of its to kill. That is slice 6's `TIOCSCTTY` paying
a second time: it is there to give the pty hangup a controlling terminal to reach, and what it
also buys is a session that outlives the login session it was started from.

Which makes this a test of §8's reconciliation more than of launchd. The new listener is a
different *process* holding a record whose runner it never spawned — the case the per-(session,
kind) `O_CREAT|O_EXCL` markers were chosen for — and it got it right: `alive()` found the pid,
the record stayed `live`, `announced-end` was never written, and the phone was not told a
working session had ended. Every earlier exercise of that path was a `launchctl kickstart`,
which leaves the machine's process tree standing; a logout does not.

Still untested: a login after a *reboot*. Today's 09:04:09 start followed a 09:03 login and may
well have been launchd's — the installed plist is byte-identical to the repo copy and dates
from 2026-09-12, so the 09:56-13:15 gap unloaded it rather than edited it — but nobody watched
it happen, and a start nobody watched is not evidence.

### Slice 10 — hardening

*Red:* `pty.log` rotates past its cap; `ended` session directories are reaped after a day but a
`live` one never is; the error tail sent to Telegram is scrubbed of anything matching a token or
a home path.
*Green:* the above.
*Run:* leave a session open for an afternoon, confirm `var/` has not grown without bound.

**Outcome: two of the three red tests, and the third had been written in slice 7.** The scrub
was already there and already tested from five directions — §11 says a test that passes the
moment you write it was testing something that already worked, so nothing was added for it.
What the slice did not plan for is the interaction between the other two: capping the
transcript breaks §4.6's tail, because a session that rotates a moment before it dies leaves a
live `pty.log` holding the last half-second of a redraw and the error in the file that was
renamed away. Keeping one predecessor and reading the tail across both is the fix, and it is
why the cap is two files rather than one.

The run step was done by flood rather than by afternoon: 26 MiB through a real pty in 2.4
seconds, ending at 5.0 MiB on disk against a 4 MiB cap, and the `/login` line at the end of it
still reached the tail — both with the error in the live transcript and with it in the rotated
one. The retention half ran over this box's own five session records on a copy: nothing goes
today, and with the records backdated two days the four ended ones go and the live one stays.
What that does not cover is a real day passing in production, which only time can do.

### Slice 11 — a project that does not exist yet

Starts by settling §9.3, because what that probe finds decides how much of this slice is a
`mkdir` and how much is a runner that answers a dialog.

*Red:* `new scratchpad` creates a direct child of the root and starts a session in it; `new`
refuses everything `claude` refuses and by the same code — `new ../etc`, `new /tmp/x`, `new
.ssh`, `new a/b`, `new` on its own — and creates nothing on any of them; a refused `new` leaves
no directory behind; `new beacon` on a name already there starts a session and says the
directory was already present, rather than failing; two `new scratchpad` in one batch create one
directory and not an error; the cap and the §5 same-directory warning apply to a `new` session
exactly as to a `claude` one; and the created directory comes up to a link rather than to §9.3's
trust prompt.
*Green:* `config.create()` beside `resolve()` — sharing checks 1-3, inverting 4 — the verb in
`commands.py`, the listener wiring, and whatever the probe says §9.3 needs.
*Run:* `new` a directory from the phone and confirm a link comes back inside the deadline rather
than a trust-prompt tail at 45s. Then `ls` it, `stop` it, and check the directory is still there.

**Outcome: §9.3 was real, and the probe found the one detail that mattered.** A fresh directory
hangs under `--dangerously-skip-permissions` exactly as the entry inferred — but the dialog's
default selection is `No, exit`, so the obvious answer to it (press Enter) would have ended the
session rather than started it. The answer is Down, a check that the marker moved, then Enter;
§9.3 carries the panel and the two other findings that came with it. Everything downstream of
the probe was the `mkdir` the slice expected.

Two things the slice did not plan for, both from `new` being the first verb that *writes*.
A project name reaching the filesystem rather than only a lookup makes a control character
worth refusing rather than merely surviving — there is no verb here that deletes anything, so a
directory called `red<ESC>[31m` is one nobody can clean up from a phone. And the cap moved: it
is now checked *before* the project is resolved, because creating a directory for a session
that is then refused leaves exactly the empty repository §5 gives `new` its own verb to prevent.

*Run step: done, from the phone, 2026-09-13 16:24.* `new scratchpad` → the directory created,
the dialog answered one second after the spawn, `live` at two, the link on the phone at four —
against a 45s deadline that the same message would have spent in full the day before. Then
`ls`, then `stop`, and `~/Projects/scratchpad` is still there and still empty. Claude Code
recorded `hasTrustDialogAccepted` for it, so the next `claude scratchpad` skips the dialog
entirely. Nothing here was run by hand: the message went to a listener launchd had started.

### Slice 12 — the failure that explains itself

*Planned, not built.*

**The one thing this file admits it does not know.** §8 stands restored on the evidence of
working rather than of the cause being fixed, which §9.13 records as a weaker warrant than the
rest of this spec accepts. §14 says what to do if it returns: `sample` the parked pid for the
stack, and have `fs_usage` running *before* the spawn, because the process is already parked by
the time you can attach and nothing new appears after that.

That is an instruction a person cannot follow. The failure announces itself as a 45-second
timeout on a phone, and by the time anyone is at the machine the session has been stopped or the
box has moved on — which is exactly how three hours of §9.13 produced no stack. **The bot is the
only thing present at the moment it matters**, and what it sends today is §4.6's `pty.log` tail,
which for this particular failure is empty: the hang is before any output. So the slice is
narrow — make the deadline take the capture that nobody can be there to take.

It does not explain §9.13 and must not claim to. It buys the evidence an explanation would need,
on the one path that has already failed, and only if the failure ever comes back.

*Red:* a session that reaches §4.6's 45s deadline with no link writes a stack capture into its
session directory before the reply is composed, and the reply names it; the capture goes through
§10's scrubber before any of it is sent, because a stack is wall-to-wall home paths and a token
in somebody's argv would go out with it; a capture that cannot be taken — no `sample` on PATH,
permission refused, the pid already reaped — leaves today's reply exactly as it is rather than
taking the waiter with it, since §4.6's failure reply is the one that has to survive everything;
nothing is captured for a session that came up fine, and nothing for one that ended on its own;
the capture is claimed once per session as a third `O_CREAT|O_EXCL` kind beside the link and the
ending, because §8 proved the waiter and the tick can hold one record from two processes; and
the capture is bounded, counts against §10.7's budget, and does not keep a session directory
from being reaped at retention.
*Green:* a bounded helper called from the deadline in the waiter, choosing the parked pid —
claude's, falling back to the runner's if claude never got far enough to have one — and giving
the `sample` its own timeout, because the thing being sampled is by hypothesis stuck.
*Run:* park slice 11's fake `claude` past the deadline and confirm the phone gets a stack
instead of an empty panel; then `du -sh var` against §10.7 with a capture on disk; then let one
real session come up normally and confirm nothing was captured at all.

Deliberately not in here: §14's other standing check, where a Claude Code upgrade moves a panel
and the field failure is a silent 45s timeout. Stamping `claude --version` into `meta.json` at
spawn and saying when it changed since the last successful scrape is the neighbouring half-slice,
and it has its own tests coming (§11 step 2).

### Slice 13 — a keyboard instead of a grammar

Everything here is already possible: `claude` lists the directories and `claude <project>` starts
a session in one. What a phone does not have is a keyboard worth typing a directory name on, so
the slice turns the list the bot already sends into buttons that send the message the user would
otherwise spell — list the folders, tap one, and the session starts there with permissions
bypassed exactly as §5 describes.

**A reply keyboard, not an inline one, and the reason is the dispatch path.** Inline buttons
arrive as `callback_query` updates: a second update type in `allowed_updates`, a second
allowlist check against a `from.id` that is *not* `message.chat.id` (§10.1 checks both today, and
a callback has neither in the same place), an `answerCallbackQuery` inside Telegram's own
deadline or the phone spins, and `callback_data` capped at 64 bytes — which a 200-byte directory
name does not fit, so the button would have to carry an index into a list that can change between
being drawn and being tapped. A reply keyboard sends ordinary message text. `claude centrion` off
a button is indistinguishable from `claude centrion` typed, so it meets §7's date guard, §10's
allowlist, §3's four checks and §4's cap on exactly the path 465 tests already cover, and
`commands.py` does not change at all. The cost is honest and small: the keyboard is chat state
held by Telegram rather than by this bot, so it outlives a restart and can show a directory that
has since gone — which is a `claude <gone>` and already a tested refusal.

**The one thing a button can get wrong is spelling, and §3 is why.** A project name may contain
spaces — `My Project` is an ordinary directory, and §3 refuses only `/`, `\`, NUL, a leading
`.` and control characters. `commands.parse` splits on whitespace, so that button arrives as
`claude My` carrying the prompt `Project`: a refusal if nothing is called `My`, and a session in
the wrong project with a stray prompt typed into it if something is. The fix is not a character
rule of its own — a second copy of §3 is how two doors drift apart (§9 slice 11) — but a round
trip: a name earns a button only if `commands.parse` reads the button's own text back as that
name and nothing else. The parser decides what the parser can read, and a name that cannot
survive it stays in the text of the reply, where it has always been, with no button beside it.

**`stop all` is not a button.** §4 already refuses to read a bare `stop` as `stop all` because it
is the one misreading in this grammar that cannot be taken back; a button for it is that same
message one thumb away from every live session on the box, and a keyboard is tapped by people who
are half-attending — which is the whole argument §5 makes against a most-recent-project default.
The menu carries projects, `ls` and `help`. Ending a session stays a thing you spell.

*Red:* the reply to bare `claude` and to `help` carries a keyboard with one button per project,
each button the exact text `claude <name>`; a name that does not round-trip through
`commands.parse` appears in the reply text and *not* as a button; the keyboard is capped and the
reply says so when it is, while the text keeps listing everything; no button sends `stop` in any
form; `ls` carries the same project keyboard (what is running, and one tap to start another)
while `stop` replies carry none of their own; an empty projects root sends no keyboard rather than an empty one; the
markup survives §7's 4096 cap being applied to the text beside it; a tapped button produces the
identical `Intent` to the typed message, over a table of hostile-but-legal names; and §10.2's
redaction test extends to the markup, because a reply now carries a second field that leaves this
process.
*Green:* `reply_markup` on `telegram.py`'s `send_message` (the body is already JSON, so it is a
nested dict and nothing needs encoding), an optional markup through `bot.say()` so §7's `fit()`
stays the single choke point, and one function that builds the keyboard by asking `commands.parse`
what it can read.
*Run:* from the phone — `help`, then tap a project and watch the link come back; tap a project
that was deleted after the keyboard was drawn and confirm it is the ordinary refusal; restart the
listener and confirm the keyboard is still there without the bot having sent anything.

**Outcome: the slice's own property is that there is nothing to see.** `claude ttsecuritas-2`
arrived at 20:33:47 off a button and reached a link at 20:33:50, and `var/bot.log` records it in
the line it would have written for the same words typed — identical by construction, which is the
design and not a gap in the evidence. What a keyboard *can* be caught doing is on the wire and in
the tests: Telegram took the markup as a nested object on the first send, which is the one thing
no unit test here could have established, because every example of `reply_markup` in circulation
is JSON inside a string field and that is what a form-encoded call needs, not this one (§7 posts
a JSON body).

Two smaller things, both from the keyboard being state this bot does not hold. It is the *chat*
that has a keyboard, not a message, so it survived the listener restart at 17:37 with nothing
sent — which is the property that makes a menu cheap here and also the reason the buttons can
outlive the directories they name. And a reply with nothing to say about the menu has to send no
markup at all rather than an empty one: an empty `keyboard` is Telegram's way of spelling
*remove the keyboard this chat has*, so the natural-looking default would have taken the menu
away on every `ls`.

The cap and the round-trip filter are the same line of code read twice, and only one of them was
in the brief. `menu_names()` is what the buttons come from *and* what the reply's text checks
itself against, so the sentence explaining a missing button cannot drift from the rule that
removed it. §11's warning earned a mention too: four of this class's assertions — no `stop`
button, no keyboard on `ls`, no keyboard for an empty root, no token in the markup — hold
trivially over no keyboard at all, so one more test asserts the keyboard under them is not empty.

---

## 13. Progress

Updated at step 7 of every slice. Notes is the column that matters.

| # | Slice | Done | Notes |
|---|---|---|---|
| 0 | repo + gitignore test | ☑ | `tests/` needs `__init__.py` — 3.9 `unittest discover` cannot import a non-package start dir. Repo had **no `git user.email`**; commits were authored `tommy <>`. Set repo-locally. |
| 1 | config, failing closed | ☑ | `~/.local/bin/claude` is a **version-pinned symlink**; `realpath()` would have frozen the daemon on 2.1.269 and broken every spawn at the next auto-update. `abspath` for the binary, `realpath` for the root. See §9.8. |
| 2 | project resolution | ☑ | `os.path.realpath` is **lexical** — it follows symlinks and does not case-fold, and this volume is case-insensitive. So `claude BEACON` starts a real session in beacon's directory under a path string no directory has. Harmless for `chdir`, wrong for `==`, and §5's same-directory warning is an `==`. See §9.9. |
| 3 | Telegram client | ☑ | `socket.timeout` is **not** a `TimeoutError` subclass on 3.9 (only from 3.10), so notify.py's `except (URLError, TimeoutError)` misses it entirely — and a 50s long poll produces one whenever a connection is dropped quietly. It would have killed the listener on an idle afternoon. Also: `getUpdates` without an offset does **not** consume the queue; §3 said it did and has been corrected. |
| 4 | command parsing | ☑ | `str.isdigit()` is True for `²` and `٢` while `int()` raises `ValueError` on the first — so a `stop ²` off a phone keyboard would have crashed the poll loop §7 requires never to die. `isascii()` *and* `isdigit()`. The design note: bare `stop` is `help`, never `stop all` — the one misreading in this grammar that cannot be taken back. |
| 5 | listener, echoing | ☑ | A corrupt `var/offset` is dangerous in only one direction, and it is the opposite of the obvious one. Too *small* replays a batch, and §7's date guard then drops it; too *large* acknowledges updates that have not arrived, and the bot goes **permanently deaf** — silently, and across restarts, because the bad number is on disk. `read_offset` therefore bounds the value as well as its type. Also, proved live on the first boot: it dropped two messages left queued by slice 3's `--whoami` three hours earlier, which is §3's correction seen from the other side — they were still there to drop. |
| 6 | PTY runner + URL scrape | ☑ | The pty hangup that §2 leans on is **racy**: it reaches the child through its controlling terminal, which it does not have until `TIOCSCTTY` has run, so a master closed in that window orphans it instead of killing it. And `killpg(pid)` fails with `ESRCH` there — indistinguishable from success — because there is no group yet. `terminate()` signals group *and* process. See §9.10; slice 8's `stop all` is what would have been quietly leaving sessions behind. Confirmed live: box-drawing rules in a real transcript now measure exactly 200, where §6 recorded 80 before the ioctl. |
| 7 | `claude` end to end | ☑ | §4.2's "never blocks" cannot be bought with a smaller poll interval — getUpdates holds for 50s, so a meta poll inside the loop answers the phone a minute late — so the wait is a thread per session and the reply arrives *after* whatever was sent behind it. The finding that mattered came from the run step and not the tests: `terminate()` reported success against a detached runner it had not signalled, because `waitpid` answers ECHILD for a process that is alive but no longer ours. That is the pid `stop` gets after any restart. See §9.11, which also records that the two grace periods nest. Then §9.11's own lesson turned up *in the tests*: one that ended by waiting out a 30s session deadline was racing `settle()`'s 30s join, which is one failure in ten under load — and was 30 of the suite's 37 seconds. Releasing the waiter instead of outliving it took the suite to 7.5s. Smaller, and nearly shipped: §7's scrub must not be a general "long opaque run" rule, because a session id is 26 characters of exactly that and the scrub would have eaten the one reply that matters. §2's claim is now verified rather than asserted — see the control experiment recorded there, and then verified again by accident: a session sent `launchctl kickstart -k` as its own prompt, ran it, and both live sessions came through it untouched. The first real use from a phone found what no test could, and it is now §9.12: Remote Control took 68 minutes to connect, the waiter had long since given up, and two working sessions were reported to the phone as failures. |
| 8 | fleet control + reconciliation | ☑ | The pass has to run **before the batch it arrived with**, or the first `claude` after a reboot is refused against a cap held entirely by records whose runners that reboot took — two of them were on disk this morning, which is where the fixture came from. Announcing exactly once is a filesystem problem and not a bookkeeping one: the waiter thread and the tick can both be holding one newly-live record (§4.6's 45s deadline falls *inside* the ≤50s tick), and after a `launchctl kickstart` they are two **processes** — so `O_CREAT|O_EXCL` per (session, kind), with the link and the ending claimed separately so neither spends the other's. §9.12's late link then falls out of the same walk for free, because a waiter that gave up claims nothing. `alive()` refuses a `runner_pid` of `0` or `-1` before `os.kill` ever sees it — those mean *this whole process group* and *every process this user owns*, and the same record is what `stop all` iterates. Two things only the run step could say: the end notice claimed sessions had **run** for 11h when the machine had been switched off for three of them (this pass cannot know when a runner died, only that it is gone, so it now reports when the session *started*), and a restarted daemon takes up to 50s to notice anything at all, because the first tick is the first *return* from a 50s poll. Run step done from the phone at 10:03–10:12: two sessions, a third `claude beacon` refused at the cap, `ls`, `stop 2` taking only the second, `stop all` taking the rest. The links never came back, but for nothing this slice does — see §9.13, which is the failure §9.12 misread as the network. |
| 9 | the link comes back, and the bot starts itself | ☑ | **The blocker cured itself, and that is the finding.** §9.13 reproduced at 10:03 and was gone by 13:07 with nothing changed between — no reboot, same 2.1.270, same desktop app, 14/14 on its own minimal repro and a real launchd pty run scraping a link in two seconds. So the probe this slice was planned around could not be run at all: `fs_usage` needs an open that never returns and there was no longer one. §8 is back on evidence of working rather than of being understood, which is a weaker warrant than this spec usually accepts and is why §9.13 was kept as a recognition guide instead of being deleted. The code finding was §14's, and it was real: `CLAUDE_PID`, `CLAUDE_EFFORT` and `AI_AGENT` all miss a `CLAUDE_CODE` prefix, so a listener started from a shell inside a Claude Code session was handing that session's effort setting, and a pid belonging to somebody else, to everything it spawned. The lock test the slice asked for was **green the moment it was written** — the property already held, and §11 says that is a test of something that already worked; it was kept anyway, because it executes the two scripts where the existing tests only string-match them, and two supported ways to start is exactly when that stops being theoretical. **The run step finished at 16:31 against a real logout, and it tested §8 more than launchd.** The listener was back 25 seconds after the login with nothing started by hand and the offset intact; the beacon session live since 13:02 came through as the *same* pids, because slice 6's `TIOCSCTTY` leaves the runner a session leader with `ppid 1` and the gui teardown had nothing of its to kill. So a brand-new listener process met a live record it had never spawned — the case the `O_CREAT|O_EXCL` markers exist for — and left it alone: still `live`, no `announced-end`, no false ending sent to the phone. Every earlier test of that path was a `kickstart`, which leaves the process tree standing. A login after a reboot is still untested. |
| 10 | hardening | ☑ | **The cap and the tail are the same file read from two ends, and only one of them was in the brief.** §4.6 reads the last 64 KB of `pty.log` to explain a session that never came up; rotating that file at 4 MiB means a session which fails just after a rotation hands the phone a cleared panel instead of the error — and §9.7's expired login, the likeliest failure after week one, is exactly a session that prints something and dies. Hence two files and a tail that reads back through the older one; a one-file cap would have been a silent regression in the reply that matters most. The third red test was already green from slice 7 and nothing was written for it (§11). Two smaller things, both in the rotation rather than the retention: the cap is checked *after* the write, because a chunk is one 64 KB read off the master and the file is briefly over either way — and a rotation that fails switches the cap off rather than retrying, because the loop it sits in is the session's life (§2) and a failing rename retried per write is a spin in the one place that has to keep reading the terminal. Retention counts from the record's mtime and not from `started`: a session left open for a week is not an old record, and the pass that finishes a reboot's orphan rewrites the record, so the day starts when the session ends rather than when it began. The sweep runs *after* the announcing loop and not inside it, because the marker that keeps an ending to one announcement lives inside the directory being removed — two days of downtime is a record that is terminal, old, and never announced. Run step by flood rather than by afternoon: 26 MiB through a real pty in 2.4s, 5.0 MiB left on disk, the `/login` line still in the tail from either file; the sweep over this box's own records, backdated, kept the live session and took the four ended ones. Nobody has yet watched a real day pass. |
| 11 | new projects from the phone | ☑ | **§9.3 was true, and the detail that decides the code is the one nobody could have guessed: the trust dialog's default selection is `No, exit`.** The obvious answer — press Enter, it is a confirmation — ends the session. So the runner sends Down, then checks that the marker moved onto `Yes, I trust this folder`, and only then confirms; a reworded or reordered dialog is left hanging, which is the old behaviour and honest, rather than confirmed blind. The matching had its own trap, the same shape as §9.5's: the panel renders words with `CSI <n> G` cursor jumps instead of spaces, so the stripped transcript reads `yes,itrustthisfolder` and any matcher written against what a human sees matches nothing. Answering is one-time — Claude Code writes `hasTrustDialogAccepted` for that path, verified by a second session coming straight up — which is what makes `new x` then `claude x` work tomorrow. The permission to answer it is deliberately split across both processes: the listener passes `--trust` only for `new`, the runner answers only if the directory is empty when it starts, and `new beacon` on an existing repository therefore behaves exactly like `claude beacon`. Two findings from `new` being the first verb that writes rather than reads: a control character in a name is now refused by *both* verbs (there is no delete verb here, so a directory called `red<ESC>[31m` is one nobody can remove from a phone), and the cap is now checked *before* the project is resolved, because a directory created for a session that is then refused is precisely the empty repository §5 gives this verb its own word to prevent. The fake `claude` the pty tests run against cost an hour to the oldest trap in this file: it mixed `select()` with a buffered reader, so it took all three bytes of an arrow key off the kernel to return one, then waited out its idle timeout on a terminal that had already answered it. Run step done twice: below the wire first, then **from the phone at 16:24** — `new scratchpad`, dialog answered one second after the spawn, link on the phone four seconds after the message, `ls`, `stop`, and the directory still there and still empty afterwards. The trust flag is now recorded for it, which is the property that makes tomorrow's `claude scratchpad` ordinary. |
| 12 | the failure that explains itself | ☐ | **Planned.** §8 stands on evidence of working rather than of being understood, and §14's way to change that — `sample` the parked pid while it is still hung — is an instruction no person can follow: the failure is a 45s timeout on a phone, and the process is killed or gone by the time anyone reaches the machine. The slice does not explain §9.13; it makes the bot take the capture an explanation would need, on the one path that has already failed. |
| 13 | a keyboard instead of a grammar | ☑ | **The slice succeeds by leaving no trace, which is also how it has to be proved.** `claude ttsecuritas-2` off a button at 20:33:47 is the same log line, the same intent and the same 3-second link as the typed message, because the button *is* the typed message — so the evidence that it worked is on the wire and in the shape of the code, not in bot.log. What only the real send could settle: Telegram takes `reply_markup` here as a nested JSON object, where every example in circulation writes it as JSON inside a string field — correct for a form-encoded call and wrong for this client, which posts a JSON body (§7). The design finding was the one the plan named: §3 permits a space in a directory name and the grammar splits on whitespace, so `My Project` would tap as `claude My` carrying the prompt `Project` — a refusal if nothing is called `My`, a session in the **wrong project** if something is. The filter is a round trip through `commands.parse` rather than a character rule of its own, for slice 11's reason: a second copy of §3 here is how two doors drift apart. Two things came from the keyboard being state Telegram holds rather than this bot: it survived the 17:37 listener restart with nothing sent, and a reply that has nothing to say about the menu must send *no* markup rather than an empty one, because an empty `keyboard` is the documented way to take a keyboard away — the obvious default would have removed the menu on every `ls`. §11's first rule bit as well: four assertions in this class (no `stop` button, no keyboard on `ls` or on an empty root, no token in the markup) are green over an absent keyboard, so one more test holds the others honest by asserting the keyboard beneath them is not empty. |

---

## 14. Standing checks

Not slices — things that stay true after the build.

- **After any Claude Code upgrade, re-run slice 6's and slice 11's tests.** Both read a UI, and
  UI moves. The URL lives inside a panel — `tests/fixtures/rc_startup.log` pins today's shape,
  and when it stops matching, capture a fresh transcript with `session.py --foreground` and
  diff the two. §9.3's trust dialog is the second coupling and the one that fails quietly:
  `TestTheTrustDialog` pins the wording and the marker, and if the panel is reworded the
  failure in the field is `new <name>` timing out at 45s with the dialog in the tail. That is
  the designed behaviour rather than a crash, so nothing will page you — check it deliberately.
  The one-line probe: `python3 session.py --cwd <a fresh empty dir in ~/Projects> --name probe
  --trust --foreground` should come up to a link in about three seconds.
  *Run on 2026-09-23 against v2.1.263, and both couplings hold*: slice 6's transcript tests and
  slice 11's `TestTheTrustDialog` are green, and the probe answered the dialog and reached a link
  in 3.0s. The direction is the finding — 2.1.263 is **older** than the 2.1.269/270 everything
  else here was verified against, because the box is on the homebrew build now (§9.8). So
  "after any upgrade" is the wrong half of the rule: it is after any *change*.
- `python3 bot.py --whoami` stays the way `allowed_chat_ids` gets filled in, as `notify.py`
  already does it.
- `tail -f var/bot.log` is the first thing to look at when the phone gets no reply; a silent
  drop there is §10.3 working as designed, not a bug.
- `launchctl print gui/$(id -u)/com.tommy.centrion.bot | head -30` says whether the bot is up,
  and §8 is the supported way to start it again. `pgrep -f 'bot.py --serve'` still answers the
  same question and does not say who started it; between 2026-09-13 09:56 and 13:15 it was the
  only thing that could, and the plist is back.
- `du -sh var` is the whole of §10.7's audit. A session costs at most 8 MB while it runs and
  its directory goes a day after it ends, so anything past a few tens of megabytes means the
  sweep is not running — look for `directory removed` lines in `var/bot.log`, and remember
  that a session already running when a cap changes keeps the runner it started with.
- **If a link ever stops coming back under launchd, §9.13 is the first suspect, and it has to be
  caught while it is hung.** It was never explained — only outlived. `sample` the parked pid for
  the stack and get `sudo fs_usage -w -f filesys claude` running *before* the spawn, because the
  process is already parked by the time you can attach and nothing new appears after that. A
  shell-run `launchd/bot.sh` is the workaround that is known to work, and §9.13 records the shape
  of the failure so it is recognised rather than re-diagnosed.
- **Two claims here are settled only by time, and both are still open.** The logout at
  2026-09-13 16:30 proved launchd brings the listener back at login and that a live session
  outlives the login session it was started from (§12 slice 9) — but a cold *boot* is a
  different path and nobody has watched one. And no real day has yet passed under §10's
  retention: the oldest ended records are this morning's, so the first sweep that has anything
  to take falls around 09:57 on 2026-09-14. `ls` from the phone, `du -sh var`, and `directory
  removed` lines in `var/bot.log` are the whole of both checks.


---

## 15. Decisions on record

Settled 2026-09-12. Recorded so they are not silently re-litigated during the build; each names
what would have to change to reopen it.

| Decision | Choice | Reopen if |
|---|---|---|
| Bot vs `claude remote-control` server mode (§1) | The bot | You stop caring which project you land in, and only want *a* session reachable from the phone. |
| Scope | `~/Projects` only; no Full Disk Access grant (§9.2) | A repo you need daily cannot move out of `~/Documents`. |
| Telegram identity | Its own BotFather bot, separate from stock-watch | Never — the `getUpdates` conflict is structural. |
| Model / effort | Inherit `opus[1m]` at `xhigh`; pass no flags (§6) | The bill from phone-started sessions is noticed before the work they did is. |
| Concurrency | `max_sessions: 2` on 8 GB | You watch memory during two real sessions and find headroom. |
| Bare `claude` | Answers with the project list; starts nothing (§5) | The extra tap outweighs starting in the wrong repo, which it will not. |
| Same-directory sessions | Allowed, flagged in the reply (§5) | Two sessions actually clobber each other's edits — then `--worktree` per session. |
| What the menu's buttons carry | Projects, `ls` and `help` — never `stop all`, and never a bare project name without its verb (§12 slice 13) | The keyboard grows a verb whose worst misreading is recoverable; `stop all` is not one of those. |
| Creating projects from the phone | A separate `new <name>` verb; `claude <unknown>` stays a typo (§5, §12 slice 11) | A mistyped name creating an empty repository turns out to be harmless, which it is not while every session bypasses permissions. |
| Answering §9.3's trust dialog | On the PTY, and only for a directory this bot created *and* finds empty — never by writing `hasTrustDialogAccepted` into `~/.claude.json` (§9.3) | Claude Code grows a flag that means "this directory is trusted", or stops rewriting `~/.claude.json` from every live process, which is what makes seeding it a race today. |
| Runtime | System `/usr/bin/python3`, stdlib only (§3) | Something here genuinely needs a third-party package, which nothing does yet. |
