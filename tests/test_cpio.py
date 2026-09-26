import struct

from veritpath import compression
from veritpath.cpio import CpioArchive, CpioEntry


def test_roundtrip_single_segment():
    archive = CpioArchive.empty()
    archive.add(CpioEntry(name="init", mode=0o100755, data=b"#!/bin/sh\n"))
    archive.add(CpioEntry(name="system", mode=0o40755, nlink=2))
    archive.add(CpioEntry(name="system/build.prop", mode=0o100644, data=b"ro.foo=1\n"))

    blob = archive.serialize()
    again = CpioArchive.parse(blob)

    assert [e.name for e in again.all_entries()] == ["init", "system", "system/build.prop"]
    assert again.find("/init").data == b"#!/bin/sh\n"
    assert again.find("/system").is_dir
    assert again.find("/system/build.prop").data == b"ro.foo=1\n"


def test_multi_segment_roundtrip():
    archive = CpioArchive(
        [
            CpioArchive.empty().segments[0],
            CpioArchive.empty().segments[0],
        ]
    )
    archive.segments[0].entries = [
        CpioEntry(name="first_stage_ramdisk/init", mode=0o100755, data=b"one\n")
    ]
    archive.segments[1].entries = [CpioEntry(name="init", mode=0o100755, data=b"two\n")]
    blob = archive.serialize()
    again = CpioArchive.parse(blob)
    assert len(again.segments) == 2
    assert again.segments[0].entries[0].data == b"one\n"
    assert again.segments[1].entries[0].data == b"two\n"
    assert again.main is again.segments[-1]


def test_serialize_segments_matches_concat():
    archive = CpioArchive.empty()
    archive.add(CpioEntry(name="a", mode=0o100644, data=b"x"))
    parts = archive.serialize_segments()
    assert b"".join(parts) == archive.serialize()


def test_gzip_compression_roundtrip():
    data = b"hello ramdisk " * 100
    packed = compression.compress(data, compression.Format.GZIP)
    assert compression.detect(packed) == compression.Format.GZIP
    assert compression.decompress(packed) == data


def test_lz4_legacy_container_roundtrip():
    data = b"A" * 5000 + b"BCDEFG" * 40
    packed = compression.lz4_compress(data, prefer_legacy=True)
    assert compression.detect(packed) == compression.Format.LZ4_LEGACY
    assert compression.lz4_legacy_decompress(packed) == data


def test_lz4_block_literals():
    # literal-only LZ4 block: token 0x50 == 5 literals, 0 match
    assert compression.lz4_block_decompress(b"\x50hello") == b"hello"


def test_split_chunks_detects_concatenated_archives():
    one = compression.compress(b"first archive", compression.Format.GZIP)
    two = compression.compress(b"second archive", compression.Format.GZIP)
    chunks = compression.split_chunks(one + two)
    assert [c[1] for c in chunks] == [b"first archive", b"second archive"]


def _lz4_frame(
    payload: bytes, flg: int = 0x40, content_size: bool = False, content_checksum: bool = False
) -> bytes:
    """Build a minimal LZ4 modern frame wrapping one uncompressed block."""
    flags = flg
    if content_size:
        flags |= 0x08
    if content_checksum:
        flags |= 0x04
    header = b"\x04\x22\x4d\x18" + bytes([flags, 0x70])
    if content_size:
        header += struct.pack("<Q", len(payload))
    header += b"\x00"  # header checksum
    body = struct.pack("<I", 0x80000000 | len(payload)) + payload + b"\x00\x00\x00\x00"
    if content_checksum:
        body += b"\x00\x00\x00\x00"
    return header + body


def test_lz4_frame_header_offsets():
    data = b"veritpath frame" * 40
    assert compression.lz4_frame_decompress(_lz4_frame(data)) == data
    assert compression.lz4_frame_decompress(_lz4_frame(data, content_size=True)) == data
    assert (
        compression.lz4_frame_decompress(_lz4_frame(data, content_size=True, content_checksum=True))
        == data
    )


def test_lz4_frame_detected_and_split():
    data = b"ramdisk payload" * 10
    frame = _lz4_frame(data)
    assert compression.detect(frame) == compression.Format.LZ4
    chunks = compression.split_chunks(frame + frame)
    assert [c[1] for c in chunks] == [data, data]
