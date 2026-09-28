#!/usr/bin/env bash
# Build veritpath as a native Android binary (like magiskboot: no interpreter,
# no runtime deps - adb push it to the phone and run it).
#
#   ./build-android.sh                 # every ABI the NDK supports
#   ./build-android.sh arm64           # only arm64-v8a
#   ANDROID_NDK_HOME=/path/to/ndk ./build-android.sh
#
# If no NDK is present it tries to locate one, then to install one with
# sdkmanager (so CI only needs the stock Android SDK), and finally falls back to
# on-device clang when running inside Termux / adb shell.
#
# Output: dist/veritpath-android-<abi>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
OUT="$ROOT/dist"
mkdir -p "$OUT"

API="${ANDROID_API:-21}"
NDK_VERSION="${NDK_VERSION:-26.3.11579264}"
SRCS=(src/main.c src/util.c src/compress.c src/cpio.c src/bootimg.c \
      src/detect.c src/json.c src/payload.c src/strategy.c)

HOST_OS=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$HOST_OS" in
    linux) HOST_TAG=linux-x86_64 ;;
    darwin) HOST_TAG=darwin-x86_64 ;;
    *) echo "unsupported host: $HOST_OS" >&2; exit 1 ;;
esac

# ------------------------------------------------------------- locate the NDK
find_ndk() {
    local ndk="${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}"
    [ -n "$ndk" ] && [ -x "$ndk/toolchains/llvm/prebuilt/$HOST_TAG/bin/clang" ] && {
        echo "$ndk"
        return 0
    }
    local root
    for root in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}" \
                "$HOME/Android/Sdk" /usr/local/lib/android/sdk /opt/android-sdk; do
        [ -d "$root/ndk" ] || continue
        local v
        v=$(ls -1 "$root/ndk" 2>/dev/null | sort -V | tail -1)
        [ -n "$v" ] || continue
        if [ -x "$root/ndk/$v/toolchains/llvm/prebuilt/$HOST_TAG/bin/clang" ]; then
            echo "$root/ndk/$v"
            return 0
        fi
    done
    # an NDK unpacked somewhere plain
    for cand in /opt/android-ndk /usr/local/android-ndk; do
        [ -x "$cand/toolchains/llvm/prebuilt/$HOST_TAG/bin/clang" ] && {
            echo "$cand"
            return 0
        }
    done
    return 1
}

