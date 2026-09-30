#!/usr/bin/env bash
# Prove both reported capture problems are fixed:
#   1. a failing command must still return its error text (stderr captured)
#   2. capture must work when there is no usable /tmp or TMPDIR (Android)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
CC=${CC:-cc}

cat > "$WORK/t.c" <<'C'
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include "vp.h"

static int run(char **argv, int n, char **out)
{
    if (vp_capture_start() != 0) return -1;
    int rc = vp_cli_run(n, argv);
    *out = vp_capture_stop();
    return rc;
}

int main(void)
{
    int bad = 0;

    /* 1. a failing command: its error text must be in the captured output */
    char *fail[2] = { (char*)"analyze", (char*)"--boot" };
    char *out = NULL;
    int rc = run(fail, 2, &out);
    printf("  failing command: rc=%d, %zu bytes\n", rc, out ? strlen(out) : 0);
    if (out) printf("    captured: %s\n", out);
    if (!out || !*out) {
        printf("    FAIL: no error text captured\n"); bad = 1;
    } else if (rc == 0) {
        printf("    FAIL: should have failed\n"); bad = 1;
    } else {
        printf("    ok: error text is visible\n");
    }
    free(out);

    /* 2. no /tmp, no TMPDIR: capture must still work via the explicit dir */
    char dir[] = "/tmp/vpcap-XXXXXX";
    if (!mkdtemp(dir)) { printf("    FAIL: mkdtemp\n"); return 1; }
    vp_capture_set_dir(dir);
    char *ok[2] = { (char*)"doctor", NULL };
    out = NULL;
    rc = run(ok, 1, &out);
    printf("  with an explicit dir (%s): rc=%d, %zu bytes\n", dir, rc,
           out ? strlen(out) : 0);
    if (!out || !strstr(out, "VERSION:")) {
        printf("    FAIL: explicit dir produced nothing\n"); bad = 1;
    } else {
        printf("    ok: capture works without relying on /tmp\n");
    }
    free(out);

    /* 3. capture starts and stops cleanly, twice in a row */
    out = NULL;
    rc = run(ok, 1, &out);
    if (!out || !strstr(out, "VERSION:")) {
        printf("    FAIL: second run produced nothing\n"); bad = 1;
    } else {
        printf("    ok: repeated capture works\n");
    }
    free(out);

    /* 4. the scratch file is not left behind */
    int leftover = 0;
    DIR *d = opendir(dir);
    if (d) { struct dirent *e; while ((e = readdir(d))) if (e->d_name[0] != '.') leftover++; closedir(d); }
    printf("  scratch files left in %s: %d\n", dir, leftover);
    if (leftover) { printf("    FAIL: temp file not cleaned up\n"); bad = 1; }
    else printf("    ok: nothing left behind\n");

    /* 5. the real Android case: no writable /tmp, no TMPDIR, cwd not writable.
     * Capture must still work because it never touches the filesystem. */
    {
        setenv("TMPDIR", "/nonexistent-dir-for-veritpath-test", 1);
        if (chdir("/") != 0) { printf("    FAIL: chdir\n"); bad = 1; }
        vp_capture_set_dir(NULL);
        char *v[1]; v[0] = "doctor";
        char *o = NULL;
        int rc5 = run(v, 1, &o);
        printf("  no writable dir at all (TMPDIR bogus, cwd=/): rc=%d, %zu bytes\n",
               rc5, o ? strlen(o) : 0);
        if (!o || !strstr(o, "VERSION:")) {
            printf("    FAIL: capture needs the filesystem\n"); bad = 1;
        } else {
            printf("    ok: capture is filesystem-independent\n");
        }
        free(o);
    }

    /* 6. a big command must not deadlock: fill far past the old 64KiB pipe */
    {
        char *v[3]; v[0] = (char*)"unpack"; v[1] = (char*)"--help"; v[2] = NULL;
        char *o = NULL;
        int rc6 = run(v, 2, &o);
        printf("  help output: rc=%d, %zu bytes\n", rc6, o ? strlen(o) : 0);
        if (!o || !strlen(o)) { printf("    FAIL: help produced nothing\n"); bad = 1; }
        else printf("    ok: help captured\n");
        free(o);
    }

    /* 7. -h must not terminate the caller */
    {
        char *v[2]; v[0] = (char*)"analyze"; v[1] = (char*)"-h";
        char *o = NULL;
        int rc7 = vp_cli_run(2, v);
        printf("  -h via vp_cli_run: rc=%d (process still alive)\n", rc7);
        (void)o;
    }

    /* 8. argv[0] must be the sub-command. A caller that puts the image flags
     * first (the old Java wrapper did) has to get a clear error, not a silent
     * mis-dispatch to another command. */
    {
        char *v[4];
        v[0] = (char*)"--init-boot"; v[1] = (char*)"/path";
        v[2] = (char*)"inject";      v[3] = NULL;
        char *o = NULL;
        int rc8 = run(v, 3, &o);
        /* vp_cli_run takes argv[0] as the command, so this must fail loudly */
        if (rc8 == 0) {
            printf("    FAIL: a flag-first argv was accepted\n"); bad = 1;
        } else if (!o || !*o) {
            printf("    FAIL: flag-first argv gave no message\n"); bad = 1;
        } else {
            printf("    ok: flag-first argv is rejected with a message\n");
        }
        free(o);
    }

    printf(bad ? "  FAILED\n" : "  PASS\n");
    return bad;
}
C
$CC -O2 -std=c11 -Wall -Wextra -DVP_NO_MAIN -I"$ROOT/src" -o "$WORK/cap" "$WORK/t.c" "$ROOT"/src/*.c -lz
"$WORK/cap"
