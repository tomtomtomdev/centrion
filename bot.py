#!/usr/bin/env python3
"""The listener: long-polls Telegram, dispatches commands, reconciles sessions.

Slice 3 gives it only `--whoami`, the step that makes the allowlist fillable. The poll loop
arrives in slice 5, the spawn in slice 7, the fleet verbs and the reconciliation tick in
slice 8 (SPEC.md §12). Nothing here can start a session yet, which is deliberate: §12 puts the
launchd install in slice 5 precisely so the first thing to run under it is harmless if the
allowlist is wrong.
"""
import argparse
import sys

import config
import telegram


def whoami(cfg=None, client=None):
    """Print the chat ids that have messaged the bot, and whether they are allowed in.

    The way `allowed_chat_ids` gets filled in, and §14 keeps it that way. Two things it checks
    that a bare id list would not:

    - **private vs group.** §10.2 requires `chat.type == "private"`, because anyone can add a
      bot to a group and a group id is not an identity. A group id pasted into the allowlist
      would be refused at runtime and look like the bot ignoring you.
    - **what is already configured.** A hand-typed id that is one digit out fails closed — the
      bot answers nobody, silently, exactly as §10.3 requires for a stranger. That is correct
      behaviour and indistinguishable from a broken bot, so it is worth saying out loud here.

    Non-destructive: getUpdates is called once without an offset and nothing is acknowledged,
    so a pending message is still there afterwards.
    """
    cfg = cfg or config.load()
    tg = client or telegram.Telegram(cfg.bot_token)

    # A webhook left set from any point in this bot's past makes getUpdates 409 forever (§7),
    # and that is exactly the failure this command would otherwise report as "no updates".
    tg.delete_webhook()

    seen = {}
    for u in tg.poll(timeout=0):
        chat = (u.get("message") or {}).get("chat") or {}
        if isinstance(chat.get("id"), int):
            who = " ".join(x for x in (chat.get("title"), chat.get("first_name"),
                                       chat.get("username") and "@" + chat["username"]) if x)
            seen[chat["id"]] = (chat.get("type") or "?", who or "?")

    if not seen:
        print("No updates. Message the bot once from the phone, then run this again.\n"
              "If it stays empty, the daemon may already be polling this token — stop it "
              "first, or the two will 409 each other (SPEC.md §7).", file=sys.stderr)

    for cid, (kind, who) in sorted(seen.items()):
        flags = []
        if cid in cfg.allowed_chat_ids:
            flags.append("already allowed")
        if kind != "private":
            flags.append("NOT private — §10.2 refuses this, do not add it")
        print("%-14d %-10s %-28s %s" % (cid, kind, who, "· ".join(flags)))

    extra = sorted(set(cfg.allowed_chat_ids) - set(seen))
    if extra:
        print("\nAllowlisted but not seen here: %s\nThat is fine if you have not messaged the "
              "bot from them today — but a mistyped id looks exactly like this, and the bot "
              "answers a mistyped id with silence (§10.3)."
              % ", ".join(str(i) for i in extra), file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(description="centrion — Claude Code sessions from Telegram.")
    ap.add_argument("--whoami", action="store_true",
                    help="print the chat ids that have messaged the bot")
    a = ap.parse_args()
    try:
        if a.whoami:
            return whoami()
    except config.ConfigError as e:
        sys.exit("config: %s" % e)
    except telegram.TelegramError as e:
        sys.exit("telegram: %s" % e)
    ap.error("nothing to do yet — the listener loop arrives in slice 5 (SPEC.md §12)")


if __name__ == "__main__":
    sys.exit(main())
