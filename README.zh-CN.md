# veritpath

一款用 **C** 写的轻量工具，风格对齐 `magiskboot`：解析 `boot.img` /
`init_boot.img` / `vendor_boot.img`，判断设备架构与 root 布局，然后把第三方
`su` / `init` 文件**正确地**注入进去。

不需要 Python、不需要解释器、不需要运行时。`adb push` 到手机上直接跑。

> English README: [README.md](README.md)

## 编译

```bash
make                 # -> build/veritpath
make static          # 全静态，任意发行版 / 安卓都能跑
bash build.sh        # 交叉编译，见下文
```

依赖只有 `libc` 和 `zlib`——glibc、musl、安卓 NDK 都自带。LZ4 是内置的；
xz / bzip2 / zstd 有库就用、没有就跳过。

## 支持的平台

| 目标 | 怎么得到 |
|---|---|
| Linux x86_64 / aarch64 | `make static`，或 Release 工作流 |
| 安卓 arm64 / arm / x86_64 / x86 | `bash build-android.sh`，或 Release 工作流 |
| Windows x86_64 | `bash build.sh windows-x86_64`（需 mingw-w64），或 Release 工作流 |
| macOS x86_64 / arm64 / universal | Release 工作流在 macOS runner 上构建 |

`build-android.sh` 会自己找 NDK；找不到就用 `sdkmanager` 装一个（CI 只需要
这些，不依赖任何第三方 setup action）；在 Termux 或 `adb shell` 里运行时则退回
设备自带的 clang。

两份工作流只用了 GitHub 官方 action（`actions/checkout`、
`upload/download-artifact`）加上 runner 自带的 `gh` CLI——没有会突然从市场消失
的东西。打 tag 就会构建所有平台：

```bash
git tag v0.2.0 && git push origin v0.2.0
```

## 命令

```
veritpath analyze --boot boot.img [--init-boot ...] [--vendor-boot ...]
veritpath plan    --init-boot init_boot.img -p payload --permissive
veritpath inject  --init-boot init_boot.img -p payload -o out/
veritpath unpack  boot.img -d work/          # 解出 ramdisk 树 + original.img
veritpath repack  work/ -o boot.new.img
veritpath verify  out/boot.veritpath.img     # 检查注入结果
veritpath payload-check my-su                # 检查 payload 目录
veritpath hexdump boot.img                   # 诊断用
veritpath doctor                             # 当前跑的是哪个版本
sh install.sh                                # 安装（并清理旧文件）
```

可选参数：`--header-version N`（强制头版本）、`--patch-vendor-boot`、
`--permissive`、`--cmdline`、`--segment N`、`--format lz4_legacy`、
`--force`、`--dry-run`、`--brief`、`--json`、`-v`。

### 安卓上用

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

`/data/local/tmp/` 可写可执行。`/storage/emulated/0`（sdcard）是 `noexec`
挂载，二进制放那里**永远跑不起来**，必须先拷出来。

### 命令敲了没反应？加 `./`

很多安卓设备上，当前目录不在 `PATH` 里，所以就算二进制就在眼前，裸敲
`veritpath` 也找不到：

```
$ veritpath analyze --boot boot.img
veritpath: inaccessible or not found
```

加上前缀，或者写完整路径：

```bash
./veritpath analyze --boot boot.img
/data/local/tmp/veritpath analyze --boot boot.img
```

`command -v veritpath` 可以判断它到底在不在 `PATH`（只有跑过 `install.sh`
才算）。改过 `PATH` 或删过旧文件后，执行一下 `hash -r`——bash 会缓存命令的
解析结果，否则会一直指向一个已经不存在的路径。

## 输出格式

`analyze` 默认输出分组报告：先结论，再每个镜像一块，最后是 findings 和
注入目标：

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

每条 `·` 都是一个具体后果，不是装饰：ramdisk 在哪、`/system` 会不会盖上去、
重建时会用哪种压缩。

另外两种格式：

```bash
veritpath analyze --brief --boot boot.img    # magiskboot 风格 KEY:VALUE
veritpath analyze --json  --boot boot.img    # 机器可读
```

