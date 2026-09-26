# 架构说明

veritpath 分五层：**解析 → 判定 → 对策 → 注入 → 重建**。每层职责单一，可单独替换。

```
boot.img / init_boot.img / vendor_boot.img
        │
        ▼
┌─────────────────┐
│ bootimg.py      │  头解析：v0/v1/v2/v3/v4 + vendor_boot v3/v4
│                 │  保留原始 header 字节，只 patch 必要字段
└────────┬────────┘
         ▼
┌─────────────────┐
│ compression.py  │  gzip / xz / lzma / bzip2 / lz4(legacy+frame)
│                 │  纯 Python LZ4；支持"多段独立压缩"的 ramdisk
└────────┬────────┘
         ▼
┌─────────────────┐
│ cpio.py         │  cpio newc 读写；多段归档分别序列化
└────────┬────────┘
         ▼
┌─────────────────┐
│ detector.py     │  架构 / API / 布局 / system-as-root / GKI / A-B / 多段
└────────┬────────┘
         ▼
┌─────────────────┐
│ strategy.py     │  把判定结果翻译成"做什么"（对策清单）
└────────┬────────┘
         ▼
┌─────────────────┐
│ payload.py      │  manifest 解析 + 实际写入动作
│ injector.py     │  解包 → 注入 → 重打包 → 备份 → 写 JSON 记录
└────────┬────────┘
         ▼
    *.veritpath.img
```

## 关键设计

### 1. 只改 ramdisk，其余字节尽量不变

`BootImage.raw_header` 保存原始 header 的全部字节，`pack()` 只覆写
kernel/ramdisk/dtb 的大小字段与 cmdline。页对齐、header 私有字段、os_version
都保持原样。GKI 设备上 kernel 与 dtb 完全不触碰（vendor 签名校验需要）。

### 2. ramdisk 是"多段 + 独立压缩"的

Android 的 ramdisk 常常是几个 cpio 归档首尾相接（`first_stage_ramdisk` + main），
而 `vendor_boot` 里每个 fragment 又是**各自独立压缩**的。因此：

- `compression.split_chunks()` 按"压缩块"切分，返回 `(格式, 解压后数据, 下一块偏移)`
- `CpioArchive.segments` 保留每一段，`serialize_segments()` 分别序列化
- 重建时按段分别压缩，并用 `bootimg._compress_segments()` 保持每段原有格式
- vendor_boot 的 fragment table（size / offset）在 ramdisk 变长后自动重算

### 3. 纯 Python，零依赖

LZ4 块格式与 legacy/frame 两种容器都自己实现（`compression.py`）。压缩时若装了
`lz4` wheel 或能调用 CLI 就用它，否则退回"legacy 容器 + 未压缩块"——这是格式
允许的写法，bootloader 的 LZ4 解码器照常接受。

### 4. 判定顺序（detector.analyze）

1. **架构**：kernel 的 ARM64 EFI stub 头 → ELF e_machine → ARM32 zImage magic → x86 bzImage `HdrS` → DTB compatible 串。
2. **Android 版本**：`os_version` 字段（位编码）→ 有无 init_boot → vendor_boot v4 → header 版本。
3. **ramdisk 位置**：init_boot 存在 → init_boot；boot 自带 ramdisk → boot；vendor_boot 有 ramdisk → vendor_boot。
4. **system-as-root**：cmdline 显式标志 → ramdisk 内容（有 `/system/bin/init` 就不是 SAR）→ API ≥ 28。
5. **A/B 分区**：cmdline 的 `androidboot.slot_suffix` → bootconfig → 文件名后缀。
6. **多段 ramdisk**：cpio 段里是否含 `first_stage_ramdisk`。
7. **重复注入**：ramdisk 里是否有 `veritpath.json` / `init.veritpath.rc`。

### 5. 对策（strategy.build_plan）

不直接改文件，先产出 `Plan`：目标镜像、要打的 cpio 段、压缩格式、每个 payload 文件的
落点、SELinux 与 cmdline 变更、vendor_boot 是否连带注入、刷机命令、风险告警。
`plan` 命令就是把它打印出来；`inject` 执行它。同一份 `Plan` 也可 `to_dict()` 给别的工具用。

## 模块速查

| 模块 | 主要 API |
|---|---|
| `bootimg` | `BootImage.parse/pack`、`VendorBootImage.parse/pack`、`load_image()`、`detect_header_version()` |
| `cpio` | `CpioArchive.parse/serialize/serialize_segments`、`extract_to_dir()`、`build_from_dir()` |
| `compression` | `detect()`、`decompress()`、`compress()`、`split_chunks()`、`consume_chunk()` |
| `detector` | `analyze()`、`detect_kernel_arch()`、`guess_api()` |
| `payload` | `PayloadSpec.load()`、`apply_payload()` |
| `strategy` | `build_plan()`、`Options` |
| `injector` | `run_injection()`、`load_images()` |

## 扩展新的布局

1. 在 `detector.analyze()` 里加判定分支，写进 `Analysis`（含 evidence）。
2. 在 `strategy.LAYOUT_ADVICE` 加对应说明，并在 `build_plan()` 里加处理步骤。
3. 若需要改 ramdisk 之外的结构（新的 header 版本），改 `bootimg.py` 的
   `parse`/`pack`，保持"只 patch 必要字段"的原则。
4. 在 `tests/fixtures.py` 加一份合成镜像，并补测试。

## 已知限制

- 不合并 SELinux policy：`--permissive` 只是全局放宽。
- 不做 AVB 重签名：改过的镜像需 `fastboot --disable-verity flash vbmeta vbmeta.img`。
- 不解析 dtbo / bootconfig 语义，只原样保留。
- LZ4 压缩在无 `lz4` 依赖时用"未压缩块"容器，体积比真压缩大（但一定可启动）。
