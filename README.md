# veritpath

A dependency-light **C** tool in the spirit of `magiskboot`: it parses
`boot.img` / `init_boot.img` / `vendor_boot.img`, works out the device's
architecture and root layout, and injects third-party `su` / `init` payloads
correctly.

No Python, no interpreter, no runtime. `adb push` it to a phone and run it.

## Build

```bash
make                 # -> build/veritpath
make static          # fully static, runs on any distro / on Android
bash build.sh        # cross targets, see below
```

Only `libc` and `zlib` are needed — both ship with glibc, musl and the Android
NDK. LZ4 is implemented in-tree; xz/bzip2/zstd are used when the matching
library is present and skipped when it is not.

## Platforms

| Target | How you get it |
|---|---|
| Linux x86_64 / aarch64 | `make static`, or the Release workflow |
| Android arm64 / arm / x86_64 / x86 | `bash build-android.sh`, or the Release workflow |
| Windows x86_64 | `bash build.sh windows-x86_64` (mingw-w64), or the Release workflow |
| macOS x86_64 / arm64 / universal | the Release workflow builds on a macOS runner |

`build-android.sh` finds an NDK on its own, installs one with `sdkmanager` when
there is none (that is all CI needs — no third-party setup action), and falls
back to on-device clang when it is run inside Termux or an `adb shell`.

Both workflows use only GitHub's own actions (`actions/checkout`,
`upload/download-artifact`) plus the preinstalled `gh` CLI — nothing that can
vanish from the marketplace. Pushing a tag builds every platform:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

## Usage

```
veritpath analyze --boot boot.img [--init-boot ...] [--vendor-boot ...]
veritpath plan    --init-boot init_boot.img -p payload --permissive
veritpath inject  --init-boot init_boot.img -p payload -o out/
veritpath unpack  boot.img -d work/          # ramdisk tree + original.img
veritpath repack  work/ -o boot.new.img
veritpath hexdump boot.img                   # diagnostics
veritpath doctor                             # which build is running
```

Options: `--header-version N` (force a header version), `--patch-vendor-boot`,
`--permissive`, `--cmdline`, `--segment N`, `--format lz4_legacy`,
`--force`, `--dry-run`, `--json`, `-v`.

### Android

```bash
adb push dist/veritpath-android-arm64-v8a /data/local/tmp/veritpath
adb shell chmod 755 /data/local/tmp/veritpath
adb shell
su
dd if=/dev/block/by-name/init_boot_a of=/data/local/tmp/init_boot.img
/data/local/tmp/veritpath analyze --boot /data/local/tmp/boot.img \
                                  --init-boot /data/local/tmp/init_boot.img
/data/local/tmp/veritpath inject --init-boot /data/local/tmp/init_boot.img \
                                 -p /sdcard/my-su -o /data/local/tmp/
```

`/data/local/tmp/` is writable and executable. `/storage/emulated/0` (sdcard)
is mounted `noexec`, so binaries there never run — copy them out first.

## Output format

`analyze` prints magiskboot-style `KEY:VALUE`, one item per line, no colour and
no decoration, so it is easy to `grep`:

```
[init_boot.img]
HEADER_VER:4
RAMDISK_SZ:12721173
RAMDISK_FMT:lz4_legacy
PAGESIZE:4096

ARCH:arm64
ANDROID:13 (API 33)
LAYOUT:init_boot
SYSTEM_AS_ROOT:1
GKI:1
SEGMENTS:1
PATCHED:0
TARGET:init_boot
OPTIONAL:vendor_boot
```

`-v` appends findings and advice; `--json` prints structured output.

## Troubleshooting

### `cannot determine boot image header version`

Large images are **not** the problem — a GKI 1.0 boot.img (Snapdragon 888,
OnePlus 9 / 9 Pro, kernel 5.4.x) is legitimately ~192MB, and veritpath has no
size limit. The message means the header version fields did not look like any
known layout. Start with:

```bash
veritpath hexdump boot.img
```

which prints the file size, the first 64 bytes as hex + ASCII, the magic, and
the values at `@8 @12 @20 @24 @36 @40`.

What veritpath already handles automatically:

| Situation | Behaviour |
|---|---|
| magic not at offset 0 (board header / padding) | scans and skips the prefix |
| whole image wrapped in gzip/xz/lz4 | unwraps first |
| `@20` header size padded (4096), zeroed, or garbage | ignored / clamped |
| `@24` is 5 or 6 (future versions) | parsed as a v3/v4 layout |
| both version fields garbage | version recovered from the layout itself |
| sparse / payload.bin / zip / AVB / ELF / bare dtb | named, with the command to fix it |

If none of those apply, force a version and see:

```bash
veritpath analyze --boot boot.img --header-version 3
```

### `no such file`

The error lists the path it resolved, the working directory, the directory
contents and a close match, so it is obvious where the mismatch is.

## Tests

```bash
bash tests/run.sh          # 48 checks, self-contained (cc + python3 only)
```

`tests/imgkit.py` builds the synthetic images and verifies patched output using
nothing but the standard library; the Python implementation that used to sit
next to this project is gone.

Known limitation: rebuilding a `vendor_boot.img` merges its ramdisk fragments
into one. The payload still lands in the vendor ramdisk, but per-fragment
separation is not preserved yet.
