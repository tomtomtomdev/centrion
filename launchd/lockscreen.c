/*
 * Lock the screen the moment an automatic login lands on the desktop (SPEC.md §8).
 *
 * The listener is a LaunchAgent, so it only runs inside a logged-in session, and a Mac that
 * pmset powers on at 08:45 sits at the login window until somebody types a password — with
 * nothing polling Telegram. Automatic login fixes that and leaves the desktop open to whoever is
 * in the room, so com.tommy.centrion.lock runs this at login and puts the lock screen straight
 * back. The session behind it keeps running: the listener, its Claude sessions, and the login
 * keychain those sessions read their claude.ai login from.
 *
 * SACLockScreenImmediate is private (login.framework), because the public way went away:
 * `CGSession -suspend` is gone from macOS 26, and ctrl-cmd-Q by osascript needs an Accessibility
 * grant a launchd job does not have. Being private it can vanish in any update, so this does not
 * trust the call — it asks CoreGraphics whether the screen is actually locked, retries for a
 * while (at login the window server may not be ready for the first try), and exits 1 with a
 * line in var/lockscreen.log if it never is. A silent failure here is an open desktop.
 *
 *   lockscreen            lock, and confirm it; exit 0 locked, 1 not
 *   lockscreen --status   print "locked" or "unlocked", change nothing
 *
 * install.sh builds it into var/ with the system cc.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <CoreGraphics/CoreGraphics.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

extern int SACLockScreenImmediate(void);

static int locked(void) {
    CFDictionaryRef session = CGSessionCopyCurrentDictionary();
    if (session == NULL) return 0;   /* no GUI session yet: certainly not locked */
    CFBooleanRef flag = CFDictionaryGetValue(session, CFSTR("CGSSessionScreenIsLocked"));
    int result = flag != NULL && CFBooleanGetValue(flag);
    CFRelease(session);
    return result;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--status") == 0) {
        puts(locked() ? "locked" : "unlocked");
        return 0;
    }
    /* 30 tries a second apart: enough for a slow login to finish drawing the desktop. */
    for (int attempt = 1; attempt <= 30; attempt++) {
        if (locked()) {
            printf("locked (attempt %d)\n", attempt);
            return 0;
        }
        int rc = SACLockScreenImmediate();
        sleep(1);
        if (locked()) {
            printf("locked (attempt %d, SACLockScreenImmediate returned %d)\n", attempt, rc);
            return 0;
        }
    }
    printf("NOT LOCKED after 30 attempts — the desktop is open (SPEC.md §8)\n");
    return 1;
}
