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
