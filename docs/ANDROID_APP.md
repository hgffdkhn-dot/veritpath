# 集成到 APK（安卓应用）

veritpath 的核心就是一堆 C 文件，**既能编成命令行二进制，也能编成动态库**。
APK 里有两条路可走，按你的场景选。

> English: [ANDROID_APP.en.md](ANDROID_APP.en.md)

## 方案对比

| | 方案 A：JNI 动态库（推荐） | 方案 B：打包二进制 + `exec` |
|---|---|---|
| 集成方式 | `System.loadLibrary("veritpath")` | 把二进制当 asset 拷出来再 `Runtime.exec()` |
| 需要的权限 | 无 | 需要可执行目录（不能放 sdcard） |
| 每 ABI 一份 | 由 Gradle 自动拆分 | 要自己判断 `Build.SUPPORTED_ABIS` |
| 取输出 | 直接返回 String | 要读进程的 stdout 流 |
| 稳定性 | 高 | 受 `noexec` 挂载、SELinux 影响 |
| 体积 | 略小（不重复打进几份） | 每个 ABI 一份完整二进制 |

**建议用方案 A。** 方案 B 在部分 ROM 上会踩坑：sdcard 是 `noexec`，而且 Android 10+
对应用私有目录执行二进制的限制越来越严。

## 方案 A：JNI 动态库

### 1. 放源码

把这几样拷进你的工程：

```
app/src/main/cpp/veritpath/
├── CMakeLists.txt          ← 项目里已提供
├── veritpath_jni.c         ← 项目里已提供
└── src/*.c, src/vp.h       ← veritpath 核心
```

Java 类放 `app/src/main/java/dev/veritpath/Veritpath.java`（项目里已提供）。

### 2. CMake 接进去

`app/src/main/cpp/CMakeLists.txt`：

```cmake
add_subdirectory(veritpath)
target_link_libraries(your_native_lib PRIVATE veritpath)
```

或者直接用现成的脚本构建：

```bash
cmake -S jni -B build-jni \
  -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24
cmake --build build-jni
```

用 `ndk-build` 的话，项目里也备好了 `jni/Android.mk` 和 `jni/Application.mk`。

一次性产出四个 ABI 的 `.so`：

```bash
bash build-android.sh --jni
# -> dist/jniLibs/{arm64-v8a,armeabi-v7a,x86_64,x86}/libveritpath.so
```

把 `jniLibs/` 整个拷到 `app/src/main/` 即可，不用配 CMake。

### 3. Java 里调用

```java
Veritpath.Result r = Veritpath.run(
        "analyze", "--brief", "--boot", bootFile.getAbsolutePath());

if (r.ok()) {
    String arch   = r.line("ARCH");     // "arm64"
    String target = r.line("TARGET");   // "init_boot"
}

// 或者一步拿到 JSON
String json = Veritpath.analyzeJson("--boot", bootPath);
```

注入：

```java
Veritpath.Result r = Veritpath.inject(
        payloadDir,                 // -p
        outputDir,                  // -o
        "--init-boot", initBootPath // 镜像参数
);
```

刷之前自检：

```java
if (!Veritpath.verify(patchedImage).ok()) {
    // 别刷，镜像不完整
}
```

### 4. 注意

- **别在主线程调**。GKI 1.0 的 boot.img 有 190MB 左右，解析要花点时间。
- `run()` 的参数就是命令行里 `veritpath` 后面敲的那些，一个 String 一个 token。
- 输出靠 `nativeLastOutput()` 取，下一次 `run()` 会覆盖，长期持有请自己拷贝。
- 不需要任何 root 权限就能**分析**；注入后的镜像要刷入仍然得走 fastboot。

## 方案 B：打包二进制 + exec

适合只是想快速验证、或者已有 exec 框架的情况。

```java
// 1. 从 asset 拷到私有目录
File bin = new File(getFilesDir(), "veritpath");
try (InputStream in = getAssets().open("veritpath-" + abi)) {
    Files.copy(in, bin.toPath(), REPLACE_EXISTING);
}
bin.setExecutable(true);

// 2. 执行
Process p = new ProcessBuilder(bin.getAbsolutePath(), "analyze", "--brief",
                               "--boot", bootPath)
        .redirectErrorStream(true).start();
String out = new String(p.getInputStream().readAllBytes());
int code = p.waitFor();
```

**三个坑**：

1. 别放 sdcard（`/storage/emulated/0`）——`noexec` 挂载，永远执行不了
2. `getFilesDir()` 在某些 ROM 上也有限制，优先用 `/data/data/<pkg>/files`
3. 要按 ABI 分别打包，`Build.SUPPORTED_ABIS[0]` 取当前架构

## 两种方案都要注意的

### 架构与 ABI

`veritpath analyze` 输出的 `ARCH` 是**内核架构**，和 App 自己的 ABI 不是一回事。
在 32 位进程里分析 arm64 镜像完全没问题——分析只是读文件。

### 权限

- **分析**：只读镜像文件，需要读权限（用 Storage Access Framework 让用户选文件最稳）
- **注入**：写输出文件，需要写权限
- **刷入**：必须走 fastboot，App 做不到

### 体积

静态库约 180KB，动态库更小。LZ4 是内置的，zlib 由系统提供，没有额外依赖。

### 线程安全

`run()` 内部用的是进程级 stdout 重定向（dup2），**不要并发调用**。串行执行，或者
自己加锁。

## 自检

没有 NDK 也能验证 JNI 层真的能跑：

```bash
bash tools/test_jni.sh
```

它会生成一份最小的 `jni.h` 存根，编出绑定，再用一个能干活的 JNIEnv 跑
`analyze --brief`，确认输出里真有 `ARCH:`。CI 里也有这一步。
