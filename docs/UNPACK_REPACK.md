# 拆解与重组（magiskboot 风格）

veritpath 可以把一个镜像拆成**每个组件一个文件**，改完再拼回去。工作目录就是
接口——这也是"一串命令"能成立的原因。

> English: [UNPACK_REPACK.en.md](UNPACK_REPACK.en.md)

## 一串命令

```bash
# 1. 拆
veritpath unpack boot.img -d work/

# 2. 改（任选）
echo "# my tweak" >> work/ramdisk/init.rc     # 改 ramdisk 里的文件
cp mykernel work/kernel                        # 换内核
cp my.dtb   work/dtb                           # 换 dtb

# 3. 拼回去
veritpath repack work/ -o boot.new.img

# 4. 确认
veritpath analyze --brief boot.new.img
```

## 工作目录里有什么

```
work/
├── image.json        元数据：header 字段 + 每个组件的说明
├── original.img      原始镜像副本（重组基底，保证 header 逐字节还原）
├── header.bin        原始 header
├── kernel            kernel（保持原始压缩）
├── second            second stage（若有）
├── dtb               dtb（若有）
├── recovery_dtbo     recovery dtbo（若有）
├── boot_signature    boot signature（v4，若有）
├── bootconfig        bootconfig（vendor v4，若有）
├── ramdisk.cpio      解压后的 cpio
├── ramdisk/          cpio 解出的目录树
└── ramdisk-<n>.cpio  多段时的其余各段
```

`image.json` 让这个目录自解释：

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

## 重组时谁优先

| 情况 | 行为 |
|---|---|
| `ramdisk/` 目录存在 | **优先**从目录树重建（多段时按 `segment<N>/`） |
| 目录不存在，有 `ramdisk.cpio` | 从 `.cpio` 文件重建 |
| 都没有 | 保留原始 ramdisk |
| `kernel` / `dtb` / `second` / `recovery_dtbo` / `boot_signature` / `bootconfig` 存在 | 用该文件替换 |
| 文件不存在 | 保留原始的 |

所以**只替换你想换的那一个**就行，其余不用管。

## 常见操作

**改 ramdisk 里的文件**

```bash
veritpath unpack init_boot.img -d work/
vim work/ramdisk/init.rc
veritpath repack work/ -o init_boot.new.img
```

**换内核**（kernel 会保持你给的原样，veritpath 不重新压缩）

```bash
veritpath unpack boot.img -d work/
cp zImage work/kernel
veritpath repack work/ -o boot.new.img
```

**改压缩格式**

```bash
veritpath repack work/ --format lz4_legacy -o boot.new.img
```

**多段 ramdisk（vendor_boot）**

```bash
veritpath unpack vendor_boot.img -d work/
ls work/ramdisk/                      # segment0/ segment1/ ...
# 每个段单独改
veritpath repack work/ -o vendor_boot.new.img
```

fragment 表会按新大小重写，段分隔完整保留。

## 和 inject 的区别

| | 用途 |
|---|---|
| `inject -p payload` | 按 manifest 把别人的 su/init 注入进去，自动选段、改 rc、同步 fragment 表 |
| `unpack` + 手改 + `repack` | 你自己精确控制加什么、放哪 |

想自动化、可复现用 `inject`；想手动调一个文件用 `unpack`。

## 注意

- **必须有 `original.img`**。它是重组基底，用来还原 header 里我们没建模的字段。
  删了就拼不回去。
- **尾部字节会保留**。如果原镜像是整分区 dump，`repack` 会连填充一起带过去，
  所以未改动时输出大小不变。
- 换 kernel 后大小变了，后面各段的 offset 会自动重算，不用手动调。
