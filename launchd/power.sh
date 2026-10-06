#!/bin/sh
# The Mac's daily power schedule (SPEC.md §8): off overnight, back on before the working day.
#
# This is pmset's, not launchd's: a LaunchAgent cannot power a Mac on, and one that ran
# `shutdown` would need root anyway. Setting it needs sudo, so it is only set when this Mac's
# repeating events differ from the ones below; install.sh runs it after bootstrapping the bot.
#
#   sh launchd/power.sh            set it, if it differs (asks for your password)
#   sh launchd/power.sh --check    say whether it matches, and change nothing; exit 1 if not
#   sh launchd/power.sh --cancel   clear every repeating power event
#
# A power-on alone brings the Mac up to the login window, and the listener is a LaunchAgent that
# only runs once somebody is logged in (2026-09-26: booted 08:02, silent until a login at 08:11,
# and the 08:03 message from the phone dropped as stale). So both paths also report whether this
# user is logged in automatically, and --check fails when not. That is a setting, not a pmset
# event, and this script does not change it: System Settings → Users & Groups → "Automatically
# log in as". com.tommy.centrion.lock then locks the screen that login lands on.
#
# The listener runs this too, for `power`, `power cancel` and `power set` from the phone (SPEC.md
# §5), and it has no terminal to type a password into. So the interactive set also installs
# /etc/sudoers.d/centrion-power, which lets this user run exactly the two pmset commands below
# without one and nothing else, and without a terminal sudo is -n: a missing rule is a quick
# "a password is required", never a hang.
set -eu

PMSET=/usr/bin/pmset
SUDOERS=/etc/sudoers.d/centrion-power
if [ -t 0 ]; then SUDO=sudo; else SUDO="sudo -n"; fi

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
# The two commands the sudoers rule allows, word for word: sudo matches the arguments exactly.
cancel() { $SUDO "$PMSET" repeat cancel; }
schedule() { $SUDO "$PMSET" repeat shutdown "$DAYS" "$OFF" wakeorpoweron "$DAYS" "$ON"; }

passwordless() {   # 0 = the listener can run both without a password
    sudo -n -l "$PMSET" repeat cancel >/dev/null 2>&1 &&
        sudo -n -l "$PMSET" repeat shutdown "$DAYS" "$OFF" wakeorpoweron "$DAYS" "$ON" >/dev/null 2>&1
}
install_sudoers() {   # interactive only: asks for the password once, then never again
    passwordless && return 0
    [ -t 0 ] || return 0
    rule=$(mktemp)
    esc() { printf '%s' "$1" | sed 's/:/\\:/g'; }
    printf '# centrion: `power` from the phone (SPEC.md §5). Written by launchd/power.sh.\n' >"$rule"
    printf '%s ALL=(root) NOPASSWD: %s repeat cancel, %s repeat shutdown %s %s wakeorpoweron %s %s\n' \
        "$(id -un)" "$PMSET" "$PMSET" "$DAYS" "$(esc "$OFF")" "$DAYS" "$(esc "$ON")" >>"$rule"
    if ! visudo -cqf "$rule"; then
        echo "the sudoers rule did not parse; not installing it:" >&2
        cat "$rule" >&2; rm -f "$rule"; return 1
    fi
    sudo install -m 0440 -o root -g wheel "$rule" "$SUDOERS"
    rm -f "$rule"
    echo "installed $SUDOERS: the listener can now set and cancel the schedule"
}
say_sudoers() {
    if passwordless; then echo "the listener can set and cancel it (power from the phone)"; return 0; fi
    echo "the listener cannot change it without a password: run sh launchd/power.sh in a terminal"
    return 1
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
        say_sudoers || ok=1
        exit $ok ;;
    --cancel)
        if [ -z "$(have)" ]; then echo "power schedule already cleared: no shutdown, no power-on"; exit 0; fi
        cancel
        echo "power schedule cleared: no shutdown at $OFF, no power-on at $ON, until it is set again"
        exit 0 ;;
    "") ;;
    *) echo "usage: sh $0 [--check|--cancel]" >&2; exit 2 ;;
esac

install_sudoers
if [ "$(have)" = "$(want)" ]; then
    echo "power schedule already set: shutdown $OFF, on $ON, $DAYS"
    say_autologin || true
    exit 0
fi
schedule
if [ "$(have)" != "$(want)" ]; then
    echo "pmset took the schedule but reports something else:" >&2
    pmset -g sched >&2
    exit 1
fi
echo "power schedule set: shutdown $OFF, on $ON, $DAYS"
say_autologin || true
