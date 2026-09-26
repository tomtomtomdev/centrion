#!/bin/sh
# The Mac's daily power schedule (SPEC.md §8): off overnight, back on before the working day.
#
# This is pmset's, not launchd's: a LaunchAgent cannot power a Mac on, and one that ran
# `shutdown` would need root anyway. Setting it needs sudo, so it is only set when this Mac's
# repeating events differ from the ones below; install.sh runs it after bootstrapping the bot.
#
#   sh launchd/power.sh            set it, if it differs (asks for your password)
#   sh launchd/power.sh --check    say whether it matches, and change nothing; exit 1 if not
#
# A power-on alone brings the Mac up to the login window, and the listener is a LaunchAgent that
# only runs once somebody is logged in (2026-09-26: booted 08:02, silent until a login at 08:11,
# and the 08:03 message from the phone dropped as stale). So both paths also report whether this
# user is logged in automatically, and --check fails when not. That is a setting, not a pmset
# event, and this script does not change it: System Settings → Users & Groups → "Automatically
# log in as". com.tommy.centrion.lock then locks the screen that login lands on.
#   sh launchd/power.sh --cancel   clear every repeating power event
set -eu

DAYS=MTWRFSU          # pmset's weekday letters: every day
OFF=06:00:00          # shutdown
ON=08:45:00           # wake if asleep, power on if off

# What `pmset -g sched` prints for the schedule above, under "Repeating power events:".
want() {
    printf '  wakepoweron at %s every day\n' "$(clock "$ON")"
    printf '  shutdown at %s every day\n' "$(clock "$OFF")"
}
clock() {   # 08:45:00 → 8:45AM, the way pmset shows it
    h=${1%%:*}; m=${1#*:}; m=${m%%:*}; h=${h#0}
    if [ "$h" -eq 0 ]; then echo "12:${m}AM"
    elif [ "$h" -lt 12 ]; then echo "${h}:${m}AM"
    elif [ "$h" -eq 12 ]; then echo "12:${m}PM"
    else echo "$((h - 12)):${m}PM"; fi
}
have() {
    pmset -g sched | sed -n '/^Repeating power events:/,/^[^ ]/{/^  /p;}'
}
autologin() {   # 0 = this user is logged in automatically at boot
    [ "$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null)" = "$(id -un)" ]
}
say_autologin() {
    if autologin; then
        echo "automatic login is on for $(id -un)"
        return 0
    fi
    echo "automatic login is OFF for $(id -un): after a power-on the listener waits for someone to"
    echo "log in. System Settings → Users & Groups → \"Automatically log in as\" (SPEC.md §8)."
    return 1
}

case "${1:-}" in
    --check)
        ok=0
        if [ "$(have)" = "$(want)" ]; then echo "power schedule matches"
        else echo "power schedule differs"; echo "want:"; want; echo "have:"; have; ok=1; fi
        say_autologin || ok=1
        exit $ok ;;
    --cancel)
        sudo pmset repeat cancel
        echo "power schedule cleared"
        exit 0 ;;
    "") ;;
    *) echo "usage: sh $0 [--check|--cancel]" >&2; exit 2 ;;
esac

if [ "$(have)" = "$(want)" ]; then
    echo "power schedule already set: shutdown $OFF, on $ON, $DAYS"
    say_autologin || true
    exit 0
fi
sudo pmset repeat shutdown "$DAYS" "$OFF" wakeorpoweron "$DAYS" "$ON"
if [ "$(have)" != "$(want)" ]; then
    echo "pmset took the schedule but reports something else:" >&2
    pmset -g sched >&2
    exit 1
fi
echo "power schedule set: shutdown $OFF, on $ON, $DAYS"
say_autologin || true
