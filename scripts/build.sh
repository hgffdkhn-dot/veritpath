#!/usr/bin/env bash
# Build a single-file veritpath binary with PyInstaller.
# usage: ./scripts/build.sh [output-dir]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/dist}"
cd "$ROOT"

python -m PyInstaller \
  --noconfirm \
  --onefile \
  --clean \
  --name veritpath \
  --paths "$ROOT" \
  --distpath "$OUT" \
  --workpath "$ROOT/build/pyinstaller" \
  --specpath "$ROOT/build" \
  veritpath/__main__.py

echo "built: $OUT/veritpath"
"$OUT/veritpath" --version
