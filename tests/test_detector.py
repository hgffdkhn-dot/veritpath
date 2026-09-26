import json

from tests.fixtures import (
    make_gki_boot,
    make_legacy_boot,
    make_vendor_boot,
    write_images,
)
from veritpath.detector import analyze
from veritpath.injector import load_images


def test_android13_init_boot_layout(tmp_path):
    images = load_images(write_images(tmp_path))
    report = analyze(images)
    assert report.target == "init_boot"
    assert report.ramdisk_layout == "init_boot"
    assert report.gki is True
    assert report.arch == "arm64"
    assert report.android_api == 33
    assert report.system_as_root is True
    assert report.patch_marker_present is False


def test_legacy_boot_ramdisk_layout(tmp_path):
    path = tmp_path / "boot.img"
    path.write_bytes(make_legacy_boot(2))
    report = analyze(load_images({"boot": str(path)}))
    assert report.target == "boot"
    assert report.ramdisk_layout == "boot"
    assert report.gki is False
    assert report.android_api == 29


def test_gki1_vendor_ramdisk_layout(tmp_path):
    boot = tmp_path / "boot.img"
    vendor = tmp_path / "vendor_boot.img"
    boot.write_bytes(make_gki_boot())
    vendor.write_bytes(make_vendor_boot())
    report = analyze(load_images({"boot": str(boot), "vendor_boot": str(vendor)}))
    assert report.target == "vendor_boot"
    assert report.ramdisk_layout == "vendor_boot"
    assert report.gki is True


def test_missing_ramdisk_reports_a_warning(tmp_path):
    boot = tmp_path / "boot.img"
    boot.write_bytes(make_gki_boot())
    report = analyze(load_images({"boot": str(boot)}))
    assert report.target == ""
    assert report.warnings


def test_report_is_json_serialisable(tmp_path):
    report = analyze(load_images(write_images(tmp_path)))
    json.dumps(report.to_dict(), default=str)


def test_arch_unknown_without_kernel_gets_a_hint(tmp_path):
    images = load_images(write_images(tmp_path))
    report = analyze({"init_boot": images["init_boot"]})
    assert report.arch == "unknown"
    assert any("boot.img" in note for note in report.notes)


def test_arch_is_found_from_boot_kernel(tmp_path):
    images = load_images(write_images(tmp_path))
    report = analyze(images)
    assert report.arch == "arm64"
