# veritpath

A dependency-light **C** tool in the spirit of `magiskboot`: it parses
`boot.img` / `init_boot.img` / `vendor_boot.img`, works out the device's
architecture and root layout, and injects third-party `su` / `init` payloads
correctly.

No Python, no interpreter, no runtime. `adb push` it to a phone and run it.

README in Chinese: [README.zh-CN.md](README.zh-CN.md)

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
sh install.sh                                # install (and drop old files)
veritpath verify out/boot.veritpath.img      # check a patched image
```

Options: `--header-version N` (force a header version), `--patch-vendor-boot`,
`--permissive`, `--cmdline`, `--segment N`, `--format lz4_legacy`,
`--force`, `--dry-run`, `--brief`, `--json`, `-v`.

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

### "command not found" — prefix it with `./`

On many Android devices the current directory is not in `PATH`, so a bare
`veritpath` is not found even when the binary is right there:

```
$ veritpath analyze --boot boot.img
veritpath: inaccessible or not found
```

Prefix it, or use the full path:

```bash
./veritpath analyze --boot boot.img
/data/local/tmp/veritpath analyze --boot boot.img
```

`command -v veritpath` tells you whether it is on `PATH` at all (only true if
you ran `install.sh`). After changing `PATH` or deleting an old copy, run
`hash -r` — bash caches resolved command locations and will otherwise keep
pointing at a path that no longer exists.

## Output format

`analyze` prints a grouped, annotated report by default — one block for the
verdict, one per image, then the findings and the injection target:

```
veritpath 0.2.0 — boot image analysis
==============================================================
  arch                 arm64
  android_version      13
  ramdisk_layout       init_boot
  system_as_root       True
  gki                  True
  kernel_compression   gzip
  ramdisk_compression  gzip
  ramdisk_segments     ['main']
  already_patched      False
--------------------------------------------------------------
  boot.header_version  4  (boot image header)
  boot.kernel_size     34
  boot.page_size       4096
  init_boot.header_version 4  (boot image header)
  init_boot.ramdisk_size 224
  init_boot.ramdisk_format gzip
  init_boot.page_size  4096
  kernel_compression   gzip  (kernel payload)
  ramdisk_segments     ['main']  (cpio segments inside the ramdisk)
  ramdisk_entries      4
  has_init             True  (/init present in ramdisk)
  ramdisk_contains_system False  (/system/bin/init inside the ramdisk)
  already_patched      False  (no veritpath marker in ramdisk)
  ramdisk_compression  gzip  (ramdisk payload magic)
--------------------------------------------------------------
  · Android 13+ GKI: the generic ramdisk lives in init_boot.img...
  · system-as-root: the ramdisk is only the first-stage init...
--------------------------------------------------------------
  injection target     init_boot
