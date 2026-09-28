# Sourced, never run: `. ~/.claude/skills/ticket-workflow/env.sh`, after `eval "$(tt-slot env --with-jira)"`
# (TT_HOST_ID has to be set first, because the claim labels below are built from it).
#
# Every TW_* the runbook reads comes from here. Two files may set any of them, the later winning:
#
#   ~/.config/ticket-workflow/config.env    this Mac (install.sh writes it from config.example.env)
#   .tuntun/ticket-workflow.env             this checkout, for a repo on a different board
#
# Whatever neither sets falls back to the defaults below, which are the Tuntun iOS pipeline's.

[ -f "$HOME/.config/ticket-workflow/config.env" ] && . "$HOME/.config/ticket-workflow/config.env"
[ -f .tuntun/ticket-workflow.env ] && . .tuntun/ticket-workflow.env

# Jira. Both queries are bare JQL clauses with no ORDER BY, since the runbook ANDs its own onto them.
: "${TW_BOARD_JQL:=filter = 11001}"         # the pickup board: my Open/Reopened/Issues/Backlog tickets
: "${TW_FIXING_JQL:=filter = 10550}"        # my tickets in status fixing
: "${TW_COMPONENT:=ios}"                    # the one component this pipeline fixes
: "${TW_EXCLUDE_COMPONENTS=BE}"             # comma-separated; a ticket carrying any is skipped. Empty = none
: "${TW_JIRA_USER:=tommy.yohanes}"          # the account this pipeline comments as
: "${TW_NOT_QA:=admin $TW_JIRA_USER}"       # authors whose comments are never the QA/design brief
: "${TW_HOSTS:=A B}"                        # every Mac's TT_HOST_ID; one claim label, mac-<id>, each

# GitLab
: "${TW_GITLAB_HOST:=git.tuntun.co.id}"
: "${TW_GITLAB_PROJECT:=49}"
: "${TW_MR_ASSIGNEE:=$TW_JIRA_USER}"

# Branches
: "${TW_MAIN_BRANCH:=main}"                 # an RC already merged here is released
: "${TW_RC_PREFIX:=release_candidate}"      # RC branches are <prefix>/X.Y.Z
: "${TW_RC_FLOOR:=2.4.0}"                   # no Affects Version and no vX.Y.Z label → oldest live RC at or above this
: "${TW_BRANCH_PREFIX:=tommy}"              # fix branches are <prefix>/<RC>/<KEY>-<slug>

# Build and drive
: "${TW_WORKSPACE:=TTSecuritas.xcworkspace}"
: "${TW_SCHEME:=TTSecuritas Staging}"
: "${TW_PRODUCTS:=Staging-iphonesimulator}" # DerivedData Build/Products folder the scheme builds into
: "${TW_APP_NAME:=Tuntun Sekuritas.app}"
: "${TW_SIM_DEVICE:=iPhone 17}"

# Derived, never configured
TW_MINE="mac-${TT_HOST_ID:-}"
TW_ALL_MACS=""                              # "mac-A","mac-B" — every claim label, JQL-quoted
TW_OTHERS=""                                # the same, without this Mac's
for _h in $(echo "$TW_HOSTS"); do          # $(…) splits in zsh too; a bare $TW_HOSTS does not
    TW_ALL_MACS="$TW_ALL_MACS${TW_ALL_MACS:+,}\"mac-$_h\""
    [ "mac-$_h" = "$TW_MINE" ] || TW_OTHERS="$TW_OTHERS${TW_OTHERS:+,}\"mac-$_h\""
done
unset _h
TW_EXCLUDE_JQL=""                           # " AND component NOT IN (BE)", or nothing
[ -n "$TW_EXCLUDE_COMPONENTS" ] && TW_EXCLUDE_JQL=" AND component NOT IN ($TW_EXCLUDE_COMPONENTS)"
