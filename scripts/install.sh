#!/bin/sh
# Install veritpath (zipapp + launcher) into a directory on PATH.
#
#   sh install.sh                 # auto-detect prefix
#   sh install.sh --prefix DIR    # install into DIR/bin
#
# Works on Termux ($PREFIX/bin), plain Linux (/usr/local/bin) and any
# writable directory you pass explicitly.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=

while [ $# -gt 0 ]; do
    case $1 in
        --prefix) PREFIX=$2; shift 2 ;;
        --prefix=*) PREFIX=${1#--prefix=}; shift ;;
        -h|--help)
            echo "usage: sh install.sh [--prefix DIR]"
            exit 0
            ;;
        *) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$PREFIX" ]; then
    if [ -n "${PREFIX_OVERRIDE:-}" ]; then
        PREFIX=$PREFIX_OVERRIDE
    elif [ -d /data/data/com.termux/files/usr ]; then
        PREFIX=/data/data/com.termux/files/usr            # Termux
    elif [ "$(id -u)" = "0" ] && [ -d /usr/local/bin ]; then
        PREFIX=/usr/local
    elif [ -d "$HOME/.local" ]; then
        PREFIX=$HOME/.local
    else
        PREFIX=$HOME/.local
    fi
fi

BIN=$PREFIX/bin
SHARE=$PREFIX/share/veritpath

# pick a python now so we can fail early with a useful message
PY=
for name in python3 python python3.12 python3.11 python3.10 python3.9 python3.8; do
    if command -v "$name" >/dev/null 2>&1; then
        PY=$name
        break
    fi
done
if [ -z "$PY" ]; then
    echo "veritpath: no python interpreter found" >&2
    echo "  Termux: pkg install python" >&2
    echo "  Linux:  apt install python3   (or your distro equivalent)" >&2
    exit 127
fi

echo "==> installing veritpath"
echo "    python : $PY ($($PY --version 2>&1))"
echo "    prefix : $PREFIX"

mkdir -p "$BIN" "$SHARE"
cp "$HERE/veritpath.pyz" "$SHARE/veritpath.pyz"
chmod 644 "$SHARE/veritpath.pyz"
cp "$HERE/veritpath" "$BIN/veritpath"
chmod 755 "$BIN/veritpath"

# make the launcher find the zipapp even when installed
if [ -d "$HERE/payloads" ]; then
    cp -r "$HERE/payloads" "$SHARE/payloads" 2>/dev/null || true
fi

echo "==> verifying"
if "$BIN/veritpath" --version; then
    echo
    echo "installed: $BIN/veritpath"
    case ":$PATH:" in
        *":$BIN:"*) echo "it is already on your PATH" ;;
        *) echo "add it to your PATH:  export PATH=\"$BIN:\$PATH\"" ;;
    esac
else
    echo "veritpath: install verification failed" >&2
    exit 1
fi
