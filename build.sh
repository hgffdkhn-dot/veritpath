#!/usr/bin/env bash
# Build veritpath for any supported target.
#
#   ./build.sh                 # native build for this machine
#   ./build.sh android         # every Android ABI (needs NDK)
#   ./build.sh windows-x86_64  # needs mingw-w64
#   ./build.sh all             # everything the local toolchains allow
#
# Targets that have no toolchain installed are skipped with a hint, so the
# script never fails just because a cross compiler is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
OUT="$ROOT/dist"
mkdir -p "$OUT"

# -D_GNU_SOURCE matters: without it -std=c11 hides POSIX declarations
# (strtok_r, symlink, readlink, lstat ...) and the build warn-spams
CC_BASE="-O2 -std=c11 -Wall -Wextra -Isrc -D_GNU_SOURCE"
SRCS=(src/*.c)
LIBS="-lz"

# zlib is the one hard dependency: gzip-compressed ramdisks are everywhere.
# A cross compiler being installed says nothing about the target's zlib - the
# usual failure is "cannot find -lz" deep in the link, which tells nobody what
# to install. Check first and say exactly what is missing.
if [ -n "${ZLIB_DIR:-}" ]; then
    LIBS="-L$ZLIB_DIR $LIBS"
fi

can_link_z() {          # compiler, extra-flags
    local cc="$1" extra="$2"
    local tmp; tmp=$(mktemp -d)
    printf 'int main(void){return 0;}\n' > "$tmp/t.c"
    # shellcheck disable=SC2086
    if $cc $extra $LIBS "$tmp/t.c" -o "$tmp/t" >"$tmp/log" 2>&1; then
        rm -rf "$tmp"
        return 0
    fi
    rm -rf "$tmp"
    return 1
}

zlib_hint() {           # compiler
    local cc="$1"
    echo "        the target's zlib is missing, so the link would fail with"
    echo "        'cannot find -lz'. Fix one of these ways:"
    case "$cc" in
        aarch64-linux-gnu-gcc)
            echo "          sudo dpkg --add-architecture arm64"
            echo "          sudo apt-get update && sudo apt-get install zlib1g-dev:arm64" ;;
        arm-linux-gnueabihf-gcc)
            echo "          sudo dpkg --add-architecture armhf"
            echo "          sudo apt-get update && sudo apt-get install zlib1g-dev:armhf" ;;
        i686-linux-gnu-gcc)
            echo "          sudo apt-get install zlib1g-dev:i386" ;;
        *mingw*)
            echo "          sudo apt-get install libz-mingw-w64-dev" ;;
        *)
            echo "          install a zlib built for this target, or point at one:"
            echo "          ZLIB_DIR=/path/to/sysroot/lib ./build.sh <target>" ;;
    esac
    echo "        or let this script skip the target:  VP_SKIP_MISSING=1 ./build.sh all"
}

build_one() {           # name, compiler, extra-flags, suffix
    local name="$1" cc="$2" extra="$3" suffix="${4:-}"
    if ! command -v "${cc%% *}" >/dev/null 2>&1; then
        echo "  skip  $name (no ${cc%% *})"
        return 0
    fi
    if ! can_link_z "$cc" "$extra"; then
        echo "  skip  $name (no zlib for this target)"
        zlib_hint "$cc"
        if [ -n "${VP_SKIP_MISSING:-}" ]; then
            return 0
        fi
        # keep going on 'all', fail loudly for an explicit target
        if [ "${VP_TARGET_EXPLICIT:-1}" = "1" ]; then
            return 1
        fi
        return 0
    fi
    echo "  build $name"
    # shellcheck disable=SC2086
    $cc $CC_BASE $extra "${SRCS[@]}" $LIBS -o "$OUT/veritpath-$name$suffix" 2>&1 | sed 's/^/        /'
    file "$OUT/veritpath-$name$suffix" 2>/dev/null | sed 's/^/        /' || true
}

build_android() {
    # the android script already knows how to find, install or fall back
    bash "$ROOT/build-android.sh" "${1:-all}"
}

target="${1:-native}"
if [ "$target" = "all" ] || [ "$target" = "android" ]; then
    VP_TARGET_EXPLICIT=0
fi
export VP_TARGET_EXPLICIT
case "$target" in
    native)
        cc="${CC:-cc}"
        echo "  build native"
        $cc $CC_BASE "${SRCS[@]}" $LIBS -o "$OUT/veritpath"
        echo "  -> $OUT/veritpath"
        ;;
    linux-x86_64)  build_one linux-x86_64  x86_64-linux-gnu-gcc "-static" ;;
    linux-aarch64) build_one linux-aarch64 aarch64-linux-gnu-gcc "-static" ;;
    linux-i686)    build_one linux-i686    i686-linux-gnu-gcc "-static" ;;
    windows-x86_64) build_one windows-x86_64.exe x86_64-w64-mingw32-gcc "-static" "" ;;
    windows-i686)   build_one windows-i686.exe   i686-w64-mingw32-gcc   "-static" "" ;;
    macos-x86_64)  build_one macos-x86_64  x86_64-apple-darwin-clang "" ;;
    macos-arm64)   build_one macos-arm64   arm64-apple-darwin-clang  "" ;;
    android)       build_android "${2:-all}" ;;
    all)
        build_one linux-x86_64   x86_64-linux-gnu-gcc "-static"
        build_one linux-aarch64  aarch64-linux-gnu-gcc "-static"
        build_one windows-x86_64.exe x86_64-w64-mingw32-gcc "-static" ""
        build_android
        echo "  note: macOS binaries need an Apple toolchain; use the GitHub"
        echo "        Actions release workflow to produce them."
        ;;
    *) echo "unknown target: $target" >&2; exit 2 ;;
esac
echo "done: $target"
