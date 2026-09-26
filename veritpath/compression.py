"""Compression helpers for kernels / ramdisks.

veritpath has **zero hard dependencies**: gzip, lzma, bzip2 come from the
standard library, and LZ4 is implemented here in pure Python (both the modern
block format and the legacy frame format used by Android bootloaders).
If the optional `lz4` wheel happens to be installed, it is preferred for
compression speed.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import struct
from typing import Optional

from .utils import VeritpathError

# ---------------------------------------------------------------------------
# format detection
# ---------------------------------------------------------------------------

MAGIC_GZIP = b"\x1f\x8b"
MAGIC_XZ = b"\xfd7zXZ\x00"
MAGIC_LZ4_FRAME = b"\x04\x22\x4d\x18"
MAGIC_LZ4_LEGACY = b"\x02\x21\x4c\x18"
MAGIC_BZIP2 = b"BZh"
MAGIC_LZMA_ALONE = b"\x5d\x00\x00"
MAGIC_ZSTD = b"\x28\xb5\x2f\xfd"


class Format:
    GZIP = "gzip"
    XZ = "xz"
    LZMA = "lzma"
    LZ4 = "lz4"
    LZ4_LEGACY = "lz4_legacy"
    BZIP2 = "bzip2"
    ZSTD = "zstd"
    RAW = "raw"


def detect(data: bytes) -> str:
    """Guess the compression format of `data`."""
    if len(data) >= 2 and data[:2] == MAGIC_GZIP:
        return Format.GZIP
    if len(data) >= 6 and data[:6] == MAGIC_XZ:
        return Format.XZ
    if len(data) >= 4 and data[:4] == MAGIC_LZ4_FRAME:
        return Format.LZ4
    if len(data) >= 4 and data[:4] == MAGIC_LZ4_LEGACY:
        return Format.LZ4_LEGACY
    if len(data) >= 4 and data[:4] == MAGIC_ZSTD:
        return Format.ZSTD
    if len(data) >= 3 and data[:3] == MAGIC_BZIP2:
        return Format.BZIP2
    if len(data) >= 3 and data[:3] == MAGIC_LZMA_ALONE:
        return Format.LZMA
    return Format.RAW


# ---------------------------------------------------------------------------
# LZ4 (pure python)
# ---------------------------------------------------------------------------


def lz4_block_decompress(src: bytes, uncompressed_size: Optional[int] = None) -> bytes:
    """Decompress an LZ4 *block* (no frame header)."""
    out = bytearray()
    pos = 0
    end = len(src)
    while pos < end:
        token = src[pos]
        pos += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                b = src[pos]
                pos += 1
                lit_len += b
                if b != 255:
                    break
        if lit_len:
            if pos + lit_len > end:
                raise VeritpathError("lz4: truncated literal run")
            out += src[pos : pos + lit_len]
            pos += lit_len
        if pos >= end:
            break
        offset = struct.unpack_from("<H", src, pos)[0]
        pos += 2
        if offset == 0 or offset > len(out):
            raise VeritpathError("lz4: invalid match offset")
        match_len = token & 0x0F
        if match_len == 15:
            while True:
                b = src[pos]
                pos += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4
        start = len(out) - offset
        for i in range(match_len):
            out.append(out[start + i])
    if uncompressed_size is not None and len(out) != uncompressed_size:
        raise VeritpathError(f"lz4: size mismatch ({len(out)} != {uncompressed_size})")
    return bytes(out)


def lz4_legacy_decompress_ex(src: bytes) -> tuple:
    """Decompress an LZ4 *legacy frame*; returns (data, consumed_bytes)."""
    if len(src) < 4 or src[:4] != MAGIC_LZ4_LEGACY:
        raise VeritpathError("lz4 legacy: bad magic")
    out = bytearray()
    pos = 4
    while True:
        if pos + 4 > len(src):
            raise VeritpathError("lz4 legacy: truncated block header")
        raw_size = struct.unpack_from("<I", src, pos)[0]
        pos += 4
        if raw_size == 0:
            break
        compressed = not (raw_size & 0x80000000)
        size = raw_size & 0x7FFFFFFF
        if pos + size > len(src):
            raise VeritpathError("lz4 legacy: truncated block")
        chunk = src[pos : pos + size]
        pos += size
        out += lz4_block_decompress(chunk) if compressed else chunk
    return bytes(out), pos


def lz4_legacy_decompress(src: bytes) -> bytes:
    """Decompress an LZ4 *legacy frame* (magic 0x184C2102)."""
    return lz4_legacy_decompress_ex(src)[0]


def lz4_frame_decompress_ex(src: bytes) -> tuple:
    """Decompress an LZ4 *modern frame*; returns (data, consumed_bytes)."""
    if len(src) < 7 or src[:4] != MAGIC_LZ4_FRAME:
        raise VeritpathError("lz4 frame: bad magic")
    flg = src[4]
    bd = src[5]
    # FLG: bit3 = content size, bit2 = content checksum, bit1 = reserved,
    #      bit0 = dictionary id, bit4 = block checksum
    has_content_size = bool(flg & 0x08)
    has_dict_id = bool(flg & 0x01)
    has_block_checksum = bool(flg & 0x10)
    block_max = (4, 64, 256, 1024)[(bd >> 4) & 0x03] * 1024
    # header = magic(4) + FLG(1) + BD(1) + [content size 8] + [dict id 4] + HC(1)
    pos = 6
    if has_content_size:
        pos += 8
    if has_dict_id:
        pos += 4
    pos += 1  # header checksum
    out = bytearray()
    while True:
        if pos + 4 > len(src):
            raise VeritpathError("lz4 frame: truncated block size")
        raw = struct.unpack_from("<I", src, pos)[0]
        pos += 4
        if raw == 0:
            break  # end mark
        size = raw & 0x7FFFFFFF
        uncompressed = bool(raw & 0x80000000)
        if size > block_max:
            raise VeritpathError("lz4 frame: block too large")
        if pos + size > len(src):
            raise VeritpathError("lz4 frame: truncated block")
        chunk = src[pos : pos + size]
        pos += size
        if has_block_checksum:
            pos += 4
        out += chunk if uncompressed else lz4_block_decompress(chunk)
    if flg & 0x04:  # content checksum trailing the end mark
        pos += 4
    return bytes(out), pos


def lz4_frame_decompress(src: bytes) -> bytes:
    """Decompress an LZ4 *modern frame* (magic 0x184D2204)."""
    return lz4_frame_decompress_ex(src)[0]


def lz4_legacy_compress(data: bytes, chunk: int = 1 << 20) -> bytes:
    """Wrap `data` into an LZ4 legacy frame using *uncompressed* blocks.

    The legacy container allows uncompressed blocks, so the bootloader's LZ4
    decoder accepts this without needing a real compressor.  Used as the
    zero-dependency fallback when the `lz4` wheel / CLI is unavailable.
    """
    out = bytearray(MAGIC_LZ4_LEGACY)
    pos = 0
    total = len(data)
    while pos < total:
        piece = data[pos : pos + chunk]
        pos += len(piece)
        out += struct.pack("<I", len(piece) | 0x80000000)
        out += piece
    out += struct.pack("<I", 0)
    return bytes(out)


def lz4_compress(data: bytes, prefer_legacy: bool = True) -> bytes:
    """Compress with LZ4. Falls back to a lossless legacy container."""
    try:  # optional fast path
        import lz4.block  # type: ignore

        payload = lz4.block.compress(data, mode="default", store_size=False)
        if prefer_legacy:
            return (
                MAGIC_LZ4_LEGACY + struct.pack("<I", len(payload)) + payload + struct.pack("<I", 0)
            )
        return payload
    except Exception:
        return lz4_legacy_compress(data)


# ---------------------------------------------------------------------------
# generic API
# ---------------------------------------------------------------------------


def decompress(data: bytes, fmt: Optional[str] = None) -> bytes:
    fmt = fmt or detect(data)
    if fmt == Format.GZIP:
        return gzip.decompress(data)
    if fmt == Format.XZ:
        return lzma.decompress(data)
    if fmt == Format.LZMA:
        return lzma.decompress(data, format=lzma.FORMAT_ALONE)
    if fmt == Format.BZIP2:
        return bz2.decompress(data)
    if fmt == Format.LZ4:
        return lz4_frame_decompress(data)
    if fmt == Format.LZ4_LEGACY:
        return lz4_legacy_decompress(data)
    if fmt == Format.ZSTD:
        try:
            import zstandard  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional
            raise VeritpathError("zstd compressed image needs the 'zstandard' package") from exc
        return zstandard.ZstdDecompressor().decompress(data)
    if fmt == Format.RAW:
        return data
    raise VeritpathError(f"unsupported compression format: {fmt}")


def compress(data: bytes, fmt: str) -> bytes:
    if fmt == Format.GZIP:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as fh:
            fh.write(data)
        return buf.getvalue()
    if fmt == Format.XZ:
        return lzma.compress(
            data,
            format=lzma.FORMAT_XZ,
            filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}],
        )
    if fmt == Format.LZMA:
        return lzma.compress(data, format=lzma.FORMAT_ALONE)
    if fmt == Format.BZIP2:
        return bz2.compress(data, compresslevel=9)
    if fmt in (Format.LZ4, Format.LZ4_LEGACY):
        return lz4_compress(data, prefer_legacy=(fmt == Format.LZ4_LEGACY))
    if fmt == Format.RAW:
        return data
    raise VeritpathError(f"unsupported compression format: {fmt}")


def consume_chunk(data: bytes, pos: int = 0) -> tuple:
    """Decompress exactly one compressed chunk starting at `pos`.

    Ramdisks (especially `vendor_boot` ramdisks) are concatenations of
    *independently* compressed cpio archives, so we have to know where each
    chunk ends.  Returns (format, decompressed_bytes, next_offset).
    """
    fmt = detect(data[pos:])
    tail = data[pos:]
    if fmt == Format.GZIP:
        import zlib

        obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = obj.decompress(tail)
        out += obj.flush()
        consumed = len(tail) - len(obj.unused_data)
        return fmt, out, pos + consumed
    if fmt == Format.XZ:
        obj = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        out = obj.decompress(tail)
        consumed = len(tail) - len(obj.unused_data)
        return fmt, out, pos + consumed
    if fmt == Format.LZMA:
        obj = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
        out = obj.decompress(tail)
        consumed = len(tail) - len(obj.unused_data)
        return fmt, out, pos + consumed
    if fmt == Format.BZIP2:
        import bz2 as _bz2

        obj = _bz2.BZ2Decompressor()
        out = obj.decompress(tail)
        consumed = len(tail) - len(obj.unused_data)
        return fmt, out, pos + consumed
    if fmt == Format.LZ4:
        out, consumed = lz4_frame_decompress_ex(tail)
        return fmt, out, pos + consumed
    if fmt == Format.LZ4_LEGACY:
        out, consumed = lz4_legacy_decompress_ex(tail)
        return fmt, out, pos + consumed
    if fmt == Format.ZSTD:
        out = decompress(tail, fmt)
        return fmt, out, len(data)
    return Format.RAW, tail, len(data)


def split_chunks(data: bytes) -> list:
    """Split a ramdisk blob into [(format, decompressed_bytes), ...]."""
    chunks = []
    pos = 0
    while pos < len(data):
        # skip zero padding between chunks
        while pos < len(data) and data[pos] == 0:
            pos += 1
        if pos >= len(data):
            break
        fmt, out, nxt = consume_chunk(data, pos)
        chunks.append((fmt, out))
        if nxt <= pos:
            break
        pos = nxt
    return chunks or [(Format.RAW, data)]


def probe_kernel(data: bytes) -> bytes:
    """Return raw kernel bytes (decompressed when we know how)."""
    fmt = detect(data)
    if fmt == Format.RAW:
        return data
    try:
        return decompress(data, fmt)
    except VeritpathError:
        return data
