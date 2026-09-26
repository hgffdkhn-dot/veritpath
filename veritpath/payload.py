"""Payload handling: what to inject, where, and with which permissions.

A *payload* is a directory (or zip) contributed by any developer, described by
a `manifest.json`:

    {
      "name": "my-su",
      "version": "1.0",
      "arch": ["arm64", "x86_64"],
      "min_api": 26,
      "files": [
        {"src": "su", "dest": "/su", "mode": "0755",
         "context": "u:object_r:rootfs:s0", "backup_as": "/su.orig"}
      ],
      "rc": {
        "file": "/init.veritpath.rc",
        "import_into": ["/init.rc"],
        "content": "service ..."
      },
      "cmdline_append": ["androidboot.veritpath=1"],
      "selinux": "permissive"
    }

Without a manifest veritpath auto-detects common file names (`su`, `init`,
`*.rc`, ...) so a plain "drop your files here" folder also works.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .cpio import CpioArchive, CpioEntry
from .utils import VeritpathError, parse_mode, warn

DEFAULT_RC_TEMPLATE = """# injected by veritpath (payload: {name})
on early-init
    export VERITPATH_PAYLOAD {name}

on post-fs-data
    exec - root root -- {su_path} --install

service veritpath-{name} {su_path} --daemon
    class late_start
    user root
    group root
    seclabel u:r:init:s0
    oneshot
