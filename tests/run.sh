#!/usr/bin/env bash
# End-to-end regression test for the veritpath binary.
#
#   ./tests/run.sh [path-to-binary]
#
# Generates synthetic Android images with the reference Python implementation
# (../veritpath), runs the C binary against them and verifies the patched
# output with Python - two independent implementations checking each other.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${1:-$ROOT/build/veritpath}"
PYPROJ="${VERITPATH_PY:-$ROOT/../veritpath}"
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
if [ -d "$PYPROJ" ] && python3 -c "import sys; sys.path.insert(0,'$PYPROJ'); import veritpath" 2>/dev/null; then
    python3 "$PYPROJ/scripts/make_sample_images.py" "$WORK/img" >/dev/null
    python3 - "$PYPROJ" "$WORK/img" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from tests.fixtures import make_legacy_boot
from pathlib import Path
out = Path(sys.argv[2])
for hv in (1, 2):
    out.joinpath("legacy%d.img" % hv).write_bytes(make_legacy_boot(hv))
PY
    HAVE_PY=1
else
    echo "  (python reference project not available - skipping generated images)"
    HAVE_PY=0
fi

if [ "$HAVE_PY" = 1 ]; then
    IMG="$WORK/img"
    for f in boot.img init_boot.img vendor_boot.img legacy1.img legacy2.img; do
        [ -s "$IMG/$f" ] || { echo "missing $f" >&2; exit 1; }
    done
    ok "synthetic images generated"

    # ------------------------------------------------------------ analyze
    echo "== analyze"
    out=$("$BIN" analyze --boot "$IMG/boot.img" --init-boot "$IMG/init_boot.img" \
          --vendor-boot "$IMG/vendor_boot.img")
    check "arch detected"        "$(grep -c 'arm64' <<<"$out")" "1"
    check "layout is init_boot"  "$(grep -c 'ramdisk_layout         init_boot' <<<"$out")" "1"
    check "target is init_boot"  "$(grep -c 'injection target     init_boot' <<<"$out")" "1"
    check "system-as-root"       "$(grep -c 'system_as_root  *true' <<<"$out")" "1"

    out=$("$BIN" analyze --boot "$IMG/boot.img" --init-boot "$IMG/init_boot.img" --json)
    check "json output parses"   "$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["target"])' <<<"$out")" "init_boot"

    out=$("$BIN" analyze --boot "$IMG/boot.img" --vendor-boot "$IMG/vendor_boot.img")
    check "vendor_boot layout"   "$(grep -c 'ramdisk_layout         vendor_boot' <<<"$out")" "1"

    # --------------------------------------------------------------- plan
    echo "== plan"
    out=$("$BIN" plan --init-boot "$IMG/init_boot.img" -p "$PYPROJ/payloads/example-su" --permissive)
    check "plan lists payload"   "$(grep -c 'payload-file' <<<"$out")" "1"
    check "plan has selinux"     "$(grep -c 'androidboot.selinux=permissive' <<<"$out")" "1"
    check "plan is read only"    "$([ -e "$IMG/init_boot.veritpath.img" ] && echo yes || echo no)" "no"

    # ------------------------------------------------------------- inject
    echo "== inject"
    out=$("$BIN" inject --boot "$IMG/boot.img" --init-boot "$IMG/init_boot.img" \
          -p "$PYPROJ/payloads/example-su" --permissive -o "$WORK/out")
    check "output written"       "$([ -s "$WORK/out/init_boot.veritpath.img" ] && echo yes || echo no)" "yes"
    check "boot.img untouched"   "$([ -e "$WORK/out/boot.veritpath.img" ] && echo yes || echo no)" "no"
    check "backup kept"          "$([ -e "$IMG/init_boot.img.veritpath.bak" ] && echo yes || echo no)" "yes"

    python3 - "$PYPROJ" "$WORK/out/init_boot.veritpath.img" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from veritpath.bootimg import BootImage
img = BootImage.parse(open(sys.argv[2], "rb").read(), "init_boot")
a = img.ramdisk_archive()
assert a.find("/su") is not None, "su missing"
assert a.find("/init.veritpath.rc") is not None, "rc missing"
assert a.find("/veritpath.json") is not None, "marker missing"
assert a.find("/init") is not None, "original init lost"
assert a.find("/su").perms == 0o755, "wrong su permissions"
assert "androidboot.selinux=permissive" in img.full_cmdline, "cmdline not patched"
assert b"import /init.veritpath.rc" in a.find("/init.rc").data, "init.rc not hooked"
assert b"u:object_r:rootfs:s0" in a.find("/file_contexts").data, "file_contexts not patched"
print("  ok   python verifies patched init_boot")
PY
    pass=$((pass + 1))

    # --------------------------------------------------- vendor fragments
    echo "== vendor_boot"
    "$BIN" inject --boot "$IMG/boot.img" --init-boot "$IMG/init_boot.img" \
        --vendor-boot "$IMG/vendor_boot.img" -p "$PYPROJ/payloads/example-su" \
        --patch-vendor-boot -o "$WORK/out2" >/dev/null
    python3 - "$PYPROJ" "$WORK/out2/vendor_boot.veritpath.img" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from veritpath.bootimg import VendorBootImage
v = VendorBootImage.parse(open(sys.argv[2], "rb").read())
a = v.ramdisk_archive()
assert len(a.segments) == 2, "wrong segment count"
assert all(s.find("/su") is not None for s in a.segments), "fragment not patched"
assert sum(e.size for e in v.table) == len(v.ramdisk), "fragment table out of sync"
print("  ok   python verifies patched vendor_boot")
PY
    pass=$((pass + 1))

    # ------------------------------------------------------- legacy images
    echo "== legacy headers"
    for hv in 1 2; do
        "$BIN" inject --boot "$IMG/legacy$hv.img" -p "$PYPROJ/payloads/example-su" \
            -o "$WORK/out3" >/dev/null
        python3 - "$PYPROJ" "$WORK/out3/legacy$hv.veritpath.img" "$hv" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from veritpath.bootimg import BootImage
