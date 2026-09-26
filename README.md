# veritpath

**分析 boot 镜像 → 判定系统架构 → 给出对策 → 正确注入第三方 su / init 文件。**

veritpath 是一个面向 Android 开发者的**注入工具链**：你把 `boot.img` / `init_boot.img` / `vendor_boot.img` 和自己的 payload（su、init、rc 片段……）交给它，它自己判断这台机器属于哪种布局（system-as-root、GKI、init_boot、vendor ramdisk 多段……），然后只改该改的地方，重新打包出一个能启动的镜像。

- 纯 Python，**零运行时依赖**（自带 LZ4 解压实现，不依赖 magiskboot / mkbootimg）
- 覆盖 boot header **v0 / v1 / v2 / v3 / v4** 与 **vendor_boot v3 / v4**（含 vendor ramdisk fragment table）
- 保留原镜像的 header 私有字段、页对齐、压缩格式、cmdline，只改 ramdisk
- 自动备份原镜像，并在镜像旁写入 JSON 注入记录，便于回溯
- 输出人类可读的"对策报告"与 JSON，方便接入你自己的工具链

> 请只对你拥有或有授权的设备与镜像使用本工具；刷机有风险，注入后的镜像不再有 OEM 签名。

---

## 安装

```bash
git clone https://github.com/<your-account>/veritpath
cd veritpath
python -m pip install -e .      # 提供 `veritpath` 命令
# 或者直接跑源码，无需安装：
python -m veritpath --help
```

单文件二进制（CI 已配置好，本地也能一键打）：

```bash
bash scripts/build.sh dist      # 需要 pip install pyinstaller
./dist/veritpath --help
```

## 快速开始

```bash
# 1) 看看这台机器是什么布局
veritpath analyze --boot boot.img --init-boot init_boot.img --vendor-boot vendor_boot.img

# 2) 先看对策，不改任何文件
veritpath plan --init-boot init_boot.img --payload payloads/example-su --permissive

# 3) 真的注入（自动备份原镜像，输出 *.veritpath.img）
veritpath inject --boot boot.img --init-boot init_boot.img \
                 --payload payloads/example-su \
                 --permissive --patch-vendor-boot -o out/

# 4) 手动改 ramdisk 也行
veritpath unpack init_boot.img -d work/     # 解出 ramdisk/ 目录树
vim work/ramdisk/init.rc
veritpath repack work -o init_boot.new.img
```

没有真机镜像？生成一套合成的 GKI 镜像先试手：

```bash
python scripts/make_sample_images.py samples
```

## 在 Android / Linux 上运行

手机里直接跑不起来？那是因为 veritpath 是 Python 项目，而 Android 原生 shell 没有
Python，且 `/storage/emulated/0` 是 noexec 分区。三种正确姿势见
[docs/ANDROID.md](docs/ANDROID.md)，最省事的是在电脑上处理完再刷回手机。

手机上（Termux）想直接跑：

```bash
pkg install python
bash scripts/build-android.sh          # 电脑上生成 dist/veritpath-portable/
adb push dist/veritpath-portable /sdcard/veritpath
cp -r /storage/emulated/0/veritpath ~/veritpath && cd ~/veritpath
sh install.sh            # 装到 $PREFIX/bin，之后直接 veritpath ...
sh veritpath analyze --boot boot.img      # 不装也能用（sdcard 上亦可）
```

Linux 上三种零依赖用法（`pip install -e .` / `python3 veritpath.pyz` / portable 包）。

## 命令

| 命令 | 作用 |
|---|---|
| `analyze` | 判定架构 / Android 版本 / ramdisk 位置 / 是否 system-as-root / 是否 GKI，指出注入目标 |
| `plan` | 输出"对策"清单（等价于 inject 的 dry run） |
| `inject` | 执行注入并输出新镜像 + JSON 记录 |
| `unpack` / `repack` | 手工拆解 / 重组镜像（保留原始 header 与 kernel） |

常用参数：`--payload`（payload 目录）、`-o`（输出文件/目录）、`--patch-vendor-boot`（连 recovery/fastbootd 一起注入）、`--permissive`（cmdline 加 `androidboot.selinux=permissive`）、`--cmdline`、`--segment`（指定 cpio 段）、`--format`（强制 ramdisk 压缩格式）、`--force`、`--dry-run`、`--json`。

## 判定与对策