"""

SUPPORTED_MANIFESTS = (
    "manifest.json",
    "veritpath.json",
    "manifest.yaml",
    "manifest.yml",
    "veritpath.yaml",
)


@dataclass
class PayloadFile:
    src: str
    dest: str
    mode: str = "0755"
    uid: int = 0
    gid: int = 0
    context: Optional[str] = None
    backup_as: Optional[str] = None
    required: bool = True
    symlink: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "PayloadFile":
        if "src" not in data or "dest" not in data:
            raise VeritpathError(f"payload file entry needs 'src' and 'dest': {data}")
        return cls(
            src=str(data["src"]),
            dest=str(data["dest"]),
            mode=str(data.get("mode", "0755")),
            uid=int(data.get("uid", 0)),
            gid=int(data.get("gid", 0)),
            context=data.get("context"),
            backup_as=data.get("backup_as"),
            required=bool(data.get("required", True)),
            symlink=data.get("symlink"),
        )


@dataclass
class RcInjection:
    file: str = "/init.veritpath.rc"
    import_into: List[str] = field(default_factory=list)
    append_to: Optional[str] = None
    content: str = ""
    content_file: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "RcInjection":
        return cls(
            file=str(data.get("file", "/init.veritpath.rc")),
            import_into=list(data.get("import_into", [])),
            append_to=data.get("append_to"),
            content=str(data.get("content", "")),
            content_file=data.get("content_file"),
        )


@dataclass
class PayloadSpec:
    name: str = "payload"
    version: str = "0.0.0"
    arch: List[str] = field(default_factory=list)
    min_api: Optional[int] = None
    max_api: Optional[int] = None
    files: List[PayloadFile] = field(default_factory=list)
    rc: Optional[RcInjection] = None
    cmdline_append: List[str] = field(default_factory=list)
    selinux: str = "keep"  # keep | permissive
    root_dir: Path = Path(".")
    markers: List[str] = field(default_factory=list)

    # -- loading -----------------------------------------------------------
    @classmethod
    def load(cls, directory: str) -> "PayloadSpec":
        root = Path(directory)
        if not root.is_dir():
            raise VeritpathError(f"payload directory not found: {directory}")
        manifest_path = None
        for candidate in SUPPORTED_MANIFESTS:
            if (root / candidate).is_file():
                manifest_path = root / candidate
                break
        if manifest_path is None:
            spec = cls.from_directory(root)
        else:
            spec = cls.from_manifest(manifest_path)
        spec.root_dir = root
        spec.validate_files()
        return spec

    @classmethod
    def from_manifest(cls, path: Path) -> "PayloadSpec":
        text = path.read_text()
        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional
                raise VeritpathError("YAML manifests need PyYAML (pip install pyyaml)") from exc
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        spec = cls(
            name=str(data.get("name", path.parent.name)),
            version=str(data.get("version", "0.0.0")),
            arch=list(data.get("arch", []) or []),
            min_api=data.get("min_api"),
            max_api=data.get("max_api"),
            files=[PayloadFile.from_dict(f) for f in data.get("files", [])],
            rc=RcInjection.from_dict(data["rc"]) if data.get("rc") else None,
            cmdline_append=list(data.get("cmdline_append", []) or []),
            selinux=str(data.get("selinux", "keep")),
            markers=list(data.get("markers", []) or []),
        )
        if spec.rc and spec.rc.content_file:
            spec.rc.content = (path.parent / spec.rc.content_file).read_text()
        return spec

    @classmethod
    def from_directory(cls, root: Path) -> "PayloadSpec":
        """Best-effort spec when no manifest is present."""
        spec = cls(name=root.name or "payload")
        su_path = None
        for path in sorted(p for p in root.iterdir() if p.is_file()):
            name = path.name
            if name in SUPPORTED_MANIFESTS:
                continue
            if name == "su" or name.startswith("su."):
                spec.files.append(
                    PayloadFile(src=name, dest="/su", mode="0755", context="u:object_r:rootfs:s0")
                )
                su_path = "/su"
            elif name == "init":
                spec.files.append(
                    PayloadFile(src=name, dest="/init", mode="0755", backup_as="/init.real")
                )
            elif name.endswith(".rc"):
                spec.files.append(PayloadFile(src=name, dest=f"/{name}", mode="0644"))
                spec.rc = RcInjection(
                    content=path.read_text(), file=f"/{name}", import_into=["/init.rc"]
                )
            elif name.endswith(".sh"):
                spec.files.append(PayloadFile(src=name, dest=f"/veritpath/{name}", mode="0755"))
            else:
                spec.files.append(PayloadFile(src=name, dest=f"/veritpath/{name}", mode="0755"))
        if su_path and spec.rc is None:
            spec.rc = RcInjection(
                content=DEFAULT_RC_TEMPLATE.format(name=spec.name, su_path=su_path),
                import_into=["/init.rc"],
            )
        return spec

    def validate_files(self) -> None:
        missing = [f.src for f in self.files if f.required and not (self.root_dir / f.src).exists()]
        if missing:
            raise VeritpathError(
                f"payload '{self.name}' misses required file(s): {', '.join(missing)}"
            )
        for f in self.files:
            if not f.dest.startswith("/"):
                raise VeritpathError(f"dest must be an absolute ramdisk path: {f.dest}")

    def check_compatibility(self, arch: str, api: Optional[int]) -> List[str]:
        problems = []
        if self.arch and arch not in self.arch and arch != "unknown":
            problems.append(f"payload supports {','.join(self.arch)} but the image is {arch}")
        if self.min_api and api and api < self.min_api:
            problems.append(f"payload needs API>={self.min_api}, image is API {api}")
        if self.max_api and api and api > self.max_api:
            problems.append(f"payload supports up to API {self.max_api}, image is {api}")
        return problems

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "arch": self.arch,
            "min_api": self.min_api,
            "files": [f.__dict__ for f in self.files],
            "rc": self.rc.__dict__ if self.rc else None,
            "cmdline_append": self.cmdline_append,
            "selinux": self.selinux,
        }


# ---------------------------------------------------------------------------
# injection
# ---------------------------------------------------------------------------


@dataclass
class InjectionResult:
    added: List[str] = field(default_factory=list)
    replaced: List[str] = field(default_factory=list)
    backed_up: List[str] = field(default_factory=list)
    rc_files: List[str] = field(default_factory=list)


def apply_payload(
    archive: CpioArchive, spec: PayloadSpec, segment: Optional[int] = None
) -> InjectionResult:
    """Inject every payload file / rc snippet into a ramdisk archive."""
    result = InjectionResult()
    for entry in spec.files:
        src = spec.root_dir / entry.src
        if not src.exists():
            if entry.required:
                raise VeritpathError(f"missing payload file: {src}")
            continue
        data = src.read_bytes()
        if entry.symlink:
            new = CpioEntry(
                name=entry.dest.lstrip("/"),
                mode=0o120777,
                uid=entry.uid,
                gid=entry.gid,
                data=entry.symlink.encode(),
            )
        else:
            mode = parse_mode(entry.mode) | 0o100000
            new = CpioEntry(
                name=entry.dest.lstrip("/"), mode=mode, uid=entry.uid, gid=entry.gid, data=data
            )
        existing = archive.find(entry.dest)
        if existing is not None:
            if entry.backup_as:
                from dataclasses import replace as _replace

                backup = _replace(existing)
                backup.name = entry.backup_as.lstrip("/")
                archive.add(backup, segment)
                result.backed_up.append(entry.backup_as)
            result.replaced.append(entry.dest)
        else:
            result.added.append(entry.dest)
        archive.ensure_dir(entry.dest)
        archive.add(new, segment)
    if spec.rc:
        _apply_rc(archive, spec, segment, result)
    _apply_file_contexts(archive, spec, segment, result)
    _write_marker(archive, spec, segment, result)
    return result


def _apply_rc(
    archive: CpioArchive, spec: PayloadSpec, segment: Optional[int], result: InjectionResult
) -> None:
    rc = spec.rc
    if rc is None:
        return
    content = rc.content or ""
    if not content:
        return
    if not content.endswith("\n"):
        content += "\n"
    rc_entry = CpioEntry(name=rc.file.lstrip("/"), mode=0o100644, data=content.encode())
    archive.add(rc_entry, segment)
    result.added.append(rc.file)
    result.rc_files.append(rc.file)

    targets = list(rc.import_into)
    if rc.append_to:
        _append_to_rc(archive, rc.append_to, content, segment, result, import_line=False)
        return
    if not targets:
        targets = ["/init.rc"]
    for target in targets:
        existing = archive.find(target)
        if existing is None:
            warn(f"rc target {target} not found in ramdisk — creating it")
            archive.add(
                CpioEntry(name=target.lstrip("/"), mode=0o100644, data=content.encode()), segment
            )
            result.added.append(target)
            continue
        text = existing.data.decode("utf-8", errors="replace")
        if rc.file in text:
            continue
        import_line = f"\nimport {rc.file}\n"
        existing.data = (text.rstrip("\n") + import_line).encode()
        result.rc_files.append(target)


def _append_to_rc(
    archive: CpioArchive,
    target: str,
    content: str,
    segment: Optional[int],
    result: InjectionResult,
    import_line: bool,
) -> None:
    existing = archive.find(target)
    text = existing.data.decode("utf-8", errors="replace") if existing else ""
    if content.strip() in text:
        return
    new_text = text.rstrip("\n") + "\n\n" + content
    archive.add(
        CpioEntry(
            name=target.lstrip("/"),
            mode=getattr(existing, "mode", 0o100644),
            data=new_text.encode(),
        ),
        segment,
    )
    result.added.append(target)


def _apply_file_contexts(
    archive: CpioArchive, spec: PayloadSpec, segment: Optional[int], result: InjectionResult
) -> None:
    """Append SELinux labels to the ramdisk file_contexts, when present."""
    labels = [(f.dest, f.context) for f in spec.files if f.context]
    if not labels:
        return
    target = None
    for candidate in ("/file_contexts", "/plat_file_contexts", "/sepolicy_contexts"):
        if archive.find(candidate) is not None:
            target = archive.find(candidate)
            break
    if target is None:
        # the ramdisk has no file_contexts: nothing to patch, init keeps defaults
        return
    text = target.data.decode("utf-8", errors="replace")
    lines = []
    for dest, context in labels:
        entry_line = f"{dest} {context}"
        if entry_line not in text:
            lines.append(entry_line)
    if lines:
        target.data = (text.rstrip("\n") + "\n" + "\n".join(lines) + "\n").encode()
        result.rc_files.append("/file_contexts")


def _write_marker(
    archive: CpioArchive, spec: PayloadSpec, segment: Optional[int], result: InjectionResult
) -> None:
    marker = {
        "tool": "veritpath",
        "payload": spec.name,
        "version": spec.version,
        "files": [f.dest for f in spec.files],
    }
    archive.add(
        CpioEntry(name="veritpath.json", mode=0o100644, data=json.dumps(marker, indent=2).encode()),
        segment,
    )
    result.added.append("/veritpath.json")
