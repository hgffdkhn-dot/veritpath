"""Architecture / layout detection for Android boot images.

This is the "brain" of veritpath: it turns a pile of raw image bytes into a
set of facts (which slot holds the ramdisk, is this system-as-root, is this a
GKI device, which CPU arch, ...) that the strategy layer turns into actions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import compression
from .bootimg import (
    VRAMDISK_RECOVERY,
    BootImage,
    decode_os_version,
)
from .utils import VeritpathError

# Android API level -> marketing version
API_LEVELS = {
    26: "8.0",
    27: "8.1",
    28: "9",
    29: "10",
    30: "11",
    31: "12",
    32: "12L",
    33: "13",
    34: "14",
    35: "15",
    36: "16",
}

ELF_MACHINES = {
    3: "x86",
    40: "arm",
    62: "x86_64",
    183: "arm64",
    243: "riscv64",
    258: "riscv64",
    21: "ppc64",
}


@dataclass
class Finding:
    key: str
    value: object
    evidence: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {"key": self.key, "value": self.value, "evidence": self.evidence}


@dataclass
class Analysis:
    """Result of inspecting one device's boot images."""

    findings: List[Finding] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    images: Dict[str, object] = field(default_factory=dict)
    target: str = ""  # which image will be injected
    extra_targets: List[str] = field(default_factory=list)
    arch: str = "unknown"
    android_api: Optional[int] = None
    android_version: str = "unknown"
    ramdisk_layout: str = "unknown"
    system_as_root: bool = False
    gki: bool = False
    patch_marker_present: bool = False

    def add(self, key: str, value: object, evidence: str = "") -> None:
        self.findings.append(Finding(key, value, evidence))

    def get(self, key: str, default=None):
        for f in self.findings:
            if f.key == key:
                return f.value
        return default

    def to_dict(self) -> Dict[str, object]:
        return {
            "target": self.target,
            "extra_targets": self.extra_targets,
            "arch": self.arch,
            "android_api": self.android_api,
            "android_version": self.android_version,
            "ramdisk_layout": self.ramdisk_layout,
            "system_as_root": self.system_as_root,
            "gki": self.gki,
            "already_patched": self.patch_marker_present,
            "findings": [f.as_dict() for f in self.findings],
            "notes": self.notes,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# kernel / arch probing
# ---------------------------------------------------------------------------


def detect_kernel_arch(kernel: bytes, dtb: bytes = b"") -> Tuple[str, str]:
    """Return (arch, evidence) for a kernel payload."""
    if not kernel:
        return "unknown", "no kernel payload"
    raw = compression.probe_kernel(kernel)
    # ARM64 Linux "Image": PE/EFI header containing the ARM64 marker
    if raw[:2] == b"MZ" and b"ARM64" in raw[:0x60]:
        return "arm64", "ARM64 Image (EFI stub header)"
    if raw[:4] == b"\x7fELF":
        ei_class = raw[4]
        machine = struct.unpack_from("<H", raw, 18)[0]
        name = ELF_MACHINES.get(machine, f"elf-{machine}")
        if name == "x86" and ei_class == 2:
            name = "x86_64"
        return name, f"ELF{'64' if ei_class == 2 else '32'} e_machine={machine}"
    if len(raw) > 0x30 and struct.unpack_from("<I", raw, 0x24)[0] == 0x016F2818:
        return "arm", "ARM32 zImage magic 0x016f2818"
    if len(raw) > 0x206 and raw[0x202:0x206] == b"HdrS":
        # bzImage setup header: xloadflags bit0 means 64-bit entry is usable
        xloadflags = struct.unpack_from("<H", raw, 0x236)[0] if len(raw) > 0x238 else 0
        arch = "x86_64" if xloadflags & 0x01 else "x86"
        return arch, "x86 bzImage setup header (HdrS)"
    # fall back to the DTB
    if dtb:
        if b"arm,armv8" in dtb or b"arm,arm-v8" in dtb:
            return "arm64", "DTB compatible string"
        if b"arm,v7" in dtb or b"arm,arm1176" in dtb:
            return "arm", "DTB compatible string"
        if b"riscv" in dtb:
            return "riscv64", "DTB compatible string"
    return "unknown", "unable to identify kernel format"


def detect_kernel_compression(kernel: bytes) -> str:
    return compression.detect(kernel) if kernel else "none"


# ---------------------------------------------------------------------------
# API level heuristics
# ---------------------------------------------------------------------------


def guess_api(images: Dict[str, object]) -> Tuple[Optional[int], str]:
    """Infer the Android API level from every clue we have."""
    boot = images.get("boot") or images.get("init_boot")
    vendor = images.get("vendor_boot")
    hv = getattr(boot, "header_version", None)
    osv = getattr(boot, "os_version", 0)
    info = decode_os_version(osv) if osv else {}
    version_str = info.get("version") if isinstance(info, dict) else None
    if version_str:
        major = int(str(version_str).split(".")[0])
        for api, ver in API_LEVELS.items():
            if str(ver).startswith(str(major)) and "." not in str(ver):
                return api, str(ver)
        return None, str(version_str)
    if images.get("init_boot") is not None:
        return 33, "13"
    if vendor is not None and getattr(vendor, "header_version", 3) >= 4:
        return 31, "12"
    if hv == 4:
        return 31, "12"
    if hv == 3:
        return 30, "11"
    if hv == 2:
        return 29, "10"
    if hv == 1:
        return 27, "8.1"
    if hv == 0:
        return 25, "7.1"
    return None, "unknown"


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

PATCH_MARKERS = ("init.veritpath.rc", "veritpath.json")


def analyze(images: Dict[str, object]) -> Analysis:
    """Inspect a set of loaded images and describe the device layout."""
    result = Analysis(images=images)
    boot = images.get("boot")
    init_boot = images.get("init_boot")
    vendor_boot = images.get("vendor_boot")
    recovery = images.get("recovery")

    for name, img in images.items():
        if img is None:
            continue
        result.add(
            f"{name}.header_version", getattr(img, "header_version", None), f"{name} image header"
        )
        if isinstance(img, BootImage):
            result.add(f"{name}.kernel_size", len(img.kernel))
            result.add(f"{name}.ramdisk_size", len(img.ramdisk))
            result.add(f"{name}.ramdisk_format", img.ramdisk_format if img.ramdisk else None)
            result.add(f"{name}.page_size", img.page_size)
            result.add(f"{name}.cmdline", img.full_cmdline)
            os_info = decode_os_version(img.os_version)
            if os_info.get("version"):
                result.add(
                    f"{name}.os_version",
                    os_info["version"],
                    f"patch level {os_info.get('patch_level')}",
                )
        else:
            result.add(f"{name}.ramdisk_size", len(img.ramdisk))
            result.add(f"{name}.fragments", [e.name for e in img.table] or "single vendor ramdisk")

    # --- arch -------------------------------------------------------------
    kernel_src = boot or init_boot or recovery
    dtb = getattr(kernel_src, "dtb_blob", b"") or b""
    # init_boot carries no kernel, so look for the first image that has one
    for candidate in (kernel_src, boot, recovery, init_boot):
        if candidate is not None and getattr(candidate, "kernel", b""):
            kernel_src = candidate
            break
    if kernel_src is not None and getattr(kernel_src, "kernel", b""):
        result.arch, evidence = detect_kernel_arch(kernel_src.kernel, dtb)
        result.add("arch", result.arch, evidence)
        result.add(
            "kernel_compression", detect_kernel_compression(kernel_src.kernel), "kernel payload"
        )
    elif vendor_boot is not None:
        result.arch, evidence = detect_kernel_arch(b"", vendor_boot.dtb)
        result.add("arch", result.arch, evidence)
    if result.arch == "unknown":
        result.add("arch", "unknown", "no kernel payload available")
        result.notes.append(
            "Architecture could not be determined: no image carries a kernel "
            "(init_boot.img alone has none). Supply boot.img as well, or pass "
            "the architecture explicitly to your payload to skip arch checks."
        )

    # --- android version --------------------------------------------------
    api, version = guess_api(images)
    result.android_api = api
    result.android_version = version
    result.add("android_version", version, f"api={api}" if api else "guessed from header layout")

    # --- where does the ramdisk live -------------------------------------
    boot_img: Optional[BootImage] = boot or init_boot or recovery  # type: ignore
    result.gki = bool(
        (boot_img is not None and boot_img.header_version >= 3 and not boot_img.has_ramdisk)
        or init_boot is not None
    )
    result.add("gki", result.gki, "boot image carries no ramdisk / init_boot present")

    if init_boot is not None:
        result.ramdisk_layout = "init_boot"
        result.target = "init_boot"
        evidence = "init_boot.img supplied: generic ramdisk lives there (Android 13+ GKI)"
    elif boot_img is not None and boot_img.has_ramdisk:
        result.ramdisk_layout = "boot"
        result.target = "boot"
        evidence = "boot/recovery image carries the ramdisk directly"
    elif vendor_boot is not None and vendor_boot.has_ramdisk:
        result.ramdisk_layout = "vendor_boot"
        result.target = "vendor_boot"
        evidence = "boot has no ramdisk, vendor_boot does (GKI Android 11/12 layout)"
    else:
        result.ramdisk_layout = "unknown"
        evidence = "no ramdisk found in any supplied image"
        result.warnings.append(
            "No ramdisk found — veritpath needs a boot/init_boot/vendor_boot image "
            "that actually contains one."
        )
    result.add("ramdisk_layout", result.ramdisk_layout, evidence)

    # --- system-as-root ---------------------------------------------------
    result.system_as_root, sar_evidence = _detect_system_as_root(images, result)
    result.add("system_as_root", result.system_as_root, sar_evidence)
    if result.system_as_root:
        result.notes.append(
            "system-as-root: the ramdisk is only the first-stage init, /system is "
            "mounted as '/'. Inject into the ramdisk, not into /system."
        )
    else:
        result.notes.append(
            "Legacy root layout: the ramdisk itself is the root filesystem, files "
            "injected here are visible right after boot."
        )

    # --- A/B --------------------------------------------------------------
    slot = _detect_slot(images)
    if slot:
        result.add("slot", slot, "slot suffix in cmdline or file name")
        result.notes.append(
            f"A/B device detected (slot '{slot}') — flash the patched image to the "
            "active slot or to both slots."
        )

    # --- first stage ramdisk / treble -------------------------------------
    target_img = images.get(result.target)
    if target_img is not None:
        try:
            archive = target_img.ramdisk_archive()
        except VeritpathError as exc:
            result.warnings.append(str(exc))
            archive = None
        if archive is not None:
            labels = [s.label for s in archive.segments]
            result.add("ramdisk_segments", labels, "cpio segments inside the ramdisk")
            if "first_stage_ramdisk" in labels:
                result.notes.append(
                    "Multi-stage ramdisk: 'first_stage_ramdisk' is loaded before the "
                    "main one — init/overlay files must go into the main segment."
                )
            result.add("ramdisk_entries", len(archive.all_entries()))
            if archive.find("/init") is not None:
                result.add("has_init", True, "/init present in ramdisk")
            if archive.find("/system/bin/init") is not None:
                result.add("ramdisk_contains_system", True, "/system/bin/init inside the ramdisk")
            result.patch_marker_present = any(
                archive.find("/" + marker) is not None for marker in PATCH_MARKERS
            )
            result.add(
                "already_patched",
                result.patch_marker_present,
                "veritpath marker file found in ramdisk"
                if result.patch_marker_present
                else "no veritpath marker in ramdisk",
            )
            if result.patch_marker_present:
                result.warnings.append(
                    "This image already carries a veritpath injection — a second "
                    "pass will replace it (use --force if that is what you want)."
                )

    # --- vendor_boot extras ----------------------------------------------
    if vendor_boot is not None and result.target != "vendor_boot":
        types = {e.type for e in vendor_boot.table}
        if VRAMDISK_RECOVERY in types:
            result.extra_targets.append("vendor_boot")
            result.notes.append(
                "vendor_boot holds a RECOVERY ramdisk fragment: patch it too if you "
                "need su inside recovery/fastbootd (--patch-vendor-boot)."
            )
    if vendor_boot is not None:
        result.add(
            "vendor_boot_fragments",
            [{"name": e.name, "type": e.type} for e in vendor_boot.table] or "none",
        )

    # --- compression ------------------------------------------------------
    if target_img is not None and getattr(target_img, "ramdisk", b""):
        result.add(
            "ramdisk_compression", compression.detect(target_img.ramdisk), "ramdisk payload magic"
        )
        result.notes.append(
            f"Ramdisk is {compression.detect(target_img.ramdisk)} compressed — "
            "veritpath rebuilds it with the same format."
        )
    return result


def _detect_system_as_root(images: Dict[str, object], result: Analysis) -> Tuple[bool, str]:
    """Heuristics for system-as-root (Android 9+)."""
    boot = images.get("boot") or images.get("init_boot")
    if boot is None:
        return False, "no boot image to inspect"
    # 1) explicit cmdline hints
    cmdline = getattr(boot, "full_cmdline", "")
    if "system_as_root" in cmdline or "androidboot.system_as_root" in cmdline:
        return True, "kernel cmdline mentions system_as_root"
    # 2) ramdisk content
    try:
        archive = boot.ramdisk_archive()
    except VeritpathError:
        archive = None
    if archive is not None:
        names = set(archive.names())
        if "/system/bin/init" in names or "/system/build.prop" in names:
            return False, "ramdisk contains /system/bin/init (rootfs-style image)"
        if "/init" in names and "/system" in names:
            return True, "ramdisk has /init plus an empty /system mount point"
        if "/init" in names and any(n.startswith("system/") for n in names) is False:
            return True, "ramdisk only holds first-stage init (no /system payload)"
    api = result.android_api
    if api is not None and api >= 28:
        return True, f"API {api} (>=28) implies system-as-root"
    if api is not None:
        return False, f"API {api} (<28) predates system-as-root"
    return False, "insufficient evidence"


def _detect_slot(images: Dict[str, object]) -> Optional[str]:
    for name, img in images.items():
        cmdline = getattr(img, "full_cmdline", "") or getattr(img, "cmdline", "")
        for token in cmdline.split():
            if token.startswith("androidboot.slot_suffix="):
                return token.split("=", 1)[1]
        bootconfig = getattr(img, "bootconfig", b"")
        if bootconfig and b"androidboot.slot_suffix" in bootconfig:
            return "a/b (from bootconfig)"
        path = getattr(img, "path", "")
        if path:
            stem = Path(path).stem
            if stem.endswith(("_a", "_b")):
                return stem[-1]
    return None
