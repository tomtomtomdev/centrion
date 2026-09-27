<#
    Set this checkout up to run centrion on Windows. WINDOWS.md section 7, slice W5a.

    The analogue of launchd/install.sh. windows\bot.cmd finds its own checkout from %~dp0, so
    unlike the plist it needs no substitution at all and is already right in every clone;
    windows\centrion.xml is the one rendered file here, and it turned out to need two
    substitutions rather than the one section 9's W5b predicted - see Register-Task below. What is
    left over from install.sh is the part the Mac never needed:

      1. the venv. SPEC.md section 3's "stdlib only" was a defence against brew churn and it still
         holds on the Mac. It does not survive ConPTY: pywinpty is the terminal, pywin32 is
         the DACL check config.py fails closed without, and psutil is the process tree that
         `terminate` has to prove it killed. requirements-win.txt pins all three to what was
         measured on this box.
      2. var\, before anything writes to it.
      3. the token's permissions. NTFS has no mode bits, so the Mac's `chmod 600` has no
         meaning here and the answer is the DACL (W2b). This does not set it - a file created
         under the checkout already inherits owner/SYSTEM/Administrators, which is what the
         check wants, so setting it unasked would be ceremony. It *asks* instead, by loading
         the config the way the listener will, and prints whatever config.py says. When that
         is a refusal it carries the exact `icacls` line for the principals actually on the
         file, which W2b made executable on purpose: section 5.1's original line named
         `/inheritance:r /grant:r "%USERNAME%":F`, which removes inherited entries only and
         measurably did not shift the explicit `Users` grant it was printed about.

      4. the scheduled task (W5b). windows\centrion.xml with this checkout's path and this
         box's user written in, registered as `centrion` with `schtasks /Create /XML /F`.
         Starting the listener by hand with windows\bot.cmd stays a supported way to run it.

    Idempotent: re-running reuses an existing venv, re-installs the pinned wheels (a no-op
    when they are already there), re-runs the check and re-registers the task (/F replaces).

        powershell -ExecutionPolicy Bypass -File windows\install.ps1
        powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Print
        powershell -ExecutionPolicy Bypass -File windows\install.ps1 -NoTask

    -Print writes the rendered XML to stdout and does nothing else - `install.sh --print`,
    and what test_layout.TestTheScheduledTask compares its own rendering against. -NoTask
    installs everything but the task, for a clone that is only there to run the suite.

    The ExecutionPolicy argument is not optional on a default Windows 11 install, where the
    per-user policy is Restricted and a downloaded or checked-out .ps1 will not run at all.
#>
param(
    [switch]$Print,
    [switch]$NoTask
)
$ErrorActionPreference = "Stop"

$Checkout = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Checkout ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$Requirements = Join-Path $Checkout "requirements-win.txt"
$TaskXml = Join-Path $PSScriptRoot "centrion.xml"
$TaskName = "centrion"

# The plist's `render`, and it substitutes twice where install.sh substitutes __CHECKOUT__
# and __HOME__ for reasons that do not apply here. The checkout is the plist's own reason:
# this repo lives under a different path on every box and nothing expands a ~. The user is
# the one the Mac does not have - a LaunchAgent belongs to this user because of the
# directory it is installed into, where a task lives in one machine-global store, so a
# LogonTrigger with no UserId means *any* user's logon and `schtasks /Create` answers
# "Access is denied" to that from an unelevated shell (measured, W5b; and the error names
# neither the trigger nor the missing element).
function Render-Task {
    $xml = [System.IO.File]::ReadAllText($TaskXml, [System.Text.Encoding]::ASCII)
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    return $xml.Replace("__CHECKOUT__", $Checkout).Replace("__USER__", $me)
}

if ($Print) {
    [Console]::Out.Write((Render-Task))
    exit 0
}

Write-Host "centrion: installing into $Checkout"

if (-not (Test-Path $Requirements)) {
    throw "$Requirements is missing - this is not a centrion checkout"
}

