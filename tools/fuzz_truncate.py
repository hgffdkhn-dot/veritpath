"""Truncation fuzzer: every header offset is a fixed position in the file.

Feeding short files used to read past the buffer (heap-buffer-overflow in
rd32 at offsets 1564/1632/1644/1648/2108..). This walks every interesting
cut point of a real image of each header version, under ASan when available.

usage: python3 tools/fuzz_truncate.py [binary]
"""
import os
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix="vp-fuzz-")
shutil.copytree(ROOT + "/payloads", WORK + "/payloads")
sys.path.insert(0, ROOT + "/tests")
import imgkit

BIN = os.environ.get("VP_BIN", ROOT + "/build/veritpath")
if len(sys.argv) > 1:
    BIN = sys.argv[1]
random.seed(7)
imgkit.cmd_images(WORK)
crashes = []

targets = {
    "legacy1": WORK + "/legacy1.img",
    "legacy2": WORK + "/legacy2.img",
    "vendor":  WORK + "/vendor_boot.img",
    "boot":    WORK + "/boot.img",
    "init":    WORK + "/init_boot.img",
}
for name, path in targets.items():
    good = open(path, "rb").read()
    # every truncation boundary, plus random ones
    cuts = set([0,1,7,8,16,32,63,64,65,100,127,128,255,256,511,512,600,608,
                1023,1024,1536,1560,1564,1568,1632,1636,1644,1648,1652,2048,
                2080,2096,2100,2112,4095,4096,4097])
    cuts |= set(random.randint(0, len(good)) for _ in range(120))
    for cut in sorted(c for c in cuts if c <= len(good)):
        open(WORK + "/t.img", "wb").write(good[:cut])
        for args in (["analyze", "--brief", "--boot", WORK + "/t.img"],
                     ["unpack", WORK + "/t.img", "-d", WORK + "/w"],
                     ["inject", "--boot", WORK + "/t.img", "-p", ROOT + "/payloads/example-su",
                      "-o", WORK + "/out", "--no-backup"]):
            r = subprocess.run([BIN] + args, capture_output=True, timeout=25)
            err = r.stderr
            if b"Sanitizer" in err or b"runtime error" in err:
                crashes.append((name, cut, args[0], r.returncode,
                                err[:150].decode("utf8", "replace")))
    print("%-8s done, crashes so far %d" % (name, len(crashes)))

print("TOTAL crashes:", len(crashes))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if crashes else 0)
for c in crashes[:8]:
    print(" ", c)
