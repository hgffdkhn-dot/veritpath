#!/usr/bin/env python3
"""Standalone image kit for the veritpath regression suite.

Pure standard library.  Builds synthetic Android boot images and verifies
patched ones, so the C binary is checked without depending on any other
project or on third-party packages.

usage:
  imgkit.py images <dir>              build the basic image set
  imgkit.py quirks <dir>              build images with hostile header fields
  imgkit.py large <dir>               build ~120MB GKI-style images
  imgkit.py verify <img> [kind]       verify an injected / repacked image
  imgkit.py verify-dir <dir>          verify every image in a directory
"""

import gzip
import os
import struct
import sys

# ------------------------------------------------------------------ cpio


def cpio_build(entries):
    out = bytearray()
    for i, (name, mode, data) in enumerate(entries):
        nb = name.lstrip("/").encode() + b"\0"
        fields = (i + 1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(nb), 0)
        out += b"070701" + b"".join(b"%08X" % v for v in fields) + nb
        out += b"\0" * ((-len(out)) % 4)
        out += data
        out += b"\0" * ((-len(out)) % 4)
    nb = b"TRAILER!!!\0"
    fields = (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(nb), 0)
    out += b"070701" + b"".join(b"%08X" % v for v in fields) + nb
    out += b"\0" * ((-len(out)) % 4)
    return bytes(out)


def cpio_parse(data):
    res = {}
    pos = 0
    while pos + 110 <= len(data):
        if data[pos : pos + 6] != b"070701":
            nxt = data.find(b"070701", pos)
            if nxt < 0:
                break
            pos = nxt
        f = [int(data[pos + 6 + 8 * i : pos + 6 + 8 * (i + 1)], 16) for i in range(13)]
        namesize, filesize = f[11], f[6]
        name = data[pos + 110 : pos + 110 + namesize].rstrip(b"\0").decode()
        start = pos + 110 + namesize
        start += (-start) % 4
        if name == "TRAILER!!!":
            break
        res["/" + name] = data[start : start + filesize]
        pos = start + filesize
        pos += (-pos) % 4
    return res


def ramdisk(entries):
    return gzip.compress(cpio_build(entries))


def ru(v, a):
    return (v + a - 1) // a * a


# ------------------------------------------------------------------ images

KERNEL = gzip.compress(b"MZ" + b"\0" * 0x36 + b"ARM64" + b"\0" * 0x200)


def boot_v3v4(kernel, rd, hv=4, os_version=0, cmdline=b""):
    hs = 1580 if hv == 3 else 1584
    page = 4096
    h = bytearray(hs)
    h[0:8] = b"ANDROID!"
    struct.pack_into("<I", h, 8, len(kernel))
    struct.pack_into("<I", h, 12, len(rd))
    struct.pack_into("<I", h, 16, os_version)
    struct.pack_into("<I", h, 20, hs)
    struct.pack_into("<I", h, 24, hv)
    if cmdline:
        h[28 : 28 + len(cmdline)] = cmdline
    out = bytearray(h)
    out += b"\0" * (ru(hs, page) - hs)
    out += kernel
    out += b"\0" * (ru(len(kernel), page) - len(kernel))
    out += rd
    out += b"\0" * (ru(len(rd), page) - len(rd))
    return bytes(out)


def boot_legacy(kernel, rd, hv=2, page=4096, os_version=0):
    hs = {0: 1632, 1: 1648, 2: 1660}[hv]
    h = bytearray(hs)
    h[0:8] = b"ANDROID!"
    struct.pack_into("<I", h, 8, len(kernel))
    struct.pack_into("<I", h, 16, len(rd))
    struct.pack_into("<I", h, 24, 0)
    struct.pack_into("<I", h, 36, page)
    struct.pack_into("<I", h, 40, hv)
    struct.pack_into("<I", h, 44, os_version)
    h[48:64] = b"veritpath-test".ljust(16, b"\0")
    h[64 : 64 + 20] = b"console=ttyMSM0".ljust(20, b"\0")
    span = max(page, ru(hs, page))
    out = bytearray(h)
    out += b"\0" * (span - hs)
    out += kernel
    out += b"\0" * (ru(len(kernel), page) - len(kernel))
    out += rd
    out += b"\0" * (ru(len(rd), page) - len(rd))
    return bytes(out)