install_ndk() {
    local sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
    [ -z "$sdk" ] && { [ -d /usr/local/lib/android/sdk ] && sdk=/usr/local/lib/android/sdk; }
    [ -z "$sdk" ] && return 1
    local sm
    sm=$(ls -1 "$sdk"/cmdline-tools/*/bin/sdkmanager 2>/dev/null | head -1)
    [ -z "$sm" ] && sm=$(command -v sdkmanager 2>/dev/null || true)
    [ -z "$sm" ] && return 1
    echo "==> installing NDK $NDK_VERSION via sdkmanager"
    yes 2>/dev/null | "$sm" --sdk_root="$sdk" --install "ndk;$NDK_VERSION" >/dev/null 2>&1 || true
    [ -x "$sdk/ndk/$NDK_VERSION/toolchains/llvm/prebuilt/$HOST_TAG/bin/clang" ] || return 1
    echo "$sdk/ndk/$NDK_VERSION"
}

NDK=""
if NDK=$(find_ndk); then
    :
elif NDK=$(install_ndk); then
    :
else
    # running on Android (Termux / adb shell) -> the system clang is enough
    if { [ -d /data/data/com.termux/files/usr ] || [ -d /system/lib64 ]; } \
        && command -v clang >/dev/null 2>&1; then
        echo "==> no NDK found, building on-device with clang"
        ON_DEVICE=1
        CC_BIN=clang
    else
        echo "veritpath: Android NDK not found" >&2
        echo "  install one:  sdkmanager --install 'ndk;$NDK_VERSION'" >&2
        echo "  or point at:  ANDROID_NDK_HOME=/path/to/ndk $0" >&2
        exit 1
    fi
fi

ON_DEVICE="${ON_DEVICE:-0}"
if [ "$ON_DEVICE" = 0 ]; then
    CC_BIN="$NDK/toolchains/llvm/prebuilt/$HOST_TAG/bin/clang"
    [ -x "$CC_BIN" ] || { echo "veritpath: clang missing at $CC_BIN" >&2; exit 1; }
    echo "==> NDK: $NDK"
fi

# ------------------------------------------------------------------ ABI table
TRIPLES=(aarch64-linux-android armv7a-linux-androideabi x86_64-linux-android i686-linux-android)
ABIS=(arm64-v8a armeabi-v7a x86_64 x86)

# --jni builds libveritpath.so for APKs instead of a command-line binary
if [ "${1:-}" = "--jni" ] || [ "${1:-}" = "jni" ]; then
    ABI_WANT="${2:-all}"
    echo "==> building the JNI shared library"
    [ "$ON_DEVICE" = 0 ] || { echo "on-device builds use the system clang"; }
    for i in "${!ABIS[@]}"; do
        abi="${ABIS[$i]}"
        triple="${TRIPLES[$i]}"
        [ "$ABI_WANT" != "all" ] && [ "$ABI_WANT" != "$abi" ] && continue
        outdir="$OUT/jniLibs/$abi"
        mkdir -p "$outdir"
        if [ "$ON_DEVICE" = 1 ]; then
            "$CC_BIN" -O2 -std=c11 -Wall -Wextra -DVP_NO_MAIN -Isrc -fPIC -shared \
                -Wl,-z,max-page-size=16384 "${SRCS[@]}" jni/veritpath_jni.c -lz \
                -o "$outdir/libveritpath.so"
        else
            "$CC_BIN" --target="${triple}${API}" -O2 -std=c11 -Wall -Wextra \
                -DVP_NO_MAIN -Isrc -fPIC -shared -Wl,-z,max-page-size=16384 \
                "${SRCS[@]}" jni/veritpath_jni.c -lz -o "$outdir/libveritpath.so"
        fi
        echo "    -> $outdir/libveritpath.so"
    done
    echo
    echo "copy jniLibs/ into app/src/main/ of your Android Studio project"
    exit 0
fi

WANT="${1:-all}"
built=0

# Android quirks that have to be handled after linking:
#   * Bionic insists on PT_TLS alignment >= 64 (32-bit: >= 32); lld emits 8
#     for static binaries, and the loader aborts with
#     "executable's TLS segment is underaligned".
#   * Android 5+ only loads PIE (ET_DYN); a static link can come out ET_EXEC.
#   * Android 15+ devices may use 16KB pages, so ask for that alignment too.
TLSFIX="$ROOT/tools/elf_fix.py"
COMMON_FLAGS=(-O2 -std=c11 -Wall -Wextra -Isrc -D_GNU_SOURCE
              -Wl,-z,max-page-size=16384)

for i in "${!ABIS[@]}"; do
    abi="${ABIS[$i]}"
    triple="${TRIPLES[$i]}"
    if [ "$WANT" != "all" ] && [ "$WANT" != "$abi" ] && [ "$WANT" != "${abi%%-*}" ]; then
        continue
    fi
    echo "==> building $abi"
    target="$OUT/veritpath-android-$abi"

    if [ "$ON_DEVICE" = 1 ]; then
        "$CC_BIN" "${COMMON_FLAGS[@]}" -static "${SRCS[@]}" -lz -o "$target"
    else
        "$CC_BIN" --target="${triple}${API}" "${COMMON_FLAGS[@]}" -static \
            "${SRCS[@]}" -lz -o "$target"
    fi

    # fix the TLS alignment, then make sure the result is PIE
    python3 "$TLSFIX" "$target" | sed 's/^/        /'
    if ! python3 "$TLSFIX" --check "$target" >/dev/null 2>&1; then
        # usually means the static link is not PIE - relink dynamically, which
        # is how native Android executables are normally built anyway
        echo "        relinking $abi as PIE against Bionic"
        if [ "$ON_DEVICE" = 1 ]; then
            "$CC_BIN" "${COMMON_FLAGS[@]}" -pie "${SRCS[@]}" -lz -o "$target"
        else
            "$CC_BIN" --target="${triple}${API}" "${COMMON_FLAGS[@]}" -pie \
                "${SRCS[@]}" -lz -o "$target"
        fi
        python3 "$TLSFIX" "$target" | sed 's/^/        /'
    fi

    chmod +x "$target"
    echo "    -> $target"
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
