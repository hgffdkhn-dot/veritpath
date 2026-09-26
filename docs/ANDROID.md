# 在 Android / Linux 上运行 veritpath

## 先搞清楚：为什么直接拷过去跑不起来

你遇到 `veritpath: inaccessible or not found`，是三个原因叠在一起：

1. **veritpath 是 Python 项目**，Android 原生 shell（`/system/bin/sh`）不带 Python 解释器，
   二进制 ELF 和 Python 脚本完全两回事。
2. **`/storage/emulated/0`（sdcard）是 FUSE 挂载的 noexec 分区**，
   即使 `chmod +x` 了也不能执行任何东西——这是 Android 的安全策略，改不了。
3. 当前目录不在 `PATH`，就算可执行也得写 `./veritpath`。

所以有两条现实可行的路：**Termux 里跑**，或**在电脑上跑完传回手机**。

---

## 方案 A：Termux 里跑（手机上直接处理，推荐）

### 1. 装 Termux 与 Python

```bash
pkg update
pkg install python
python --version          # 确认 3.8+
```

### 2. 把 portable 包弄到手机

```bash
# 电脑上
bash scripts/build-android.sh          # 生成 dist/veritpath-portable/
adb push dist/veritpath-portable /sdcard/veritpath
```

```bash
# Termux 里，先授权存储
termux-setup-storage
cp -r /storage/emulated/0/veritpath ~/veritpath
cd ~/veritpath
```

> **必须拷进 `~/`**（Termux 家目录）。`/sdcard` 下 noexec，装不了也跑不顺畅。

### 3. 安装

```bash
sh install.sh            # 自动识别 Termux，装到 $PREFIX/bin/veritpath
veritpath --version      # 装完直接用
```

### 4. 不装也能用

```bash
cd ~/veritpath
sh veritpath analyze --boot boot.img          # 注意：用 sh 调用
```

`sh veritpath` 这个写法在 **noexec 的 sdcard 上也能跑**——因为解释器脚本不需要
执行位，由 `sh` 读入执行。launcher 会自动找 `python3` / `python` 并定位旁边的
`veritpath.pyz`。

### 5. 有 root：直接从分区提 boot.img

```bash
su
getprop ro.boot.slot_suffix              # 看当前槽位，_a 或 _b
ls /dev/block/by-name/ | grep -E 'boot'

dd if=/dev/block/by-name/init_boot_a of=/data/local/tmp/init_boot.img
dd if=/dev/block/by-name/boot_a      of=/data/local/tmp/boot.img
chmod 644 /data/local/tmp/*.img
```

`/data/local/tmp/` root 可写且可 exec，适合当工作区。

---

## 方案 B：电脑上跑（最省事，推荐新手）

手机里 `dd` 出 boot.img → `adb pull` → 电脑处理 → `adb push` → fastboot 刷回。

```bash
# 电脑
cd veritpath
python -m pip install -e .
veritpath analyze --boot boot.img --init-boot init_boot.img
veritpath inject --boot boot.img --init-boot init_boot.img \
                 --payload payloads/example-su --permissive -o out/

adb push out/init_boot.veritpath.img /sdcard/
adb reboot bootloader
fastboot flash init_boot init_boot.veritpath.img
fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
fastboot reboot
```

---

## 方案 C：Linux 桌面 / 服务器

三选一，都零依赖：

```bash
# 1) pip 安装（最常规）
python -m pip install -e .
veritpath --help

# 2) 单文件 zipapp（拷到任何有 python3 的机器就能跑）
python3 scripts/make_zipapp.py
python3 dist/veritpath.pyz --help
./dist/veritpath.pyz --help              # 有 exec 权限时

# 3) portable 包 + 安装脚本
bash scripts/build-android.sh
sudo sh dist/veritpath-portable/install.sh --prefix /usr/local
veritpath --help
```

---

## 关于"真正的原生二进制"

有人会想：能不能给个不用 Python 的 `veritpath` 可执行文件？

- **Linux**：可以，PyInstaller 已配好，`bash scripts/build.sh` 出单文件二进制，
  CI 也自动打 Linux/macOS/Windows 三平台。
- **Android 原生（非 Termux）**：**不行**，至少不现实。Android 用 bionic libc，
  PyInstaller 打的 glibc 二进制在 bionic 上跑不起来，而交叉编译静态 Python 需要
  整套 NDK 工具链，成本远超收益。Termux 提供的 Python 正是为此存在的——它编译在
  bionic 上，所以方案 A 是唯一"在手机上真跑"的合理途径。

CI 打出来的 Linux 二进制**不要**往手机上拷，报 `not found` 或 `No such file or
directory`（其实是动态链接器缺失）就是这个原因。

---

## 排错

| 现象 | 原因 | 解决 |
|---|---|---|
| `veritpath: inaccessible or not found` | 没有 python，或文件不是可执行格式 | 用方案 A / B |
| `veritpath: command not found` | 不在 PATH | `sh veritpath` 或装到 `$PREFIX/bin` |
| `Permission denied` | sdcard noexec | 拷进 `~/`，或用 `sh veritpath` |
| `cannot find veritpath.pyz` | launcher 与 pyz 没放一起 | 两个文件放同一目录，或跑 `install.sh` |
| `no python interpreter found` | 没装 Python | Termux `pkg install python`；Linux `apt install python3` |
| `cannot determine boot image header version` | 不是 boot 镜像 | 确认 `dd` 对了分区，文件头是 `ANDROID!` |

## portable 包里有什么

```
veritpath-portable/
├── veritpath.pyz   单文件 zipapp（全部源码，零第三方依赖）
├── veritpath       shell launcher，自动找 python 并定位 pyz
├── install.sh      Termux / Linux 安装器，自动识别 prefix
└── payloads/       示例 payload
```

`veritpath.pyz` 就是个 ZIP，想看内容直接 `unzip -l veritpath.pyz`。
