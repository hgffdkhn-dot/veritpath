# veritpath (C)

**分析 boot 镜像 → 判定系统架构 → 给出对策 → 注入第三方 su / init 文件。**

C 语言实现的原生命令行工具，单文件可执行程序，用法和 magiskboot 一样：
`adb push` 到手机就能跑，**不需要 Python、不需要解释器、不需要任何运行时依赖**。

```
veritpath analyze --boot boot.img --init-boot init_boot.img
veritpath inject  --init-boot init_boot.img -p payloads/example-su -o out/
```

- 一个二进制，静态链接后 ~800KB，动态链接 ~88KB
- 只依赖 libc 与 zlib（gzip），两者 glibc / musl / Android NDK 都自带
- LZ4 自己实现（块格式 + legacy/frame 容器），xz/bzip2/zstd 有库才启用，没有就自动跳过
- 支持 boot header **v0–v4**、**vendor_boot v3–v4**（含 vendor ramdisk fragment 表）
- 保留原镜像 header 私有字段、页对齐、压缩格式、cmdline，只改 ramdisk

## 构建

```bash
make                    # build/veritpath
make static             # 全静态链接
make install            # 装到 /usr/local/bin（PREFIX=... 可改）
bash tests/run.sh       # 端到端回归测试
```

Android 原生二进制（交叉编译，需要 NDK）：

```bash
ANDROID_NDK_HOME=~/Android/Sdk/ndk/26.3.11579264 bash build-android.sh
# 产出 dist/veritpath-android-{arm64-v8a,armeabi-v7a,x86_64,x86}
```

手机上（Termux 装了 clang）直接本机编译：

```bash
pkg install clang
bash build-android.sh arm64-v8a
```

可选依赖（有就自动启用，没有也能编）：`lzma.h` → xz/lzma，`bzlib.h` → bzip2，
`zstd.h` → zstd。检测逻辑在 Makefile 里，不需要配置。

**诊断命令**：镜像解析不了时先查它到底是什么：

```bash
veritpath hexdump boot.img     # 前 64 字节 hex + 关键偏移 + 判定结果
```

## 输出格式

`analyze` 默认 magiskboot 风格 `KEY:VALUE`，一行一项、无颜色无装饰，便于
`grep`；`-v` 追加 findings 与对策建议；`--json` 输出结构化结果。

## 用法

```
veritpath hexdump <image>              dump header bytes (diagnostics)
veritpath analyze --boot boot.img ...  KEY:VALUE summary
veritpath plan    -p payload ...       dry-run injection plan
veritpath inject  -p payload -o out    patch and write
veritpath unpack <image> -d dir        unpack a ramdisk
veritpath repack <dir> -o image        rebuild
```


```bash
# 判定布局
veritpath analyze --boot boot.img --init-boot init_boot.img --vendor-boot vendor_boot.img

# 只看对策不改文件
veritpath plan --init-boot init_boot.img -p payloads/example-su --permissive

# 注入（自动备份原镜像）
veritpath inject --boot boot.img --init-boot init_boot.img \
                 -p payloads/example-su --permissive --patch-vendor-boot -o out/

# 手工改 ramdisk
veritpath unpack init_boot.img -d work/
vim work/ramdisk/init.rc
veritpath repack work -o init_boot.new.img
```

参数：`--boot/--init-boot/--vendor-boot/--recovery`、`-p/--payload`、`-o/--output`、
`--patch-vendor-boot`、`--permissive`、`--cmdline`、`--segment N`、
`--format gzip|lz4|lz4_legacy|xz|lzma|bzip2`、`--force`、`--no-backup`、
`--dry-run`、`--json`、`-v`。

## 在手机上使用

```bash
adb push dist/veritpath-android-arm64-v8a /data/local/tmp/veritpath
adb shell chmod 755 /data/local/tmp/veritpath
adb shell

# 有 root：直接从分区把镜像 dump 出来
su
dd if=/dev/block/by-name/init_boot_a of=/data/local/tmp/init_boot.img
dd if=/dev/block/by-name/boot_a      of=/data/local/tmp/boot.img
chmod 644 /data/local/tmp/*.img
/data/local/tmp/veritpath analyze --boot /data/local/tmp/boot.img \
                                  --init-boot /data/local/tmp/init_boot.img

/data/local/tmp/veritpath inject --init-boot /data/local/tmp/init_boot.img \
                                 -p /sdcard/my-su -o /data/local/tmp/
```

