@echo off
rem What the scheduled task runs, and the right way to start the listener by hand on Windows.
rem WINDOWS.md section 7. The analogue of launchd/bot.sh, and shorter than it for one reason.
rem
rem bot.sh exists to hold the single-instance lock: `exec 9>>` opens fd 9 without
rem close-on-exec, which is the only way a flock survives the exec into python, so on the Mac
rem the shell has to outlive the listener. Windows has no such constraint. W4d put the lock
rem inside the listener as a named mutex (`Local\centrion-<sha1 of var\.bot.lock>`), the kernel
rem releases it when the process dies however it dies, and `serve()` takes it before the first
rem getUpdates - so a second copy is refused in about a tenth of a second and exits 0, before
rem it can 409 the one that is working (SPEC.md section 7, and a 409 is mutual). This file therefore
rem locks nothing. Do not add a lock here; there would then be two, and the outer one would be
rem the one with no test.
rem
rem What it *is* is the KeepAlive loop. launchd restarts the job itself; Task Scheduler does
rem not - it runs an action once and is finished - so the restart has to be written down, and
rem this is where. Unconditional, on every exit, with no errorlevel guard: a listener that has
rem exited has stopped listening, and there is no successful version of that. Ten seconds is
rem launchd's default ThrottleInterval and the plist's explicit value; it is what keeps a
rem config error from becoming a hot loop that fills var\bot.log.
rem
rem   windows\install.ps1              build the venv and check the token's permissions
rem   windows\bot.cmd                  run the listener here, in this console, forever
rem   type var\bot.log                 SPEC.md section 14 starts here, on this platform too
rem
rem Ctrl-C in the console ends the listener and then asks about the batch file; answering N
rem leaves the loop, answering Y does the same thing more slowly. To stop it from elsewhere,
rem kill the python process - the loop will restart it in ten seconds, which is the point.

rem %~dp0 is this file's own directory with a trailing backslash, so the checkout is one level
rem up from it, whatever the clone is called and wherever it is. The plist has to be rendered
rem by install.sh because launchd expands nothing; batch expands this, so the committed file is
rem already right in every checkout and the installer has one less thing to get wrong.
cd /d "%~dp0.."
if not exist var mkdir var

rem PYTHONIOENCODING is deliberately NOT set, and this line is the decision rather than the
rem oversight it was until W5b. The plist has an EnvironmentVariables dict; the Task
rem Scheduler schema has no element for an environment at all - <EnvironmentVariables> and
rem <Environment> are both "ERROR: The task XML contains an unexpected node", measured - so
rem section 9's W5b was wrong that this belongs in the XML, and a `set` here is the only
rem place it could go. It does not go here either. var\bot.log is written in the ACP (cp1252
rem on this box), so a project name outside it reaches section 14's one diagnostic as
rem backslash escapes; nothing raises, sys.stderr being backslashreplace, and `type
rem var\bot.log` - the instruction section 14 actually gives - stays readable. Setting utf-8
rem would trade ASCII escapes for mojibake in that console and change nothing else. If it is
rem ever wanted, it is one `set PYTHONIOENCODING=utf-8` above this loop, and the thing to
rem check first is what `type` does with the result.

:loop
echo === %date% %time% centrion listener starting === >> var\bot.log
.venv\Scripts\python.exe bot.py --serve >> var\bot.log 2>&1
echo === %date% %time% listener exited %errorlevel% === >> var\bot.log
rem Ten seconds, twice, because `timeout` needs a console and this file will not always have
rem one. Measured (W5a): with stdin anything but a console - which is what a process started
rem without one inherits - `timeout /t 10 /nobreak` prints "ERROR: Input redirection is not
rem supported, exiting the process immediately", sets errorlevel 1 and returns in 0.02s. The
rem throttle is the only thing standing between a listener that cannot start at all and a
rem loop that writes two log lines every twenty milliseconds until the disk is full, so it
rem does not get to depend on how this was launched. `ping` needs no console and no venv;
rem eleven echoes one second apart is ten seconds of gaps.
rem
rem Neither wait redirects to var\bot.log, and that line is W5c's finding rather than a
rem tidy-up. `cmd` opens a redirection target denying other writers, so ANY process still
rem holding the log open for writing makes every `>>` above fail - and after `schtasks /End`
rem there is always one, because /End kills only the cmd.exe and the listener it started
rem keeps the handle it inherited (W5b, W0c). Two measured facts then compound: a failed
rem redirection prints to stderr and leaves errorlevel at 0, so `if errorlevel 1` never
rem fires; and `2>> var\bot.log`, which this line used to carry, made the timeout itself one
rem of the commands that did not run. The loop spun at 13% of a core with no wait at all,
rem nothing in the log, nothing in the task's last result and no symptom but a fan. `> nul`
rem cannot fail, so a wait redirected only there always runs. The cost is that timeout's own
rem complaint no longer reaches the log - and it only ever could in the case where the log
rem opens, which is the case where the throttle was never in danger.
timeout /t 10 /nobreak > nul
if errorlevel 1 ping -n 11 127.0.0.1 > nul
goto loop
