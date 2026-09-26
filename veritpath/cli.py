"""veritpath command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .bootimg import BootImage, load_image
from .detector import analyze
from .injector import load_images, run_injection
from .payload import PayloadSpec
from .strategy import Options, build_plan
from .utils import VeritpathError, human_size, log, set_verbose


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="veritpath",
        description="veritpath — analyze boot/init_boot/vendor_boot images, "
        "detect the root layout and inject third-party su/init "
        "payloads correctly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  veritpath analyze --boot boot.img --vendor-boot vendor_boot.img\n"
            "  veritpath plan --init-boot init_boot.img --payload payloads/my-su\n"
            "  veritpath inject --init-boot init_boot.img --payload payloads/my-su \\\n"
            "                   --permissive -o out/\n"
            "  veritpath unpack boot.img -d work/ && veritpath repack work -o new.img\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"veritpath {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_inputs(p: argparse.ArgumentParser) -> None:
        p.add_argument("--boot", help="boot.img")
        p.add_argument("--init-boot", dest="init_boot", help="init_boot.img (Android 13+)")
        p.add_argument("--vendor-boot", dest="vendor_boot", help="vendor_boot.img")
        p.add_argument("--recovery", help="recovery.img")

    # analyze -------------------------------------------------------------
    p_analyze = sub.add_parser("analyze", help="detect arch / layout of boot images")
    add_inputs(p_analyze)
    p_analyze.add_argument("--json", action="store_true", help="machine readable output")
    p_analyze.set_defaults(func=cmd_analyze)

    # plan ----------------------------------------------------------------
    p_plan = sub.add_parser("plan", help="show the injection plan (dry run)")
    add_inputs(p_plan)
    p_plan.add_argument("--payload", required=True, help="payload directory")
    p_plan.add_argument("--patch-vendor-boot", action="store_true")
    p_plan.add_argument("--permissive", action="store_true")
    p_plan.add_argument("--cmdline", default="", help="extra kernel cmdline tokens")
    p_plan.add_argument("--segment", type=int, help="cpio segment index to patch")
    p_plan.add_argument(
        "--format",
        dest="ramdisk_format",
        choices=["gzip", "lz4", "lz4_legacy", "xz", "lzma", "bzip2"],
    )
    p_plan.add_argument("--force", action="store_true")
    p_plan.add_argument("--json", action="store_true")
    p_plan.set_defaults(func=cmd_plan)

    # inject --------------------------------------------------------------
    p_inject = sub.add_parser("inject", help="inject a payload into the boot images")
    add_inputs(p_inject)
    p_inject.add_argument("--payload", "-p", required=True, help="payload directory")
    p_inject.add_argument("-o", "--output", help="output file or directory")
    p_inject.add_argument(
        "--patch-vendor-boot",
        action="store_true",
        help="also patch the vendor_ramdisk (recovery/fastbootd)",
    )
    p_inject.add_argument(
        "--permissive",
        action="store_true",
        help="add androidboot.selinux=permissive to the cmdline",
    )
    p_inject.add_argument("--cmdline", default="", help="extra kernel cmdline tokens")
    p_inject.add_argument("--segment", type=int, help="cpio segment index to patch")
    p_inject.add_argument(
        "--format",
        dest="ramdisk_format",
        choices=["gzip", "lz4", "lz4_legacy", "xz", "lzma", "bzip2"],
    )
    p_inject.add_argument(
        "--force", action="store_true", help="ignore compatibility checks / double injection"
    )
    p_inject.add_argument(
        "--no-backup", action="store_true", help="do not keep a backup of the original image"
    )
    p_inject.add_argument(
        "--dry-run", action="store_true", help="patch in memory only, write nothing"
    )
    p_inject.add_argument("--json", action="store_true")
    p_inject.set_defaults(func=cmd_inject)

    # unpack / repack ------------------------------------------------------
    p_unpack = sub.add_parser("unpack", help="unpack an image into a directory")
    p_unpack.add_argument("image")
    p_unpack.add_argument("-d", "--dir", required=True)
    p_unpack.set_defaults(func=cmd_unpack)

    p_repack = sub.add_parser("repack", help="repack a directory into an image")
    p_repack.add_argument("dir")
    p_repack.add_argument("-o", "--output", required=True)
    p_repack.set_defaults(func=cmd_repack)

    return parser


def _paths(args) -> dict:
    return {
        "boot": args.boot,
        "init_boot": args.init_boot,
        "vendor_boot": args.vendor_boot,
        "recovery": args.recovery,
    }


def _options(args) -> Options:
    return Options(
        patch_vendor_boot=getattr(args, "patch_vendor_boot", False),
        selinux="permissive" if getattr(args, "permissive", False) else "keep",
        cmdline=getattr(args, "cmdline", ""),
        force=getattr(args, "force", False),
        segment=getattr(args, "segment", None),
        ramdisk_format=getattr(args, "ramdisk_format", None),
        output=getattr(args, "output", None),
        keep_backup=not getattr(args, "no_backup", False),
    )


# ---------------------------------------------------------------------------


def cmd_analyze(args) -> int:
    images = load_images(_paths(args))
    analysis = analyze(images)
    if args.json:
        print(json.dumps(analysis.to_dict(), indent=2, default=str))
        return 0
    print(f"veritpath {__version__} — boot image analysis")
    print("=" * 62)
    for key in (
        "arch",
        "android_version",
        "ramdisk_layout",
        "system_as_root",
        "gki",
        "kernel_compression",
        "ramdisk_compression",
        "slot",
        "ramdisk_segments",
        "already_patched",
    ):
        value = analysis.get(key)
        if value is None:
            continue
        print(f"  {key:<20} {value}")
    print("-" * 62)
    for finding in analysis.findings:
        if finding.key in (
            "arch",
            "android_version",
            "ramdisk_layout",
            "system_as_root",
            "gki",
            "slot",
        ):
            continue
        evidence = f"  ({finding.evidence})" if finding.evidence else ""
        print(f"  {finding.key:<20} {finding.value}{evidence}")
    if analysis.notes:
        print("-" * 62)
        for note in analysis.notes:
            print(f"  · {note}")
    if analysis.warnings:
        print("-" * 62)
        for warning in analysis.warnings:
            print(f"  ! {warning}")
    print("-" * 62)
    print(f"  injection target     {analysis.target or '(unknown)'}")
    if analysis.extra_targets:
        print(f"  optional targets     {', '.join(analysis.extra_targets)}")
    return 0


def cmd_plan(args) -> int:
    images = load_images(_paths(args))
    analysis = analyze(images)
    spec = PayloadSpec.load(args.payload)
    plan = build_plan(analysis, spec, _options(args))
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, default=str))
    else:
        print(plan.to_text())
    return 0


def cmd_inject(args) -> int:
    outcome = run_injection(_paths(args), args.payload, _options(args), dry_run=args.dry_run)
    if args.json:
        print(json.dumps(outcome.to_dict(), indent=2, default=str))
        return 0
    print(outcome.plan.to_text())
    print()
    if args.dry_run:
        print("dry run — nothing was written")
        return 0
    for role, path in outcome.outputs.items():
        print(f"  [{role}] -> {path}")
    for role, path in outcome.backups.items():
        print(f"  backup of {role}: {path}")
    for role, result in outcome.results.items():
        if result.added:
            print(f"  {role}: added {', '.join(result.added)}")
        if result.replaced:
            print(f"  {role}: replaced {', '.join(result.replaced)}")
        if result.backed_up:
            print(f"  {role}: kept originals as {', '.join(result.backed_up)}")
    if outcome.cmdline_added:
        print(f"  cmdline += {' '.join(outcome.cmdline_added)}")
    return 0


def cmd_unpack(args) -> int:
    from .cpio import extract_to_dir

    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    img = load_image(args.image)
    # keep the pristine image: repack needs its header, kernel and cmdline
    (out / "original.img").write_bytes(Path(args.image).read_bytes())
    (out / "header.json").write_text(json.dumps(img.summary(), indent=2, default=str))
    if isinstance(img, BootImage):
        (out / "kernel").write_bytes(img.kernel)
        (out / "dtb").write_bytes(img.dtb_blob)
        if img.ramdisk:
            archive = img.ramdisk_archive()
            (out / "ramdisk.cpio").write_bytes(archive.serialize())
            extract_to_dir(archive, str(out / "ramdisk"))
    else:
        if img.ramdisk:
            archive = img.ramdisk_archive()
            (out / "ramdisk.cpio").write_bytes(archive.serialize())
            extract_to_dir(archive, str(out / "ramdisk"))
        (out / "dtb").write_bytes(img.dtb)
        (out / "bootconfig").write_bytes(img.bootconfig)
    log(f"unpacked {args.image} into {out}")
    sizes = {
        "kernel": len(getattr(img, "kernel", b"")),
        "ramdisk": len(img.ramdisk),
        "dtb": len(img.dtb_blob if isinstance(img, BootImage) else img.dtb),
    }
    for name, size in sizes.items():
        if size:
            print(f"  {name:<8} {human_size(size)}")
    print(f"  edit ramdisk/ then: veritpath repack {out} -o {Path(args.image).stem}.new.img")
    return 0


def cmd_repack(args) -> int:
    from .cpio import build_from_dir

    work = Path(args.dir)
    original = work / "original.img"
    if not original.is_file():
        raise VeritpathError(
            f"{work}/original.img missing — only directories created by "
            "'veritpath unpack' can be repacked"
        )
    img = load_image(str(original))
    ramdisk_dir = work / "ramdisk"
    if ramdisk_dir.is_dir():
        archive = build_from_dir(str(ramdisk_dir))
        img.set_ramdisk(archive)
        log(f"rebuilt ramdisk from {ramdisk_dir} ({len(archive.all_entries())} entries)")
    kernel_file = work / "kernel"
    if isinstance(img, BootImage) and kernel_file.is_file():
        img.kernel = kernel_file.read_bytes()
    data = img.pack()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_bytes(data)
    log(f"wrote {args.output} ({human_size(len(data))})")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    set_verbose(args.verbose)
    try:
        return args.func(args)
    except VeritpathError as exc:
        print(f"veritpath: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
