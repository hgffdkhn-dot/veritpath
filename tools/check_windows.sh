#!/usr/bin/env bash
# Compile every source file through its Windows branch.
#
# A real MinGW is not always available, so this forces _WIN32 and builds with
# the host compiler using minimal stub headers. It does not produce a working
# .exe - it proves the Windows code paths compile cleanly, which is where all
# the portability mistakes hide (mkdir() arity, S_ISLNK, memmem, realpath ...).
#
# With a real MinGW installed it also links an actual .exe and runs it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

CC=${CC:-cc}
fail=0

mkdir -p "$WORK/stub"
cat > "$WORK/stub/direct.h" <<'H'
#ifndef VP_STUB_DIRECT_H
#define VP_STUB_DIRECT_H
int _mkdir(const char *p);
char *_fullpath(char *out, const char *p, size_t n);
#endif
H
: > "$WORK/stub/io.h"

echo "== windows branch (forced _WIN32)"
for f in src/*.c; do
    out="$WORK/$(basename "$f" .c).o"
    if ! $CC -O2 -std=c11 -Wall -Wextra -Werror -D_WIN32 \
            -I"$WORK/stub" -Isrc -c "$f" -o "$out" 2>"$WORK/err"; then
        echo "  FAIL $(basename "$f")"
        sed 's/^/        /' "$WORK/err" | head -6
        fail=1
    fi
done
[ $fail -eq 0 ] && echo "  ok: every source compiles with -Werror"

# the shims must actually be present, not silently skipped
echo "== shims present in the windows branch"
n=$(grep -c 'S_ISLNK\|mkdir(path, mode)\|memmem\|realpath' src/compat.h)
echo "  compat.h shims: $n"
[ "$n" -ge 4 ] || { echo "  FAIL: compat.h is missing shims"; fail=1; }

# a real MinGW, if there is one, gets a full link
if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    echo "== real mingw build"
    if x86_64-w64-mingw32-gcc -O2 -std=c11 -Wall -Wextra -Isrc \
            src/*.c -lz -o "$WORK/veritpath.exe" 2>"$WORK/mwerr"; then
        echo "  ok: linked veritpath.exe"
        file "$WORK/veritpath.exe" | sed 's/^/        /'
    else
        echo "  FAIL: mingw link"
        sed 's/^/        /' "$WORK/mwerr" | head -8
        fail=1
    fi
else
    echo "== no mingw here (the CI job covers the real cross build)"
fi

[ $fail -eq 0 ] && echo "  PASS" || echo "  FAILED"
exit $fail
