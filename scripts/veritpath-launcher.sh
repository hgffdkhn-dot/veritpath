#!/bin/sh
# veritpath launcher — runs the bundled zipapp with whatever Python is around.
#
# Why a launcher instead of relying on the .pyz shebang:
#   * /storage/emulated/0 (sdcard) is mounted noexec on Android, so a shebang
#     line can never be honoured there — `sh veritpath` still works.
#   * not every environment has `python3`; Termux has `python`, some distros
#     only have `python3.x`.
#
# usage:
#   sh veritpath analyze --boot boot.img
#   ./veritpath analyze --boot boot.img      # where exec is allowed

set -eu

# resolve the directory holding this script, following symlinks
SELF=$0
while [ -L "$SELF" ]; do
    LINK=$(readlink "$SELF")
    case $LINK in
        /*) SELF=$LINK ;;
        *) SELF=$(dirname "$SELF")/$LINK ;;
    esac
done
HERE=$(cd "$(dirname "$SELF")" && pwd)

PYZ=
for candidate in \
    "$HERE/veritpath.pyz" \
    "$HERE/../share/veritpath/veritpath.pyz" \
    "$HOME/veritpath.pyz" \
    "$HOME/.local/share/veritpath/veritpath.pyz" \
    "/data/data/com.termux/files/usr/share/veritpath/veritpath.pyz"; do
    if [ -f "$candidate" ]; then
        PYZ=$candidate
        break
    fi
done

if [ -z "$PYZ" ]; then
    echo "veritpath: cannot find veritpath.pyz next to $HERE" >&2
    echo "  keep veritpath.pyz in the same directory, or run:" >&2
    echo "    python3 /path/to/veritpath.pyz --help" >&2
    exit 127
fi

# find a python interpreter
PY=
for name in python3 python python3.12 python3.11 python3.10 python3.9 python3.8; do
    if command -v "$name" >/dev/null 2>&1; then
        PY=$name
        break
    fi
done

if [ -z "$PY" ]; then
    echo "veritpath: no python interpreter found" >&2
    echo "  Linux:   apt install python3  (or your distro equivalent)" >&2
    echo "  Termux:  pkg install python" >&2
    exit 127
fi

exec "$PY" "$PYZ" "$@"
