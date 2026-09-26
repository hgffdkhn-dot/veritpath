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

build_one() {           # name, compiler, extra-flags, suffix
    local name="$1" cc="$2" extra="$3" suffix="${4:-}"
    if ! command -v "${cc%% *}" >/dev/null 2>&1; then
        echo "  skip  $name (no ${cc%% *})"
        return 0
    fi
    echo "  build $name"
    $cc $CC_BASE $extra "${SRCS[@]}" $LIBS -o "$OUT/veritpath-$name$suffix" 2>&1 | sed 's/^/        /'
    file "$OUT/veritpath-$name$suffix" 2>/dev/null | sed 's/^/        /' || true
}

build_android() {
    # the android script already knows how to find, install or fall back
    bash "$ROOT/build-android.sh" "${1:-all}"
}

target="${1:-native}"
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
    android)       build_android "$2" ;;
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
