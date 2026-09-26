#!/bin/sh
# Install the veritpath binary.
#
# The C build is a single self-contained file - there is no .pyz, no payload
# directory and no launcher, so one copy is all it takes. Anywhere you can
# chmod +x works: /data/local/tmp on a phone, /usr/local/bin on a desktop.
#
#   sh install.sh                 # pick a sensible prefix automatically
#   sh install.sh --prefix DIR    # install into DIR
#   sh install.sh --to-tmp        # /data/local/tmp (Android, needs no root)
#   sh install.sh --clean         # only remove a previous Python install
#
# It also removes the old Python-era installation. Those files are why a newly
# built binary used to be shadowed by a stale 0.1.0 copy on PATH.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=
MODE=auto

while [ $# -gt 0 ]; do
    case $1 in
        --prefix) PREFIX=$2; shift 2 ;;
        --prefix=*) PREFIX=${1#--prefix=}; shift ;;
        --to-tmp) MODE=tmp ;;
        --clean) MODE=clean ;;
        -h|--help)
            sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
    esac
done

# --------------------------------------------------------- find the binary
BIN_SRC=
for cand in "$HERE/build/veritpath" "$HERE/dist/veritpath" "$HERE/veritpath" \
            "$HERE/veritpath-linux-x86_64" "$HERE/veritpath-android-arm64-v8a"; do
    if [ -f "$cand" ] && [ -x "$cand" ]; then
        BIN_SRC=$cand
        break
    fi
done

# ------------------------------------------- drop the old Python-era install
clean_python() {
    removed=0
    for dir in /data/data/com.termux/files/usr "$HOME/.local" /usr/local; do
        [ -d "$dir" ] || continue
        if [ -f "$dir/bin/veritpath" ] && head -c 2 "$dir/bin/veritpath" 2>/dev/null | grep -q '#!'; then
            echo "==> removing old launcher $dir/bin/veritpath"
            rm -f "$dir/bin/veritpath"
            removed=1
        fi
        if [ -d "$dir/share/veritpath" ]; then
            echo "==> removing old data dir $dir/share/veritpath"
            rm -rf "$dir/share/veritpath"
            removed=1
        fi
    done
    [ "$removed" = 1 ] || echo "==> no old Python install found"
}

if [ "$MODE" = clean ]; then
    clean_python
    exit 0
fi

clean_python

if [ -z "$BIN_SRC" ]; then
    echo "veritpath: no built binary next to this script" >&2
    echo "  build it first:  make            (or: bash build.sh native)" >&2
    echo "  then re-run:     sh install.sh" >&2
    exit 1
fi

# ------------------------------------------------------------- pick a prefix
if [ -n "$PREFIX" ]; then
    :
elif [ "$MODE" = tmp ]; then
    PREFIX=/data/local/tmp
elif [ -d /data/data/com.termux/files/usr ]; then
    PREFIX=/data/data/com.termux/files/usr          # Termux
elif [ -d /system/bin ] && [ "$(id -u)" = 0 ]; then
    PREFIX=/data/local/tmp                          # rooted Android shell
elif [ "$(id -u)" = 0 ] && [ -d /usr/local/bin ]; then
    PREFIX=/usr/local
else
    PREFIX=$HOME/.local
fi

DEST_DIR=$PREFIX
case $PREFIX in
    */usr|*/.local|*/usr/local) DEST_DIR=$PREFIX/bin ;;
esac

echo "==> installing veritpath"
echo "    source : $BIN_SRC"
echo "    target : $DEST_DIR"

mkdir -p "$DEST_DIR"

# never leave a shadowing copy behind
rm -f "$DEST_DIR/veritpath"
cp "$BIN_SRC" "$DEST_DIR/veritpath"
chmod 755 "$DEST_DIR/veritpath"

echo "==> verifying"
if ! "$DEST_DIR/veritpath" --version; then
    echo "veritpath: installed binary does not run" >&2
    exit 1
fi
"$DEST_DIR/veritpath" doctor | sed 's/^/    /'

echo
echo "installed: $DEST_DIR/veritpath"
case ":$PATH:" in
    *":$DEST_DIR:"*) echo "  already on your PATH" ;;
    *) echo "  add to PATH:  export PATH=\"$DEST_DIR:\$PATH\"" ;;
esac
echo "  or just run it directly:  $DEST_DIR/veritpath --help"