```

Each `·` line is a concrete consequence, not decoration: where the ramdisk
lives, whether /system gets mounted over it, and which compression will be used
when rebuilding.

Two other shapes:

```bash
veritpath analyze --brief --boot boot.img    # magiskboot-style KEY:VALUE
veritpath analyze --json  --boot boot.img    # machine readable
```

`--brief` emits `HEADER_VER:` / `RAMDISK_SZ:` / `ARCH:` / `TARGET:` one per
line, which is what you want when piping into `grep`.

## Installing

The binary is self-contained — no `.pyz`, no launcher, no data directory. Copy
it anywhere you can `chmod +x` and run it:

```bash
adb push veritpath /data/local/tmp/veritpath
adb shell chmod 755 /data/local/tmp/veritpath
adb shell /data/local/tmp/veritpath analyze --boot /sdcard/boot.img
```

Or let the installer pick a prefix (Termux → `$PREFIX/bin`, root →
`/usr/local/bin`, rooted Android shell → `/data/local/tmp`):

```bash
sh install.sh              # auto-detect
sh install.sh --to-tmp     # /data/local/tmp
sh install.sh --prefix DIR # somewhere specific
```

`install.sh` also removes the old Python-era files (`bin/veritpath` launcher
and `share/veritpath/`). Those are what shadowed freshly built binaries and
made `veritpath --version` keep printing 0.1.0. If you only want the cleanup:

```bash
sh install.sh --clean
```

Note that `/storage/emulated/0` (sdcard) is mounted `noexec` — a binary there
never runs, so always copy it somewhere like `/data/local/tmp` first.

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

### `TLS segment is underaligned` on Android

Bionic requires `PT_TLS` alignment >= 64 while a static NDK link emits 8, and
the loader aborts. `build-android.sh` fixes it automatically; to patch a binary
by hand:

```bash
python3 tools/elf_fix.py /data/local/tmp/veritpath
```

### No ramdisk at all (system-as-root)

A pure SAR `boot.img` carries no ramdisk — the kernel mounts `/system` as `/`
and runs `/system/bin/init`. `analyze` reports this as `LAYOUT:no_ramdisk` with
`NEEDS_RAMDISK:1`. Create one and patch it in a single step:

```bash
veritpath inject --boot boot.img -p my-su --create-ramdisk -o out/
```

Without the flag the command fails instead of writing an untouched image. The
generated `/init` is a placeholder: supply a real static first-stage init in the
payload (`{"src": "init", "dest": "/init"}`), or the image will not boot. See
[docs/DEVELOPERS.en.md](docs/DEVELOPERS.en.md) section 10.

### The output is much smaller than the input

Nothing was lost. `dd` of a whole partition (`/dev/block/by-name/boot_a`)
includes zero padding past the real image, and repacking emits only the image.
A 192MB partition dump whose real image is 42MB correctly yields a 42MB output.
`analyze` reports this as a `TRAILING.<role>:<bytes>` line/`trailing` JSON field
plus a plain-language note. Pass `--keep-trailing` to carry the padding over.

### `no such file`

The error lists the path it resolved, the working directory, the directory
contents and a close match, so it is obvious where the mismatch is.

## Documentation

| Doc | Language | What it covers |
|---|---|---|
| [README.zh-CN.md](README.zh-CN.md) | Chinese | this page in Chinese |
| [docs/QUICKSTART.en.md](docs/QUICKSTART.en.md) | English | install, pull an image, inject, flash, troubleshooting |
| [docs/DEVELOPERS.en.md](docs/DEVELOPERS.en.md) | English | payload format, manifest fields, layout rules, SELinux, exit codes |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | Chinese | same as QUICKSTART.en, in Chinese |
| [docs/DEVELOPERS.md](docs/DEVELOPERS.md) | Chinese | same as DEVELOPERS.en, in Chinese |
| [docs/ANDROID_APP.en.md](docs/ANDROID_APP.en.md) | English | embedding in an APK: JNI library or bundled binary |
| [docs/ANDROID_APP.md](docs/ANDROID_APP.md) | Chinese | same as ANDROID_APP.en, in Chinese |

For payload authors the short version is: a directory with your binaries plus a
`manifest.json` declaring where each file goes (see `payloads/template/`).
veritpath ships no su implementation — it decides *where* your files belong for
the detected layout and rebuilds a bootable image.

Checking a payload without touching an image:

```bash
veritpath payload-check my-su
```

Checking an image you just built (non-zero exit if anything is missing):

```bash
veritpath verify out/init_boot.veritpath.img -p my-su
```

## Tests

```bash
bash tests/run.sh          # 68 checks, self-contained (cc + python3 only)
```

`tests/imgkit.py` builds the synthetic images and verifies patched output using
nothing but the standard library; the Python implementation that used to sit
next to this project is gone.

`vendor_boot.img` handling: the ramdisk is split using the fragment table
rather than by sniffing compression, so every fragment is rebuilt separately and
the table is rewritten with correct sizes and offsets (page-aligned or
back-to-back, whichever the original used). Every vendor ramdisk fragment
receives the payload.
