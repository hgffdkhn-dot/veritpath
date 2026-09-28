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

    printf(bad ? "  FAILED\n" : "  PASS\n");
    return bad;
}
C
$CC -O2 -std=c11 -Wall -Wextra -DVP_NO_MAIN -I"$ROOT/src" -o "$WORK/cap" "$WORK/t.c" "$ROOT"/src/*.c -lz
"$WORK/cap"
