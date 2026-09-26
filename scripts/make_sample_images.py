#!/usr/bin/env python3
"""Generate synthetic boot images so you can try veritpath without a device image.

    python scripts/make_sample_images.py [output-dir]

Creates boot.img (GKI, kernel only), init_boot.img (generic ramdisk) and
vendor_boot.img (platform + recovery fragments) — exactly the layout of a
modern Android 13+ GKI device.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fixtures import write_images  # noqa: E402


def main(argv: list) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "samples"
    paths = write_images(out)
    print(f"synthetic images written to {out}")
    for role, path in paths.items():
        print(f"  {role:<12} {path}")
    print()
    print("next steps:")
    print(
        f"  veritpath analyze --boot {paths['boot']} --init-boot "
        f"{paths['init_boot']} --vendor-boot {paths['vendor_boot']}"
    )
    print(
        f"  veritpath inject --init-boot {paths['init_boot']} "
        f"--payload payloads/example-su -o {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