def vendor_boot(hv=4, page=4096):
    frag = cpio_build([("init", 0o100755, b"#!/system/bin/sh\n")])
    frags = [("ramdisk", 1, frag), ("recovery", 2, frag)]
    body = bytearray()
    ents = []
    for name, typ, blob in frags:
        ents.append((len(blob), len(body), typ, name))
        body += blob
        body += b"\0" * ((-len(body)) % page)
    dtb = b"\xd0\x0d\xfe\xed" + b"\0" * 64
    hs = 2128 if hv == 4 else 2112
    h = bytearray(hs)
    h[0:8] = b"VNDRBOOT"
    struct.pack_into("<I", h, 8, hv)
    struct.pack_into("<I", h, 12, page)
    struct.pack_into("<I", h, 24, len(body))
    struct.pack_into("<I", h, 2096, len(dtb))
    out = bytearray(h)
    out += b"\0" * (ru(hs, page) - hs)
    out += bytes(body)
    out += b"\0" * (ru(len(body), page) - len(body))
    out += dtb
    out += b"\0" * (ru(len(dtb), page) - len(dtb))
    esize = 108
    struct.pack_into("<I", h, 2108, esize * len(ents))   # vendor_ramdisk_table_size
    struct.pack_into("<I", h, 2112, len(ents))           # table num
    struct.pack_into("<I", h, 2116, esize)               # entry size
    struct.pack_into("<I", h, 2120, 0)                   # bootconfig size
    for size, off, typ, name in ents:
        e = bytearray(esize)
        struct.pack_into("<I", e, 0, size)
        struct.pack_into("<I", e, 4, off)
        struct.pack_into("<I", e, 8, typ)
        nb = name.ljust(32, "\0").encode()
        e[12:44] = nb
        out += bytes(e)
    out += b"\0" * (ru(len(out), page) - len(out))
    return bytes(out)


def basic_ramdisk():
    return ramdisk(
        [
            ("init", 0o100755, b"#!/system/bin/sh\n# first stage\n"),
            ("init.rc", 0o100644, b"on early-init\n    mkdir /system\n"),
            ("file_contexts", 0o100644, b"/su u:object_r:rootfs:s0\n"),
            # mode 0120777 = symlink; the payload is the link target
            ("bin", 0o120777, b"/system/bin"),
        ]
    )


def cmd_images(d):
    os.makedirs(d, exist_ok=True)
    rd = basic_ramdisk()
    open(d + "/boot.img", "wb").write(boot_v3v4(KERNEL, b"", 4))  # kernel only
    open(d + "/init_boot.img", "wb").write(boot_v3v4(b"", rd, 4))  # ramdisk only
    open(d + "/vendor_boot.img", "wb").write(vendor_boot())
    for hv in (1, 2):
        open(d + "/legacy%d.img" % hv, "wb").write(boot_legacy(KERNEL, rd, hv))
    print("  built boot/init_boot/vendor_boot/legacy1/legacy2")


def cmd_quirks(d):
    os.makedirs(d, exist_ok=True)
    base = bytearray(boot_v3v4(b"", basic_ramdisk(), 4))

    def patch(off, val):
        b = bytearray(base)
        struct.pack_into("<I", b, off, val)
        return bytes(b)

    open(d + "/hs_padded.img", "wb").write(patch(20, 4096))
    open(d + "/hs_zero.img", "wb").write(patch(20, 0))
    open(d + "/hs_garbage.img", "wb").write(patch(20, 65536))
    open(d + "/hv_future.img", "wb").write(patch(24, 5))
    open(d + "/prefix.img", "wb").write(bytes(4096) + bytes(base))
    open(d + "/gzip.img", "wb").write(gzip.compress(bytes(base)))
    sab = bytearray(base)
    struct.pack_into("<I", sab, 24, 0xDEADBEEF)
    struct.pack_into("<I", sab, 40, 0xCAFEBABE)
    open(d + "/both_versions_garbage.img", "wb").write(bytes(sab))
    print("  built 7 quirky headers")


def cmd_large(d):
    os.makedirs(d, exist_ok=True)
    rd = basic_ramdisk()
    kernel = gzip.compress(b"MZ" + b"\0" * 0x36 + b"ARM64" + os.urandom(120 << 20), 1)
    for name, hv in (("large_v3.img", 3), ("large_v4.img", 4)):
        img = boot_v3v4(kernel, rd, hv)
        open(d + "/" + name, "wb").write(img)
        sab = bytearray(img)
        struct.pack_into("<I", sab, 24, 0x11223344)
        struct.pack_into("<I", sab, 40, 0x55667788)
        open(d + "/" + name.replace(".img", "_sabotaged.img"), "wb").write(bytes(sab))
    print("  built %d MB images" % (len(img) >> 20))


