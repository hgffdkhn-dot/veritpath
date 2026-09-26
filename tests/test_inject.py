import json
from pathlib import Path

import pytest

from tests.fixtures import make_legacy_boot, write_images
from veritpath.bootimg import BootImage, VendorBootImage
from veritpath.injector import run_injection
from veritpath.payload import PayloadSpec
from veritpath.strategy import Options
from veritpath.utils import VeritpathError

SU_BODY = b"#!/system/bin/sh\n# fake su\necho root\n"


def make_payload(tmp_path, manifest=None, name="payload"):
    payload = tmp_path / name
    payload.mkdir()
    (payload / "su").write_bytes(SU_BODY)
    if manifest is not None:
        (payload / "manifest.json").write_text(json.dumps(manifest))
    return str(payload)


def test_inject_into_init_boot(tmp_path):
    paths = write_images(tmp_path)
    payload = make_payload(
        tmp_path,
        {
            "name": "demo-su",
            "arch": ["arm64"],
            "files": [
                {"src": "su", "dest": "/su", "mode": "0755", "context": "u:object_r:rootfs:s0"}
            ],
            "rc": {
                "file": "/init.veritpath.rc",
                "import_into": ["/init.rc"],
                "content": "service demo /su --daemon\n    user root\n",
            },
        },
    )
    outcome = run_injection(
        paths, payload, Options(selinux="permissive", output=str(tmp_path / "out"))
    )

    assert list(outcome.outputs) == ["init_boot"]
    assert "boot" not in outcome.outputs  # GKI boot.img must stay untouched

    patched = BootImage.parse(Path(outcome.outputs["init_boot"]).read_bytes(), "init_boot")
    archive = patched.ramdisk_archive()
    assert archive.find("/su").data == SU_BODY
    assert archive.find("/su").perms == 0o755
    assert archive.find("/init.veritpath.rc") is not None
    assert b"import /init.veritpath.rc" in archive.find("/init.rc").data
    assert b"u:object_r:rootfs:s0" in archive.find("/file_contexts").data
    assert "androidboot.selinux=permissive" in patched.full_cmdline
    assert archive.find("/init") is not None  # original init untouched
    assert Path(outcome.outputs["init_boot"]).with_suffix(".img.veritpath.json").is_file()


def test_inject_into_legacy_boot(tmp_path):
    boot = tmp_path / "boot.img"
    boot.write_bytes(make_legacy_boot(2))
    outcome = run_injection(
        {"boot": str(boot)}, make_payload(tmp_path), Options(output=str(tmp_path / "out"))
    )
    patched = BootImage.parse(Path(outcome.outputs["boot"]).read_bytes(), "boot")
    assert patched.ramdisk_archive().find("/su") is not None
    assert outcome.backups["boot"].endswith(".veritpath.bak")


def test_vendor_boot_fragments_are_patched_and_table_updated(tmp_path):
    paths = write_images(tmp_path)
    outcome = run_injection(
        paths, make_payload(tmp_path), Options(patch_vendor_boot=True, output=str(tmp_path / "out"))
    )
    vendor = VendorBootImage.parse(Path(outcome.outputs["vendor_boot"]).read_bytes())
    archive = vendor.ramdisk_archive()
    assert len(archive.segments) == 2
    for segment in archive.segments:
        assert segment.find("/su") is not None
    # table sizes must describe the new fragments
    total = sum(e.size for e in vendor.table)
    assert total == len(vendor.ramdisk)


def test_auto_payload_directory_without_manifest(tmp_path):
    paths = write_images(tmp_path)
    payload = make_payload(tmp_path)
    spec = PayloadSpec.load(payload)
    assert any(f.dest == "/su" for f in spec.files)
    assert spec.rc is not None
    outcome = run_injection(paths, payload, Options(output=str(tmp_path / "out")))
    assert outcome.spec.name
    patched = BootImage.parse(Path(outcome.outputs["init_boot"]).read_bytes(), "init_boot")
    assert patched.ramdisk_archive().find("/su") is not None


def test_incompatible_payload_is_rejected_unless_forced(tmp_path):
    paths = write_images(tmp_path)
    payload = make_payload(tmp_path, {"name": "x86-only", "arch": ["x86_64"]})
    with pytest.raises(VeritpathError):
        run_injection(paths, payload, Options(output=str(tmp_path / "out")))
    outcome = run_injection(paths, payload, Options(force=True, output=str(tmp_path / "out2")))
    assert outcome.outputs


def test_dry_run_writes_nothing(tmp_path):
    paths = write_images(tmp_path)
    before = {k: Path(v).read_bytes() for k, v in paths.items()}
    outcome = run_injection(
        paths, make_payload(tmp_path), Options(output=str(tmp_path / "out")), dry_run=True
    )
    assert outcome.outputs == {}
    for key, path in paths.items():
        assert Path(path).read_bytes() == before[key]


def test_original_image_is_never_overwritten(tmp_path):
    paths = write_images(tmp_path)
    original = Path(paths["init_boot"]).read_bytes()
    run_injection(paths, make_payload(tmp_path), Options())
    assert Path(paths["init_boot"]).read_bytes() == original
    assert (tmp_path / "init_boot.veritpath.img").is_file()


def test_multi_stage_ramdisk_keeps_first_stage_clean(tmp_path):
    import veritpath.cpio as cpio
    from tests.fixtures import first_stage_ramdisk, make_legacy_boot, sample_ramdisk
    from veritpath import compression

    archive = cpio.CpioArchive([first_stage_ramdisk(), sample_ramdisk().segments[0]])
    boot = tmp_path / "boot.img"
    boot.write_bytes(make_legacy_boot(2, ramdisk=compression.compress(archive.serialize(), "gzip")))

    outcome = run_injection(
        {"boot": str(boot)}, make_payload(tmp_path), Options(output=str(tmp_path / "out"))
    )
    patched = BootImage.parse(Path(outcome.outputs["boot"]).read_bytes(), "boot")
    segments = patched.ramdisk_archive().segments
    assert [s.label for s in segments] == ["first_stage_ramdisk", "main"]
    assert segments[0].find("/su") is None  # first stage untouched
    assert segments[1].find("/su") is not None  # payload lands in main
