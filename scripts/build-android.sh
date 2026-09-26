#!/usr/bin/env bash
# Build the portable (Android / Linux) distribution of veritpath.
#
# Produces dist/veritpath-portable/ containing:
#   veritpath.pyz   single-file zipapp (needs any python3)
#   veritpath       shell launcher, works even from a noexec sdcard
#   install.sh      Termux / Linux installer
#
# usage: bash scripts/build-android.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/veritpath-portable"
cd "$ROOT"

rm -rf "$OUT"
mkdir -p "$OUT"

echo "==> building zipapp"
python3 scripts/make_zipapp.py "$OUT/veritpath.pyz"

echo "==> copying launcher"
cp scripts/veritpath-launcher.sh "$OUT/veritpath"
chmod +x "$OUT/veritpath"

echo "==> copying install script"
cp scripts/install.sh "$OUT/install.sh"
chmod +x "$OUT/install.sh"

echo "==> copying payload example"
cp -r payloads "$OUT/payloads"

echo "==> smoke test"
(cd "$OUT" && sh veritpath --version)

echo
echo "portable build ready: $OUT"
ls -l "$OUT"
cat <<'TIP'

on the phone (Termux):
  pkg install python
  cd <this directory>
  sh install.sh            # installs into $PREFIX/bin/veritpath
  veritpath analyze --boot boot.img

without installing, from anywhere (sdcard included):
  sh veritpath analyze --boot boot.img
TIP