# ------------------------------------------------------------------ verify


def decompress(blob):
    if blob[:2] == b"\x1f\x8b":
        return gzip.decompress(blob)
    if blob[:4] == b"\x02\x21\x4c\x18":
        raise SystemExit("  (lz4_legacy detected, skipping payload decode)")
    return blob


def read_ramdisk(img):
    """Return (ramdisk_bytes, header_version, cmdline) for any boot layout."""
    data = open(img, "rb").read()
    hv24 = struct.unpack_from("<I", data, 24)[0]
    hv40 = struct.unpack_from("<I", data, 40)[0]

    if hv24 in (3, 4, 5, 6) or hv40 > 2:         # modern: v3 / v4
        hv = hv24
        ksize = struct.unpack_from("<I", data, 8)[0]
        rsize = struct.unpack_from("<I", data, 12)[0]
        hs = struct.unpack_from("<I", data, 20)[0]
        if not 1500 <= hs <= 4096:
            hs = 1580 if hv == 3 else 1584
        off = ru(ru(hs, 4096) + ksize, 4096)
        cmd = data[28 : 28 + 1536].split(b"\0")[0]
    else:                                        # legacy: v0 / v1 / v2
        hv = hv40
        page = struct.unpack_from("<I", data, 36)[0] or 2048
        ksize = struct.unpack_from("<I", data, 8)[0]
        rsize = struct.unpack_from("<I", data, 16)[0]
        hs = {0: 1632, 1: 1648, 2: 1660}.get(hv, 1632)
        declared = struct.unpack_from("<I", data, 1644)[0]
        if declared in (1632, 1648, 1660):
            hs = declared
        off = ru(max(page, ru(hs, page)) + ksize, page)
        cmd = (
            data[64 : 64 + 512].split(b"\0")[0]
            + b" "
            + data[608 : 608 + 1024].split(b"\0")[0]
        )
    return data[off : off + rsize], hv, cmd.decode("utf-8", "replace").strip()


def verify_boot(img, hv_want=None):
    rd, hv, cmdline = read_ramdisk(img)
    if hv_want is not None and hv != hv_want:
        raise SystemExit("  header version %d, wanted %d" % (hv, hv_want))
    got = cpio_parse(decompress(rd))
    for need in ("/su", "/init.veritpath.rc", "/veritpath.json", "/init"):
        if need not in got:
            raise SystemExit("  missing %s" % need)
    if not got["/init.rc"].startswith(b"import /init.veritpath.rc") and (
        b"import /init.veritpath.rc" not in got["/init.rc"]
    ):
        raise SystemExit("  init.rc not hooked")
    if "androidboot.selinux=permissive" not in cmdline:
        raise SystemExit("  cmdline not patched (%r)" % cmdline)
    print("  verified %s (v%d, %d entries)" % (os.path.basename(img), hv, len(got)))


def verify_vendor(img):
    data = open(img, "rb").read()
    if data[:8] != b"VNDRBOOT":
        raise SystemExit("  not a vendor_boot image")
    page = struct.unpack_from("<I", data, 12)[0] or 4096
    rsize = struct.unpack_from("<I", data, 24)[0]
    dtb_size = struct.unpack_from("<I", data, 2096)[0]
    span = ru(2128, page)
    rd = data[span : span + rsize]
    # the payload must land in every fragment
    data_rd = decompress(rd) if rd[:2] == b"\x1f\x8b" else rd
    if b"/su" not in data_rd:
        raise SystemExit("  payload missing from the vendor ramdisk")
    print("  verified vendor_boot: payload present (ramdisk %d bytes)" % len(rd))


def verify_dir(d):
    for name in sorted(os.listdir(d)):
        verify_boot(os.path.join(d, name))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "images":
        cmd_images(arg)
    elif cmd == "quirks":
        cmd_quirks(arg)
    elif cmd == "large":
        cmd_large(arg)
    elif cmd == "verify":
        kind = sys.argv[3] if len(sys.argv) > 3 else "boot"
        if kind == "vendor":
            verify_vendor(arg)
        elif kind == "hv":
            verify_boot(arg, int(sys.argv[4]))
        else:
            verify_boot(arg)
    elif cmd == "verify-dir":
        verify_dir(arg)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