img = BootImage.parse(open(sys.argv[2], "rb").read(), "boot")
assert img.header_version == int(sys.argv[3]), "header version changed"
a = img.ramdisk_archive()
assert a.find("/su") is not None, "su missing"
assert len(img.kernel) > 0, "kernel lost"
print(f"  ok   legacy v{sys.argv[3]} round-trip")
PY
        pass=$((pass + 1))
    done

    # --------------------------------------------------------- compression
    echo "== compression"
    "$BIN" inject --init-boot "$IMG/init_boot.img" -p "$PYPROJ/payloads/example-su" \
        --format lz4_legacy -o "$WORK/out4" >/dev/null
    python3 - "$PYPROJ" "$WORK/out4/init_boot.veritpath.img" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from veritpath.bootimg import BootImage
img = BootImage.parse(open(sys.argv[2], "rb").read(), "init_boot")
assert img.ramdisk_format == "lz4_legacy", img.ramdisk_format
assert img.ramdisk_archive().find("/su") is not None
print("  ok   forced lz4_legacy compression")
PY
    pass=$((pass + 1))

    # ------------------------------------------------------- unpack/repack
    echo "== unpack / repack"
    "$BIN" unpack "$IMG/init_boot.img" -d "$WORK/work" >/dev/null
    check "ramdisk extracted" "$([ -f "$WORK/work/ramdisk/init" ] && echo yes || echo no)" "yes"
    check "original kept"     "$([ -f "$WORK/work/original.img" ] && echo yes || echo no)" "yes"
    echo "handmade" > "$WORK/work/ramdisk/handmade.txt"
    "$BIN" repack "$WORK/work" -o "$WORK/repacked.img" >/dev/null
    python3 - "$PYPROJ" "$WORK/repacked.img" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from veritpath.bootimg import BootImage
img = BootImage.parse(open(sys.argv[2], "rb").read(), "init_boot")
a = img.ramdisk_archive()
assert a.find("/handmade.txt").data == b"handmade\n", "handmade file lost"
assert a.find("/init") is not None, "init lost"
print("  ok   python verifies repacked image")
PY
    pass=$((pass + 1))
fi

# ------------------------------------------------------- real-world quirks
echo "== header quirks (vendor tools / odd dumps)"
mkdir -p "$WORK/quirks"
python3 - "$PYPROJ" "$WORK/quirks" <<'PYEOF'
import gzip, struct, sys
sys.path.insert(0, sys.argv[1])
from tests.fixtures import make_init_boot
base = make_init_boot()
def patch(off, val):
    b = bytearray(base); struct.pack_into("<I", b, off, val); return bytes(b)
d = sys.argv[2] + "/"
open(d + "hs_padded.img", "wb").write(patch(20, 4096))
open(d + "hs_zero.img", "wb").write(patch(20, 0))
open(d + "hs_garbage.img", "wb").write(patch(20, 65536))
open(d + "hv_future.img", "wb").write(patch(24, 5))
open(d + "prefix.img", "wb").write(bytes(4096) + base)
open(d + "gzip.img", "wb").write(gzip.compress(base))
PYEOF
for f in hs_padded hs_zero hs_garbage hv_future prefix gzip; do
    if "$BIN" inject --init-boot "$WORK/quirks/$f.img" \
        -p "$PYPROJ/payloads/example-su" -o "$WORK/qout" >/dev/null 2>&1; then
        ok "tolerates $f"
    else
        bad "tolerates $f"
    fi
done
python3 - "$PYPROJ" "$WORK/qout" <<'PYEOF'
import sys, os
sys.path.insert(0, sys.argv[1])
from veritpath.bootimg import BootImage
for name in sorted(os.listdir(sys.argv[2])):
    img = BootImage.parse(open(os.path.join(sys.argv[2], name), "rb").read(), "init_boot")
    a = img.ramdisk_archive()
    assert a.find("/su") is not None, name
    assert b"import /init.veritpath.rc" in a.find("/init.rc").data, name
print("  ok   python verifies every quirky header was patched correctly")
PYEOF
pass=$((pass + 1))

# ------------------------------------------------------------------ errors
echo "== error handling"
head -c 8192 /dev/zero > "$WORK/junk.img"
"$BIN" analyze --boot "$WORK/junk.img" >/dev/null 2>&1 && rc=0 || rc=$?
check "rejects junk image" "$rc" "1"
"$BIN" bogus-command >/dev/null 2>&1 && rc=0 || rc=$?
check "rejects bad command" "$rc" "1"
check "version prints" "$("$BIN" --version)" "veritpath 0.2.0"

head -c 4096 /dev/urandom > "$WORK/random.img"
err=$("$BIN" analyze --boot "$WORK/random.img" 2>&1 || true)
check "explains no magic"   "$(grep -c 'no ANDROID' <<<"$err")" "1"
printf '\x3a\xff\x26\xed' > "$WORK/sparse.img"
head -c 4096 /dev/zero >> "$WORK/sparse.img"
err=$("$BIN" analyze --boot "$WORK/sparse.img" 2>&1 || true)
check "names sparse images" "$(grep -c 'simg2img' <<<"$err")" "1"

echo
echo "== $pass passed, $fail failed =="
[ "$fail" = 0 ]
