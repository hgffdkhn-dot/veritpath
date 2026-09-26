"""Execute an injection plan: unpack -> patch -> rebuild -> report."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .bootimg import BootImage, VendorBootImage, load_image
from .detector import Analysis, analyze
from .payload import InjectionResult, PayloadSpec, apply_payload
from .strategy import Options, Plan, build_plan
from .utils import VeritpathError, backup_file, log, sha256, sha256_file, warn


@dataclass
class InjectOutcome:
    plan: Plan
    analysis: Analysis
    spec: PayloadSpec
    outputs: Dict[str, str] = field(default_factory=dict)
    results: Dict[str, InjectionResult] = field(default_factory=dict)
    backups: Dict[str, str] = field(default_factory=dict)
    cmdline_added: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "payload": {"name": self.spec.name, "version": self.spec.version},
            "outputs": self.outputs,
            "backups": self.backups,
            "cmdline_added": self.cmdline_added,
            "added_files": {k: v.added for k, v in self.results.items()},
            "replaced_files": {k: v.replaced for k, v in self.results.items()},
            "plan": self.plan.to_dict(),
        }


def load_images(paths: Dict[str, str]) -> Dict[str, object]:
    images: Dict[str, object] = {}
    for role, path in paths.items():
        if not path:
            continue
        if not Path(path).is_file():
            raise VeritpathError(f"{role}: no such file: {path}")
        img = load_image(path, role_hint=role)
        setattr(img, "path", path)
        # init_boot.img is a boot image too, so make sure the role sticks
        img.role = role
        images[role] = img
    if not images:
        raise VeritpathError("no input images given")
    return images


def run_injection(
    paths: Dict[str, str],
    payload_dir: str,
    options: Optional[Options] = None,
    dry_run: bool = False,
) -> InjectOutcome:
    options = options or Options()
    images = load_images(paths)
    analysis = analyze(images)
    spec = PayloadSpec.load(payload_dir)

    problems = spec.check_compatibility(analysis.arch, analysis.android_api)
    plan = build_plan(analysis, spec, options)
    for problem in problems:
        plan.warnings.append(problem)
    if problems and not options.force:
        raise VeritpathError(
            "payload is not compatible with this image:\n  - "
            + "\n  - ".join(problems)
            + "\npass --force to inject anyway"
        )

    outcome = InjectOutcome(plan=plan, analysis=analysis, spec=spec)

    targets = [analysis.target] + list(plan.extra_targets)
    for role in targets:
        if not role:
            continue
        img = images.get(role)
        if img is None:
            warn(f"target {role} not supplied — skipped")
            continue
        archive = img.ramdisk_archive()
        result = _apply_to_image(img, archive, spec, plan, role)
        outcome.results[role] = result
        img.set_ramdisk(archive, options.ramdisk_format)
        if role == analysis.target:
            tokens: List[str] = []
            if plan.steps and any(
                s.action == "selinux" and "permissive" in s.detail for s in plan.steps
            ):
                tokens.append("androidboot.selinux=permissive")
            if options.cmdline:
                tokens += options.cmdline.split()
            tokens += spec.cmdline_append
            if tokens:
                _append_cmdline(img, tokens)
                outcome.cmdline_added = tokens

    if dry_run:
        return outcome

    for role in targets:
        if not role or role not in outcome.results:
            continue
        img = images[role]
        data = img.pack()
        src_path = Path(getattr(img, "path"))
        out_path = _output_path(src_path, options.output, role, len(targets) > 1)
        if out_path.exists() and Path(getattr(img, "path")).resolve() == out_path.resolve():
            raise VeritpathError("refusing to overwrite the input image in place")
        if options.keep_backup:
            backup = backup_file(src_path)
            outcome.backups[role] = str(backup)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        outcome.outputs[role] = str(out_path)
        log(f"wrote {out_path} ({len(data)} bytes, sha256={sha256(data)[:16]}…)")

    # machine readable record next to the output
    if outcome.outputs:
        first = Path(next(iter(outcome.outputs.values())))
        record = outcome.to_dict()
        record["input_sha256"] = {
            role: sha256_file(Path(getattr(images[role], "path"))) for role in outcome.outputs
        }
        (first.with_suffix(first.suffix + ".veritpath.json")).write_text(
            json.dumps(record, indent=2, default=str)
        )
    return outcome


def _apply_to_image(img, archive, spec: PayloadSpec, plan: Plan, role: str) -> InjectionResult:
    """Inject into the right cpio segment(s) of one image.

    vendor_boot carries several independent ramdisks (platform / recovery /
    dlkm): every one of them needs the payload, otherwise recovery or fastbootd
    would boot without it.
    """
    if isinstance(img, VendorBootImage) and len(archive.segments) > 1:
        merged = InjectionResult()
        for idx in range(len(archive.segments)):
            partial = apply_payload(archive, spec, idx)
            for field_name in ("added", "replaced", "backed_up", "rc_files"):
                merged_list = getattr(merged, field_name)
                merged_list += [
                    item for item in getattr(partial, field_name) if item not in merged_list
                ]
        return merged
    return apply_payload(archive, spec, plan.segment)


def _append_cmdline(img, tokens: List[str]) -> None:
    if isinstance(img, BootImage):
        img.append_cmdline(" ".join(tokens))
        return
    # vendor_boot keeps its cmdline in the vendor header
    existing = img.cmdline or ""
    addition = " ".join(t for t in tokens if t not in existing.split())
    if addition:
        img.cmdline = (existing + " " + addition).strip()


def _output_path(src: Path, output: Optional[str], role: str, multiple: bool) -> Path:
    if not output:
        return src.with_name(src.stem + ".veritpath" + src.suffix)
    out = Path(output)
    treat_as_dir = out.is_dir() or (out.suffix == "" and not out.exists())
    if treat_as_dir:
        name = f"{role}.veritpath.img" if multiple else src.stem + ".veritpath" + src.suffix
        return out / name
    if multiple:
        return out.with_name(out.stem + f".{role}" + out.suffix)
    return out
