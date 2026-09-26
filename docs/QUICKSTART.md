# 快速上手

## 一、装好

三种方式任选，装完都是同一个单文件二进制。

```bash
# 方式 A：直接用，不装
adb push veritpath /data/local/tmp/veritpath
adb shell chmod 755 /data/local/tmp/veritpath

# 方式 B：install.sh 自动选目录
sh install.sh

# 方式 C：手动复制
cp veritpath /data/local/tmp/veritpath && chmod 755 /data/local/tmp/veritpath
```

> sdcard（`/storage/emulated/0`）是 `noexec` 挂载，二进制放那里**永远跑不起来**，
> 必须先拷到 `/data/local/tmp` 或 Termux 家目录。

确认装对了：

```bash
veritpath doctor          # VERSION:0.2.0 / VERDICT:OK
veritpath --version       # veritpath 0.2.0
```

## 二、在手机上取出镜像

有 root 的话不用到处找文件，直接从分区读：

```bash
su
getprop ro.boot.slot_suffix              # A/B 设备看当前槽位，如 _a
ls -l /dev/block/by-name/ | grep -E "boot|init_boot"

dd if=/dev/block/by-name/boot_a      of=/data/local/tmp/boot.img
dd if=/dev/block/by-name/init_boot_a of=/data/local/tmp/init_boot.img
chmod 644 /data/local/tmp/*.img
```

## 三、三步走

```bash
cd /data/local/tmp

# 1. 看看这台机器是什么布局
veritpath analyze --boot boot.img --init-boot init_boot.img

# 2. 只看对策，不动文件
veritpath plan --boot boot.img --init-boot init_boot.img \
               -p /sdcard/my-su --permissive

# 3. 真注入
veritpath inject --boot boot.img --init-boot init_boot.img \
                 -p /sdcard/my-su --permissive -o out/
```

输出 `out/init_boot.veritpath.img`，原镜像自动备份成 `*.img.veritpath.bak`。

## 四、注入后自检

```bash
veritpath verify out/init_boot.veritpath.img -p /sdcard/my-su
```

看到 `VERDICT:OK` 再刷。没注入成功会打 `VERDICT:INCOMPLETE` 并**退出码 1**，
可以直接拿来卡脚本：

```bash
veritpath verify out/init_boot.veritpath.img || exit 1
fastboot flash init_boot out/init_boot.veritpath.img
```

## 五、刷回去

```bash
# 先临时启动试一次，能起来再真刷
fastboot boot out/init_boot.veritpath.img

# 确认没问题后
fastboot flash init_boot out/init_boot.veritpath.img
fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
fastboot reboot
```

镜像改过就没 OEM 签名了，vbmeta 校验必须关掉或自行重签。

## 六、想手动改 ramdisk

```bash
veritpath unpack init_boot.img -d work/
# work/ramdisk/ 里是解出来的完整目录树，随你改
# work/original.img 是原始镜像，repack 要用
vim work/ramdisk/init.rc
veritpath repack work/ -o init_boot.new.img
```

## 常用参数

| 参数 | 作用 |
|---|---|
| `-p, --payload DIR` | payload 目录 |
| `-o, --output PATH` | 输出文件或目录 |
| `--permissive` | cmdline 加 `androidboot.selinux=permissive` |
| `--cmdline TOKENS` | 追加自定义 cmdline |
| `--segment N` | 指定注入第几个 cpio 段 |
| `--format NAME` | 强制 ramdisk 压缩（gzip / lz4_legacy / …） |
| `--patch-vendor-boot` | 连 vendor ramdisk（recovery/fastbootd）一起注入 |
| `--force` | 忽略告警、允许重复注入 |
| `--dry-run` | 内存里跑一遍，不落盘 |
| `--brief` | analyze 输出紧凑 KEY:VALUE |
| `--json` | 机器可读输出 |
| `--header-version N` | 强制指定头版本（解析失败时用） |
| `-v` | 详细输出 |

## 卡住了怎么办

```bash
veritpath hexdump boot.img     # 解析不了就先看这个
```

它会打印文件大小、前 64 字节 hex + ASCII、magic，以及 `@8/@12/@20/@24/@36/@40`
的实际值，外加工具自己的判定结果。把这段贴出来即可定位。
