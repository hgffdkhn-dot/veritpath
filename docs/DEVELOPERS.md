# 开发者对接指南

veritpath **不提供任何 su 实现**。它做的是：拿到你给的文件 → 判断设备布局 →
决定这些文件该放哪 → 重建一个能启动的镜像。你只需要按约定准备一个 payload 目录。

## 一、payload 是什么

一个目录 + 一个 `manifest.json`：

```
my-su/
├── manifest.json
└── su              ← 你的二进制
```

最小可用的 manifest：

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

`payloads/template/` 是一份可以直接抄的空壳。

**没有 manifest 也能用**——veritpath 会按文件名猜：

| 文件名 | 落到哪 |
|---|---|
| `su` / `su.*` | `/su`（0755，带 `u:object_r:rootfs:s0` 标签，并自动生成 rc） |
| `init` | `/init`（原 `/init` 自动备份为 `/init.real`） |
| `*.rc` | `/<原名>`，并挂进 `/init.rc` |
| 其他 | `/veritpath/<原名>`（0755） |

## 二、manifest 完整字段

### 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | payload 名字，写进 `/veritpath.json` |
| `version` | string | 仅记录用 |
| `arch` | array | 允许的架构：`arm64` / `arm` / `x86_64` / `x86`。不匹配会告警，`--force` 可忽略 |
| `min_api` / `max_api` | number | 允许的 Android API 区间 |
| `selinux` | string | `permissive` 会在 cmdline 加 `androidboot.selinux=permissive`；`keep` 则不动 |
| `cmdline_append` | array | 追加到 kernel cmdline 的 token |
| `files` | array | 要写进 ramdisk 的文件 |
| `rc` | object | init rc 片段 |

### files[] 每项

| 字段 | 说明 |
|---|---|
| `src` | payload 目录里的相对路径 |
| `dest` | ramdisk 里的绝对路径 |
| `mode` | 权限，八进制字符串，默认 `0755` |
| `uid` / `gid` | 默认 0 |
| `context` | SELinux 标签，会写进 `/file_contexts` |
| `backup_as` | 若 `dest` 已存在，先把原文件备份到这个路径 |
| `symlink` | 额外创建一个指向 `dest` 的符号链接 |
| `required` | 默认 true。设为 false 时缺失只告警不失败 |

### rc 对象

| 字段 | 说明 |
|---|---|
| `file` | rc 片段在 ramdisk 里的路径，默认 `/init.veritpath.rc` |
| `content` | rc 正文（字符串，用 `\n` 换行） |
| `content_file` | 或者指向 payload 目录里的一个文件，比写在 JSON 里好维护 |
| `import_into` | 数组。在列出的 rc 文件头部插入 `import <file>` |
| `append_to` | 把正文追加到某个已存在的 rc 文件末尾 |

`import_into` 和 `append_to` 可以都用：前者保证被 init 加载，后者在你需要精确
控制顺序时用。

## 三、布局决定注入点

`veritpath analyze` 会告诉你 ramdisk 在哪，不同布局对应不同镜像：

| 布局 | 典型设备 | ramdisk 位置 | 注入对象 |
|---|---|---|---|
| `init_boot` | Android 13+ GKI | `init_boot.img` | 只改 init_boot，boot.img 别碰（它只有内核） |
| `vendor_boot` | Android 11/12 GKI 1.0 | `vendor_boot.img` 的 vendor_ramdisk | 改 vendor ramdisk，fragment 表会自动同步 |
| `boot` | Android ≤12 非 GKI | `boot.img` | 直接改 boot.img |

**多段 ramdisk 要特别注意。** 现代镜像常带 `first_stage_ramdisk`，它比主段先加载。
你的文件必须进**主段**，进错阶段会拿不到该有的能力。

```bash
veritpath unpack init_boot.img -d work/
ls work/ramdisk/          # segment0 / segment1 ...

veritpath inject --init-boot init_boot.img -p my-su --segment 1 -o out/
```

不指定 `--segment` 时，veritpath 选条目最多的那段当主段。

**system-as-root 设备**上，init 切根到 `/system` 后 ramdisk 就消失了。你的 rc
需要在 `on post-fs-data` 把要保留的文件拷到 `/data`。

## 四、SELinux

两条路，可以都用：

1. 给每个文件写 `context`，veritpath 会追加到 `/file_contexts`
2. manifest 里 `"selinux": "permissive"`，或命令行 `--permissive`

调试阶段建议先 permissive，确认功能正常后再补正式标签切回 enforcing。

## 五、对接流程建议

```bash
# 1. 检查 payload 写对了没（不用碰镜像）
veritpath payload-check my-su

# 2. 看对策（dry run，不动文件）
veritpath plan --init-boot init_boot.img -p my-su --permissive

# 3. 内存里跑一遍，确认无误再落盘
veritpath inject --init-boot init_boot.img -p my-su --dry-run

# 4. 真注入
veritpath inject --init-boot init_boot.img -p my-su --permissive -o out/

# 5. 自检
veritpath verify out/init_boot.veritpath.img -p my-su
```

## 六、退出码与自动化

| 命令 | 成功 | 失败 |
|---|---|---|
| `analyze` | 0 | 解析失败 / 文件不存在 → 1 |
| `plan` | 0 | 1 |
| `inject` | 0 | 1 |
| `verify` | 0（`VERDICT:OK`） | **1（`VERDICT:INCOMPLETE`）** |
| `payload-check` | 0 | manifest 损坏 → 1 |

`verify` 的非零退出码就是给 CI 和刷机脚本用的闸门：

```bash
veritpath inject --init-boot init_boot.img -p my-su -o out/ || exit 1
veritpath verify out/init_boot.veritpath.img -p my-su || exit 1
fastboot flash init_boot out/init_boot.veritpath.img
```

`--json` 让 `analyze` 输出结构化结果，方便脚本取 `target` 字段自动选镜像。

## 七、留痕

注入后 ramdisk 里会多出 `/veritpath.json`，记录 payload 名、版本、注入时间、
目标布局。veritpath 靠它判断「已经注入过」，重复注入会告警（用 `--force` 覆盖）。
你的代码也可以读它做运行时判断。

## 八、当前已知限制

- 重建 `vendor_boot.img` 时会把多个 ramdisk fragment 合并成一个。payload 仍会
  进入 vendor ramdisk，但 fragment 分隔不保留。
- `arch` 检测依赖 kernel（判断 `ARM64` 魔数）。`init_boot.img` 不带 kernel，
  单独给它时判不出架构，需要同时传 `boot.img`。
