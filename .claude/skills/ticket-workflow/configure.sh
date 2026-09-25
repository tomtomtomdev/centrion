#!/bin/sh
# Ask for every /ticket-workflow setting and write this Mac's config (env.sh reads it).
#
# The questions are config.example.env's commented `# TW_NAME=default   # what it is` lines, in
# order. Each shows its current value (this Mac's config, else the default); Enter keeps it.
#
#   sh configure.sh              ask, then write ~/.config/ticket-workflow/config.env
#   sh configure.sh --defaults   write it without asking: the current values, else the defaults
#   sh configure.sh --if-missing ask only when this Mac has no config yet (what install.sh runs)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
EXAMPLE="$HERE/config.example.env"
CONFIG="$HOME/.config/ticket-workflow/config.env"

ASK=1
case "${1:-}" in
    --defaults) ASK=0 ;;
    --if-missing) [ -f "$CONFIG" ] && { echo "keeping $CONFIG (re-run $0 to change it)"; exit 0; } ;;
esac
# No terminal to ask on (a pipe, launchd) → keep what is there rather than hang on a read.
if [ $ASK -eq 1 ] && ! { : </dev/tty; } 2>/dev/null; then
    echo "no terminal to ask on; writing current values and defaults" >&2
    ASK=0
fi

# The current config's values become the defaults, so a re-run only changes what you retype.
[ -f "$CONFIG" ] && . "$CONFIG"

quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

out=$(mktemp)
{
    echo "# /ticket-workflow settings for this Mac, written by configure.sh on $(date '+%F %T')."
    echo "# Re-run $HERE/configure.sh to change them."
} > "$out"

# Read the questions on fd 3, so stdin (the terminal) stays free for the answers.
while IFS= read -r line <&3; do
    case "$line" in
        "# ---"*)
            section=$(printf '%s' "$line" | sed 's/^# -* *//; s/ *-*$//')
            [ $ASK -eq 1 ] && printf '\n\033[1m%s\033[0m\n' "$section" >/dev/tty
            printf '\n# %s\n' "$section" >> "$out"
            continue ;;
        "# TW_"*=*) ;;
        *) continue ;;
    esac
    body=${line#"# "}
    name=${body%%=*}
    rest=${body#*=}
    raw=$(printf '%s' "$rest" | sed 's/[[:space:]]*#.*$//')   # the default, still quoted
    help=$(printf '%s' "$rest" | sed -n 's/^[^#]*#[[:space:]]*//p')
    eval "def=$raw"                                       # our own file: expands "$TW_JIRA_USER"
    eval "cur=\${$name-\$def}"
    val=$cur
    if [ $ASK -eq 1 ]; then
        [ -n "$help" ] && printf '  %s\n' "$help" >/dev/tty
        printf '  %s [%s]: ' "$name" "$cur" >/dev/tty
        IFS= read -r ans </dev/tty || ans=""
        [ -n "$ans" ] && val=$ans
        [ "$ans" = "-" ] && val=""                        # "-" clears it (e.g. no excluded components)
    fi
    eval "$name=\$val"                                    # later defaults may read it
    printf '%s=%s\n' "$name" "$(quote "$val")" >> "$out"
done 3< "$EXAMPLE"

mkdir -p "$(dirname "$CONFIG")"
mv "$out" "$CONFIG"
echo "wrote $CONFIG"
