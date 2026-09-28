# Quick start

> 中文版：[QUICKSTART.md](QUICKSTART.md)

## 1. Install

Any of the three — you end up with the same single-file binary.

```bash
# A: just run it, no installation
adb push veritpath /data/local/tmp/veritpath
adb shell chmod 755 /data/local/tmp/veritpath

# B: let install.sh pick a directory
sh install.sh

# C: copy it yourself
cp veritpath /data/local/tmp/veritpath && chmod 755 /data/local/tmp/veritpath
```

> The sdcard (`/storage/emulated/0`) is mounted `noexec` — a binary placed
> there **never runs**. Copy it to `/data/local/tmp` or your Termux home first.

Confirm it is the right build:

```bash
veritpath doctor          # VERSION:0.2.0 / VERDICT:OK
veritpath --version       # veritpath 0.2.0
```

## 2. "command not found"? Prefix it with `./`

**On many Android devices a bare `veritpath` does not work**, and you get:

```
veritpath: command not found
veritpath: inaccessible or not found
```

The reason is simple: the current directory is **not in `PATH`**, and the shell
only looks through the directories listed in `PATH` — it does not check where
you happen to be. So state the location explicitly:

```bash
./veritpath analyze --boot boot.img
```

Or use the full path, which works from any directory:

```bash
/data/local/tmp/veritpath analyze --boot boot.img
```

All three forms below are valid; use whichever suits you:

```bash
./veritpath                      # if it is in the current directory
/data/local/tmp/veritpath        # full path
veritpath                        # only if on PATH (i.e. you ran install.sh)
```

**To check whether it is on PATH:**

```bash
command -v veritpath
```

Any output means it is on PATH and you can type the bare name. No output means
use `./` or the full path.

Two more traps that tend to come as a pair:

- **After editing PATH or deleting an old copy, bash keeps the stale location**
  in its cache and reports `No such file or directory`. Refresh it: `hash -r`
- **Still failing right after running `install.sh`** — same fix, `hash -r` first

## 3. Pull the image off the phone

With root you can read the partitions directly instead of hunting for files:

```bash
su
getprop ro.boot.slot_suffix              # A/B devices: current slot, e.g. _a
ls -l /dev/block/by-name/ | grep -E "boot|init_boot"

dd if=/dev/block/by-name/boot_a      of=/data/local/tmp/boot.img
dd if=/dev/block/by-name/init_boot_a of=/data/local/tmp/init_boot.img
chmod 644 /data/local/tmp/*.img
```

## 4. Three steps

```bash
cd /data/local/tmp

# 1. what layout is this device?
veritpath analyze --boot boot.img --init-boot init_boot.img

# 2. see the plan only, files untouched
veritpath plan --boot boot.img --init-boot init_boot.img \
               -p /sdcard/my-su --permissive

# 3. actually inject
veritpath inject --boot boot.img --init-boot init_boot.img \
                 -p /sdcard/my-su --permissive -o out/
```

This writes `out/init_boot.veritpath.img` and backs the original up as
`*.img.veritpath.bak`.

## 5. Check the result before flashing

```bash
veritpath verify out/init_boot.veritpath.img -p /sdcard/my-su
```

Flash only after seeing `VERDICT:OK`. A failed injection prints
`VERDICT:INCOMPLETE` and **exits 1**, so it can gate a script:

```bash
veritpath verify out/init_boot.veritpath.img || exit 1
fastboot flash init_boot out/init_boot.veritpath.img
```

## 6. Flash it

```bash
# boot it once without committing, to see whether it comes up
fastboot boot out/init_boot.veritpath.img

# once you are satisfied
fastboot flash init_boot out/init_boot.veritpath.img
fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
fastboot reboot
```

A modified image is no longer OEM-signed, so vbmeta verification must be
disabled or the image re-signed.

## 7. Editing the ramdisk by hand / swapping components

See [UNPACK_REPACK.en.md](UNPACK_REPACK.en.md) for the full split-and-rebuild
workflow.

```bash
veritpath unpack init_boot.img -d work/
# work/ramdisk/   is the extracted tree - edit freely
# work/original.img  is the original image, needed by repack
vim work/ramdisk/init.rc
veritpath repack work/ -o init_boot.new.img
```

## Options

| Option | Meaning |
|---|---|
| `-p, --payload DIR` | payload directory |
| `-o, --output PATH` | output file or directory |
| `--permissive` | add `androidboot.selinux=permissive` to cmdline |
| `--cmdline TOKENS` | append custom cmdline tokens |
| `--segment N` | which cpio segment to inject into |
| `--format NAME` | force ramdisk compression (gzip / lz4_legacy / …) |
| `--patch-vendor-boot` | also patch the vendor ramdisk (recovery/fastbootd) |
| `--force` | ignore warnings, allow re-injection |
| `--create-ramdisk` | build a ramdisk when the image has none (SAR devices) |
| `--keep-trailing` | carry bytes after the image into the output (partition dumps) |
| `--dry-run` | patch in memory, write nothing |
| `--brief` | compact KEY:VALUE output from analyze |
| `--json` | machine readable output |
| `--header-version N` | force a header version (when parsing fails) |
| `-v` | verbose |

## Stuck?

```bash
veritpath hexdump boot.img     # start here if it will not parse
```

It prints the file size, the first 64 bytes as hex + ASCII, the magic, and the
values at `@8/@12/@20/@24/@36/@40`, plus what veritpath itself concluded. Paste
that output when asking for help.
### Why is the output much smaller than the input?

**This is not data loss.** `dd if=/dev/block/by-name/boot_a of=boot.img` copies
the **whole partition**, and the real boot image is usually smaller than that —
the rest is zero padding. Repacking emits the image itself, so the padding goes
away.

veritpath says so explicitly:

```
ramdisk_layout       boot
...
  · boot: the file holds 72.0MiB that is not part of the boot image - a whole-partition
    dump (dd of /dev/block/by-name/...). That padding is dropped when repacking, so the
    output is smaller than the input. This is correct, not data loss.
```

With `--brief` you get `TRAILING.boot:75448320`; with `--json`, a `trailing`
object.

So a 192MB partition dump whose real image is 42MB producing a 42MB output is
working correctly.

**To extract just the real image**, compute the length from the header sizes
(`veritpath hexdump boot.img` prints them).

**To carry the tail over anyway** (a few vendors append data after the image):

```bash
veritpath inject --boot boot.img -p my-su --keep-trailing -o out/
```

The bytes are appended to the output untouched; the image itself is unaffected
and `verify` still passes.