`/data/local/tmp` 可 exec 且 root 可写，是唯一适合当工作区的地方；
`/sdcard`（FUSE）是 noexec，二进制放那里**不能**直接执行。

## 判定与对策

| 布局 | 判定依据 | 对策 |
|---|---|---|
| Android ≤12 非 GKI | boot 内有 ramdisk | 直接改 boot.img 的 ramdisk，kernel/dtb 不动 |
| Android 13+ GKI | 有 init_boot.img | 注入 init_boot；boot.img 只装 kernel，绝不碰 |
| GKI 1.0（11/12） | boot 无 ramdisk、vendor_boot 有 | 改 vendor ramdisk，fragment 表自动同步 |
| recovery 也要 | vendor_boot 含 RECOVERY fragment | `--patch-vendor-boot`，两个 fragment 都注入 |
| system-as-root | API≥28 或 ramdisk 只有 first stage | 只注入 ramdisk，提示 payload 自行拷出文件 |
| 多段 ramdisk | cpio 含 `first_stage_ramdisk` | 只改 main 段，first stage 保持原样 |

架构判定顺序：ARM64 Image 的 EFI stub 头 → ELF `e_machine` → ARM32 zImage magic
→ x86 bzImage `HdrS` → DTB compatible 串。

## Payload 格式

一个目录 + `manifest.json`（没有就按文件名猜：`su`→`/su`、`init`→`/init`、`.rc`→rc 片段）：

```json
{
  "name": "my-su",
  "arch": ["arm64"],
  "min_api": 26,
  "files": [
    {"src": "su", "dest": "/su", "mode": "0755",
     "context": "u:object_r:rootfs:s0"}
  ],
  "rc": {
    "file": "/init.mysu.rc",
    "import_into": ["/init.rc"],
    "content": "service mysu /su --daemon\n    user root\n    oneshot\n"
  },
  "cmdline_append": ["androidboot.mysu=1"],
  "selinux": "permissive"
}
```

字段与 Python 版完全一致，详见 `../veritpath/docs/PAYLOAD.md`。
替换 `/init` 时原文件自动留作 `/init.real`；`context` 会追加进 ramdisk 的
`file_contexts`；注入后写入 `veritpath.json` 标记用于重复注入检测。

## 代码结构

```
src/
├── vp.h        单个公共头文件（所有结构体/原型）
├── main.c      CLI：analyze / plan / inject / unpack / repack
├── bootimg.c   boot v0-v4、vendor_boot v3-v4 解析与重建
├── cpio.c      cpio newc 读写、多段 ramdisk、目录树导出/导入
├── compress.c  gzip(zlib) / lz4(自实现) / xz / bzip2 / zstd
├── detect.c    架构、Android 版本、布局、system-as-root、A/B 判定
├── json.c      极简 JSON 解析（只为读 manifest）
├── payload.c   manifest 加载 + ramdisk 注入动作
├── strategy.c  对策报告输出
└── util.c      动态缓冲、文件 IO、日志
```

核心原则：**只改 ramdisk**。`raw_header` 保存原始 header 全部字节，`pack()` 只覆写
kernel/ramdisk/dtb 的大小字段与 cmdline；vendor_boot 的 fragment 表在 ramdisk
变长后自动重算 size/offset。

## 测试

`tests/run.sh` 用 Python 参考实现（同仓库的 `../veritpath`）生成合成镜像，
用 C 二进制处理，再用 Python 独立解析校验产物——两套实现互相验证：

```
== 24 passed, 0 failed ==
```

覆盖：布局判定、JSON 输出、plan 只读性、init_boot 注入、vendor_boot 双 fragment +
fragment 表一致性、legacy v1/v2 往返、lz4 强制压缩、unpack/repack 往返、错误输入。

## 与 Python 版的关系

`../veritpath` 是同一套逻辑的 Python 实现，保留作为**参考实现和测试基准**。
功能对等，但 Python 版没法直接在 Android 原生环境运行；C 版是发布形态。

## 已知边界

- 不合并 SELinux policy：`--permissive` 只是全局放宽
- 不做 AVB 重签名：需 `fastboot --disable-verity flash vbmeta vbmeta.img`
- 只给 `init_boot.img` 判不出架构（不带 kernel），需同时给 `boot.img`
- Windows 原生二进制需要 MinGW/MSYS2 提供 zlib，CI 未覆盖

## License

MIT
