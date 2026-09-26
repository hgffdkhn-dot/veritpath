"""Small helpers shared across veritpath."""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import Iterable, Iterator

VERBOSE = False


class VeritpathError(Exception):
    """Base error type for all veritpath failures."""


def set_verbose(value: bool) -> None:
    global VERBOSE
    VERBOSE = value


def log(msg: str) -> None:
    print(f"==> {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"  · {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def debug(msg: str) -> None:
    if VERBOSE:
        print(f"  # {msg}", file=sys.stderr)


def read_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def write_bytes(path: str | Path, data: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pad_to(data: bytes, alignment: int, fill: bytes = b"\x00") -> bytes:
    if alignment <= 0:
        return data
    rem = len(data) % alignment
    if rem == 0:
        return data
    return data + fill * (alignment - rem)


def round_up(value: int, alignment: int) -> int:
    if alignment <= 0:
        return value
    rem = value % alignment
    return value if rem == 0 else value + (alignment - rem)


def parse_mode(value: str | int) -> int:
    if isinstance(value, int):
        return value
    value = value.strip()
    return int(value, 8) if len(value) <= 4 and value.isdigit() else int(value)


def format_mode(mode: int) -> str:
    return f"{mode & 0o7777:04o}"


def human_size(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}TiB"


def backup_file(path: str | Path, suffix: str = ".veritpath.bak") -> Path:
    src = Path(path)
    dst = src.with_name(src.name + suffix)
    idx = 1
    while dst.exists():
        idx += 1
        dst = src.with_name(src.name + suffix + f".{idx}")
    shutil.copy2(src, dst)
    return dst


def iter_files(root: Path) -> Iterator[Path]:
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def cstr(data: bytes) -> str:
    """Decode a NUL-terminated byte string."""
    end = data.find(b"\x00")
    if end == -1:
        end = len(data)
    return data[:end].decode("utf-8", errors="replace")


def flatten(items: Iterable) -> list:
    out = []
    for item in items:
        out.append(item)
    return out