`--brief` 每行一个 `HEADER_VER:` / `RAMDISK_SZ:` / `ARCH:` / `TARGET:`，
要管道给 `grep` 时用这个。

## 安装

二进制是自包含的——没有 `.pyz`、没有 launcher、没有数据目录。拷到任何能
`chmod +x` 的地方就能跑：

```bash
adb push veritpath /data/local/tmp/veritpath
adb shell chmod 755 /data/local/tmp/veritpath
adb shell /data/local/tmp/veritpath analyze --boot /sdcard/boot.img
```

或者让安装脚本自动挑目录（Termux → `$PREFIX/bin`，root → `/usr/local/bin`，
已 root 的安卓 shell → `/data/local/tmp`）：

```bash
sh install.sh              # 自动判断
sh install.sh --to-tmp     # 装到 /data/local/tmp
sh install.sh --prefix DIR # 装到指定目录
```

`install.sh` 还会顺手删掉 Python 时代的残留（`bin/veritpath` 启动器和
`share/veritpath/`）。正是那些文件把新编译的二进制挡在 PATH 外面，导致
`veritpath --version` 一直显示 0.1.0。只想清理不装：

```bash
sh install.sh --clean
```

## 排错

### `cannot determine boot image header version`

**体积大不是问题**——GKI 1.0 的 boot.img（骁龙 888、一加 9 / 9 Pro，内核
5.4.x）本来就有 192MB 左右，veritpath 对文件大小没有上限。这个报错是说头版本
字段的值对不上任何一种已知布局。先跑：

```bash
veritpath hexdump boot.img
```

它会打印文件大小、前 64 字节的 hex + ASCII、magic，以及 `@8 @12 @20 @24 @36
@40` 的实际值。

下面这些情况 veritpath 已经自动处理了：

| 情况 | 行为 |
|---|---|
| magic 不在偏移 0（板级头 / 填充） | 扫描并跳过前缀 |
| 整份镜像被 gzip/xz/lz4 包着 | 先解开 |
| `@20` 头部大小被页对齐填成 4096、填 0、或垃圾值 | 忽略 / 钳制 |
| `@24` 是 5 或 6（未来版本） | 按 v3/v4 布局解析 |
| 两个版本字段都是垃圾 | 从布局本身反推版本 |
| sparse / payload.bin / zip / AVB / ELF / 裸 dtb | 指名并给出转换命令 |

都不适用时，强制指定版本试试：

```bash
veritpath analyze --boot boot.img --header-version 3
```

### 完全没有 ramdisk（system-as-root）

纯 SAR 设备的 `boot.img` 不含 ramdisk——内核把 `/system` 挂成 `/`，直接跑
`/system/bin/init`。`analyze` 会报 `LAYOUT:no_ramdisk` 和 `NEEDS_RAMDISK:1`。

```bash
veritpath inject --boot boot.img -p my-su --create-ramdisk -o out/
```

不加这个 flag 会直接失败退出，不会写出一个没动过的镜像。生成的 `/init` 只是占位，
必须在 payload 里提供真正的静态 init（`{"src": "init", "dest": "/init"}`），
否则开不了机。详见 [docs/DEVELOPERS.md](docs/DEVELOPERS.md) 第十节。

### 交叉编译时 `cannot find -lz`

交叉编译器装好了并不代表目标的 zlib 也装好了，链接时会报 `cannot find -lz`。

```bash
# aarch64
sudo dpkg --add-architecture arm64
sudo apt-get update && sudo apt-get install zlib1g-dev:arm64

# 32 位 x86
sudo apt-get install zlib1g-dev:i386

# Windows (mingw)
sudo apt-get install libz-mingw-w64-dev
```

手上若有为目标架构编好的 zlib，直接指过去：

```bash
ZLIB_DIR=/path/to/sysroot/lib ./build.sh linux-aarch64
```

如果 apt 也装不上（arm64 索引在某些 runner 上 404），`build.sh` 会自动**从源码为
交叉目标编一份 zlib**，缓存到 `~/.cache/veritpath-zlib/`。也可手动指定：

