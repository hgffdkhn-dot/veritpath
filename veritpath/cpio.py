"""cpio ("newc") archive reader / writer with Android multi-segment support.

Android ramdisks are frequently a concatenation of several cpio archives
(e.g. `first_stage_ramdisk` + main ramdisk inside a vendor_ramdisk), each one
terminated by a `TRAILER!!!` record.  `CpioArchive` keeps every segment
separate so that we can patch only the one we care about and rebuild the rest
byte-for-byte.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .utils import VeritpathError

MAGIC_NEWC = b"070701"
MAGIC_NEWC_CRC = b"070702"
TRAILER = "TRAILER!!!"
HEADER_LEN = 110


@dataclass
class CpioEntry:
    name: str
    mode: int = 0o644
    uid: int = 0
    gid: int = 0
    nlink: int = 1
    mtime: int = 0
    data: bytes = b""
    devmajor: int = 0
    devminor: int = 0
    rdevmajor: int = 0
    rdevminor: int = 0
    ino: int = 0

    @property
    def is_dir(self) -> bool:
        return (self.mode & 0o170000) == 0o040000

    @property
    def is_symlink(self) -> bool:
        return (self.mode & 0o170000) == 0o120000

    @property
    def is_file(self) -> bool:
        return not self.is_dir and not self.is_symlink

    @property
    def perms(self) -> int:
        return self.mode & 0o7777

    @perms.setter
    def perms(self, value: int) -> None:
        self.mode = (self.mode & ~0o7777) | (value & 0o7777)


def _normalize(name: str) -> str:
    """Canonical form: no leading './', no leading '/'."""
    text = name
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _hex(value: int) -> bytes:
    return f"{value & 0xFFFFFFFF:08X}".encode("ascii")


def _parse_header(buf: bytes, off: int):
    if len(buf) - off < HEADER_LEN:
        raise VeritpathError("cpio: truncated header")
    magic = buf[off : off + 6]
    if magic not in (MAGIC_NEWC, MAGIC_NEWC_CRC):
        return None
    fields = buf[off + 6 : off + HEADER_LEN]
    try:
        values = [int(fields[i * 8 : (i + 1) * 8], 16) for i in range(13)]
    except ValueError as exc:
        raise VeritpathError("cpio: corrupt header") from exc
    (
        ino,
        mode,
        uid,
        gid,
        nlink,
        mtime,
        filesize,
        devmajor,
        devminor,
        rdevmajor,
        rdevminor,
        namesize,
        check,
    ) = values
    name_start = off + HEADER_LEN
    name_end = name_start + namesize
    if name_end > len(buf):
        raise VeritpathError("cpio: truncated name")
    name = buf[name_start : name_end - 1].decode("utf-8", errors="replace")
    data_start = _align4(name_end)
    data_end = data_start + filesize
    if data_end > len(buf):
        raise VeritpathError(f"cpio: truncated payload for {name}")
    entry = CpioEntry(
        name=name,
        mode=mode,
        uid=uid,
        gid=gid,
        nlink=nlink,
        mtime=mtime,
        data=buf[data_start:data_end],
        devmajor=devmajor,
        devminor=devminor,
        rdevmajor=rdevmajor,
        rdevminor=rdevminor,
        ino=ino,
    )
    next_off = _align4(data_end)
    return entry, next_off


def _serialize_entry(entry: CpioEntry, ino: int) -> bytes:
    name = entry.name.encode("utf-8") + b"\x00"
    payload = bytes(entry.data)
    header = MAGIC_NEWC
    header += _hex(ino or entry.ino or 1)
    header += _hex(entry.mode)
    header += _hex(entry.uid)
    header += _hex(entry.gid)
    header += _hex(entry.nlink if not entry.is_dir else 2)
    header += _hex(entry.mtime)
    header += _hex(len(payload))
    header += _hex(entry.devmajor)
    header += _hex(entry.devminor)
    header += _hex(entry.rdevmajor)
    header += _hex(entry.rdevminor)
    header += _hex(len(name))
    header += _hex(0)
    blob = header + name
    blob = blob + b"\x00" * (_align4(len(blob)) - len(blob))
    blob += payload
    blob += b"\x00" * (_align4(len(blob)) - len(blob))
    return blob


def _serialize_trailer() -> bytes:
    name = TRAILER.encode("ascii") + b"\x00"
    # 11 zero fields (ino..rdevminor), then namesize, then check
    header = MAGIC_NEWC + b"0" * (8 * 11) + _hex(len(name)) + _hex(0)
    blob = header + name
    blob += b"\x00" * (_align4(len(blob)) - len(blob))
    return blob


@dataclass
class Segment:
    """One cpio archive inside the ramdisk blob."""

    entries: List[CpioEntry] = field(default_factory=list)
    gap: bytes = b""  # bytes that followed the trailer originally
    label: str = ""

    def find(self, name: str) -> Optional[CpioEntry]:
        target = _normalize(name)
        for entry in self.entries:
            if _normalize(entry.name) == target:
                return entry
        return None

    def has(self, name: str) -> bool:
        return self.find(name) is not None

    def add(self, entry: CpioEntry) -> None:
        for idx, existing in enumerate(self.entries):
            if existing.name == entry.name:
                self.entries[idx] = entry
                return
        self.entries.append(entry)

    def names(self) -> List[str]:
        return [e.name for e in self.entries]


class CpioArchive:
    """Ordered collection of cpio segments parsed from one ramdisk blob."""

    def __init__(self, segments: Optional[List[Segment]] = None):
        self.segments: List[Segment] = segments or []
        # compression format of each independently-compressed chunk
        self.chunk_formats: List[str] = []

    # -- construction ------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes) -> "CpioArchive":
        segments: List[Segment] = []
        pos = 0
        total = len(data)
        while pos < total:
            if data[pos : pos + 6] != MAGIC_NEWC:
                # padding or junk: jump to the next archive start
                nxt = data.find(MAGIC_NEWC, pos)
                if nxt == -1:
                    break
                pos = nxt
            parsed = _parse_header(data, pos)
            if parsed is None:
                # skip junk / padding until the next archive starts
                nxt = data.find(MAGIC_NEWC, pos)
                if nxt == -1:
                    break
                pos = nxt
                continue
            entry, pos = parsed
            entries: List[CpioEntry] = []
            while entry.name != TRAILER:
                entries.append(entry)
                if pos >= total:
                    raise VeritpathError("cpio: missing TRAILER!!!")
                parsed = _parse_header(data, pos)
                if parsed is None:
                    raise VeritpathError("cpio: bad entry inside archive")
                entry, pos = parsed
            # capture padding until either EOF or the next archive magic
            gap_start = pos
            scan = pos
            while scan < total and data[scan] == 0:
                scan += 1
            if scan < total and data[scan : scan + 6] == MAGIC_NEWC:
                gap = data[gap_start:scan]
                pos = scan
            else:
                gap = data[gap_start:total]
                pos = total
            label = cls._guess_label(entries)
            segments.append(Segment(entries=entries, gap=gap, label=label))
        if not segments:
            raise VeritpathError("no valid cpio archive found in ramdisk")
        return cls(segments)

    @staticmethod
    def _guess_label(entries: List[CpioEntry]) -> str:
        names = {e.name for e in entries}
        if any(n.startswith("first_stage_ramdisk") for n in names):
            return "first_stage_ramdisk"
        if "/init" in names or "init" in names:
            return "main"
        if any(n.startswith("system/etc/") for n in names):
            return "recovery"
        return "segment"

    @classmethod
    def empty(cls) -> "CpioArchive":
        return cls([Segment([], b"", "main")])

    # -- inspection --------------------------------------------------------
    @property
    def main(self) -> Segment:
        """The segment that most likely holds `/init` (fallback: last)."""
        for seg in self.segments:
            if seg.label == "main":
                return seg
        return self.segments[-1]

    def all_entries(self) -> List[CpioEntry]:
        out: List[CpioEntry] = []
        for seg in self.segments:
            out.extend(seg.entries)
        return out

    def find(self, name: str) -> Optional[CpioEntry]:
        for seg in self.segments:
            entry = seg.find(name)
            if entry is not None:
                return entry
        return None

    def names(self) -> List[str]:
        return [e.name for e in self.all_entries()]

    def add(self, entry: CpioEntry, segment: Optional[int] = None) -> None:
        seg = self.segments[segment] if segment is not None else self.main
        seg.add(entry)

    def ensure_dir(self, path: str) -> None:
        """Create the leading directories of `path` if they are missing."""
        parts = [p for p in path.strip("/").split("/") if p]
        for i in range(len(parts) - 1):
            parent = "/" + "/".join(parts[: i + 1])
            if self.find(parent) is None:
                self.add(CpioEntry(name=parent, mode=0o40755, nlink=2))

    # -- serialization -----------------------------------------------------
    def serialize_segments(self) -> List[bytes]:
        """Serialise every segment separately (vendor_boot needs the offsets)."""
        blobs: List[bytes] = []
        ino = 300000
        for seg in self.segments:
            out = bytearray()
            for entry in seg.entries:
                ino += 1
                out += _serialize_entry(entry, ino)
            out += _serialize_trailer()
            out += seg.gap
            blobs.append(bytes(out))
        return blobs

    def serialize(self) -> bytes:
        return b"".join(self.serialize_segments())

    def to_file_listing(self) -> str:
        lines = []
        for idx, seg in enumerate(self.segments):
            lines.append(f"# segment {idx} ({seg.label}) - {len(seg.entries)} entries")
            for entry in seg.entries:
                lines.append(
                    f"{entry.mode & 0o7777:04o} {entry.uid}:{entry.gid} "
                    f"{len(entry.data):>9} {entry.name}"
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# on-disk extraction / building
# ---------------------------------------------------------------------------


def extract_to_dir(archive: CpioArchive, root: str) -> None:
    """Materialise a ramdisk archive into a directory tree."""
    root_path = Path(root)
    for idx, seg in enumerate(archive.segments):
        base = root_path if len(archive.segments) == 1 else root_path / f"segment{idx}"
        base.mkdir(parents=True, exist_ok=True)
        (base / ".segment-label").write_text(seg.label + "\n")
        for entry in seg.entries:
            target = base / entry.name.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            if entry.is_symlink:
                link = entry.data.decode("utf-8", errors="replace")
                if target.exists() or target.is_symlink():
                    target.unlink()
                os.symlink(link, target)
                continue
            target.write_bytes(entry.data)
            os.chmod(target, entry.perms)


def build_from_dir(root: str) -> CpioArchive:
    """Rebuild a ramdisk archive from a directory produced by `extract_to_dir`."""
    root_path = Path(root)
    label_files = sorted(root_path.glob("**/.segment-label"))
    segment_dirs = sorted(
        p for p in root_path.iterdir() if p.is_dir() and p.name.startswith("segment")
    )
    archive = CpioArchive([])
    if not segment_dirs or not label_files:
        archive.segments.append(
            Segment(entries=_collect(root_path, root_path), gap=b"", label="main")
        )
        return archive
    for seg_dir in segment_dirs:
        label = (
            (seg_dir / ".segment-label").read_text().strip()
            if (seg_dir / ".segment-label").exists()
            else "segment"
        )
        entries = _collect(seg_dir, seg_dir)
        entries = [e for e in entries if e.name != ".segment-label"]
        archive.segments.append(Segment(entries=entries, gap=b"", label=label or "segment"))
    return archive


def _collect(base: Path, root: Path) -> List[CpioEntry]:
    entries: List[CpioEntry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        for name in sorted(dirnames):
            rel_dir = (rel / name).as_posix()
            entries.append(
                CpioEntry(name=(rel_dir if rel_dir != "." else "."), mode=0o40755, nlink=2)
            )
        for name in sorted(filenames):
            file_path = Path(dirpath) / name
            rel_file = (rel / name).as_posix()
            if rel_file.startswith("./"):
                rel_file = rel_file[2:]
            st = file_path.lstat()
            data = b""
            mode = st.st_mode & 0o7777
            if os.path.islink(file_path):
                mode = 0o120777
                data = os.readlink(file_path).encode("utf-8")
            elif file_path.is_file():
                data = file_path.read_bytes()
            entries.append(
                CpioEntry(
                    name=rel_file,
                    mode=mode,
                    uid=st.st_uid,
                    gid=st.st_gid,
                    mtime=int(st.st_mtime),
                    data=data,
                )
            )
    # keep a stable order: parents before children
    entries.sort(key=lambda e: (e.name.count("/"), e.name))
    return entries
