#!/bin/sh
# Install this Mac's LaunchAgents for *this* checkout, then its power schedule (SPEC.md §8).
#
# The committed plists say __CHECKOUT__ and __HOME__ wherever they need the repo's path or the
# home directory, because both differ on each Mac and launchd expands nothing. This writes the
# real paths in, copies each result to ~/Library/LaunchAgents, and (re)loads it. power.sh then
# sets pmset's daily shutdown and power-on, which is the one step that asks for a password.
#
#   sh launchd/install.sh                 install, or replace the installed copies and restart them
#   sh launchd/install.sh --print [label] the rendered plist on stdout (default: the bot), and nothing else
#
#   com.tommy.centrion.bot    the Telegram listener
#   com.tommy.centrion.lock   locks the screen after an automatic login; copied, not bootstrapped
#   com.tommy.tt-lcmp-pull    tt-lcmp-pull at 09:00, from the tuntun tooling in ~/.tuntun/bin
#   com.tommy.ticket-workflow       09:15: claude in a Warp tab in ~/Projects/ttsecuritas-2, /loop /ticket-workflow
#   com.tommy.ticket-workflow.stop  16:45: ends that loop (both are ticket-workflow.sh)
#   com.tommy.figma-spec.serve      figma-spec --serve, always up: the Figma plugin's Send lands in ~/figma-specs
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CHECKOUT=$(cd "$HERE/.." && pwd)
LABELS="com.tommy.centrion.bot com.tommy.centrion.lock com.tommy.tt-lcmp-pull com.tommy.ticket-workflow com.tommy.ticket-workflow.stop com.tommy.figma-spec.serve"
# Installed for the next login and never started here: bootstrapping a RunAtLoad job runs it,
# and this one locks the screen of whoever is running install.sh (see lockscreen.sh).
AT_LOGIN_ONLY="com.tommy.centrion.lock"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

render() {
    # `|` as the delimiter: the paths are full of `/`. A `|` or `&` in one would be misread, and
    # neither is in any home on this box.
    sed -e "s|__CHECKOUT__|$CHECKOUT|g" -e "s|__HOME__|$HOME|g" "$HERE/$1.plist"
}

if [ "${1:-}" = "--print" ]; then
    render "${2:-com.tommy.centrion.bot}"
    exit 0
fi

install_agent() {
    label=$1
    target="$AGENTS/$label.plist"
    render "$label" > "$target.tmp"
    plutil -lint -s "$target.tmp"
    # Every file launchd opens itself (the program, the log's directory) has to exist before the
    # job runs: a missing one is a job that never starts and says nothing.
    program=$(plutil -extract ProgramArguments.0 raw -o - "$target.tmp")
    if [ ! -x "$program" ]; then
        rm "$target.tmp"
        echo "skipped $label: $program is not installed" >&2
        return 0
    fi
    for key in StandardOutPath StandardErrorPath; do
        log=$(plutil -extract "$key" raw -o - "$target.tmp" 2>/dev/null) && mkdir -p "$(dirname "$log")"
    done
    mv "$target.tmp" "$target"

    case " $AT_LOGIN_ONLY " in *" $label "*)
        echo "installed $target (runs at the next login)"
        return 0 ;;
    esac

    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    # bootout returns before the job is gone, and bootstrapping over it fails with an I/O error.
    i=0
    while launchctl print "$DOMAIN/$label" >/dev/null 2>&1 && [ $i -lt 50 ]; do
        sleep 0.2
        i=$((i + 1))
    done
    launchctl bootstrap "$DOMAIN" "$target"
    echo "installed $target"
}

mkdir -p "$CHECKOUT/var" "$AGENTS"
# The lock agent's binary. Rebuilt every install, so an OS update that moves the private symbol
# fails here, loudly, rather than at a login nobody is watching.
cc -Wall -o "$CHECKOUT/var/lockscreen" "$HERE/lockscreen.c" -F/System/Library/PrivateFrameworks \
    -framework login -framework CoreGraphics -framework CoreFoundation
for label in $LABELS; do
    install_agent "$label"
done
echo "for $CHECKOUT"

sh "$HERE/power.sh"
