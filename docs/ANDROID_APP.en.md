# Embedding in an APK

The core of veritpath is plain C, so it builds either as a command-line binary
or as a shared library. There are two ways to use it from an app; pick based on
your situation.

> 中文版：[ANDROID_APP.md](ANDROID_APP.md)

## Which approach

| | A: JNI shared library (recommended) | B: bundle the binary and `exec` it |
|---|---|---|
| Integration | `System.loadLibrary("veritpath")` | copy a binary out of assets, then `Runtime.exec()` |
| Permissions needed | none | an executable directory (not the sdcard) |
| Per-ABI builds | Gradle splits them automatically | you pick via `Build.SUPPORTED_ABIS` |
| Getting output | returned as a String | read the process stdout stream |
| Robustness | high | subject to `noexec` mounts and SELinux |
| Size | smaller | one full binary per ABI |

**Prefer A.** Option B breaks on some ROMs: the sdcard is `noexec`, and Android
10+ keeps tightening the rules on executing binaries from an app's private
directory.

## Option A: JNI shared library

### 1. Add the sources

Copy these into your project:

```
app/src/main/cpp/veritpath/
├── CMakeLists.txt          ← provided
├── veritpath_jni.c         ← provided
└── src/*.c, src/vp.h       ← the veritpath core
```

And the Java class at `app/src/main/java/dev/veritpath/Veritpath.java`
(also provided).

### 2. Wire it into CMake

In `app/src/main/cpp/CMakeLists.txt`:

```cmake
add_subdirectory(veritpath)
target_link_libraries(your_native_lib PRIVATE veritpath)
```

Or build it standalone:

```bash
cmake -S jni -B build-jni \
  -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24
cmake --build build-jni
```

If you use `ndk-build`, `jni/Android.mk` and `jni/Application.mk` are ready.

To produce `.so` files for all four ABIs in one go:

```bash
bash build-android.sh --jni
# -> dist/jniLibs/{arm64-v8a,armeabi-v7a,x86_64,x86}/libveritpath.so
```

Then copy `jniLibs/` straight into `app/src/main/` — no CMake needed.

### 3. Call it from Java

```java
Veritpath.Result r = Veritpath.analyze(
        Veritpath.Image.initBoot(initBootPath),
        Veritpath.Image.boot(bootPath));

if (r.ok()) {
    String arch   = r.line("ARCH");     // "arm64"
    String target = r.line("TARGET");   // "init_boot"
}

// or get JSON directly
String json = Veritpath.analyzeJson(Veritpath.Image.boot(bootPath));
```

Injecting:

```java
Veritpath.Result r = Veritpath.inject(
        Veritpath.Image.initBoot(initBootPath),
        payloadDir,
        outputDir);
```

Build images with `Image.boot()` / `initBoot()` / `vendorBoot()` /
`recovery()` / `auto()` so the flag can never be wrong or missing. Use
`analyzeRaw(...)` or `run(...)` when you want full control.

> An earlier `inject(payloadDir, outputPath, images...)` passed images as bare
> positionals, but the CLI only recognises `--boot` / `--init-boot` /
> `--vendor-boot` / `--recovery`, so getopt dropped them and no image was
> loaded. The CLI now accepts a bare path too (detecting the role itself), and
> the Java side is typed, so the mistake cannot recur.

Check before flashing:

```java
if (!Veritpath.verify(patchedImage).ok()) {
    // do not flash, the image is incomplete
}
```

### 4. Notes

- **Do not call this on the main thread.** A GKI 1.0 boot.img is ~190MB and
  takes a moment to parse.
- The arguments to `run()` are exactly what you would type after `veritpath` on
  a shell, one String per token.
- Output comes from `nativeLastOutput()` and is overwritten by the next `run()`,
  so copy it if you need to keep it.
- **Analysing needs no root.** Flashing a patched image still has to go through
  fastboot.

## Option B: bundle the binary and exec it

Fine for a quick prototype, or if you already have an exec wrapper.

```java
// 1. copy out of assets into the private directory
File bin = new File(getFilesDir(), "veritpath");
try (InputStream in = getAssets().open("veritpath-" + abi)) {
    Files.copy(in, bin.toPath(), REPLACE_EXISTING);
}
bin.setExecutable(true);

// 2. run it
Process p = new ProcessBuilder(bin.getAbsolutePath(), "analyze", "--brief",
                               "--boot", bootPath)
        .redirectErrorStream(true).start();
String out = new String(p.getInputStream().readAllBytes());
int code = p.waitFor();
```

**Three traps:**

1. Never put it on the sdcard (`/storage/emulated/0`) — that mount is `noexec`
   and the binary will never run
2. `getFilesDir()` is restricted on some ROMs too; `/data/data/<pkg>/files` is
   the safer bet
3. You must ship one binary per ABI and select with `Build.SUPPORTED_ABIS[0]`

## Applies to both

### Architecture vs ABI

The `ARCH` that `veritpath analyze` reports is the **kernel** architecture, not
your app's ABI. Analysing an arm64 image from a 32-bit process is fine —
analysis only reads a file.

### Permissions

- **Analyse**: read-only on the image. Using the Storage Access Framework and
  letting the user pick the file is the most reliable route.
- **Inject**: needs write access for the output file.
- **Flash**: only fastboot can do this; an app cannot.

### Size

The static library is about 180KB, the shared object smaller. LZ4 is built in
and zlib comes from the system, so there are no third-party dependencies.

### Thread safety

`run()` redirects process-wide stdout internally (via `dup2`), so **do not call
it concurrently**. Serialise the calls or guard them with a lock.

## Verifying the binding

You can prove the JNI layer really works even without an NDK:

```bash
bash tools/test_jni.sh
```

It generates a minimal `jni.h` stub, builds the binding, and drives it with a
working JNIEnv to run `analyze --brief`, checking that the captured output
actually contains `ARCH:`. CI runs this too.

## Troubleshooting

### `no input images given`

No image was loaded. Usual causes:

1. **The image was passed as a bare positional** — older CLI builds only honour
   `--boot` / `--init-boot` / `--vendor-boot` / `--recovery`. Current builds
   accept a bare path and detect the role, but passing the flag explicitly is
   still clearer.
2. **The path is missing or unreadable** — the error prints the resolved path,
   the working directory and a suggestion.
3. **The file is not a boot image** — run `hexdump` and check the first 8 bytes
   for `ANDROID!` or `VNDRBOOT`.

```java
// see what the command actually looks like
Log.d("vp", String.join(" ", args));
```

### Two images supplied but `TARGET` is none

Neither parsed to a ramdisk. `analyze` lists `header_version` and
`ramdisk_size` per image, so you can see which one came up empty.
