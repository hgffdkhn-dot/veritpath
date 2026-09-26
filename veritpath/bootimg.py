"""Android boot image parser / builder.

Supports
  * boot.img / init_boot.img / recovery.img headers v0, v1, v2, v3, v4
  * vendor_boot.img headers v3, v4 (including the vendor ramdisk table)

The parser keeps the *raw header bytes* around and only patches the fields it
actually changes, so vendor-specific header quirks survive a round-trip.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import compression
from .cpio import CpioArchive
from .utils import VeritpathError, cstr, pad_to, round_up

BOOT_MAGIC = b"ANDROID!"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"

# canonical header sizes
BOOT_HEADER_SIZE = {0: 1632, 1: 1648, 2: 1660, 3: 1580, 4: 1584}
VENDOR_HEADER_SIZE = {3: 2112, 4: 2128}

# vendor ramdisk types (vendor_boot v4)
VRAMDISK_NONE = 0
VRAMDISK_PLATFORM = 1
VRAMDISK_RECOVERY = 2
VRAMDISK_DLKM = 3

VALID_PAGE_SIZES = (2048, 4096, 8192, 16384, 32768, 65536, 131072)


# ---------------------------------------------------------------------------
# os_version helpers
# ---------------------------------------------------------------------------


def decode_os_version(value: int) -> Dict[str, object]:
    """Decode the packed os_version field (AOSP / AVB style)."""
    if not value:
        return {"raw": 0, "version": None, "patch_level": None}
    version = value >> 11
    patch = value & 0x7FF
    year = 2000 + (patch >> 4)
    month = patch & 0x0F
    a, b, c = (version >> 14) & 0x3F, (version >> 7) & 0x7F, version & 0x7F
    return {
        "raw": value,
        "version": f"{a}.{b}.{c}" if version else None,
        "patch_level": f"{year}-{month:02d}" if patch else None,
    }


def encode_os_version(version_str: str, patch_level: str) -> int:
    """Encode e.g. ('13.0.0', '2023-08') back into the packed field."""
    try:
        a, b, c = (int(x) for x in version_str.split("."))
        version = (a << 14) | (b << 7) | c
    except (ValueError, AttributeError):
        version = 0
    try:
        year, month = (int(x) for x in patch_level.split("-"))
        patch = ((year - 2000) << 4) | month
    except (ValueError, AttributeError):
        patch = 0
    return (version << 11) | patch


# ---------------------------------------------------------------------------
# header version detection
# ---------------------------------------------------------------------------


def detect_header_version(data: bytes) -> int:
    """Tell v0/v1/v2 apart from v3/v4.

    v0-v2 keep `header_version` at offset 40; v3/v4 moved it to offset 24 and
    store the header size at offset 20 (1580 / 1584).  Check the v3/v4 markers
    first, because offset 40 of a v3/v4 header is the start of the cmdline and
    reads as 0 for images with no kernel arguments.
    """
    hv_at_24 = struct.unpack_from("<I", data, 24)[0]
    size_at_20 = struct.unpack_from("<I", data, 20)[0]
    if hv_at_24 in (3, 4) and size_at_20 in (1564, 1568, 1576, 1580, 1584, 1588, 1592):
        return hv_at_24
    hv_at_40 = struct.unpack_from("<I", data, 40)[0]
    if hv_at_40 in (0, 1, 2):
        page = struct.unpack_from("<I", data, 36)[0]
        if page in VALID_PAGE_SIZES or page == 0:
            return hv_at_40
    if hv_at_24 in (3, 4):
        return hv_at_24
    raise VeritpathError("cannot determine boot image header version")


def probe_role(data: bytes, filename: str = "") -> str:
    """Guess whether a blob is boot / init_boot / vendor_boot / recovery."""
    name = filename.lower()
    if data[:8] == VENDOR_BOOT_MAGIC:
        return "vendor_boot"
    for key in ("init_boot", "vendor_boot", "recovery", "boot"):
        if key in name:
            return key
    hv = detect_header_version(data)
    if hv >= 3 and struct.unpack_from("<I", data, 12)[0] != 0:
        return "boot"
    return "boot"


# ---------------------------------------------------------------------------
# boot / init_boot / recovery image
# ---------------------------------------------------------------------------


@dataclass
class BootImage:
    role: str = "boot"
    header_version: int = 0
    page_size: int = 4096
    os_version: int = 0
    name: str = ""
    cmdline: str = ""
    extra_cmdline: str = ""
    kernel: bytes = b""
    ramdisk: bytes = b""
    second: bytes = b""
    dtb: bytes = b""
    recovery_dtbo: bytes = b""
    boot_signature: bytes = b""
    image_id: bytes = b""
    raw_header: bytes = b""
    header_span: int = 0
    _ramdisk_format: Optional[str] = None

    # -- parsing -----------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes, role: str = "boot") -> "BootImage":
        if data[:8] != BOOT_MAGIC:
            raise VeritpathError("not an Android boot image (bad magic)")
        hv = detect_header_version(data)
        img = cls(role=role, header_version=hv)
        if hv <= 2:
            img._parse_legacy(data)
        else:
            img._parse_v3(data)
        return img

    def _parse_legacy(self, data: bytes) -> None:
        hv = self.header_version
        u32 = lambda off: struct.unpack_from("<I", data, off)[0]
        u64 = lambda off: struct.unpack_from("<Q", data, off)[0]
        ksize, rsize, ssize = u32(8), u32(16), u32(24)
        self.page_size = u32(36) or 2048
        self.os_version = u32(44)
        self.name = cstr(data[48:64])
        self.cmdline = cstr(data[64:576])
        self.image_id = data[576:608]
        self.extra_cmdline = cstr(data[608:1632])
        header_size = BOOT_HEADER_SIZE[hv]
        if hv >= 1:
            declared = u32(1644)
            if declared in (1632, 1648, 1660):
                header_size = declared
        self.header_span = max(self.page_size, round_up(header_size, self.page_size))
        self.raw_header = data[: self.header_span]

        page = self.page_size
        off = self.header_span
        self.kernel = data[off : off + ksize]
        off = round_up(off + ksize, page)
        self.ramdisk = data[off : off + rsize]
        off = round_up(off + rsize, page)
        self.second = data[off : off + ssize] if ssize else b""
        off = round_up(off + ssize, page)
        if hv >= 1:
            rd_size = u32(1632)
            rd_off = u64(1636) or off
            self.recovery_dtbo = data[rd_off : rd_off + rd_size] if rd_size else b""
            off = round_up(rd_off + rd_size, page)
        if hv >= 2:
            dtb_size = u32(1648)
            self.dtb = data[off : off + dtb_size] if dtb_size else b""

    def _parse_v3(self, data: bytes) -> None:
        kernel_size = struct.unpack_from("<I", data, 8)[0]
        ramdisk_size = struct.unpack_from("<I", data, 12)[0]
        self.os_version = struct.unpack_from("<I", data, 16)[0]
        header_size = struct.unpack_from("<I", data, 20)[0] or BOOT_HEADER_SIZE[self.header_version]
        self.header_version = struct.unpack_from("<I", data, 24)[0]
        self.page_size = 4096
        self.cmdline = cstr(data[28 : 28 + 1536])
        self.header_span = round_up(header_size, self.page_size)
        self.raw_header = data[: self.header_span]
        kernel_off = self.header_span
        self.kernel = data[kernel_off : kernel_off + kernel_size]
        ramdisk_off = round_up(kernel_off + kernel_size, self.page_size)
        self.ramdisk = data[ramdisk_off : ramdisk_off + ramdisk_size]
        if self.header_version >= 4:
            sig_size = struct.unpack_from("<I", data, 1564)[0]
            sig_off = round_up(ramdisk_off + ramdisk_size, self.page_size)
            self.boot_signature = data[sig_off : sig_off + sig_size] if sig_size else b""

    def _field_size(self, data: bytes, size_off: int) -> int:
        return struct.unpack_from("<I", data, size_off)[0]

    # -- properties --------------------------------------------------------
    @property
    def has_ramdisk(self) -> bool:
        return bool(self.ramdisk)

    @property
    def ramdisk_format(self) -> str:
        if self._ramdisk_format is None:
            self._ramdisk_format = compression.detect(self.ramdisk) if self.ramdisk else "raw"
        return self._ramdisk_format

    def ramdisk_archive(self) -> CpioArchive:
        if not self.ramdisk:
            raise VeritpathError(f"{self.role}: image carries no ramdisk")
        chunks = compression.split_chunks(self.ramdisk)
        raw = b"".join(c[1] for c in chunks)
        archive = CpioArchive.parse(raw)
        archive.chunk_formats = [c[0] for c in chunks]
        return archive

    def set_ramdisk(self, archive: CpioArchive, fmt: Optional[str] = None) -> None:
        blobs = _compress_segments(archive, fmt)
        self.ramdisk = b"".join(blobs)
        self._ramdisk_format = archive.chunk_formats[0] if archive.chunk_formats else fmt or "gzip"

    @property
    def dtb_blob(self) -> bytes:
        """Return the DTB payload, wherever it hides."""
        if self.header_version <= 2:
            return self.dtb
        # v3/v4: DTB is appended to the kernel image
        idx = self.kernel.find(b"\xd0\x0d\xfe\xed")
        return self.kernel[idx:] if idx != -1 else b""

    @property
    def full_cmdline(self) -> str:
        parts = [self.cmdline]
        if self.header_version <= 2 and self.extra_cmdline:
            parts.append(self.extra_cmdline)
        return " ".join(p for p in parts if p).strip()

    # -- mutation ----------------------------------------------------------
    def append_cmdline(self, extra: str) -> None:
        if not extra:
            return
        tokens = [t for t in extra.split() if t not in self.full_cmdline.split()]
        if not tokens:
            return
        addition = " ".join(tokens)
        if self.header_version <= 2:
            room = 512 - len(self.cmdline.encode()) - 1
            if room > len(addition):
                self.cmdline = (self.cmdline + " " + addition).strip()
                return
            room = 1024 - len(self.extra_cmdline.encode()) - 1
            if room > len(addition):
                self.extra_cmdline = (self.extra_cmdline + " " + addition).strip()
                return
            raise VeritpathError("no room left in the kernel cmdline")
        room = 1536 - len(self.cmdline.encode()) - 1
        if room <= len(addition):
            raise VeritpathError("no room left in the kernel cmdline")
        self.cmdline = (self.cmdline + " " + addition).strip()

    # -- serialization -----------------------------------------------------
    def pack(self) -> bytes:
        hv = self.header_version
        if self.raw_header:
            header = bytearray(self.raw_header)
        else:
            header = bytearray(BOOT_HEADER_SIZE[hv])
            header[0:8] = BOOT_MAGIC
            if hv <= 2:
                struct.pack_into("<I", header, 36, self.page_size)
                struct.pack_into("<I", header, 40, hv)
                struct.pack_into("<I", header, 44, self.os_version)
            if 1 <= hv <= 2:
                struct.pack_into("<I", header, 1644, BOOT_HEADER_SIZE[hv])
        if len(header) < BOOT_HEADER_SIZE[hv]:
            header += bytes(BOOT_HEADER_SIZE[hv] - len(header))
        if hv <= 2:
            struct.pack_into("<I", header, 8, len(self.kernel))
            struct.pack_into("<I", header, 16, len(self.ramdisk))
            struct.pack_into("<I", header, 24, len(self.second))
            cmd = self.cmdline.encode()[:511]
            header[64:576] = cmd + b"\x00" * (512 - len(cmd))
            extra = self.extra_cmdline.encode()[:1023]
            header[608:1632] = extra + b"\x00" * (1024 - len(extra))
            if hv >= 2:
                struct.pack_into("<I", header, 1648, len(self.dtb))
            if hv >= 1:
                struct.pack_into("<I", header, 1632, len(self.recovery_dtbo))
                struct.pack_into("<Q", header, 1636, self._recovery_dtbo_offset())
        else:
            struct.pack_into("<I", header, 8, len(self.kernel))
            struct.pack_into("<I", header, 12, len(self.ramdisk))
            struct.pack_into("<I", header, 16, self.os_version)
            struct.pack_into("<I", header, 20, BOOT_HEADER_SIZE[hv])
            struct.pack_into("<I", header, 24, hv)
            cmd = self.cmdline.encode()[:1535]
            header[28 : 28 + 1536] = cmd + b"\x00" * (1536 - len(cmd))
            if hv >= 4:
                struct.pack_into("<I", header, 1564, len(self.boot_signature))
        page = self.page_size if hv <= 2 else 4096
        out = bytearray(pad_to(bytes(header), self.header_span or page))
        out += pad_to(self.kernel, page)
        out += pad_to(self.ramdisk, page)
        if hv <= 2:
            out += pad_to(self.second, page)
            if hv >= 1:
                out += pad_to(self.recovery_dtbo, page)
            if hv >= 2:
                out += pad_to(self.dtb, page)
        elif hv >= 4:
            out += pad_to(self.boot_signature, page)
        return bytes(out)

    def _recovery_dtbo_offset(self) -> int:
        page = self.page_size
        off = self.header_span
        off = round_up(off + len(self.kernel), page)
        off = round_up(off + len(self.ramdisk), page)
        off = round_up(off + len(self.second), page)
        return off

    # -- reporting ---------------------------------------------------------
    def summary(self) -> Dict[str, object]:
        return {
            "role": self.role,
            "header_version": self.header_version,
            "page_size": self.page_size,
            "os_version": decode_os_version(self.os_version),
            "cmdline": self.full_cmdline,
            "kernel_size": len(self.kernel),
            "ramdisk_size": len(self.ramdisk),
            "ramdisk_format": self.ramdisk_format if self.ramdisk else None,
            "second_size": len(self.second),
            "dtb_size": len(self.dtb_blob),
            "recovery_dtbo_size": len(self.recovery_dtbo),
            "boot_signature_size": len(self.boot_signature),
        }


# ---------------------------------------------------------------------------
# vendor_boot image
# ---------------------------------------------------------------------------


@dataclass
class VendorRamdiskEntry:
    size: int = 0
    offset: int = 0
    type: int = VRAMDISK_NONE
    name: str = ""
    board_id: bytes = b""


@dataclass
class VendorBootImage:
    role: str = "vendor_boot"
    header_version: int = 3
    page_size: int = 4096
    cmdline: str = ""
    name: str = ""
    kernel_addr: int = 0
    ramdisk_addr: int = 0
    tags_addr: int = 0
    dtb_addr: int = 0
    ramdisk: bytes = b""
    dtb: bytes = b""
    bootconfig: bytes = b""
    table: List[VendorRamdiskEntry] = field(default_factory=list)
    table_entry_size: int = 108
    raw_header: bytes = b""
    header_span: int = 0

    @classmethod
    def parse(cls, data: bytes) -> "VendorBootImage":
        if data[:8] != VENDOR_BOOT_MAGIC:
            raise VeritpathError("not a vendor_boot image (bad magic)")
        hv = struct.unpack_from("<I", data, 8)[0]
        if hv not in (3, 4):
            raise VeritpathError(f"unsupported vendor_boot header version: {hv}")
        img = cls(header_version=hv)
        img.page_size = struct.unpack_from("<I", data, 12)[0] or 4096
        img.kernel_addr = struct.unpack_from("<I", data, 16)[0]
        img.ramdisk_addr = struct.unpack_from("<I", data, 20)[0]
        ramdisk_size = struct.unpack_from("<I", data, 24)[0]
        img.cmdline = cstr(data[28 : 28 + 2048])
        img.tags_addr = struct.unpack_from("<I", data, 2076)[0]
        img.name = cstr(data[2080:2096])
        dtb_size = struct.unpack_from("<I", data, 2096)[0]
        img.dtb_addr = struct.unpack_from("<Q", data, 2100)[0]
        header_size = VENDOR_HEADER_SIZE[hv]
        img.header_span = round_up(header_size, img.page_size)
        img.raw_header = data[: img.header_span]
        off = img.header_span
        img.ramdisk = data[off : off + ramdisk_size]
        off = round_up(off + ramdisk_size, img.page_size)
        img.dtb = data[off : off + dtb_size] if dtb_size else b""
        off = round_up(off + dtb_size, img.page_size)
        if hv >= 4:
            table_size = struct.unpack_from("<I", data, 2108)[0]
            entry_num = struct.unpack_from("<I", data, 2112)[0]
            img.table_entry_size = struct.unpack_from("<I", data, 2116)[0] or 108
            bootconfig_size = struct.unpack_from("<I", data, 2120)[0]
            table_blob = data[off : off + table_size]
            for i in range(entry_num):
                base = i * img.table_entry_size
                if base + 44 > len(table_blob):
                    break
                size, roff, rtype = struct.unpack_from("<3I", table_blob, base)
                rname = cstr(table_blob[base + 12 : base + 44])
                img.table.append(
                    VendorRamdiskEntry(
                        size=size,
                        offset=roff,
                        type=rtype,
                        name=rname,
                        board_id=table_blob[base + 44 : base + img.table_entry_size],
                    )
                )
            off = round_up(off + table_size, img.page_size)
            img.bootconfig = data[off : off + bootconfig_size] if bootconfig_size else b""
        return img

    @property
    def has_ramdisk(self) -> bool:
        return bool(self.ramdisk)

    def ramdisk_archive(self) -> CpioArchive:
        chunks = compression.split_chunks(self.ramdisk)
        raw = b"".join(c[1] for c in chunks)
        archive = CpioArchive.parse(raw)
        archive.chunk_formats = [c[0] for c in chunks]
        return archive

    def set_ramdisk(self, archive: CpioArchive, fmt: Optional[str] = None) -> None:
        blobs = _compress_segments(archive, fmt)
        old_sizes = [e.size for e in self.table]
        if self.table and len(self.table) == len(blobs):
            # refresh the fragment table: offsets shift by the size delta
            delta = 0
            for idx, entry in enumerate(self.table):
                entry.offset += delta
                delta += len(blobs[idx]) - old_sizes[idx]
                entry.size = len(blobs[idx])
        self.ramdisk = b"".join(blobs)

    def _table_blob(self) -> bytes:
        blob = bytearray()
        for entry in self.table:
            buf = bytearray(self.table_entry_size)
            struct.pack_into("<3I", buf, 0, entry.size, entry.offset, entry.type)
            name = entry.name.encode()[:31]
            buf[12 : 12 + len(name)] = name
            tail = entry.board_id[: max(0, self.table_entry_size - 44)]
            buf[44 : 44 + len(tail)] = tail
            blob += buf
        return bytes(blob)

    def pack(self) -> bytes:
        hv = self.header_version
        page = self.page_size
        if self.raw_header:
            header = bytearray(self.raw_header)
        else:
            header = bytearray(VENDOR_HEADER_SIZE[hv])
            header[0:8] = VENDOR_BOOT_MAGIC
            struct.pack_into("<I", header, 8, hv)
            struct.pack_into("<I", header, 12, self.page_size)
            struct.pack_into("<I", header, 16, self.kernel_addr)
            struct.pack_into("<I", header, 20, self.ramdisk_addr)
        struct.pack_into("<I", header, 24, len(self.ramdisk))
        struct.pack_into("<I", header, 2096, len(self.dtb))
        cmd = self.cmdline.encode()[:2047]
        header[28 : 28 + 2048] = cmd + b"\x00" * (2048 - len(cmd))
        table_blob = b""
        if hv >= 4:
            table_blob = self._table_blob()
            struct.pack_into("<I", header, 2108, len(table_blob))
            struct.pack_into("<I", header, 2112, len(self.table))
            struct.pack_into("<I", header, 2116, self.table_entry_size)
            struct.pack_into("<I", header, 2120, len(self.bootconfig))
        out = bytearray(pad_to(bytes(header), self.header_span or page))
        out += pad_to(self.ramdisk, page)
        out += pad_to(self.dtb, page)
        if hv >= 4:
            out += pad_to(table_blob, page)
            out += pad_to(self.bootconfig, page)
        return bytes(out)

    def summary(self) -> Dict[str, object]:
        return {
            "role": self.role,
            "header_version": self.header_version,
            "page_size": self.page_size,
            "ramdisk_size": len(self.ramdisk),
            "ramdisk_format": compression.detect(self.ramdisk) if self.ramdisk else None,
            "dtb_size": len(self.dtb),
            "bootconfig_size": len(self.bootconfig),
            "vendor_ramdisk_fragments": [
                {"name": e.name, "type": e.type, "size": e.size, "offset": e.offset}
                for e in self.table
            ],
            "cmdline": self.cmdline,
        }


def _compress_segments(archive: CpioArchive, fmt: Optional[str]) -> List[bytes]:
    """Compress each cpio segment, keeping the original per-chunk format."""
    from .cpio import CpioArchive as _Archive  # noqa: F401  (typing clarity)

    segments = archive.serialize_segments()
    formats = list(archive.chunk_formats)
    if len(segments) == len(formats) and len(formats) > 1:
        return [compression.compress(blob, f) for blob, f in zip(segments, formats)]
    single = fmt or (formats[0] if formats else compression.Format.GZIP)
    if single == compression.Format.RAW:
        single = compression.Format.GZIP
    return [compression.compress(b"".join(segments), single)]


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


def load_image(path: str, role_hint: str = "") -> object:
    """Load any supported Android boot image."""
    from pathlib import Path

    data = Path(path).read_bytes()
    if data[:8] == VENDOR_BOOT_MAGIC:
        return VendorBootImage.parse(data)
    if data[:8] != BOOT_MAGIC:
        raise VeritpathError(f"{path}: unrecognised image (no ANDROID!/VNDRBOOT magic)")
    role = role_hint or probe_role(data, Path(path).name)
    return BootImage.parse(data, role=role)