# The interpreter that *builds* the venv is not the one that runs anything afterwards. `py -3`
# is the launcher, and on a stock python.org install it is the ONLY thing on PATH - "Add
# python.exe to PATH" is off by default in that installer. A Store install leaves `python`
# instead, and Windows 11 ships an `python.exe` app-execution alias of that name which opens
# the Store and runs nothing, so a candidate is not accepted until it has answered a version.
#
# Two things here are written the long way on purpose, both found by W5a's run step:
#
#   - the argument list is a `[string[]]` and is splatted from a variable that is always an
#     array. `@(...) | Select-Object -Skip 1` returns a *scalar* when one element is left, and
#     splatting a scalar String in PowerShell 5.1 silently passes nothing at all - measured:
#     `$e = "-3"; & py @e -c "print(3)"` starts an interactive REPL. That probe answered
#     nothing, `py` was skipped without a word, and the fall-through to `python` hid it on
#     this box. On a stock python.org box it would have been "no python found on PATH" with
#     the launcher sitting right there.
#   - `--version`, not `-c`. A probe that can start a REPL is a probe that can hang an
#     installer, and there is nothing in a version banner worth that risk.
function Find-BasePython {
    $candidates = @(
        [pscustomobject]@{ Exe = "py";      Args = [string[]]@("-3") },
        [pscustomobject]@{ Exe = "python";  Args = [string[]]@() },
        [pscustomobject]@{ Exe = "python3"; Args = [string[]]@() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $probe = [string[]]$candidate.Args + "--version"
        # No `2>$null` here: redirecting a native command's stderr under
        # $ErrorActionPreference = "Stop" turns each line into a terminating NativeCommandError,
        # and the Store alias's complaint would be caught as a crash rather than read as a no.
        $version = & $candidate.Exe @probe
        if ($LASTEXITCODE -eq 0 -and "$version" -match "(\d+\.\d+\.\d+)") {
            return [pscustomobject]@{
                Exe = $candidate.Exe; Args = [string[]]$candidate.Args; Version = $Matches[1]
            }
        }
    }
    throw "no python found on PATH - install Python 3 and re-run this script"
}

if (Test-Path $VenvPython) {
    Write-Host "venv: already at $Venv"
} else {
    $base = Find-BasePython
    Write-Host ("venv: creating with {0} {1} (python {2})" -f $base.Exe, ($base.Args -join " "), $base.Version)
    # `[string[]]`, so a one-argument list is still an array when it is splatted. See above.
    $baseArgs = [string[]]$base.Args + @("-m", "venv")
    & $base.Exe @baseArgs $Venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed ($LASTEXITCODE)" }
}

Write-Host "wheels: installing $Requirements"
& $VenvPython -m pip install --disable-pip-version-check --requirement $Requirements
if ($LASTEXITCODE -ne 0) { throw "pip install failed ($LASTEXITCODE)" }

# Everything launchd opens itself has to exist before the job runs (the plist's comment, and
# two agents on the Mac that died before their first line for want of this). Task Scheduler
# opens nothing, but bot.cmd's very first redirection is into this directory.
$Var = Join-Path $Checkout "var"
if (-not (Test-Path $Var)) { New-Item -ItemType Directory -Path $Var | Out-Null }
Write-Host "state: $Var"

# The token check. Not fatal: a fresh clone has no .telegram.json at all, and the point of
# this step is to say so in words rather than to leave it to the first silent listener.
$Token = Join-Path $Checkout ".telegram.json"
if (-not (Test-Path $Token)) {
    Write-Host ""
    Write-Host "token: $Token does not exist yet. It holds the bot token, which per SPEC.md section 10"
    Write-Host "       is shell access to this machine. Create it under this checkout, where it"
    Write-Host "       inherits an owner/SYSTEM/Administrators DACL and needs no icacls at all;"
    Write-Host "       if you move it somewhere more permissive, config.py will refuse to load"
    Write-Host "       it and print the icacls line for whoever is on it (WINDOWS.md section 5.1)."
} else {
    Write-Host ""
    Write-Host "token: checking $Token the way the listener will"
    # From the checkout, because `import config` finds the module on sys.path[0] and nowhere
    # else; CONFIG itself is anchored to config.py's own directory, so the file is the same
    # one either way.
    Push-Location $Checkout
    try {
        & $VenvPython -c "import config; config.load(); print('       loads - owner-only as far as the DACL is concerned')"
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "       config.load() refused it. The line above is the fix, and it is meant"
        Write-Host "       to be run as printed." -ForegroundColor Yellow
    }
}

# The scheduled task. launchd's `bootout` then `bootstrap`, with `/F` doing both in one call
# and no wait loop needed - the scheduler replaces a registration synchronously where
# launchctl's bootout returns before the job is gone.
#
# The rendered XML goes to a temp file and is written with UTF8Encoding($false), which is the
# one line here that is not obvious. Measured, W5b: `schtasks /Create /XML` reads the file
# itself, refuses a UTF-8 byte order mark with "ERROR: The task XML is malformed" and
# "incorrect document syntax" at (1,2), and PowerShell 5.1's `Out-File -Encoding utf8` and
# `Set-Content -Encoding utf8` both write one. The error names the column and nothing else.
if ($NoTask) {
    Write-Host ""
    Write-Host "task: skipped (-NoTask)"
} else {
    Write-Host ""
    $rendered = Join-Path ([System.IO.Path]::GetTempPath()) ("centrion-task-{0}.xml" -f $PID)
    try {
        [System.IO.File]::WriteAllText($rendered, (Render-Task),
                                       (New-Object System.Text.UTF8Encoding($false)))
        $out = & schtasks /Create /TN $TaskName /XML $rendered /F
        if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed ($LASTEXITCODE): $out" }
        Write-Host "task: $TaskName registered - runs windows\bot.cmd at this user's logon"
    } finally {
        if (Test-Path $rendered) { Remove-Item $rendered -Force }
    }
    # It is registered, not running: the trigger is the next logon. Say so, because a task
    # that exists and is not running looks exactly like a task that failed to start.
    Write-Host "      it starts at the next logon; schtasks /Run /TN $TaskName starts it now"
}

Write-Host ""
Write-Host "next: schtasks /Run /TN $TaskName        start the listener now, hidden"
Write-Host "      windows\bot.cmd                  or run it in this console instead"
Write-Host "      type var\bot.log                 SPEC.md section 14 starts here"
Write-Host "      .venv\Scripts\python -m unittest -q    the suite, from the venv (W2b)"
