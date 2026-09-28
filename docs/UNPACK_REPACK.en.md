# Unpack and repack (magiskboot style)

veritpath can split an image into **one file per component**, let you change any
of them, and put it back together. The work directory is the interface — which
is what makes a command chain possible.

> 中文版：[UNPACK_REPACK.md](UNPACK_REPACK.md)

## The chain

```bash
# 1. split
veritpath unpack boot.img -d work/

# 2. change whatever you need
echo "# my tweak" >> work/ramdisk/init.rc     # edit a ramdisk file
cp mykernel work/kernel                        # swap the kernel
cp my.dtb   work/dtb                           # swap the dtb

# 3. put it back together
veritpath repack work/ -o boot.new.img

# 4. confirm
veritpath analyze --brief boot.new.img
```

## What is in the work directory

```
work/
├── image.json        metadata: header fields plus a note per component
├── original.img      copy of the source image (repack base)
├── header.bin        the raw header
├── kernel            kernel (still in its original compression)
├── second            second stage (if present)
├── dtb               dtb (if present)
├── recovery_dtbo     recovery dtbo (if present)
├── boot_signature    boot signature (v4, if present)
├── bootconfig        bootconfig (vendor v4, if present)
├── ramdisk.cpio      the decompressed cpio
├── ramdisk/          the cpio extracted as a tree
└── ramdisk-<n>.cpio  the further segments, when there are several
```

`image.json` makes the directory self-describing:

```json
{
  "source": "init_boot.img",
  "source_size": 8192,
  "trailing": 0,
  "image": {
    "role": "boot",
    "header_version": 4,
    "page_size": 4096,
    "os_version": 0,
    "header_span": 4096,
    "name": "",
    "cmdline": ""
  },
  "components": [
    {"file": "ramdisk.cpio", "label": "ramdisk", "size": 224,
     "segments": 1, "format": "gzip"}
  ]
}
```

## What wins on repack

| Situation | Behaviour |
|---|---|
| `ramdisk/` exists | **preferred** — rebuilt from the tree (per `segment<N>/` when multi-segment) |
| no tree, `ramdisk.cpio` present | rebuilt from the `.cpio` files |
| neither | the original ramdisk is kept |
| `kernel` / `dtb` / `second` / `recovery_dtbo` / `boot_signature` / `bootconfig` present | that file replaces the component |
| file absent | the original is kept |

So **replace only the one you care about**; leave the rest alone.

## Common operations

**Edit a ramdisk file**

```bash
veritpath unpack init_boot.img -d work/
vim work/ramdisk/init.rc
veritpath repack work/ -o init_boot.new.img
```

**Swap the kernel** (it is written as you supplied it — veritpath does not
recompress it)

```bash
veritpath unpack boot.img -d work/
cp zImage work/kernel
veritpath repack work/ -o boot.new.img
```

**Change the compression**

```bash
veritpath repack work/ --format lz4_legacy -o boot.new.img
```

**Multi-segment ramdisk (vendor_boot)**

```bash
veritpath unpack vendor_boot.img -d work/
ls work/ramdisk/                      # segment0/ segment1/ ...
# edit each segment on its own
veritpath repack work/ -o vendor_boot.new.img
```

The fragment table is rewritten for the new sizes and the segments stay
separate.

## Versus inject

| | Use it for |
|---|---|
| `inject -p payload` | applying someone's su/init payload by manifest: segment chosen for you, rc hooked up, fragment table kept in sync |
| `unpack` + edit + `repack` | precise manual control over what goes in and where |

Use `inject` when you want it automated and repeatable; use `unpack` when you
want to touch one file yourself.

## Notes

- **`original.img` is required.** It is the repack base and restores every
  header field we do not model. Delete it and the directory cannot be repacked.
- **Trailing bytes are kept.** If the source was a whole-partition dump, the
  padding comes along, so an untouched repack matches the input size.
- After swapping a kernel, the following offsets are recalculated — no manual
  fixups.
