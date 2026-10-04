# centrion

Start a Claude Code **Remote Control** session on your own machine from Telegram.

Message the bot `claude <project>` and it runs
`claude --remote-control <name> --dangerously-skip-permissions` in that project, then replies with
the session link. Open the link on your phone and you are driving a real session on your machine:
your filesystem, your MCP servers, your git checkouts.

The bot only **launches** sessions. It does not relay the conversation. Once the link comes back,
Remote Control carries everything.

> **Read [Security](#security) before running this.** Anyone who can message the bot can run
> anything as your user, with permissions bypassed.

Before you build any of this, check whether `claude remote-control` (server mode) already does what
you want. The bot is only worth it if you want to **pick the project from your phone and have the
link pushed to you**. See [SPEC.md §1](SPEC.md#1-why-this-shape).

## Commands

| Message | Effect |
|---|---|
| `claude` | lists the projects in `projects_root` as a keyboard, and starts nothing |
| `claude beacon` | starts a session in `<projects_root>/beacon` |
| `claude beacon fix the failing test` | same, then types that prompt into the session |
| `new scratchpad` | creates `<projects_root>/scratchpad`, answers the trust dialog, starts a session |
| `ls` | live sessions: index, project, name, uptime, link |
| `stop 2` / `stop all` | ends one session, or all of them |
| `help` | the commands, and the current project list |

A leading `/` works too. The bot registers these as Telegram's command menu each time it starts.
Bare `claude` never picks a project for you, and a mistyped name never creates one. Only `new`
creates projects. See [SPEC.md §5](SPEC.md#5-command-surface).

## How it works

```
supervisor (launchd on macOS, Task Scheduler on Windows)
  └── bot.py         listener: long-polls Telegram, owns nothing else
        └── session.py   runner: one per session, detached, owns the terminal for its whole life
              └── claude --remote-control <name> --dangerously-skip-permissions
```

- The runner is detached so that restarting the listener never kills a live session.
- The two processes talk only through files in `var/sessions/<sid>/` (`meta.json`, `pty.log`).
  A restarted listener re-reads them and picks up where it left off.
- The runner scrapes the session URL out of the terminal output and writes it to `meta.json`. The
  listener then sends it to your chat.
- On macOS, `terminal_window` opens a Warp (or Terminal.app) window that mirrors each live session.
  You can attach by hand from any terminal with `session.py --attach <sid>`.

| File | Role |
|---|---|
| `bot.py` | listener loop and command dispatch; `--serve` to run, `--whoami` to find your chat id |
| `commands.py` | parses a message into an intent (pure, no I/O) |
| `config.py` | loads `.telegram.json` (refusing if it is unsafe) and resolves project names |
| `telegram.py` | minimal Bot API client on `urllib` |
| `session.py` | the runner; `session_posix.py` / `session_win.py` are the platform halves |
| `attach.py` | the terminal-window viewer onto a session |
| `launchd/` | macOS LaunchAgents, lock and power schedule |
| `windows/` | `install.ps1`, `bot.cmd` (the restart loop) and the scheduled-task XML |
| `tests/` | stdlib `unittest`, no network |

## Setup

### 1. A bot and a config

Create a **new** bot with BotFather. Don't reuse a token another program polls: two consumers of one
token knock each other offline with 409 errors. Then create `.telegram.json` in the checkout root.
This file is gitignored and must stay that way:

```json
{
  "bot_token": "123456:ABC...",
  "allowed_chat_ids": [987654321],
  "projects_root": "/Users/you/Projects",
  "claude_bin": "/Users/you/.local/bin/claude",
  "max_sessions": 2,
  "terminal_window": true
}
```

To find your chat id, message the bot and then run `python3 bot.py --whoami`. If
`allowed_chat_ids` is empty or missing, the bot refuses every message.

### 2a. macOS

Stdlib only, on the system `/usr/bin/python3` (3.9). There is no venv and no requirements file.

```sh
chmod 600 .telegram.json
git submodule update --init      # figma-to-claude, only if you want that part
sh install.sh                    # every part, or: sh install.sh agent
```

`install.sh` runs each part's own installer in order:

| Part | Installer | Installs |
|---|---|---|
| `skills` | `.claude/skills/install.sh` | the `ticket-workflow`, `ios-next-slice` and `feature-work` skills, linked into `~/.claude/skills` |
| `cleanup` | `mac-cleanup/install.sh` | `daily-cleanup.sh` and `/mac-cleanup` (`--schedule HH:MM` for a daily run) |
| `figma` | `figma-to-claude/install.sh` | the `figma-spec` CLI; you import the Figma plugin by hand |
| `agent` | `launchd/install.sh` | the bot's LaunchAgent and this Mac's other agents, then a `pmset` power schedule (asks for sudo) |

To install only the bot, run `sh launchd/install.sh`. Use `--print` to see the rendered plist
without installing it. Restart the bot with
`launchctl kickstart -k gui/$(id -u)/com.tommy.centrion.bot`.

### 2b. Windows

The port uses ConPTY, so Windows needs three pinned packages (`pywinpty`, `pywin32`, `psutil`) in a
venv:

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1          # venv, permissions check, scheduled task
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -NoTask  # everything except the task
windows\bot.cmd                                                       # or run the listener in this console
```

On Windows the config file's permissions are checked through its ACL (Windows has no `chmod 600`).
If the check fails, the installer prints the exact `icacls` command that fixes it. Windows runs
`bot.cmd` from a logon-triggered task, so the bot runs only while you are logged in. See
[WINDOWS.md](WINDOWS.md).

## Running the tests

```sh
python3 -m unittest -v           # macOS: /usr/bin/python3, no dependencies
.venv\Scripts\python -m unittest -v   # Windows, after install.ps1
```

CI (`.github/workflows/test.yml`) runs the suite on `windows-latest` with 3.12, `macos-latest` with
the system 3.9, and `macos-latest` with 3.12. CI never needs a token. A test that would need one
skips.

## Troubleshooting

- Start with `var/bot.log`. On Windows, each session's runner also writes
  `var\sessions\<sid>\runner.log`.
- `var/sessions/<sid>/pty.log` is the full terminal transcript of that session. It is capped at
  4 MiB, and finished sessions are removed after a day.
- [SPEC.md §9](SPEC.md#9-constraints-discovered-on-this-box) lists failure modes seen in practice
  and how to recognise them. [§14](SPEC.md#14-standing-checks) lists the standing checks.

## Security

This bot is a remote code execution endpoint for your machine, with permissions bypassed. Losing the
**bot token** is the real breach. A leaked session link is not: it only opens in the claude.ai
account that started the session.

- Only numeric chat ids in `allowed_chat_ids` are served, and only in private chats. Unknown senders
  are dropped silently.
- A message can only name a direct child of `projects_root`. Paths, `..`, symlinks that escape the
  root and control characters are all refused.
- `.telegram.json` is `0600` (or owner-only ACL on Windows), gitignored, and never written into a
  plist.
- `max_sessions` caps how many sessions can run at once.
- Telegram is not end-to-end encrypted. The bot never echoes file contents or env vars.
- If the token leaks, revoke it with BotFather's `/revoke` and restart the bot.

Full detail is in [SPEC.md §10](SPEC.md#10-security).

## Further reading

- [SPEC.md](SPEC.md): the design, the slices it was built in, and every decision on record.
- [WINDOWS.md](WINDOWS.md): the Windows port, and what is still pending on the Mac.
