#!/usr/bin/env bash
# Build veritpath as a native Android binary (like magiskboot: no interpreter,
# no runtime deps - adb push it to the phone and run it).
#
#   ./build-android.sh                 # every ABI the NDK supports
#   ./build-android.sh arm64           # only arm64-v8a
#   ANDROID_NDK_HOME=/path/to/ndk ./build-android.sh
#
# Output: dist/veritpath-android-<abi>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
OUT="$ROOT/dist"
mkdir -p "$OUT"

API=21
SRCS=(src/main.c src/util.c src/compress.c src/cpio.c src/bootimg.c \
      src/detect.c src/json.c src/payload.c src/strategy.c)

HOST_OS=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$HOST_OS" in
    linux) HOST_TAG=linux-x86_64 ;;
    darwin) HOST_TAG=darwin-x86_64 ;;
    *) echo "unsupported host: $HOST_OS" >&2; exit 1 ;;
esac

# ---------------------------------------------------------------- find the NDK
NDK="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
if [ -z "$NDK" ]; then
    for cand in \
        "${ANDROID_HOME:-}/ndk" \
        "${ANDROID_SDK_ROOT:-}/ndk" \
        "$HOME/Android/Sdk/ndk" \
        "/opt/android-ndk" \
        "/usr/local/android-ndk"; do
        [ -d "$cand" ] || continue
        latest=$(ls -1 "$cand" 2>/dev/null | sort -V | tail -1)
        [ -n "$latest" ] && NDK="$cand/$latest"
        [ -d "${NDK:-}" ] && break || NDK=""
    done
fi

# running on Android (Termux / adb shell) -> use the system clang
ON_DEVICE=0
CC_BIN=""
if [ -z "$NDK" ] || [ ! -d "$NDK" ]; then
    if [ -d /data/data/com.termux/files/usr ] || [ -d /system/lib64 ]; then
        if command -v clang >/dev/null 2>&1; then
            CC_BIN=clang
            ON_DEVICE=1
            echo "==> no NDK found, building on-device with clang"
        fi
    fi
    if [ "$ON_DEVICE" = 0 ]; then
        echo "veritpath: Android NDK not found" >&2
        echo "  install it:  sdkmanager --install 'ndk;26.3.11579264'" >&2
        echo "  or point at it:  ANDROID_NDK_HOME=/path/to/ndk $0" >&2
        exit 1
    fi
fi

if [ "$ON_DEVICE" = 0 ]; then
    CC_BIN="$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin/clang"
    if [ ! -x "$CC_BIN" ]; then
        echo "veritpath: clang not found at $CC_BIN" >&2
        exit 1
    fi
    echo "==> NDK: $NDK"
fi

# ------------------------------------------------------------------ ABI table
declare -a TRIPLES ABIS
TRIPLES=(aarch64-linux-android armv7a-linux-androideabi x86_64-linux-android i686-linux-android)
ABIS=(arm64-v8a armeabi-v7a x86_64 x86)

WANT="${1:-all}"
built=0
for i in "${!ABIS[@]}"; do
    abi="${ABIS[$i]}"
    triple="${TRIPLES[$i]}"
    if [ "$WANT" != "all" ] && [ "$WANT" != "$abi" ] && [ "$WANT" != "${abi%%-*}" ]; then
        continue
    fi
    echo "==> building $abi ($triple$API)"
    if [ "$ON_DEVICE" = 1 ]; then
        "$CC_BIN" -O2 -std=c11 -Wall -static -Isrc -D_GNU_SOURCE \
            "${SRCS[@]}" -lz -o "$OUT/veritpath-android-$abi"
    else
        "$CC_BIN" --target="${triple}${API}" -O2 -std=c11 -Wall -static \
            -Isrc -D_GNU_SOURCE "${SRCS[@]}" -lz \
            -o "$OUT/veritpath-android-$abi"
    fi
    chmod +x "$OUT/veritpath-android-$abi"
    echo "    -> $OUT/veritpath-android-$abi"
    built=$((built + 1))
done

if [ "$built" = 0 ]; then
    echo "veritpath: unknown ABI '$WANT' (try: arm64-v8a armeabi-v7a x86_64 x86)" >&2
    exit 1
fi

echo
echo "push it to the phone:"
echo "  adb push $OUT/veritpath-android-arm64-v8a /data/local/tmp/veritpath"
echo "  adb shell chmod 755 /data/local/tmp/veritpath"
echo "  adb shell /data/local/tmp/veritpath analyze --boot /sdcard/boot.img"
