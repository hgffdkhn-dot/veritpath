# Developer integration guide

> 中文版：[DEVELOPERS.md](DEVELOPERS.md)

veritpath **ships no su implementation**. What it does is: take the files you
provide → work out the device layout → decide where those files belong → rebuild
a bootable image. All you need to prepare is a payload directory.

## 1. What a payload is

A directory plus a `manifest.json`:

```
my-su/
├── manifest.json
└── su              ← your binary
```

A minimal manifest:

```json
{
  "name": "my-su",
  "arch": ["arm64"],
  "min_api": 26,
  "files": [
    { "src": "su", "dest": "/su", "mode": "0755",
      "context": "u:object_r:rootfs:s0", "required": true }
  ],
  "rc": {
    "file": "/init.mysu.rc",
    "import_into": ["/init.rc"],
    "content": "on post-fs-data\n    chmod 0755 /su\n\nservice mysu /su --daemon\n    class late_start\n    user root\n    group root\n    seclabel u:r:init:s0\n    oneshot\n"
  },
  "cmdline_append": ["androidboot.mysu=1"],
  "selinux": "permissive"
}
```

`payloads/template/` is a ready-to-copy skeleton.

**A manifest is optional** — veritpath guesses from file names:

| File name | Destination |
|---|---|
| `su` / `su.*` | `/su` (0755, labelled `u:object_r:rootfs:s0`, rc generated automatically) |
| `init` | `/init` (the existing `/init` is backed up as `/init.real`) |
| `*.rc` | `/<same name>`, hooked into `/init.rc` |
| anything else | `/veritpath/<same name>` (0755) |

## 2. Complete manifest reference

### Top level

| Field | Type | Meaning |
|---|---|---|
| `name` | string | payload name, written into `/veritpath.json` |
| `version` | string | recorded only |
| `arch` | array | allowed architectures: `arm64` / `arm` / `x86_64` / `x86`. A mismatch warns; `--force` overrides |
| `min_api` / `max_api` | number | allowed Android API range |
| `selinux` | string | `permissive` adds `androidboot.selinux=permissive` to the cmdline; `keep` leaves it alone |
| `cmdline_append` | array | tokens appended to the kernel cmdline |
| `files` | array | files to write into the ramdisk |
| `rc` | object | init rc fragment |

### Each entry in files[]

| Field | Meaning |
|---|---|
| `src` | path relative to the payload directory |
| `dest` | absolute path inside the ramdisk |
| `mode` | permissions as an octal string, default `0755` |
| `uid` / `gid` | default 0 |
| `context` | SELinux label, appended to `/file_contexts` |
| `backup_as` | if `dest` already exists, back the original up to this path |
| `symlink` | additionally create a symlink pointing at `dest` |
| `required` | default true. Set false and a missing file only warns |

### The rc object

| Field | Meaning |
|---|---|
| `file` | where the fragment goes in the ramdisk, default `/init.veritpath.rc` |
| `content` | the rc body (a string, use `\n` for newlines) |
| `content_file` | alternatively a file in the payload directory — easier to maintain than embedding it in JSON |
| `import_into` | array. Insert `import <file>` at the top of each listed rc file |
| `append_to` | append the body to the end of an existing rc file |

`import_into` and `append_to` can be combined: the former guarantees init loads
your fragment, the latter is for when you need exact ordering control.

## 3. The layout decides the injection point

`veritpath analyze` tells you where the ramdisk lives; each layout maps to a
different image:

| Layout | Typical device | Ramdisk location | What to patch |
|---|---|---|---|
| `init_boot` | Android 13+ GKI | `init_boot.img` | patch init_boot only — leave boot.img alone (it holds just the kernel) |
| `vendor_boot` | Android 11/12 GKI 1.0 | the vendor_ramdisk inside `vendor_boot.img` | patch the vendor ramdisk; the fragment table is kept in sync automatically |
| `boot` | Android ≤12, non-GKI | `boot.img` | patch boot.img directly |

