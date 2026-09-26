import pytest

from tests.fixtures import (
    FAKE_KERNEL_GZIP,
    make_boot_image,
    make_gki_boot,
    make_init_boot,
    make_legacy_boot,
    make_vendor_boot,
    sample_ramdisk,
)
from veritpath import compression
from veritpath.bootimg import (
    BootImage,
    VendorBootImage,
    decode_os_version,
    encode_os_version,
)
from veritpath.cpio import CpioEntry


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
def test_header_versions_roundtrip(version):
    ramdisk = compression.compress(sample_ramdisk().serialize(), "gzip")
    data = make_legacy_boot(version) if version <= 2 else make_boot_image(version, ramdisk=ramdisk)
    img = BootImage.parse(data, "boot")
    assert img.header_version == version
    assert img.has_ramdisk
    assert img.ramdisk_archive().find("/init") is not None

    rebuilt = BootImage.parse(img.pack())
    assert rebuilt.header_version == version
    assert rebuilt.kernel == img.kernel
    assert rebuilt.ramdisk == img.ramdisk
    assert rebuilt.full_cmdline == img.full_cmdline


def test_gki_boot_has_no_ramdisk():
    img = BootImage.parse(make_gki_boot(), "boot")
    assert img.header_version == 4
    assert not img.has_ramdisk
    with pytest.raises(Exception):
        img.ramdisk_archive()


def test_init_boot_layout():
    img = BootImage.parse(make_init_boot(), "init_boot")
    assert img.header_version == 4
    assert img.kernel == b""
    assert img.has_ramdisk


def test_cmdline_is_preserved():
    img = BootImage.parse(make_gki_boot(), "boot")
    assert "console=ttyMSM0,115200" in img.full_cmdline
    img.append_cmdline("androidboot.selinux=permissive")
    again = BootImage.parse(img.pack())
    assert "androidboot.selinux=permissive" in again.full_cmdline
    assert "console=ttyMSM0,115200" in again.full_cmdline


def test_cmdline_overflow_is_rejected():
    img = BootImage.parse(make_gki_boot(), "boot")
    with pytest.raises(Exception):
        img.append_cmdline("x" * 4000)


def test_vendor_boot_fragment_table():
    img = VendorBootImage.parse(make_vendor_boot(fragments=2))
    assert img.header_version == 4
    assert len(img.table) == 2
    assert [e.name for e in img.table] == ["ramdisk", "recovery"]
    again = VendorBootImage.parse(img.pack())
    assert again.ramdisk == img.ramdisk
    assert [(e.name, e.size, e.offset) for e in again.table] == [
        (e.name, e.size, e.offset) for e in img.table
    ]


def test_vendor_boot_multi_fragment_ramdisk_is_split_per_chunk():
    img = VendorBootImage.parse(make_vendor_boot(fragments=2))
    archive = img.ramdisk_archive()
    assert len(archive.segments) == 2
    assert archive.chunk_formats == ["gzip", "gzip"]


def test_vendor_boot_table_follows_size_changes():
    img = VendorBootImage.parse(make_vendor_boot(fragments=2))
    before = [(e.size, e.offset) for e in img.table]
    archive = img.ramdisk_archive()
    archive.segments[0].add(CpioEntry(name="su", mode=0o100755, data=b"x" * 2048))
    img.set_ramdisk(archive)
    after = [(e.size, e.offset) for e in img.table]
    assert after[0][0] != before[0][0]
    # the recovery fragment must shift by the delta of the first one
    assert after[1][1] == before[1][1] + (after[0][0] - before[0][0])


def test_os_version_codec():
    packed = encode_os_version("13.0.0", "2023-08")
    info = decode_os_version(packed)
    assert info["version"] == "13.0.0"
    assert info["patch_level"] == "2023-08"


def test_kernel_arch_detection():
    img = BootImage.parse(make_gki_boot(), "boot")
    assert img.kernel == FAKE_KERNEL_GZIP
    from veritpath.detector import detect_kernel_arch

    arch, evidence = detect_kernel_arch(img.kernel)
    assert arch == "arm64"
    assert "ARM64" in evidence
