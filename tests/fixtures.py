"""Synthetic boot images used by the test-suite and the CI smoke test."""

from __future__ import annotations

from pathlib import Path

from veritpath import compression
from veritpath.bootimg import (
    VRAMDISK_PLATFORM,
    VRAMDISK_RECOVERY,
    BootImage,
    VendorBootImage,
    VendorRamdiskEntry,
)
from veritpath.cpio import CpioArchive, CpioEntry, Segment

FAKE_KERNEL_ARM64 = b"MZ" + b"\x00" * 0x36 + b"ARM64" + b"\x00" * 0x100
FAKE_KERNEL_GZIP = compression.compress(FAKE_KERNEL_ARM64, compression.Format.GZIP)


def sample_ramdisk(with_system_mount: bool = True) -> CpioArchive:
    """A generic Android ramdisk: /init, /init.rc, a few dirs."""
    archive = CpioArchive.empty()
    archive.add(
        CpioEntry(name="init", mode=0o100755, data=b"#!/system/bin/sh\n# fake first stage init\n")
    )
    archive.add(
        CpioEntry(
            name="init.rc",
            mode=0o100644,
            data=b"import /init.environ.rc\n\non early-init\n    mkdir /dev\n",
        )
    )
    archive.add(
        CpioEntry(
            name="init.environ.rc",
            mode=0o100644,
            data=b"on early-init\n    export ANDROID_BOOTLOGO 1\n",
        )
    )
    if with_system_mount:
        archive.add(CpioEntry(name="system", mode=0o40755, nlink=2))
        archive.add(CpioEntry(name="vendor", mode=0o40755, nlink=2))
    archive.add(CpioEntry(name="proc", mode=0o40755, nlink=2))
    archive.add(
        CpioEntry(name="file_contexts", mode=0o100644, data=b"/init u:object_r:init_exec:s0\n")
    )
    return archive


def first_stage_ramdisk() -> Segment:
    return Segment(
        entries=[
            CpioEntry(name="first_stage_ramdisk/init", mode=0o100755, data=b"# fake first stage\n"),
            CpioEntry(name="first_stage_ramdisk/system", mode=0o40755, nlink=2),
        ],
        gap=b"",
        label="first_stage_ramdisk",
    )


def make_boot_image(
    header_version: int = 4,
    ramdisk: bytes | None = None,
    kernel: bytes | None = None,
    role: str = "boot",
    page_size: int = 4096,
    cmdline: str = "",
    os_version: int = 0,
) -> bytes:
    img = BootImage(role=role, header_version=header_version, page_size=page_size)
    img.kernel = kernel if kernel is not None else FAKE_KERNEL_GZIP
    img.cmdline = cmdline
    img.os_version = os_version
    if ramdisk is not None:
        img.ramdisk = ramdisk
    return img.pack()


def make_init_boot(ramdisk: bytes | None = None) -> bytes:
    """Android 13+ init_boot: header v4, generic ramdisk, no kernel."""
    img = BootImage(role="init_boot", header_version=4, page_size=4096)
    img.kernel = b""
    img.ramdisk = (
        ramdisk
        if ramdisk is not None
        else compression.compress(sample_ramdisk().serialize(), compression.Format.GZIP)
    )
    return img.pack()


def make_gki_boot(os_version: int = 0) -> bytes:
    """Android 13+ boot.img: kernel only, no ramdisk at all."""
    img = BootImage(role="boot", header_version=4, page_size=4096)
    img.kernel = FAKE_KERNEL_GZIP
    img.cmdline = "console=ttyMSM0,115200 androidboot.hardware=qcom"
    img.os_version = os_version
    return img.pack()


def make_legacy_boot(header_version: int = 2, ramdisk: bytes | None = None) -> bytes:
    img = BootImage(role="boot", header_version=header_version, page_size=4096)
    img.kernel = FAKE_KERNEL_GZIP
    img.ramdisk = (
        ramdisk
        if ramdisk is not None
        else compression.compress(sample_ramdisk().serialize(), compression.Format.GZIP)
    )
    img.cmdline = "androidboot.selinux=enforcing"
    return img.pack()


def make_vendor_boot(header_version: int = 4, fragments: int = 2) -> bytes:
    """vendor_boot with a platform and (optionally) a recovery fragment."""
    img = VendorBootImage(header_version=header_version, page_size=4096)
    img.dtb = b"\xd0\x0d\xfe\xed" + b"\x00" * 64
    platform = sample_ramdisk()
    blobs = []
    formats = []
    if fragments >= 2:
        recovery = CpioArchive(
            [
                Segment(
                    entries=[
                        CpioEntry(name="init", mode=0o100755, data=b"# recovery\n"),
                        CpioEntry(name="sbin/recovery", mode=0o100755, data=b"#\n"),
                    ],
                    gap=b"",
                    label="main",
                )
            ]
        )
        archives = [platform, recovery]
        types = [VRAMDISK_PLATFORM, VRAMDISK_RECOVERY]
        names = ["ramdisk", "recovery"]
    else:
        archives = [platform]
        types = [VRAMDISK_PLATFORM]
        names = ["ramdisk"]
    offset = 0
    for archive in archives:
        blob = compression.compress(archive.serialize(), compression.Format.GZIP)
        blobs.append(blob)
        formats.append(compression.Format.GZIP)
        img.table.append(
            VendorRamdiskEntry(
                size=len(blob),
                offset=offset,
                type=types[len(img.table)],
                name=names[len(img.table)],
                board_id=b"\x00" * 64,
            )
        )
        offset += len(blob)
    img.ramdisk = b"".join(blobs)
    return img.pack()


def write_images(directory: str | Path) -> dict:
    """Write a complete Android 13 style image set, return the paths."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "boot": out / "boot.img",
        "init_boot": out / "init_boot.img",
        "vendor_boot": out / "vendor_boot.img",
    }
    paths["boot"].write_bytes(make_gki_boot())
    paths["init_boot"].write_bytes(make_init_boot())
    paths["vendor_boot"].write_bytes(make_vendor_boot())
    return {k: str(v) for k, v in paths.items()}
