#!/usr/bin/env python3
"""Build veritpath as a single-file zipapp (`veritpath.pyz`).

A zipapp is a ZIP archive with a `__main__.py` at its root, so any Python 3.7+
interpreter can run it directly:

    python3 veritpath.pyz analyze --boot boot.img
    ./veritpath.pyz analyze --boot boot.img      # needs exec permission

veritpath has no third-party dependencies, which makes this the easiest way to
drop the tool onto a Linux box, a Termux session, or any POSIX environment
that already ships Python.
"""

from __future__ import annotations

import shutil
import sys
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist"
ENTRY = """import sys

from veritpath.cli import main

if __name__ == "__main__":
    sys.exit(main())
"""


def main(argv: list) -> int:
    target = Path(argv[1]) if len(argv) > 1 else OUT / "veritpath.pyz"
    stage = ROOT / "build" / "zipapp"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    shutil.copytree(
        ROOT / "veritpath",
        stage / "veritpath",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (stage / "__main__.py").write_text(ENTRY)

    target.parent.mkdir(parents=True, exist_ok=True)
    zipapp.create_archive(str(stage), str(target), interpreter="/usr/bin/env python3")
    target.chmod(0o755)
    shutil.rmtree(stage)

    print(f"built {target} ({target.stat().st_size} bytes)")
    print(f"  python3 {target} --version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
