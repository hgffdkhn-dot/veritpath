#!/usr/bin/env bash
# End-to-end regression test for the veritpath binary.
#
#   ./tests/run.sh [path-to-binary]
#
# Fully self-contained: images are built and verified by tests/imgkit.py
# (pure standard library), so the suite needs nothing but python3 and cc.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${1:-$ROOT/build/veritpath}"
KIT="$ROOT/tests/imgkit.py"
PAY="$ROOT/payloads/example-su"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0

ok()   { echo "  ok   $1"; pass=$((pass + 1)); }
bad()  { echo "  FAIL $1"; fail=$((fail + 1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2' want '$3')"; fi; }

[ -x "$BIN" ] || { echo "binary not found: $BIN" >&2; exit 1; }

echo "== veritpath regression =="
echo "binary: $BIN"

# ---------------------------------------------------------------- images
echo "== generating synthetic images"
python3 "$KIT" images "$WORK/img"
for f in boot.img init_boot.img vendor_boot.img legacy1.img legacy2.img; do
    [ -s "$WORK/img/$f" ] || { echo "missing $f" >&2; exit 1; }
done
ok "synthetic images generated"

# ------------------------------------------------------------ analyze
echo "== analyze"
out=$("$BIN" analyze --brief --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img" \
      --vendor-boot "$WORK/img/vendor_boot.img")
check "arch detected"        "$(grep -c '^ARCH:arm64$' <<<"$out")" "1"
check "layout is init_boot"  "$(grep -c '^LAYOUT:init_boot$' <<<"$out")" "1"
check "target is init_boot"  "$(grep -c '^TARGET:init_boot$' <<<"$out")" "1"
check "system-as-root"       "$(grep -c '^SYSTEM_AS_ROOT:1$' <<<"$out")" "1"
check "brief lists each image" "$(grep -c '^\[' <<<"$out")" "3"

out=$("$BIN" analyze --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img" --json)
check "json output parses"   "$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["target"])' <<<"$out")" "init_boot"

out=$("$BIN" analyze --brief --boot "$WORK/img/boot.img" --vendor-boot "$WORK/img/vendor_boot.img")
check "vendor_boot layout"   "$(grep -c '^LAYOUT:vendor_boot$' <<<"$out")" "1"

# --------------------------------------------------------------- plan
echo "== plan"
out=$("$BIN" plan --init-boot "$WORK/img/init_boot.img" -p "$PAY" --permissive)
check "plan lists payload"   "$(grep -c 'payload-file' <<<"$out")" "1"
check "plan names selinux"   "$(grep -c 'permissive' <<<"$out")" "1"
check "plan has selinux"     "$(grep -c 'androidboot.selinux=permissive' <<<"$out")" "1"
check "plan is read only"    "$([ -e "$WORK/img/init_boot.veritpath.img" ] && echo yes || echo no)" "no"

# ------------------------------------------------------------- inject
echo "== inject"
"$BIN" inject --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img" \
      -p "$PAY" --permissive -o "$WORK/out" >/dev/null
check "output written"       "$([ -s "$WORK/out/init_boot.veritpath.img" ] && echo yes || echo no)" "yes"
check "boot.img untouched"   "$([ -e "$WORK/out/boot.veritpath.img" ] && echo yes || echo no)" "no"
check "backup kept"          "$([ -e "$WORK/img/init_boot.img.veritpath.bak" ] && echo yes || echo no)" "yes"
if python3 "$KIT" verify "$WORK/out/init_boot.veritpath.img"; then ok "patched init_boot"; else bad "patched init_boot"; fi

# --------------------------------------------------- vendor fragments
echo "== vendor_boot"
"$BIN" inject --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img" \
    --vendor-boot "$WORK/img/vendor_boot.img" -p "$PAY" \
    --patch-vendor-boot -o "$WORK/out2" >/dev/null
if python3 "$KIT" verify "$WORK/out2/vendor_boot.veritpath.img" vendor; then
    ok "patched vendor_boot"
else
    bad "patched vendor_boot"
fi

# the fragment table must survive: this used to collapse into one blob
if python3 "$KIT" verify-frags "$WORK/out2/vendor_boot.veritpath.img"; then
    ok "vendor fragments preserved (count, offsets, extents)"
else
    bad "vendor fragments preserved (count, offsets, extents)"
fi
if python3 "$KIT" verify-frags "$WORK/img/vendor_boot.img" >/dev/null 2>&1; then
    ok "fixture itself carries a fragment table"
else
    bad "fixture itself carries a fragment table"
fi

# ------------------------------------------------------- legacy images
echo "== legacy headers"
for hv in 1 2; do
    "$BIN" inject --boot "$WORK/img/legacy$hv.img" -p "$PAY" --permissive -o "$WORK/out3" >/dev/null
    if python3 "$KIT" verify "$WORK/out3/legacy$hv.veritpath.img" hv "$hv"; then
        ok "legacy v$hv round-trip"
    else
        bad "legacy v$hv round-trip"
    fi
done

# --------------------------------------------------------- compression
echo "== compression"
"$BIN" inject --init-boot "$WORK/img/init_boot.img" -p "$PAY" \
    --format lz4_legacy -o "$WORK/out4" >/dev/null
check "forced lz4_legacy" "$(head -c 4 "$WORK/out4/init_boot.veritpath.img" >/dev/null; \
    "$BIN" analyze --brief --init-boot "$WORK/out4/init_boot.veritpath.img" | grep -c '^RAMDISK_FMT:lz4_legacy$')" "1"

# ------------------------------------------------------- unpack/repack
echo "== unpack / repack"
"$BIN" unpack "$WORK/img/init_boot.img" -d "$WORK/work" >/dev/null
check "ramdisk extracted" "$([ -f "$WORK/work/ramdisk/init" ] && echo yes || echo no)" "yes"
check "original kept"     "$([ -f "$WORK/work/original.img" ] && echo yes || echo no)" "yes"
printf 'handmade\n' > "$WORK/work/ramdisk/handmade.txt"
"$BIN" repack "$WORK/work" -o "$WORK/repacked.img" >/dev/null
"$BIN" unpack "$WORK/repacked.img" -d "$WORK/work2" >/dev/null
check "handmade file survives" "$([ "$(cat "$WORK/work2/ramdisk/handmade.txt")" = "handmade" ] && echo yes || echo no)" "yes"
check "init survives"          "$([ -f "$WORK/work2/ramdisk/init" ] && echo yes || echo no)" "yes"
check "symlink extracted"      "$([ -L "$WORK/work/ramdisk/bin" ] && echo yes || echo no)" "yes"
check "symlink survives repack" "$([ -L "$WORK/work2/ramdisk/bin" ] && echo yes || echo no)" "yes"

# ------------------------------------------------------- real-world quirks
echo "== header quirks (vendor tools / odd dumps)"
python3 "$KIT" quirks "$WORK/quirks" >/dev/null
for f in hs_padded hs_zero hs_garbage hv_future prefix gzip both_versions_garbage; do
    if "$BIN" inject --init-boot "$WORK/quirks/$f.img" -p "$PAY" --permissive -o "$WORK/qout" >/dev/null 2>&1; then
        ok "tolerates $f"
    else
        bad "tolerates $f"
    fi
done
if python3 "$KIT" verify-dir "$WORK/qout" >/dev/null 2>&1; then
    ok "every quirky header was patched correctly"
else
    bad "quirky headers"
fi

# ------------------------------------------------------------ large images
echo "== large GKI images (a real GKI 1.0 boot.img is ~192MB)"
python3 "$KIT" large "$WORK/big" >/dev/null
for f in large_v3 large_v4 large_v3_sabotaged large_v4_sabotaged; do
    if "$BIN" analyze --brief --boot "$WORK/big/$f.img" > "$WORK/$f.out" 2>&1; then
        ok "parses $f.img"
    else
        bad "parses $f.img"
    fi
    check "$f finds arm64" "$(grep -c '^ARCH:arm64$' <"$WORK/$f.out")" "1"
done
check "hexdump works on a large image" \
    "$("$BIN" hexdump "$WORK/big/large_v4.img" | grep -c 'MAGIC8:414e44524f494421')" "1"

# ------------------------------------------------------- default report style
echo "== report format"
out=$("$BIN" analyze --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img")
check "default is the grouped report" "$(grep -c 'boot image analysis' <<<"$out")" "1"
check "report has a separator rule"  "$(grep -c '^==============================================================$' <<<"$out")" "1"
check "report names the target"      "$(grep -c '^  injection target     init_boot$' <<<"$out")" "1"
check "report shows ramdisk entries" "$(grep -c 'ramdisk_entries' <<<"$out")" "1"
check "report uses True/False"       "$(grep -c 'system_as_root       True' <<<"$out")" "1"

out=$("$BIN" analyze --brief --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img")
check "--brief keeps KEY:VALUE"      "$(grep -c '^ARCH:arm64$' <<<"$out")" "1"
check "--brief drops the report"     "$(grep -c 'boot image analysis' <<<"$out")" "0"

# -b is the short form of --brief; it was registered in the switch but missing
# from the getopt string, so it silently produced the rich report
out=$("$BIN" analyze -b --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img")
check "-b matches --brief"           "$(grep -c '^ARCH:arm64$' <<<"$out")" "1"
check "-b drops the report"          "$(grep -c 'boot image analysis' <<<"$out")" "0"

out=$("$BIN" analyze --json --boot "$WORK/img/boot.img" --init-boot "$WORK/img/init_boot.img")
check "--json still parses"          "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["target"])' <<<"$out")" "init_boot"

# ------------------------------------------------------------ verify / payload
echo "== verify and payload-check"
out=$("$BIN" verify "$WORK/out/init_boot.veritpath.img")
check "verify reports patched"   "$(grep -c '^PATCHED:1$' <<<"$out")" "1"
check "verify finds su"          "$(grep -c '/su  *present' <<<"$out")" "1"
check "verify finds rc hook"     "$(grep -c 'hooks the rc' <<<"$out")" "1"
check "verify prints verdict"    "$(grep -c '^VERDICT:OK$' <<<"$out")" "1"

if "$BIN" verify "$WORK/img/init_boot.img" >/dev/null 2>&1; then
    bad "verify rejects an unpatched image"
else
    ok "verify rejects an unpatched image"
fi

out=$("$BIN" verify "$WORK/out/init_boot.veritpath.img" -p "$PAY")
check "verify honours a payload" "$(grep -c '^VERDICT:OK$' <<<"$out")" "1"

out=$("$BIN" payload-check "$PAY")
check "payload-check names it"   "$(grep -c '^NAME:example-su$' <<<"$out")" "1"
check "payload-check lists files" "$(grep -c '^FILES:1$' <<<"$out")" "1"
check "payload-check lists rc"   "$(grep -c '^RC:/init.veritpath.rc$' <<<"$out")" "1"

# ------------------------------------------------------------------- library
echo "== embeddable library + JNI binding"
# VP_NO_MAIN keeps the CLI's main() out, the way a shared library is built
if cc -O2 -std=c11 -DVP_NO_MAIN -Isrc -c src/util.c -o "$WORK/util.o" 2>/dev/null; then
    ok "sources compile without main() (library build)"
else
    bad "sources compile without main() (library build)"
fi
# the Windows code paths must compile: mkdir() arity, S_ISLNK, memmem, realpath
# are all places a POSIX-only build hides mistakes until someone runs MinGW
if [ -f tools/check_windows.sh ]; then
    if bash tools/check_windows.sh >"$WORK/win.log" 2>&1; then
        ok "windows branch compiles cleanly"
    else
        bad "windows branch compiles cleanly"
        sed 's/^/        /' "$WORK/win.log" | head -10
    fi
fi

# the Windows code paths must compile: mkdir() arity, S_ISLNK, memmem, realpath
# are all places a POSIX-only build hides mistakes until someone runs MinGW
if [ -f tools/check_windows.sh ]; then
    if bash tools/check_windows.sh >"$WORK/win.log" 2>&1; then
        ok "windows branch compiles cleanly"
    else
        bad "windows branch compiles cleanly"
        sed 's/^/        /' "$WORK/win.log" | head -10
    fi
fi

# cross builds must skip cleanly when the target has no zlib, instead of
# dying with a bare "cannot find -lz"
if [ -f tools/ensure_zlib.sh ]; then
    FAKE=$(mktemp -d)
    printf '#!/bin/sh\nexit 1\n' > "$FAKE/aarch64-linux-gnu-gcc"
    chmod 755 "$FAKE/aarch64-linux-gnu-gcc"
    if PATH="$FAKE:$PATH" VP_SKIP_MISSING=1 VP_NO_AUTO_ZLIB=1 \
            bash build.sh linux-aarch64 >"$WORK/x.log" 2>&1; then
        ok "a target without zlib is skipped, not fatal"
    else
        bad "a target without zlib is skipped, not fatal"
        sed 's/^/        /' "$WORK/x.log" | tail -4
    fi
    # and the hint must name the package to install
    if grep -q "zlib1g-dev:arm64" "$WORK/x.log"; then
        ok "the hint names the missing package"
    else
        bad "the hint names the missing package"
    fi
    # 'all' must keep going past a skipped target
    if PATH="$FAKE:$PATH" VP_NO_AUTO_ZLIB=1 bash build.sh all >"$WORK/y.log" 2>&1; then
        ok "'all' survives a skipped target"
    else
        bad "'all' survives a skipped target"
        sed 's/^/        /' "$WORK/y.log" | tail -4
    fi
    rm -rf "$FAKE"
fi

# the library builds must link: -fPIC for the shared one, and the same
# compression DEFS as the binary or the library quietly loses formats
if command -v cc >/dev/null 2>&1; then
    if make lib >"$WORK/lib.log" 2>&1 && [ -f build/libveritpath.a ]; then
        ok "static library builds"
    else
        bad "static library builds"
        sed 's/^/        /' "$WORK/lib.log" | tail -5
    fi
    if make lib-shared >"$WORK/libso.log" 2>&1; then
        if [ -f build/libveritpath.so ]; then
            ok "shared library links (-fPIC applied)"
        else
            bad "shared library produced a .so"
        fi
    else
        bad "shared library links (-fPIC applied)"
        sed 's/^/        /' "$WORK/libso.log" | grep -E "relocation|fPIC|error" | head -5
    fi
    # a real link against the .so: undefined-symbol errors only show up here
    cat > "$WORK/use.c" <<'EOF'
#include <stdio.h>
#include "vp.h"
int main(void){ vp_set_program_name("veritpath");
  char *av[1]; av[0]=(char*)"doctor"; char *o=NULL; int rc=1;
  if(vp_capture_start()==0){ rc=vp_cli_run(1,av); o=vp_capture_stop(); }
  printf("%d %s", rc, o ? (strstr(o,"VERSION:")?"captured":"empty") : "null");
  return !(o && strstr(o,"VERSION:")); }
EOF
    if cc -O2 -std=c11 -Isrc -o "$WORK/use" "$WORK/use.c"             -Lbuild -lveritpath >>"$WORK/lib.log" 2>&1; then
        if LD_LIBRARY_PATH=build "$WORK/use" | grep -q captured; then
            ok "a program can link against the shared library"
        else
            bad "a program can link against the shared library (output)"
        fi
    else
        bad "a program can link against the shared library"
        sed 's/^/        /' "$WORK/lib.log" | tail -4
    fi
    rm -f build/libveritpath.a build/libveritpath.so
    rm -rf build/lib
fi

# an embedder must see errors, not just stdout (the "no output at all" report)
if [ -f tools/test_capture.sh ]; then
    if bash tools/test_capture.sh >"$WORK/cap.log" 2>&1; then
        ok "captured output includes stderr"
    else
        bad "captured output includes stderr"
        sed 's/^/        /' "$WORK/cap.log" | head -8
    fi
fi

# no JDK in every environment, so check the wrapper structurally: every
# native must have a JNI symbol, and required args must be validated
if python3 tools/check_java.py jni/dev/veritpath/Veritpath.java \
        jni/veritpath_jni.c >"$WORK/java.log" 2>&1; then
    ok "Java wrapper matches the JNI layer"
else
    bad "Java wrapper matches the JNI layer"
    sed 's/^/        /' "$WORK/java.log" | head -8
fi

if [ -f tools/make_stub_jni.py ] && command -v python3 >/dev/null 2>&1; then
    if bash tools/test_jni.sh "$BIN" >"$WORK/jni.log" 2>&1; then
        ok "JNI binding runs the CLI and returns output"
    else
        bad "JNI binding runs the CLI and returns output"
        sed 's/^/        /' "$WORK/jni.log" | head -10
    fi
fi

# ------------------------------------------------- bare image path (no flag)
echo "== bare positional image"
out=$("$BIN" analyze --brief "$WORK/img/init_boot.img")
check "bare path is used"          "$(grep -c '^HEADER_VER:' <<<"$out")" "1"
check "bare path gets a role"      "$(grep -c '^TARGET:init_boot$' <<<"$out")" "1"

if "$BIN" inject "$WORK/img/init_boot.img" -p "$PAY" -o "$WORK/bare" \
        >"$WORK/bare.log" 2>&1; then
    ok "inject accepts a bare path"
else
    bad "inject accepts a bare path"
    sed 's/^/        /' "$WORK/bare.log" | head -5
fi
check "bare inject produced an image" \
    "$("$BIN" verify "$WORK/bare/init_boot.veritpath.img" | grep -c '^VERDICT:OK$')" "1"

# ------------------------------------------------- no-ramdisk (system-as-root)
echo "== boot.img with no ramdisk"
out=$("$BIN" analyze --brief --boot "$WORK/img/boot.img")
check "layout is no_ramdisk"     "$(grep -c '^LAYOUT:no_ramdisk$' <<<"$out")" "1"
check "needs_ramdisk reported"   "$(grep -c '^NEEDS_RAMDISK:1$' <<<"$out")" "1"
check "target is still boot"     "$(grep -c '^TARGET:boot$' <<<"$out")" "1"

# refusing must be a failure, not a silent 0
if "$BIN" inject --boot "$WORK/img/boot.img" -p "$PAY" -o "$WORK/sar_no" \
        >"$WORK/sar_no.log" 2>&1; then
    bad "inject refuses without --create-ramdisk"
else
    ok "inject refuses without --create-ramdisk"
fi
# the message mentions --create-ramdisk at least once
if [ "$(grep -c 'create-ramdisk' "$WORK/sar_no.log")" -ge 1 ]; then
    ok "the refusal says why"
else
    bad "the refusal says why"
fi

if "$BIN" inject --boot "$WORK/img/boot.img" -p "$PAY" --create-ramdisk \
        -o "$WORK/sar" >"$WORK/sar.log" 2>&1; then
    ok "inject creates a ramdisk"
else
    bad "inject creates a ramdisk"
    sed 's/^/        /' "$WORK/sar.log" | head -6
fi
check "created ramdisk is reported" \
    "$(grep -c 'no ramdisk - creating one' "$WORK/sar.log")" "1"
check "placeholder init is flagged" \
    "$(grep -c 'placeholder /init' "$WORK/sar.log")" "1"

out=$("$BIN" analyze --brief "$WORK/sar/boot.veritpath.img")
check "patched image now has a ramdisk" "$(grep -c '^RAMDISK_SZ:' <<<"$out")" "1"
check "patched image targets boot"     "$(grep -c '^TARGET:boot$' <<<"$out")" "1"
check "patched image is marked"        "$(grep -c '^PATCHED:1$' <<<"$out")" "1"

"$BIN" unpack "$WORK/sar/boot.veritpath.img" -d "$WORK/sarw" >/dev/null 2>&1
for d in dev proc sys system data mnt; do
    check "skeleton has /$d" "$(test -d "$WORK/sarw/ramdisk/$d" && echo 1 || echo 0)" "1"
done
check "skeleton has init.rc" \
    "$(test -f "$WORK/sarw/ramdisk/init.rc" && echo 1 || echo 0)" "1"
check "skeleton has file_contexts" \
    "$(test -f "$WORK/sarw/ramdisk/file_contexts" && echo 1 || echo 0)" "1"

# a payload that supplies its own init must replace the placeholder
mkdir -p "$WORK/pinit"
printf '#!/system/bin/sh\nexec "$@"\n' > "$WORK/pinit/init"
cat > "$WORK/pinit/manifest.json" <<'JSON'
{"name":"with-init","arch":["arm64"],
 "files":[{"src":"init","dest":"/init","mode":"0755","required":true}],
 "rc":{"file":"/init.veritpath.rc","import_into":["/init.rc"],
       "content":"on post-fs-data\n    chmod 0755 /su\n"}}
JSON
if "$BIN" inject --boot "$WORK/img/boot.img" -p "$WORK/pinit" --create-ramdisk \
        -o "$WORK/sar2" >"$WORK/sar2.log" 2>&1; then
    ok "inject with a payload-supplied init"
else
    bad "inject with a payload-supplied init"
    sed 's/^/        /' "$WORK/sar2.log" | head -6
fi
check "no placeholder warning now" \
    "$(grep -c 'placeholder /init' "$WORK/sar2.log")" "0"
check "its own init is in place" \
    "$("$BIN" verify "$WORK/sar2/boot.veritpath.img" -p "$WORK/pinit" | grep -c '^VERDICT:OK$')" "1"

# ------------------------------------------- whole-partition dumps (dd)
echo "== partition dump: trailing padding"
python3 "$KIT" partition "$WORK/part" >/dev/null
PART=$WORK/part/partition_dump.img

out=$("$BIN" analyze --brief --boot "$PART")
check "trailing is reported"     "$(grep -c '^TRAILING.boot:' <<<"$out")" "1"
check "image still parses"       "$(grep -c '^HEADER_VER:4$' <<<"$out")" "1"
"$BIN" analyze --boot "$PART" | grep -q 'not part of the boot image'
check "the report explains it"   "$?" "0"

# default: padding dropped, output equals the real image
"$BIN" inject --boot "$PART" -p "$PAY" -o "$WORK/pd1" >"$WORK/pd1.log" 2>&1
check "dropped padding is logged" \
    "$(grep -c 'padding after the image was dropped' "$WORK/pd1.log")" "1"
REAL=$(stat -c %s "$WORK/part/real.img")
OUT1=$(stat -c %s "$WORK/pd1/partition_dump.veritpath.img")
# same content, maybe a few bytes different after recompression
if [ $(( REAL > OUT1 ? REAL - OUT1 : OUT1 - REAL )) -lt $(( 4 << 20 )) ]; then
    ok "output matches the real image size ($REAL vs $OUT1)"
else
    bad "output matches the real image size ($REAL vs $OUT1)"
fi
check "padded image still verifies" \
    "$("$BIN" verify "$WORK/pd1/partition_dump.veritpath.img" | grep -c '^VERDICT:OK$')" "1"

# --keep-trailing carries it over
"$BIN" inject --boot "$PART" -p "$PAY" --keep-trailing -o "$WORK/pd2" >/dev/null 2>&1
OUT2=$(stat -c %s "$WORK/pd2/partition_dump.veritpath.img")
PART_SZ=$(stat -c %s "$PART")
check "trailing kept: same size"  "$([ "$OUT2" = "$PART_SZ" ] && echo 1 || echo 0)" "1"
check "kept version still verifies" \
    "$("$BIN" verify "$WORK/pd2/partition_dump.veritpath.img" | grep -c '^VERDICT:OK$')" "1"

# a normal image must not claim trailing bytes
check "normal image has no trailing" \
    "$("$BIN" analyze --brief --boot "$WORK/img/boot.img" | grep -c '^TRAILING')" "0"

# ------------------------------------------------- unpack / repack (component level)
echo "== unpack into components, repack"
"$BIN" unpack "$WORK/img/init_boot.img" -d "$WORK/u1" >"$WORK/u1.log" 2>&1
check "unpack reports the header"  "$(grep -c 'header' "$WORK/u1.log")" "1"
check "unpack reports the ramdisk" "$(grep -c 'ramdisk' "$WORK/u1.log")" "1"
for f in original.img header.bin image.json ramdisk.cpio; do
    check "unpack wrote $f" \
        "$(test -f "$WORK/u1/$f" && echo 1 || echo 0)" "1"
done
check "ramdisk tree extracted" "$(test -d "$WORK/u1/ramdisk" && echo 1 || echo 0)" "1"
check "image.json lists components" \
    "$(grep -c '"components"' "$WORK/u1/image.json")" "1"

if "$BIN" repack "$WORK/u1" -o "$WORK/u1.img" >"$WORK/u1r.log" 2>&1; then
    ok "repack a component directory"
else
    bad "repack a component directory"
    sed 's/^/        /' "$WORK/u1r.log" | head -5
fi
check "round trip keeps the header" \
    "$("$BIN" analyze --brief "$WORK/u1.img" | grep -c '^HEADER_VER:4$')" "1"
check "round trip keeps the ramdisk" \
    "$("$BIN" analyze --brief "$WORK/u1.img" | grep -c '^SEGMENTS:1$')" "1"

# editing the tree must survive a repack
echo "hand-made" > "$WORK/u1/ramdisk/hello.txt"
"$BIN" repack "$WORK/u1" -o "$WORK/u2.img" >/dev/null 2>&1
"$BIN" unpack "$WORK/u2.img" -d "$WORK/u2" >/dev/null 2>&1
check "an added file survives" \
    "$(test -f "$WORK/u2/ramdisk/hello.txt" && echo 1 || echo 0)" "1"

# replacing a component (kernel) must take effect
"$BIN" unpack "$WORK/img/boot.img" -d "$WORK/u3" >/dev/null 2>&1
printf 'NEWKERNEL' > "$WORK/u3/kernel"
"$BIN" repack "$WORK/u3" -o "$WORK/u3.img" >/dev/null 2>&1
check "a replaced kernel is used" \
    "$("$BIN" analyze --brief "$WORK/u3.img" | grep -c '^KERNEL_SZ:9$')" "1"

# vendor_boot keeps its fragments through unpack/repack
"$BIN" unpack "$WORK/img/vendor_boot.img" -d "$WORK/uv" >/dev/null 2>&1
check "vendor segments extracted" \
    "$(test -d "$WORK/uv/ramdisk/segment1" && echo 1 || echo 0)" "1"
check "per-segment cpio written" \
    "$(test -f "$WORK/uv/ramdisk-1.cpio" && echo 1 || echo 0)" "1"
if "$BIN" repack "$WORK/uv" -o "$WORK/uv.img" >/dev/null 2>&1; then
    ok "repack a vendor_boot directory"
else
    bad "repack a vendor_boot directory"
fi
if python3 "$KIT" verify-frags "$WORK/uv.img" >/dev/null 2>&1; then
    ok "vendor fragments survive unpack/repack"
else
    bad "vendor fragments survive unpack/repack"
fi

# --------------------------------------------- untrusted input must not escape
echo "== path traversal"
python3 "$KIT" evil "$WORK/evil" >/dev/null
rm -f /veritpath_escape_test /veritpath_escape_pay 2>/dev/null || true
"$BIN" unpack "$WORK/evil/traversal.img" -d "$WORK/evil/work" \
    >"$WORK/ev.log" 2>&1 || true
if [ -e /veritpath_escape_test ]; then
    bad "a traversing cpio entry cannot escape the work dir"
else
    ok "a traversing cpio entry cannot escape the work dir"
fi
check "the escape attempt is reported" \
    "$(grep -c 'escapes the output directory' "$WORK/ev.log")" "1"

rm -f /veritpath_symlink_pwn 2>/dev/null || true
"$BIN" unpack "$WORK/evil/symlink_escape.img" -d "$WORK/evil/sw" \
    >/dev/null 2>&1 || true
if [ -e /veritpath_symlink_pwn ]; then
    bad "writing an entry does not follow a planted symlink"
else
    ok "writing an entry does not follow a planted symlink"
fi
# and the entry itself still lands, as a plain file
if [ -f "$WORK/evil/sw/ramdisk/pwn" ] && [ ! -L "$WORK/evil/sw/ramdisk/pwn" ]; then
    ok "the replacing entry is written as a plain file"
else
    bad "the replacing entry is written as a plain file"
fi

if "$BIN" payload-check "$WORK/evil/evilpayload" >"$WORK/ev2.log" 2>&1; then
    bad "a traversing payload dest is rejected"
else
    ok "a traversing payload dest is rejected"
fi
check "payload escape is named" \
    "$(grep -c 'escapes the ramdisk root' "$WORK/ev2.log")" "1"

# ------------------------------------------------- numeric options are validated
echo "== numeric options"
for bad_opt in "--header-version xyz" "--header-version 99" "--format nosuchfmt"; do
    if "$BIN" analyze --brief --boot "$WORK/img/boot.img" $bad_opt \
            >"$WORK/num.log" 2>&1; then
        bad "analyze $bad_opt is rejected"
    else
        ok "analyze $bad_opt is rejected"
    fi
done
if "$BIN" inject --init-boot "$WORK/img/init_boot.img" -p "$PAY" \
        --segment abc -o "$WORK/seg1" >"$WORK/num.log" 2>&1; then
    bad "inject --segment abc is rejected"
else
    ok "inject --segment abc is rejected"
fi
if "$BIN" inject --vendor-boot "$WORK/img/vendor_boot.img" -p "$PAY" \
        --segment 99 -o "$WORK/seg2" >"$WORK/num.log" 2>&1; then
    bad "inject --segment 99 (out of range) is rejected"
else
    ok "inject --segment 99 (out of range) is rejected"
fi
check "out-of-range segment names the count" \
    "$(grep -c 'out of range' "$WORK/num.log")" "1"
# a valid one still works
if "$BIN" inject --vendor-boot "$WORK/img/vendor_boot.img" -p "$PAY" \
        --segment 1 -o "$WORK/seg3" >/dev/null 2>&1; then
    ok "a valid --segment still works"
else
    bad "a valid --segment still works"
fi

# --------------------------------------------- untrusted input must not escape
echo "== path traversal"
python3 "$KIT" evil "$WORK/evil" >/dev/null
rm -f /veritpath_escape_test /veritpath_escape_pay 2>/dev/null
"$BIN" unpack "$WORK/evil/traversal.img" -d "$WORK/evil/work" >"$WORK/ev.log" 2>&1
if [ -e /veritpath_escape_test ]; then
    bad "a traversing cpio entry cannot escape the work dir"
else
    ok "a traversing cpio entry cannot escape the work dir"
fi
check "the escape attempt is reported" \
    "$(grep -c 'escapes the output directory' "$WORK/ev.log")" "1"

if "$BIN" payload-check "$WORK/evil/evilpayload" >"$WORK/ev2.log" 2>&1; then
    bad "a traversing payload dest is rejected"
else
    ok "a traversing payload dest is rejected"
fi
check "payload escape is named" \
    "$(grep -c 'escapes the ramdisk root' "$WORK/ev2.log")" "1"

# ------------------------------------------------- numeric options are validated
echo "== numeric options"
for bad_opt in "--header-version xyz" "--header-version 99" "--format nosuchfmt"; do
    if "$BIN" analyze --brief --boot "$WORK/img/boot.img" $bad_opt \
            >"$WORK/num.log" 2>&1; then
        bad "analyze $bad_opt is rejected"
    else
        ok "analyze $bad_opt is rejected"
    fi
done
if "$BIN" inject --init-boot "$WORK/img/init_boot.img" -p "$PAY" \
        --segment abc -o "$WORK/seg1" >"$WORK/num.log" 2>&1; then
    bad "inject --segment abc is rejected"
else
    ok "inject --segment abc is rejected"
fi
if "$BIN" inject --vendor-boot "$WORK/img/vendor_boot.img" -p "$PAY" \
        --segment 99 -o "$WORK/seg2" >"$WORK/num.log" 2>&1; then
    bad "inject --segment 99 (out of range) is rejected"
else
    ok "inject --segment 99 (out of range) is rejected"
fi
check "out-of-range segment names the count" \
    "$(grep -c 'out of range' "$WORK/num.log")" "1"
# a valid one still works
if "$BIN" inject --vendor-boot "$WORK/img/vendor_boot.img" -p "$PAY" \
        --segment 1 -o "$WORK/seg3" >/dev/null 2>&1; then
    ok "a valid --segment still works"
else
    bad "a valid --segment still works"
fi

# ------------------------------------------------------------------ errors
echo "== error handling"
head -c 8192 /dev/zero > "$WORK/junk.img"
"$BIN" analyze --boot "$WORK/junk.img" >/dev/null 2>&1 && rc=0 || rc=$?
check "rejects junk image" "$rc" "1"
"$BIN" bogus-command >/dev/null 2>&1 && rc=0 || rc=$?
check "rejects bad command" "$rc" "1"
check "version prints" "$("$BIN" --version)" "veritpath 0.2.0"
check "doctor reports ok"  "$("$BIN" doctor | grep -c 'VERDICT:OK')" "1"
# invoked as ./name from a dir that is not on PATH, doctor must say so
check "doctor flags off-PATH use" \
    "$("$BIN" doctor | grep -c '^ON_PATH:0$')" "1"

head -c 4096 /dev/urandom > "$WORK/random.img"
err=$("$BIN" analyze --boot "$WORK/random.img" 2>&1 || true)
check "explains no magic"   "$(grep -c 'no ANDROID' <<<"$err")" "1"
printf '\x3a\xff\x26\xed' > "$WORK/sparse.img"
head -c 4096 /dev/zero >> "$WORK/sparse.img"
err=$("$BIN" analyze --boot "$WORK/sparse.img" 2>&1 || true)
check "names sparse images" "$(grep -c 'simg2img' <<<"$err")" "1"
err=$("$BIN" analyze --boot "$WORK/missing.img" 2>&1 || true)
check "explains missing file" "$(grep -c 'looked for' <<<"$err")" "1"

echo
echo "== $pass passed, $fail failed =="
[ "$fail" = 0 ]
