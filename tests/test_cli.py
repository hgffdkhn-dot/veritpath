import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, expect_ok=True):
    proc = subprocess.run(
        [sys.executable, "-m", "veritpath", *args], cwd=ROOT, capture_output=True, text=True
    )
    if expect_ok:
        assert proc.returncode == 0, proc.stderr
    return proc


def test_analyze_json(tmp_path):
    from tests.fixtures import write_images

    paths = write_images(tmp_path)
    proc = run(
        "analyze",
        "--boot",
        paths["boot"],
        "--init-boot",
        paths["init_boot"],
        "--vendor-boot",
        paths["vendor_boot"],
        "--json",
    )
    data = json.loads(proc.stdout)
    assert data["target"] == "init_boot"
    assert data["arch"] == "arm64"


def test_analyze_human_output(tmp_path):
    from tests.fixtures import write_images

    paths = write_images(tmp_path)
    proc = run("analyze", "--init-boot", paths["init_boot"])
    assert "ramdisk_layout" in proc.stdout
    assert "init_boot" in proc.stdout


def test_plan_then_inject(tmp_path):
    from tests.fixtures import write_images

    paths = write_images(tmp_path)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "su").write_bytes(b"#!/system/bin/sh\n")

    plan = run(
        "plan",
        "--boot",
        paths["boot"],
        "--init-boot",
        paths["init_boot"],
        "--payload",
        str(payload),
        "--permissive",
    )
    assert "counter-measures" in plan.stdout
    assert "payload-file" in plan.stdout

    out = run(
        "inject",
        "--boot",
        paths["boot"],
        "--init-boot",
        paths["init_boot"],
        "--payload",
        str(payload),
        "--permissive",
        "-o",
        str(tmp_path / "out"),
    )
    assert "init_boot" in out.stdout
    assert (tmp_path / "out" / "init_boot.veritpath.img").is_file()


def test_unpack_and_repack(tmp_path):
    from tests.fixtures import make_legacy_boot
    from veritpath.bootimg import BootImage

    image = tmp_path / "boot.img"
    image.write_bytes(make_legacy_boot(2))
    work = tmp_path / "work"
    run("unpack", str(image), "-d", str(work))
    assert (work / "ramdisk" / "init").is_file()
    assert (work / "header.json").is_file()

    # write bytes: text mode would translate \n into \r\n on Windows
    (work / "ramdisk" / "hello.txt").write_bytes(b"injected by hand\n")
    rebuilt = tmp_path / "boot.new.img"
    run("repack", str(work), "-o", str(rebuilt))

    patched = BootImage.parse(rebuilt.read_bytes(), "boot")
    archive = patched.ramdisk_archive()
    assert archive.find("/hello.txt").data == b"injected by hand\n"
    assert archive.find("/init") is not None


def test_bad_input_reports_an_error(tmp_path):
    bad = tmp_path / "notaboot.img"
    bad.write_bytes(b"\x00" * 8192)
    proc = run("analyze", "--boot", str(bad), expect_ok=False)
    assert proc.returncode == 1
    assert "error" in proc.stderr.lower()
