#!/usr/bin/env bash
# Compile and exercise the JNI binding without an NDK.
#
#   bash tools/test_jni.sh [build-dir]
#
# tools/make_stub_jni.py writes a minimal jni.h, the binding and the core are
# built with VP_NO_MAIN (no second main()), and a small harness drives the
# natives with a working JNIEnv to prove they run a command and return output.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

CC=${CC:-cc}
python3 "$ROOT/tools/make_stub_jni.py" "$WORK/include" >/dev/null

cat > "$WORK/harness.c" <<'EOF'
#include <jni.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

char *strdup(const char *);   /* -std=c11 hides it */

extern jint    Java_dev_veritpath_Veritpath_nativeRun(JNIEnv *, jclass, jobjectArray);
extern jstring Java_dev_veritpath_Veritpath_nativeLastOutput(JNIEnv *, jclass);
extern jstring Java_dev_veritpath_Veritpath_nativeVersion(JNIEnv *, jclass);

typedef struct { const char **items; jsize n; } fake_arr;

static jsize h_len(void *e, jarray a) { (void)e; return ((fake_arr *)a)->n; }
static jobject h_get(void *e, jobjectArray a, jsize i) {
    (void)e; return (jobject)((fake_arr *)a)->items[i]; }
static const char *h_chars(void *e, jstring s, void *c) { (void)e;(void)c; return (const char *)s; }
static void h_rel(void *e, jstring s, const char *c) { (void)e;(void)s;(void)c; }
static void h_del(void *e, jobject o) { (void)e;(void)o; }
static jstring h_new(void *e, const char *s) { (void)e; return (jstring)strdup(s); }

int main(int argc, char **argv)
{
    if (argc < 2) { printf("usage: harness <image>\n"); return 2; }
    struct JNINativeInterface_ t = { h_len, h_get, h_chars, h_rel, h_del, h_new };
    struct JNINativeInterface_ *pt = &t;
    struct JNINativeInterface_ **env = &pt;

    const char *ver = (const char *)Java_dev_veritpath_Veritpath_nativeVersion(env, NULL);
    if (!ver || !strstr(ver, "veritpath ")) { printf("FAIL: version\n"); return 1; }
    printf("  version  : %s\n", ver);

    /* analyze --brief --boot <image> */
    const char *items[4];
    items[0] = "analyze"; items[1] = "--brief";
    items[2] = "--boot";  items[3] = argv[1];
    fake_arr arr = { items, 4 };

    jint rc = Java_dev_veritpath_Veritpath_nativeRun(env, NULL, (jobjectArray)&arr);
    const char *out = (const char *)Java_dev_veritpath_Veritpath_nativeLastOutput(env, NULL);
    printf("  exit code: %d\n", (int)rc);
    if (rc != 0) { printf("FAIL: exit code\n"); return 1; }
    if (!out || !strstr(out, "ARCH:")) { printf("FAIL: no captured output\n"); return 1; }
    printf("  captured : %zu bytes, contains ARCH:\n", strlen(out));

    /* a bad command must come back non-zero, not crash */
    const char *bad[1]; bad[0] = "no-such-command";
    fake_arr b = { bad, 1 };
    jint rc2 = Java_dev_veritpath_Veritpath_nativeRun(env, NULL, (jobjectArray)&b);
    if (rc2 == 0) { printf("FAIL: bad command should fail\n"); return 1; }
    printf("  bad cmd  : exit %d (as expected)\n", (int)rc2);

    /* the error text must be visible to the embedder: an app cannot read
     * logcat, so an uncaptured stderr means "no output at all" */
    const char *err = (const char *)Java_dev_veritpath_Veritpath_nativeLastOutput(env, NULL);
    if (!err || !strstr(err, "no-such-command")) {
        printf("FAIL: the error text was not captured\n");
        return 1;
    }
    printf("  error    : visible to the caller (%zu bytes)\n", strlen(err));

    /* with an explicit temp dir, capture must not depend on /tmp existing */
    {
        jstring d = (jstring)"/tmp";
        Java_dev_veritpath_Veritpath_nativeSetTempDir(env, NULL, d);
        const char *v[1]; v[0] = "doctor";
        fake_arr vv = { v, 1 };
        jint rc3 = Java_dev_veritpath_Veritpath_nativeRun(env, NULL, (jobjectArray)&vv);
        const char *o3 = (const char *)Java_dev_veritpath_Veritpath_nativeLastOutput(env, NULL);
        if (rc3 != 0 || !o3 || !strstr(o3, "VERSION:")) {
            printf("FAIL: capture with an explicit dir\n");
            return 1;
        }
        printf("  tempdir  : explicit dir works\n");
    }

    printf("  PASS\n");
    return 0;
}
EOF

echo "== building the JNI binding (stub jni.h, VP_NO_MAIN)"
$CC -O2 -std=c11 -Wall -Wextra -DVP_NO_MAIN \
    -I"$WORK/include" -I"$ROOT/src" \
    -o "$WORK/jni_test" "$WORK/harness.c" "$ROOT/jni/veritpath_jni.c" \
    "$ROOT"/src/*.c -lz

echo "== running it"
# a real image to analyze
python3 - "$ROOT" <<'PY' >/dev/null
import sys, os
sys.path.insert(0, os.path.join(sys.argv[1], 'tests'))
import imgkit
imgkit.cmd_images('/tmp/vp_jni_imgs')
PY

"$WORK/jni_test" /tmp/vp_jni_imgs/boot.img
