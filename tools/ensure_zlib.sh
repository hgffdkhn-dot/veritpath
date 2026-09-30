#!/usr/bin/env bash
# Make sure a zlib usable by the given cross compiler exists.
#
#   bash tools/ensure_zlib.sh aarch64-linux-gnu-gcc
#
# Prints "<incdir>:<libz.a>" on success, and exits
# non-zero if none could be arranged.
#
# Order of attempts:
#   1. -lz already links (multi-arch already set up)  -> done
#   2. apt: zlib1g-dev:<arch>                        -> needs multi-arch
#   3. build zlib from source for the target         -> always works if there
#      is a C compiler for the target and a network
#
# Step 2 is best effort on purpose. Adding a foreign architecture makes apt
# fetch indexes for it, and those are not always where the host expects them
# (security.ubuntu.com 404s for binary-arm64 on some runners). A failed
# `apt-get update` there returns 100 and would fail the whole job, so it is
# tolerated and step 3 takes over.
set -uo pipefail

cc="${1:-cc}"
extra="${2:-}"

case "$cc" in
    aarch64-linux-gnu-*)      debarch=arm64 ;;
    arm-linux-gnueabihf-*)    debarch=armhf ;;
    i686-linux-gnu-*)         debarch=i386 ;;
    x86_64-linux-gnu-*)       debarch=amd64 ;;
    *mingw*)                  debarch=none ;;   # mingw ships its own
    *)                        debarch=none ;;
esac

work="${VP_ZLIB_CACHE:-$HOME/.cache/veritpath-zlib}/$(echo "$cc" | tr -c 'A-Za-z0-9._-' '_')"
mkdir -p "$work"

probe() {   # $1 = extra libs to try
    local t="$work/probe.c" b="$work/probe"
    printf 'int main(void){return 0;}\n' > "$t"
    $cc $extra "$t" -o "$b" $1 >/dev/null 2>&1
}

# 1. already fine? Then the compiler's own zlib works and no extra flags are
#    needed - tell the caller so it stops looking.
if probe "-lz"; then
    echo "builtin:builtin"
    exit 0
fi

# 2. apt multi-arch (best effort)
if [ "$debarch" != none ] && command -v apt-get >/dev/null 2>&1 &&
   [ -n "${SUDO:-sudo}" ] && command -v dpkg >/dev/null 2>&1; then
    dpkg --add-architecture "$debarch" >/dev/null 2>&1 || \
        ${SUDO} dpkg --add-architecture "$debarch" >/dev/null 2>&1 || true
    # arm64/armhf live on ports.ubuntu.com, not archive or security
    if [ "$debarch" = arm64 ] || [ "$debarch" = armhf ]; then
        rel=$(. /etc/os-release 2>/dev/null && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
        if [ -n "$rel" ] && [ ! -f /etc/apt/sources.list.d/veritpath-ports.list ]; then
            echo "deb [arch=$debarch] http://ports.ubuntu.com/ubuntu-ports $rel main" \
                | ${SUDO} tee /etc/apt/sources.list.d/veritpath-ports.list >/dev/null 2>&1 || true
        fi
    fi
    ${SUDO} apt-get update -qq >/dev/null 2>&1 || true   # 404s here are not fatal
    ${SUDO} apt-get install -y -qq "zlib1g-dev:$debarch" >/dev/null 2>&1 || true
    if probe "-lz"; then
        echo "builtin:builtin"
        exit 0
    fi
fi

# 3. build zlib from source for the target
ver=1.3.1
tarball="$work/zlib-$ver.tar.gz"
src="$work/zlib-$ver"
prefix="$work/prefix"
[ -f "$prefix/lib/libz.a" ] && { echo "$prefix/include:$prefix/lib/libz.a"; exit 0; }

if [ ! -d "$src" ]; then
    url="https://github.com/madler/zlib/releases/download/v${ver}/zlib-${ver}.tar.gz"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$tarball" >/dev/null 2>&1 || exit 1
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$url" -O "$tarball" >/dev/null 2>&1 || exit 1
    else
        exit 1
    fi
    tar xzf "$tarball" -C "$work" >/dev/null 2>&1 || exit 1
fi

( cd "$src" && CC="$cc" CFLAGS="$extra -O2 -fPIC" \
  ./configure --static --prefix="$prefix" >"$work/conf.log" 2>&1 &&
  make -j"$(nproc 2>/dev/null || echo 2)" >"$work/make.log" 2>&1 &&
  make install >"$work/install.log" 2>&1 ) || exit 1

[ -f "$prefix/lib/libz.a" ] || exit 1
echo "$prefix/include:$prefix/lib/libz.a"