```bash
ZLIB_DIR=/path/to/sysroot/lib ./build.sh linux-aarch64
VP_NO_AUTO_ZLIB=1 ./build.sh linux-aarch64   # 关闭自动准备
```

`apt-get update` 返回非 0（foreign 架构索引 404 时常见）不会再中断流程——装不上
就源码编译，都不行就跳过该目标。



如果 apt 也装不上（arm64 索引在某些 runner 上 404），`build.sh` 会自动**从源码为
交叉目标编一份 zlib**，缓存到 `~/.cache/veritpath-zlib/`。也可手动指定：

```bash
ZLIB_DIR=/path/to/sysroot/lib ./build.sh linux-aarch64
VP_NO_AUTO_ZLIB=1 ./build.sh linux-aarch64   # 关闭自动准备
```

`apt-get update` 返回非 0（foreign 架构索引 404 时常见）不会再中断流程——装不上
就源码编译，都不行就跳过该目标。

`build.sh` 会在编译前先测一次 `-lz`，失败就打印上面的命令而不是甩一个裸的 ld
错误。用 `all` 时缺依赖的目标会被跳过；明确指定单个目标时才会失败退出。

### 输出比输入小很多

不是数据丢失。`dd` 整个分区（`/dev/block/by-name/boot_a`）会把真实镜像之后的 0
填充一起拷出来，重打包只输出镜像本身。192MB 的分区 dump 里真实镜像只有 42MB，
输出 42MB 是正确结果。`analyze` 会输出 `TRAILING.<角色>:<字节数>`（JSON 里是
`trailing` 字段）并附一句说明。要保留填充就加 `--keep-trailing`。

### `no such file`

报错会列出它解析出的路径、当前工作目录、该目录里实际有什么，以及最接近的
文件名，一眼就能看出哪里对不上。

### 安卓上 `TLS segment is underaligned`

Bionic 要求 `PT_TLS` 对齐 ≥ 64，而 NDK 静态链接只给 8，于是直接 abort。
`build-android.sh` 已自动修复；手动修补：

```bash
python3 tools/elf_fix.py /data/local/tmp/veritpath
```

## 文档

| 文档 | 语言 | 内容 |
|---|---|---|
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | 中文 | 安装、取镜像、注入、刷机、排错 |
| [docs/DEVELOPERS.md](docs/DEVELOPERS.md) | 中文 | payload 格式、manifest 字段、布局规则、SELinux、退出码 |
| [docs/QUICKSTART.en.md](docs/QUICKSTART.en.md) | English | 同上，英文版（给非中文开发者） |
| [docs/DEVELOPERS.en.md](docs/DEVELOPERS.en.md) | English | 同上，英文版（给非中文开发者） |
| [docs/UNPACK_REPACK.md](docs/UNPACK_REPACK.md) | 中文 | 拆解镜像为组件、改完再重组 |
| [docs/ANDROID_APP.md](docs/ANDROID_APP.md) | 中文 | 集成到 APK：JNI 动态库 / 打包二进制 |
| [docs/ANDROID_APP.en.md](docs/ANDROID_APP.en.md) | English | same as ANDROID_APP, in English |

两份中文文档都配了英文版，非中文开发者直接看 `.en.md` 即可。

给 payload 作者的简述：一个目录放你的二进制，加一个 `manifest.json` 声明每个
文件去哪（参考 `payloads/template/`）。veritpath **不提供任何 su 实现**——它
只负责判断在当前布局下你的文件该放哪，并重建一个能启动的镜像。

不碰镜像就检查 payload：

```bash
veritpath payload-check my-su
```

检查刚打出来的镜像（缺东西就非零退出）：

```bash
veritpath verify out/init_boot.veritpath.img -p my-su
```

## 测试

```bash
bash tests/run.sh          # 68 项检查，自包含（只需 cc + python3）
```

`tests/imgkit.py` 用纯标准库构建合成镜像并校验注入产物，不依赖任何第三方包。

`vendor_boot.img` 的处理：ramdisk 按 fragment 表切分（而不是靠嗅探压缩格式），
每个 fragment 独立重建，表里的 size / offset 会按原图约定（页对齐或紧挨）重写。
每个 vendor ramdisk fragment 都会收到 payload。
