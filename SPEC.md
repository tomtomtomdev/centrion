# centrion — start a Remote Control Claude session from Telegram

A background LaunchAgent on this Mac long-polls a Telegram bot. Message it `claude` and it
starts `claude --remote-control <name> --dangerously-skip-permissions` in a project directory
and replies with the session link. Open the link (or find the session in the Claude app) and
you are driving a real session on this machine from your phone — your filesystem, your MCP
servers, your git checkouts, permissions already bypassed.

The bot is a **launcher**, not a bridge. It does not relay conversation. Once the link comes
back, Remote Control carries everything; Telegram's job is done.

Status: spec only. Nothing here is built yet. Every claim marked *verified* was tested on this
box on 2026-09-12 against Claude Code v2.1.269.

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
    sessions/<sid>/pty.log  full ANSI transcript of that session's terminal
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
  "max_sessions": 2
}
```

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

1. Reject a name containing `/`, `\\`, or a leading `.`, or one that is `.` or `..`.
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

**Tier 2 — enough to not need a laptop to clean up**

| Message | Effect |
|---|---|
| `ls` | live sessions: index, project, name, uptime, link |
| `stop 2` / `stop all` | SIGTERM the runner, which SIGTERMs claude |
| `help` | the two tables above, and the directories currently in `~/Projects` |

Anything else: reply with `help`. Register the tier-1 and tier-2 verbs with BotFather's
`/setcommands` so they autocomplete on the phone.

**Bare `claude` starts nothing.** It answers with the project list and waits for a second
message. The cost is one extra tap; what it buys is that no bypass-permissions session can ever
start in a repository you did not name. A "most recent project" default would be more
convenient and would make the target invisible at exactly the moment it matters — you are on a
phone, half-attending, and the first visible confirmation arrives after the session already
exists.

**A second session in a directory that already has one is allowed, and the reply says so.**
Refusing would be wrong — two sessions on one repo is a normal way to work — but they will
happily edit the same files underneath each other, which is the same hazard `claude
remote-control --spawn same-dir` carries. One line in the reply (`⚠ 2nd session in beacon`) is
the whole mitigation; anything more belongs to git, not to this bot.

Success reply — plain text, link on its own line so Telegram makes it tappable:

```
▶ beacon · beacon-3f2a
https://claude.ai/code/session_01HJK2Lh42N7JbfMGExJkpTF
bypass permissions on · 2 of 4 sessions
```

---

## 6. Environment for the spawned session

Remote Control is fussy about its environment and launchd gives you almost none of it. Build the
child env explicitly rather than inheriting.

**Must be set**

- `PATH=/Users/tomtomtomtom/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin` —
  `claude` lives at `~/.local/bin/claude`, and the session's own Bash tool needs brew on PATH to
  be useful.
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

```sh
install:  cp launchd/com.tommy.centrion.bot.plist ~/Library/LaunchAgents/
          launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tommy.centrion.bot.plist
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
   **A directory created in `~/Projects` later must be opened by hand with `claude` once
   first.** The trust dialog under a PTY with nobody watching just hangs until the 45s timeout.

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
   `claude ../../etc` and `claude ~/Documents/Junction` are both just a `help` reply.
5. `.telegram.json` at `0600`, gitignored, and **never in the plist** — files in
   `~/Library/LaunchAgents` are world-readable `0644`.
6. **Cap concurrency** (`max_sessions`, default 4). Without it, a held-down `claude` fills RAM
   with Claude Code processes.

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
produces exactly one push to its originating chat, and not a second one on the next tick; an
`ls` longer than 4096 characters is truncated rather than 400ing.
*Green:* the tier-2 verbs, the cap, and the reconciliation tick.
*Run:* start three sessions from the phone, `ls`, `stop all`, confirm all three are gone from
claude.ai/code. Then the one that needs a reboot: leave a session live, restart the Mac, and
confirm the bot comes back reporting zero sessions rather than three phantoms.

### Slice 9 — hardening

*Red:* `pty.log` rotates past its cap; `ended` session directories are reaped after a day but a
`live` one never is; the error tail sent to Telegram is scrubbed of anything matching a token or
a home path.
*Green:* the above.
*Run:* leave a session open for an afternoon, confirm `var/` has not grown without bound.

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
| 6 | PTY runner + URL scrape | ☐ | |
| 7 | `claude` end to end | ☐ | |
| 8 | fleet control + reconciliation | ☐ | |
| 9 | hardening | ☐ | |

---

## 14. Standing checks

Not slices — things that stay true after the build.

- **After any Claude Code upgrade, re-run slice 6's tests.** The URL lives inside a UI panel,
  and UI moves. `tests/fixtures/rc_startup.log` pins today's shape; when it stops matching, the
  fix is to capture a fresh transcript with `session.py --foreground` and diff the two.
- `python3 bot.py --whoami` stays the way `allowed_chat_ids` gets filled in, as `notify.py`
  already does it.
- `tail -f var/bot.log` is the first thing to look at when the phone gets no reply; a silent
  drop there is §10.3 working as designed, not a bug.


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
| Runtime | System `/usr/bin/python3`, stdlib only (§3) | Something here genuinely needs a third-party package, which nothing does yet. |
