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

CC_BASE="-O2 -std=c11 -Wall -Wextra -Isrc"
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
    local ndk="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
    [ -d "$ndk" ] || { echo "  skip  android (set ANDROID_NDK_HOME)"; return 0; }
    local tc="$ndk/toolchains/llvm/prebuilt"
    tc="$tc/$(ls "$tc" | head -1)/bin"
    local api=24
    echo "  build android ($(basename "$ndk"))"
    for spec in "arm64-v8a:aarch64-linux-android" "armeabi-v7a:armv7a-linux-androideabi" \
                "x86_64:x86_64-linux-android" "x86:i686-linux-android"; do
        local abi="${spec%%:*}" triple="${spec##*:}"
        local cc="$tc/${triple}${api}-clang"
        [ -x "$cc" ] || { echo "        skip $abi"; continue; }
        $cc $CC_BASE --static "${SRCS[@]}" $LIBS -o "$OUT/veritpath-android-$abi"
        echo "        $abi -> veritpath-android-$abi"
    done
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
    android)       build_android ;;
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
