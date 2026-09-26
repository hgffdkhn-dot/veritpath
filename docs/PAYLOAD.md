# Payload 规范

veritpath 的 payload = **一个目录 + 一份 manifest**。目录里放你要注入的二进制与脚本，
manifest 描述"放到哪、什么权限、什么时候启动"。没有 manifest 时 veritpath 会按文件名
自动推测，但建议始终显式写一份。

## 目录结构

```
my-su/
├── manifest.json        # 必需（或 manifest.yaml）
├── su                   # 你的二进制
├── init                 # 可选：替换 /init
└── init.mysu.rc         # 可选：rc 片段（也可直接写在 manifest 的 content 里）
```

用 `manifest.yaml` 需要额外装 PyYAML（`pip install pyyaml`），推荐用 JSON 以免依赖。

## manifest.json 字段

### 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | payload 名称，写入 ramdisk 的 `veritpath.json` 标记 |
| `version` | string | 版本号，仅用于记录 |
| `arch` | string[] | 支持的架构：`arm64` / `arm` / `x86_64` / `x86` / `riscv64`。留空表示任意架构；不匹配时拒绝注入（`--force` 绕过） |
| `min_api` / `max_api` | int | 支持的 Android API 区间 |
| `files` | array | 文件列表，见下 |
| `rc` | object | init rc 片段，见下 |
| `cmdline_append` | string[] | 追加到 kernel cmdline 的 token |
| `selinux` | string | `keep`（默认）或 `permissive`（等价于 `--permissive`） |
| `markers` | string[] | 额外标记文件名，用于第三方工具判断是否已注入 |

### `files[]`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `src` | string | — | 目录内的相对路径（必需） |
| `dest` | string | — | ramdisk 内的绝对路径（必需） |
| `mode` | string | `0755` | 八进制权限 |
| `uid` / `gid` | int | 0 | 属主 |
| `context` | string | — | SELinux 标签，会追加进 ramdisk 的 `file_contexts` |
| `backup_as` | string | — | 若目标已存在，先把原文件另存为该路径 |
| `required` | bool | true | 缺失时报错；false 则静默跳过 |
| `symlink` | string | — | 填写后不作为普通文件写入，而是创建指向该路径的符号链接 |

父目录会自动创建（`dest: /system/xbin/su` 会自动补出 `system`、`system/xbin`）。

**替换 `/init` 时**：若未指定 `backup_as`，veritpath 会自动把原 `/init` 留作
`/init.real` 并在报告里告警——否则你的 payload 一旦崩溃设备就变砖。

### `rc`

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | string | rc 片段写进 ramdisk 的路径，默认 `/init.veritpath.rc` |
| `import_into` | string[] | 在这些 rc 文件末尾追加 `import <file>`，默认 `["/init.rc"]` |
| `append_to` | string | 二选一：直接把内容追加进这个 rc 文件（不生成独立文件） |
| `content` | string | rc 内容 |
| `content_file` | string | 从 payload 目录里的某个文件读取 rc 内容 |

`import_into` 的目标若不存在，veritpath 会创建它并告警（老设备的 ramdisk 里
`/init.rc` 一定存在，所以出现告警说明你选的路径有问题）。

## 无 manifest 时的自动推测

| 文件名 | 推断结果 |
|---|---|
| `su` / `su.*` | → `/su`，mode `0755`，并生成默认 rc（启动 `/su --daemon`） |
| `init` | → `/init`，mode `0755`，原文件自动留作 `/init.real` |
| `*.rc` | → `/<name>`，并在 `/init.rc` 里 import |
| `*.sh` 及其他 | → `/veritpath/<name>`，mode `0755` |

## 完整示例

```json
{
  "name": "my-init-hook",
  "version": "1.2.0",
  "arch": ["arm64"],
  "min_api": 30,
  "files": [
    {"src": "libmy.so", "dest": "/system/lib64/libmy.so", "mode": "0644",
     "context": "u:object_r:system_lib_file:s0"},
    {"src": "daemon", "dest": "/system/bin/mydaemon", "mode": "0755"},
    {"src": "init", "dest": "/init", "mode": "0755", "backup_as": "/init.real"}
  ],
  "rc": {
    "file": "/init.myhook.rc",
    "import_into": ["/init.rc"],
    "content": "on post-fs-data\n    start myhook\n\nservice myhook /system/bin/mydaemon\n    class late_start\n    user root\n    seclabel u:r:init:s0\n    oneshot\n"
  },
  "cmdline_append": ["androidboot.myhook=1"],
  "selinux": "keep"
}
```

## 注入后 ramdisk 里会多出什么

- payload 声明的所有文件（按 `dest`）
- `veritpath.json`：记录工具名、payload 名、注入文件清单（重复注入检测依据）
- rc 片段文件 + 目标 rc 里的 `import` 行
- `file_contexts` 里新增的标签行（若 ramdisk 原本有 `file_contexts`）
- kernel cmdline 里新增的 token（仅 `cmdline_append` / `--permissive` / `--cmdline`）

## 自检清单

1. 架构与 API 区间是否覆盖目标设备（`veritpath analyze` 会告诉你 `arch` 与 `android_version`）。
2. system-as-root 设备：ramdisk 在 init 切根后会消失，需要你的 rc 在
   `on post-fs-data` 把文件拷到 `/data` 或 `/system`。
3. 需要 root daemon 常驻时，用 `service` + `class late_start`，不要阻塞 `on early-init`。
4. 注入后务必保留原镜像备份（`*.veritpath.bak`），变砖时可刷回。