| 布局 | 判定依据 | veritpath 的对策 |
|---|---|---|
| Android ≤12 普通 boot | boot header v0–v4 且 boot 内含 ramdisk | 直接改 boot.img 的 ramdisk，kernel / dtb 不动 |
| Android 13+ GKI | 提供 init_boot.img，或 boot 无 ramdisk | 注入 init_boot.img；boot.img 只装 kernel，绝不碰 |
| GKI 1.0（Android 11/12） | boot 无 ramdisk，vendor_boot 有 | 注入 vendor_boot 的 vendor ramdisk，并同步 fragment table 的 size/offset |
| recovery / fastbootd 也要 | vendor_boot 含 RECOVERY fragment | `--patch-vendor-boot`：platform 与 recovery 两个 fragment 都注入 |
| system-as-root（Android 9+） | API≥28，或 ramdisk 只有 first stage init | 只注入 ramdisk；提示 payload 需在 rc 里自行把文件拷出（切根后 ramdisk 会消失） |
| 多段 ramdisk | cpio 含 `first_stage_ramdisk` 段 | 只改 main 段，first stage 段保持原样 |

其他自动处理：保持原压缩格式（gzip / lz4 legacy / lz4 frame / xz / lzma / bzip2）、保持 cmdline、替换 `/init` 时自动把原文件留作 `/init.real`、检测到 payload 架构不匹配时拒绝注入（`--force` 可绕过）、A/B 设备提示刷入对应 slot。

## Payload 规范

payload 就是一个目录 + 一个 `manifest.json`（没有 manifest 时，veritpath 会按文件名自动推测：`su` → `/su`、`init` → `/init`、`*.rc` → rc 片段）。

```json
{
  "name": "my-su",
  "arch": ["arm64"],
  "min_api": 26,
  "files": [
    {"src": "su", "dest": "/su", "mode": "0755",
     "context": "u:object_r:rootfs:s0", "backup_as": "/su.orig"}
  ],
  "rc": {
    "file": "/init.veritpath.rc",
    "import_into": ["/init.rc"],
    "content": "service my-su /su --daemon\n    user root\n    seclabel u:r:init:s0\n"
  },
  "cmdline_append": ["androidboot.my-su=1"],
  "selinux": "permissive"
}
```

veritpath 会：把文件按 mode/uid/gid 写进正确的 cpio 段、自动补全父目录、把 `context` 追加进 ramdisk 的 `file_contexts`、生成 rc 并在 `/init.rc` 里 `import`、把 `cmdline_append` 与 selinux 策略写进 kernel cmdline、写入 `veritpath.json` 标记（用于检测重复注入）。

完整字段说明见 [docs/PAYLOAD.md](docs/PAYLOAD.md)，内部实现见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 项目结构

```
veritpath/
├── veritpath/
│   ├── bootimg.py      # boot/vendor_boot 头解析与重建（v0-v4）
│   ├── cpio.py         # cpio newc 读写，支持多段 ramdisk
│   ├── compression.py  # gzip/lz4(纯 Python)/xz/bzip2 分块解压与压缩
│   ├── detector.py     # 架构、Android 版本、布局、system-as-root 判定
│   ├── strategy.py     # 对策生成（做什么、为什么）
│   ├── payload.py      # payload manifest 与注入动作
│   ├── injector.py     # 解包 → 注入 → 重打包 → 写记录
│   └── cli.py          # analyze / plan / inject / unpack / repack
├── payloads/example-su # 示例 payload（占位 su，换成你自己的二进制）
├── docs/               # PAYLOAD.md / ARCHITECTURE.md / ANDROID.md
├── scripts/            # 合成镜像生成器、zipapp/portable 打包、安装脚本
├── tests/              # 43 项单元与端到端测试（含合成镜像）
└── .github/workflows/  # CI + Release
```

## 开发与发布

```bash
make install   # pip install -e ".[dev]"
make test      # pytest
make lint      # ruff check + format --check
make samples   # 生成合成镜像
make build     # PyInstaller 单文件二进制（Linux/macOS/Windows）
	@echo "make portable  zipapp + shell launcher（Android / Linux 通用）"
```

GitHub Actions（已写好，推上去即可用）：

- `ci.yml`：`push` / `PR` 触发 → ruff 检查 → Python 3.9–3.12 × Linux/macOS/Windows 单元测试 → 端到端 smoke（合成镜像 + 真实注入并校验产物）→ PyInstaller 打包三个平台的单文件二进制 → 构建 sdist/wheel，全部作为 artifact 上传。
- `release.yml`：推送 `v*.*.*` tag（或手动 workflow_dispatch 指定 tag）→ 打包 Linux x86_64 / macOS x86_64+arm64 / Windows 二进制与 wheel → 自动创建 GitHub Release 并附带更新日志。

发布一个版本：

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## 已知边界

- 不合并 SELinux policy：`--permissive` 只是放宽全局；payload 若需要自己的 domain，请自行提供 sepolicy 与 file_contexts。
- 不做 AVB 重签名：镜像改动后签名失效，需要 `fastboot --disable-verity flash vbmeta vbmeta.img` 或自行重签。
- 不修改 kernel / dtb（GKI 设备尤其不能改）。
- 只给 init_boot.img 时无法判定架构（它不带 kernel），需要同时提供 boot.img。
- 不提供任何 su 实现，`payloads/example-su/su` 只是占位脚本。

## License

MIT
