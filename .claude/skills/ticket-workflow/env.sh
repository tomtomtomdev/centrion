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
: "${TW_HOSTS:=A=macmini B=macbookpro}"      # every Mac as <TT_HOST_ID>=<device-type label>; first listed wins a double claim

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
: "${TW_SMOKE_ACCOUNTS:=macmini=tomtomtomgame5@outlook.com macbookpro=tomtomtomgame6@outlook.com}"  # <label>=<login>; each Mac drives on its own

# Derived, never configured
# Each Mac claims with its device-type label. It also still reads its legacy mac-<id> label as its
# own, and a peer's as the peer's, so tickets claimed before the rename stay tracked.
TW_MINE=""                                  # this Mac's label, the one it stamps: macmini
TW_MINE_JQL=""                              # "macmini","mac-A" — the labels it reads as its own
TW_ALL_MACS=""                              # "macmini","mac-A","macbookpro","mac-B" — every claim label, JQL-quoted
TW_OTHERS=""                                # the same, without this Mac's
TW_CLAIM_RE="mac-[A-Z]"                     # a regex matching any claim label, for the Python filters
for _p in $(echo "$TW_HOSTS"); do          # $(…) splits in zsh too; a bare $TW_HOSTS does not
    case $_p in
        ?*=?*) ;;
        *) echo "env.sh: TW_HOSTS entry '$_p' needs the <id>=<label> form (A=macmini); re-run configure.sh" >&2; continue ;;
    esac
    _id=${_p%%=*}; _label=${_p#*=}
    _q="\"$_label\",\"mac-$_id\""
    TW_ALL_MACS="$TW_ALL_MACS${TW_ALL_MACS:+,}$_q"
    TW_CLAIM_RE="$TW_CLAIM_RE|$_label"
    if [ "$_id" = "${TT_HOST_ID:-}" ]; then
        TW_MINE=$_label; TW_MINE_JQL=$_q
    else
        TW_OTHERS="$TW_OTHERS${TW_OTHERS:+,}$_q"
    fi
done
unset _p _id _label _q
TW_SMOKE_ACCOUNT=""                         # this Mac's staging/dev login for every drive
for _p in $(echo "$TW_SMOKE_ACCOUNTS"); do
    [ "${_p%%=*}" = "$TW_MINE" ] && TW_SMOKE_ACCOUNT=${_p#*=}
done
unset _p
TW_EXCLUDE_JQL=""                           # " AND component NOT IN (BE)", or nothing
[ -n "$TW_EXCLUDE_COMPONENTS" ] && TW_EXCLUDE_JQL=" AND component NOT IN ($TW_EXCLUDE_COMPONENTS)"