**Multi-segment ramdisks need care.** Modern images often carry a
`first_stage_ramdisk`, which loads before the main one. Your files must go into
the **main** segment — the wrong stage will not get the capabilities you expect.

```bash
veritpath unpack init_boot.img -d work/
ls work/ramdisk/          # segment0 / segment1 ...

veritpath inject --init-boot init_boot.img -p my-su --segment 1 -o out/
```

Without `--segment`, veritpath prefers the segment labelled `main`, falling back
to the last segment.

On **system-as-root** devices the ramdisk disappears once init switches root to
`/system`. Your rc needs to copy anything that must survive to `/data` during
`on post-fs-data`.

## 4. SELinux

Two routes, and you can use both:

1. Set `context` per file — veritpath appends them to `/file_contexts`
2. `"selinux": "permissive"` in the manifest, or `--permissive` on the command
   line

Start permissive while debugging, then add proper labels and switch back to
enforcing once everything works.

## 5. Suggested integration flow

```bash
# 1. validate the payload without touching an image
veritpath payload-check my-su

# 2. see the plan (dry run, nothing written)
veritpath plan --init-boot init_boot.img -p my-su --permissive

# 3. run it in memory first, then commit
veritpath inject --init-boot init_boot.img -p my-su --dry-run

# 4. inject for real
veritpath inject --init-boot init_boot.img -p my-su --permissive -o out/

# 5. verify
veritpath verify out/init_boot.veritpath.img -p my-su
```

## 6. Exit codes and automation

| Command | Success | Failure |
|---|---|---|
| `analyze` | 0 | parse error / missing file → 1 |
| `plan` | 0 | 1 |
| `inject` | 0 | 1 |
| `verify` | 0 (`VERDICT:OK`) | **1 (`VERDICT:INCOMPLETE`)** |
| `payload-check` | 0 | broken manifest → 1 |

The non-zero exit from `verify` is the gate for CI and flashing scripts:

```bash
veritpath inject --init-boot init_boot.img -p my-su -o out/ || exit 1
veritpath verify out/init_boot.veritpath.img -p my-su || exit 1
fastboot flash init_boot out/init_boot.veritpath.img
```

`--json` makes `analyze` emit structured output, so a script can read the
`target` field and pick the right image automatically.

## 7. Leaving a trace

After injection the ramdisk gains `/veritpath.json`, recording the payload name,
version, injection time and target layout. veritpath uses it to detect that an
image was already patched and warns on a second injection (override with
`--force`). Your own code can read it for runtime decisions too.

## 8. Current known limitations

- Rebuilding a `vendor_boot.img` merges its ramdisk fragments into one. The
  payload still lands in the vendor ramdisk, but fragment separation is not
  preserved.
- `arch` detection depends on the kernel (it looks for the `ARM64` magic).
  `init_boot.img` carries no kernel, so arch cannot be determined from it alone
  — pass `boot.img` as well.

## 9. Remember `./` in scripts

**On many Android devices a bare `veritpath` is not found** — the current
directory is usually not in `PATH`, and the shell will not infer it:

```
$ veritpath analyze --boot boot.img
veritpath: inaccessible or not found
```

So do not write a bare command in automation scripts; use one of these:

```bash
./veritpath analyze --boot boot.img                  # if it is in the current directory
/data/local/tmp/veritpath analyze --boot boot.img    # full path, safest
```

To decide programmatically, ask `doctor`:

```bash
veritpath doctor | grep '^ON_PATH:1$'    # a match means the bare name works
```

`doctor` prints `ON_PATH:0/1`, and when it is 0 it also prints a `HINT:` line
suggesting `./` or the full path. A script can fall back like this:

```bash
if command -v veritpath >/dev/null 2>&1; then
    VP=veritpath
else
    VP=./veritpath
fi
"$VP" verify out/init_boot.veritpath.img || exit 1
```

Also run `hash -r` after changing `PATH` or deleting an old copy — bash caches
resolved command locations and will otherwise report `No such file or directory`
for a path that no longer exists.
