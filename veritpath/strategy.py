"""Strategy: turn a detected layout into a concrete injection plan.

Detection answers "what is this device?" — this module answers "so what do we
do about it?".  Every rule below is data-driven so new layouts can be added
without touching the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .detector import Analysis
from .payload import PayloadSpec

LAYOUT_ADVICE = {
    "boot": (
        "Ramdisk lives inside boot.img (Android <= 12 non-GKI / v0-v4 with "
        "ramdisk). Patch boot.img only; kernel and DTB stay untouched."
    ),
    "init_boot": (
        "Android 13+ GKI: the generic ramdisk moved to init_boot.img while "
        "boot.img only carries the kernel. Patch init_boot.img."
    ),
    "vendor_boot": (
        "GKI 1.0 layout (Android 11/12): boot.img has no ramdisk, the generic "
        "ramdisk is the vendor_ramdisk inside vendor_boot.img. Patch it there "
        "and keep the fragment table in sync."
    ),
    "unknown": (
        "No ramdisk located. Supply init_boot.img (Android 13+) or a boot.img "
        "that actually contains a ramdisk."
    ),
}


@dataclass
class Options:
    patch_vendor_boot: bool = False
    selinux: str = "keep"  # keep | permissive
    cmdline: str = ""
    force: bool = False
    segment: Optional[int] = None
    ramdisk_format: Optional[str] = None
    output: Optional[str] = None
    keep_backup: bool = True


@dataclass
class Step:
    action: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {"action": self.action, "detail": self.detail}


@dataclass
class Plan:
    analysis: Analysis
    target: str = ""
    extra_targets: List[str] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    flash_commands: List[str] = field(default_factory=list)
    segment: Optional[int] = None

    def add(self, action: str, detail: str) -> None:
        self.steps.append(Step(action, detail))

    def to_dict(self) -> Dict[str, object]:
        return {
            "target": self.target,
            "extra_targets": self.extra_targets,
            "segment": self.segment,
            "steps": [s.as_dict() for s in self.steps],
            "notes": self.notes,
            "warnings": self.warnings,
            "flash_commands": self.flash_commands,
            "analysis": self.analysis.to_dict(),
        }

    def to_text(self) -> str:
        """Human readable counter-measure report."""
        a = self.analysis
        lines = ["veritpath injection plan", "=" * 60]
        lines.append(f"arch            : {a.arch}")
        lines.append(f"android         : {a.android_version} (API {a.android_api})")
        lines.append(f"ramdisk layout  : {a.ramdisk_layout}")
        lines.append(f"system-as-root  : {a.system_as_root}")
        lines.append(f"GKI             : {a.gki}")
        lines.append(f"target image    : {self.target or '(none)'}")
        if self.extra_targets:
            lines.append(f"extra targets   : {', '.join(self.extra_targets)}")
        lines.append("")
        lines.append("counter-measures")
        lines.append("-" * 60)
        lines.append(f"* {LAYOUT_ADVICE.get(a.ramdisk_layout, '')}")
        for idx, step in enumerate(self.steps, 1):
            lines.append(f"{idx:2d}. [{step.action}] {step.detail}")
        if self.notes:
            lines.append("")
            lines.append("notes")
            lines.append("-" * 60)
            for note in self.notes:
                lines.append(f"* {note}")
        if self.warnings:
            lines.append("")
            lines.append("warnings")
            lines.append("-" * 60)
            for warning in self.warnings:
                lines.append(f"! {warning}")
        if self.flash_commands:
            lines.append("")
            lines.append("flash")
            lines.append("-" * 60)
            for cmd in self.flash_commands:
                lines.append(f"  {cmd}")
        return "\n".join(lines)


def build_plan(
    analysis: Analysis, spec: Optional[PayloadSpec] = None, options: Optional[Options] = None
) -> Plan:
    options = options or Options()
    plan = Plan(analysis=analysis)
    target = analysis.target
    plan.target = target
    if not target:
        plan.warnings.append(LAYOUT_ADVICE["unknown"])
        return plan

    # 1) which segment receives the payload
    segment = options.segment
    if segment is None:
        segment = _pick_segment(analysis)
    plan.segment = segment
    segments = analysis.get("ramdisk_segments") or []
    if len(segments) > 1:
        plan.add(
            "ramdisk-segment",
            f"ramdisk has {len(segments)} cpio segments "
            f"({', '.join(str(s) for s in segments)}) -> inject into segment "
            f"{segment if segment is not None else 0}",
        )
    else:
        plan.add("ramdisk-segment", "single cpio segment ramdisk")

    # 2) compression
    fmt = analysis.get("ramdisk_compression")
    if options.ramdisk_format:
        plan.add("ramdisk-compression", f"re-compress with {options.ramdisk_format} (forced)")
    elif fmt:
        plan.add("ramdisk-compression", f"keep the original {fmt} compression when rebuilding")

    # 3) payload files
    if spec is not None:
        for f in spec.files:
            detail = f"{f.src} -> {f.dest} (mode {f.mode})"
            if f.backup_as:
                detail += f", previous file kept as {f.backup_as}"
            plan.add("payload-file", detail)
        if spec.rc:
            plan.add(
                "init-rc",
                f"write {spec.rc.file} and import it from "
                f"{', '.join(spec.rc.import_into) or '/init.rc'}",
            )
        # safety: never lose the original /init
        for f in spec.files:
            if f.dest == "/init" and not f.backup_as:
                f.backup_as = "/init.real"
                plan.warnings.append(
                    "payload replaces /init — the original is kept as /init.real "
                    "so the device can still boot if the payload fails"
                )

    # 4) system-as-root specifics
    if analysis.system_as_root:
        plan.notes.append(
            "system-as-root: ramdisk files vanish after init switches root to "
            "/system. Anything that must survive boot has to be copied out by the "
            "payload's own init/rc (veritpath runs your rc before the switch)."
        )
    else:
        plan.notes.append("legacy root layout: injected files stay visible in / after boot.")

    # 5) SELinux
    selinux = options.selinux
    if spec is not None and spec.selinux == "permissive":
        selinux = "permissive"
    if selinux == "permissive":
        plan.add(
            "selinux",
            "append androidboot.selinux=permissive to the kernel cmdline "
            "(the payload's own policy is not merged)",
        )
    else:
        plan.add(
            "selinux", "leave SELinux enforcing; the payload must ship or reuse an existing domain"
        )

    # 6) cmdline
    extra_tokens: List[str] = []
    if options.cmdline:
        extra_tokens += options.cmdline.split()
    if spec is not None:
        extra_tokens += spec.cmdline_append
    if extra_tokens:
        plan.add("cmdline", "append: " + " ".join(extra_tokens))

    # 7) vendor_boot / recovery
    if options.patch_vendor_boot and "vendor_boot" in analysis.images:
        plan.extra_targets.append("vendor_boot")
        plan.add(
            "vendor-boot",
            "also patch every vendor_ramdisk fragment (platform + recovery) "
            "so recovery/fastbootd carry the payload; the fragment table is "
            "rebuilt automatically",
        )
    elif "vendor_boot" in analysis.images and analysis.get("vendor_boot_fragments"):
        plan.notes.append(
            "vendor_boot.img present: add --patch-vendor-boot if you need the "
            "payload inside recovery / fastbootd."
        )

    # 8) GKI / kernel
    if analysis.gki:
        plan.notes.append(
            "GKI device: never touch the kernel image — only the ramdisk is "
            "patched, otherwise the GKI signature check fails."
        )

    # 9) signing & flashing
    slot = analysis.get("slot")
    plan.warnings.append(
        "The patched image is no longer signed by the OEM key: disable dm-verity "
        "or re-sign, otherwise the device refuses to boot."
    )
    flash_name = {"init_boot": "init_boot", "vendor_boot": "vendor_boot"}.get(target, "boot")
    if slot and slot in ("a", "b"):
        plan.flash_commands.append(f"fastboot flash {flash_name}_{slot} {flash_name}.veritpath.img")
    else:
        plan.flash_commands.append(f"fastboot flash {flash_name} {flash_name}.veritpath.img")
    plan.flash_commands.append(
        "fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img"
    )
    plan.flash_commands.append("fastboot reboot")

    # 10) already patched
    if analysis.patch_marker_present and not options.force:
        plan.warnings.append(
            "Image already contains a veritpath payload; pass --force to re-inject anyway."
        )

    plan.notes.extend(analysis.notes)
    plan.warnings.extend(analysis.warnings)
    return plan


def _pick_segment(analysis: Analysis) -> Optional[int]:
    segments = analysis.get("ramdisk_segments") or []
    if not segments:
        return None
    for idx, label in enumerate(segments):
        if label == "main":
            return idx
    return len(segments) - 1
