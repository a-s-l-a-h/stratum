# Stratum (v0.2)

> **A code generation pipeline that automatically creates a JNI/nanobind C++ bridge**

Stratum is a build-time pipeline that reads Android SDK `.class` files, generates real C++ source with hardcoded JNI method IDs and nanobind bindings, compiles them into a `.so` shared library, and packages the result into a Python wheel. There is no reflection at runtime. Method IDs are resolved once via `std::call_once` and cached for the lifetime of the process.

> ⚠️ **Stratum — Active Development.** This is not a finished product. You will hit errors. Stages may fail on edge cases.

**Example apps:** https://github.com/a-s-l-a-h/stratum_v0_2_android_example

---

## How It Works — Architecture

Stratum has three layers:

```
Python code (your app)
       │
       ▼
nanobind bindings  ←── generated .so (_stratum.so)
       │
       ▼
C++ JNI layer      ←── generated C++ source (hardcoded method IDs)
       │
       ▼
Android JVM        ←── real Java/Kotlin Android APIs
```

**Layer 1 — Python:** You write normal Python. You import `_stratum` and call Android APIs directly as Python method calls.

**Layer 2 — nanobind C++ bindings:** The pipeline generates a `.so` shared library with nanobind wrappers. Each Android class becomes a C++ class. Each Android method becomes a C++ method. nanobind exposes them to Python.

**Layer 3 — JNI:** The C++ layer calls the Android JVM via JNI. Method IDs (`jmethodID`) are resolved once at first use via `std::call_once` and stored as static variables. No reflection happens on every call — only once per class, per process.

**Adapter layer (optional, for abstract classes and interfaces):** When you need to implement an Android interface or override an abstract class (e.g., `CameraDevice.StateCallback`), Stratum generates a thin Java adapter class. This adapter receives Java callbacks from the Android system and dispatches them into C++ via JNI, which then calls your Python function. This is the `05_5_abstract` stage.

```
Android system → Java adapter → C++ dispatch → Python callback
```

---

## Project Structure

```
C:\projects\stratum\stratum\
├── 00_setup/
│   └── main.py
├── 01_extract/
│   └── main.py
├── 02_inspect/
│   ├── main.py
│   └── targets.json          ← edit before running Stage 03
├── 03_javap/
│   └── main.py
├── 04_parse/
│   └── main.py
├── 05_resolve/
│   ├── main.py
│   └── targets.json          ← edit before running Stage 05
├── 05_5_abstract/
│   ├── main.py
│   └── targets.json          ← edit if using abstract/interface targets
├── 06_cpp_emit/
│   └── main.py
├── 07_build/
│   ├── main.py
│   └── templates/
├── 08_pyi_emit/
│   └── main.py
├── 09_wheel/
│   └── main.py
├── runtime/
│   └── java/com/stratum/runtime/
│       ├── StratumActivity.java          ← copy to Android project
│       └── StratumInvocationHandler.java ← copy to Android project
└── third_party/
    ├── nanobind/
    ├── ndk25/android-ndk-r25c/
    └── android-35.jar
```

---

## Tool Versions Used

These are the exact versions this pipeline was developed and tested with. Using different versions may work but is untested.

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.10+ | https://www.python.org/downloads/ |
| CMake | 4.3.0 | https://cmake.org/download/ |
| Ninja | 1.13.0 (pip) | `pip install ninja==1.13.0.post1` |
| Android NDK | r25c | https://github.com/android/ndk/releases/tag/r25c |
| nanobind | v2.12.0 | https://github.com/wjakob/nanobind/releases/tag/v2.12.0 |
| Chaquopy target | 3.10.13-0 | (resolved automatically, see Stage 00) |
| Android API | 35 | included as `android-35.jar` |

**CMake download:** https://cmake.org/download/
Pick the Windows x64 installer. During install, select "Add CMake to system PATH".

**Ninja install (pip — recommended for this pipeline):**
```
pip install ninja==1.13.0.post1
```
Ninja installed via pip is NOT on your system PATH. That is expected and fine — Stage 07 finds the binary automatically via `import ninja; ninja.BIN_DIR`. Do not try to add it to PATH manually.

**To verify your Ninja install:**
```
python -c "import ninja; import os; p = os.path.join(ninja.BIN_DIR, 'ninja.exe'); print('Found:', p)"
```

**NDK r25c direct download:** https://github.com/android/ndk/releases/tag/r25c
Download `android-ndk-r25c-windows.zip`, extract to `third_party/ndk25/`.

---

## Pipeline Overview

```
android.jar
     │
     ▼
[Stage 00] setup         — validate tools, write setup_report.json
     │
     ▼
[Stage 01] extract       — unpack android.jar → .class files  ← cached, slow first run
     │
     ▼
[Stage 02] inspect       — list all classes in jar, write targets.json template
     │                  ← EDIT targets.json before continuing
     ▼
[Stage 03] javap         — run javap on selected classes → raw disassembly
     │
     ▼
[Stage 04] parse         — parse javap output → structured JSON
     │
     ▼
[Stage 05] resolve       — resolve class hierarchy and dependencies
     │                  ← OPTIONALLY run Stage 05_5 for abstract/interface targets
     │
     ├──[Stage 05.5] abstract  ← OPTIONAL: generate Java adapter classes
     │        │
     ▼        ▼
[Stage 06] cpp_emit      — generate C++ source + CMakeLists.txt
     │
     ▼
[Stage 07] build         — compile .so with NDK + CMake + Ninja  ← slow, 1-5 min
     │
     ├──[Stage 08] pyi_emit   — generate .pyi type stubs (parallel, can run anytime after 05)
     │
     ▼
[Stage 09] wheel         — package .so + .pyi into Python wheel
```

**What is cached:**
Stages 01 and 02 extract and inspect the full android.jar. This is slow. Once done, outputs are cached in `01_extract/output/` and `02_inspect/output/`. You do NOT need to re-run these unless you change the Android API version.

**When to re-run from Stage 03:**
Every time you add or remove classes in `02_inspect/targets.json`.

**When to re-run from Stage 06:**
When fixing a C++ generation bug or changing emit settings.

**Stage 07 (build) is the slowest step** — compiling the generated C++ with NDK takes 1–5 minutes depending on how many classes you included. With a full class set it can be longer.

---

## Stage Reference

### Stage 00 — Setup

Validates all tools, resolves paths, and writes `00_setup/output/setup_report.json`. This file is the single source of truth for all subsequent stages.

```
python 00_setup/main.py ^
  --ndk-path "C:\projects\stratum\stratum\third_party\ndk25\android-ndk-r25c" ^
  --jar-path "C:\projects\stratum\stratum\third_party\android-35.jar" ^
  --api-version 35 ^
  --ndk-api 24 ^
  --chaquopy-version "3.10.13-0" ^
  --output "00_setup/output/"
```

Single line:
```
python 00_setup/main.py --ndk-path "C:\projects\stratum\stratum\third_party\ndk25\android-ndk-r25c" --jar-path "C:\projects\stratum\stratum\third_party\android-35.jar" --api-version 35 --ndk-api 24 --chaquopy-version "3.10.13-0" --output "00_setup/output/"
```

| Flag | Purpose |
|------|---------|
| `--ndk-path` | Path to NDK root (the `android-ndk-r25c` folder) |
| `--jar-path` | Path to `android-35.jar` |
| `--api-version` | Android API level (35) |
| `--ndk-api` | Minimum Android API for NDK compile (24 = Android 7.0+) |
| `--chaquopy-version` | Chaquopy Python target string, e.g. `3.10.13-0` |
| `--output` | Where to write `setup_report.json` |

---

### Stage 01 — Extract

Unpacks the Android `.jar` and extracts all `.class` files.

```
python 01_extract/main.py --setup "00_setup/output/setup_report.json" --output "01_extract/output/"
```

> ⏱️ This stage can take a few minutes. Output is cached — only re-run if you change the Android API version.

---

### Stage 02 — Inspect

Scans the extracted classes and writes a `targets.json` template listing all available classes.

```
python 02_inspect/main.py --input "01_extract/output/" --output "02_inspect/output/"
```

> ⏱️ This stage can also be slow on first run. Output is cached.

**After this stage, edit `02_inspect/targets.json` before continuing.**

#### `02_inspect/targets.json` — Format

This file controls which Android classes enter the pipeline. The `mode` field is the most important setting.

**`"mode": "full"`** — Extract ALL classes in the jar. The `targets` list is ignored for extraction — every class found in the jar is included. Use this when you want maximum coverage. Warning: full mode produces a very large C++ file and Stage 07 will be significantly slower.

> ⚠️ Full mode is not recommended unless you specifically need it. It is possible to hit memory or compile-time issues. Prefer listing specific targets.

**`"mode": "selective"`** (default) — Only extract the classes listed in the `targets` array where `"enabled": true`.

```json
{
  "android_version": "35",
  "mode": "selective",
  "targets": [
    {
      "fqn": "android.app.Activity",
      "enabled": true,
      "priority": 1,
      "notes": "core lifecycle"
    },
    {
      "fqn": "android.view.View",
      "enabled": true,
      "priority": 1,
      "notes": "base of everything"
    },
    {
      "fqn": "android.view.ViewGroup",
      "enabled": true,
      "priority": 1,
      "notes": "layout base"
    },
    {
      "fqn": "android.widget.Button",
      "enabled": true,
      "priority": 1,
      "notes": "core UI"
    },
    {
      "fqn": "android.widget.TextView",
      "enabled": true,
      "priority": 1,
      "notes": "text display"
    },
    {
      "fqn": "android.widget.EditText",
      "enabled": true,
      "priority": 1,
      "notes": "text input"
    },
    {
      "fqn": "android.widget.ImageView",
      "enabled": true,
      "priority": 1,
      "notes": "image display"
    },
    {
      "fqn": "android.widget.LinearLayout",
      "enabled": true,
      "priority": 1,
      "notes": "linear layout"
    },
    {
      "fqn": "android.widget.FrameLayout",
      "enabled": true,
      "priority": 1,
      "notes": "frame layout"
    },
    {
      "fqn": "android.content.Context",
      "enabled": true,
      "priority": 1,
      "notes": "Android context"
    },
    {
      "fqn": "android.content.Intent",
      "enabled": true,
      "priority": 1,
      "notes": "navigation"
    },
    {
      "fqn": "android.os.Bundle",
      "enabled": true,
      "priority": 1,
      "notes": "data passing"
    },
    {
      "fqn": "android.hardware.camera2.CameraManager",
      "enabled": true,
      "priority": 1,
      "notes": "camera"
    },
    {
      "fqn": "android.hardware.camera2.CameraDevice",
      "enabled": true,
      "priority": 1,
      "notes": "camera device"
    },
    {
      "fqn": "android.widget.RecyclerView",
      "enabled": false,
      "priority": 2,
      "notes": "disabled for now"
    }
  ]
}
```

**Fields:**

| Field | Purpose |
|-------|---------|
| `fqn` | Fully qualified class name |
| `enabled` | `true` = include, `false` = skip |
| `priority` | Informational only, does not affect processing |
| `notes` | Your notes, not used by the pipeline |

**Full mode example (targets list is irrelevant when mode is full):**

```json
{
  "android_version": "35",
  "mode": "full",
  "targets": [
    {
      "fqn": "android.app.Activity",
      "enabled": true,
      "priority": 1,
      "notes": "mode is full — this list is ignored, all classes extracted"
    }
  ]
}
```

---

### Stage 03 — javap

Runs `javap` on every selected class to produce raw disassembly output.

```
python 03_javap/main.py --input "01_extract/output/" --targets "02_inspect/targets.json" --setup "00_setup/output/setup_report.json" --output "03_javap/output/"
```

---

### Stage 04 — Parse

Parses javap output into structured JSON describing each class, method, and field.

```
python 04_parse/main.py --input "03_javap/output/" --output "04_parse/output/"
```

---

### Stage 05 — Resolve

Resolves the class hierarchy and dependency closure for all targets. This determines which parent classes and interfaces are pulled in automatically.

```
python 05_resolve/main.py --input "04_parse/output/" --output "05_resolve/output/"
```

**After this stage, edit `05_resolve/targets.json` before continuing.**

#### `05_resolve/targets.json` — Format

This file controls how the dependency closure is computed. The `closure_mode` is the key setting.

**`"closure_mode": "parents_only"`** — Pull in only direct parent classes (extends chain). Does not follow interfaces. Recommended for most use cases.

**`"closure_mode": "parents_and_interfaces"`** — Pull in parent classes AND all implemented interfaces. Use this when you need interface callback types (e.g., `View.OnClickListener`).

**`"closure_mode": "none"`** — No automatic closure. Only the classes you explicitly list are included. Use this when you want exact control.

> ⚠️ Do not use `"mode": "full"` in the resolve stage unless you are prepared for very long compile times and potential errors. The full android.jar contains thousands of classes. Start with `parents_only` and add what you need.

**Example — simple UI app (skip Stage 05.5):**

```json
{
  "enabled": true,
  "closure_mode": "parents_only",
  "targets": [
    { "fqn": "android.app.Activity" },
    { "fqn": "android.view.ContextThemeWrapper" },
    { "fqn": "android.content.ContextWrapper" },
    { "fqn": "android.content.Context" },
    { "fqn": "android.view.View" },
    { "fqn": "android.view.ViewGroup" },
    { "fqn": "android.widget.TextView" },
    { "fqn": "android.widget.Button" },
    { "fqn": "android.widget.LinearLayout" }
  ]
}
```

For this config you can skip Stage 05.5 entirely — there are no abstract callbacks to implement. Go directly from Stage 05 to Stage 06.

**Example — camera app with callbacks (needs Stage 05.5):**

```json
{
  "enabled": true,
  "closure_mode": "parents_and_interfaces",
  "targets": [
    { "fqn": "android.app.Activity" },
    { "fqn": "android.content.Context" },
    { "fqn": "android.view.View" },
    { "fqn": "android.view.View$OnClickListener" },
    { "fqn": "android.view.ViewGroup" },
    { "fqn": "android.view.Gravity" },
    { "fqn": "android.widget.FrameLayout" },
    { "fqn": "android.widget.LinearLayout" },
    { "fqn": "android.widget.LinearLayout$LayoutParams" },
    { "fqn": "android.widget.Button" },
    { "fqn": "android.widget.ImageView" },
    { "fqn": "android.graphics.Color" },
    { "fqn": "android.os.Handler" },
    { "fqn": "android.os.Looper" },
    { "fqn": "java.util.Arrays" },
    { "fqn": "java.util.List" },
    { "fqn": "java.util.ArrayList" },
    { "fqn": "java.nio.ByteBuffer" },
    { "fqn": "java.nio.Buffer" },
    { "fqn": "android.graphics.SurfaceTexture" },
    { "fqn": "android.graphics.Bitmap" },
    { "fqn": "android.view.Surface" },
    { "fqn": "android.view.TextureView" },
    { "fqn": "android.view.TextureView$SurfaceTextureListener" },
    { "fqn": "android.hardware.camera2.CameraManager" },
    { "fqn": "android.hardware.camera2.CameraDevice" },
    { "fqn": "android.hardware.camera2.CameraDevice$StateCallback" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback" },
    { "fqn": "android.hardware.camera2.CaptureRequest" },
    { "fqn": "android.hardware.camera2.CaptureRequest$Builder" },
    { "fqn": "android.hardware.camera2.CameraCharacteristics" }
  ]
}
```

This config NEEDS Stage 05.5 because `CameraDevice$StateCallback`, `CameraCaptureSession$StateCallback`, and `TextureView$SurfaceTextureListener` are all abstract callback classes that must be implemented via Java adapters.

---

### Stage 05.5 — Abstract / Interface Adapters (OPTIONAL)

Generates Java adapter classes for abstract classes and interfaces so you can implement callbacks from Python.

**When to run:** Only when your targets include abstract callback classes (inner classes ending in `$StateCallback`, `$Listener`, etc.) that the Android system calls back into.

**When to skip:** If you only call Android APIs and don't implement any callbacks. For example, a simple counter app with buttons only calls `setText`, `setOnClickListener` etc. — no callbacks to implement — skip this stage.

```
python 05_5_abstract/main.py ^
  --mode on ^
  --input 05_resolve/output/ ^
  --output 05_5_abstract/output/ ^
  --output-java 05_5_abstract/output_java/
```

Single line:
```
python 05_5_abstract/main.py --mode on --input 05_resolve/output/ --output 05_5_abstract/output/ --output-java 05_5_abstract/output_java/
```

| Flag | Purpose |
|------|---------|
| `--mode on` | Enable adapter generation |
| `--mode off` | Passthrough — copy input to output without generating adapters |
| `--input` | Output of Stage 05 |
| `--output` | Patched resolve output (used by Stage 06) |
| `--output-java` | Generated Java adapter source files |

#### `05_5_abstract/targets.json` — Format

Controls which abstract/interface targets get Java adapters generated.

The `avoid` field lets you exclude specific classes that are known to cause issues — for example, `android.app.admin.NetworkEvent` which has complex generic signatures.

```json
{
  "enabled": true,
  "avoid": [
    "android.app.admin.NetworkEvent"
  ],
  "targets": [
    { "fqn": "android.hardware.camera2.CameraDevice$StateCallback" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback" },
    { "fqn": "android.view.TextureView$SurfaceTextureListener" }
  ]
}
```

**`avoid`** — List of class FQNs to skip even if they appear as targets. Use this for classes that cause parser or codegen errors.

#### What adapters do

When Android calls a Java callback (e.g., `onOpened(CameraDevice camera)`), it calls a real Java object. Stratum generates a thin Java class for each callback type that:

1. Extends or implements the required Java abstract class / interface
2. Holds a reference to a `StratumInvocationHandler`
3. On each callback, dispatches to C++ via JNI, which then calls your registered Python function

This is the proxy mechanism. Without adapters, you cannot receive callbacks from Android — you can only call outward into the Android API.

#### Copying adapter files to Android Studio

After running Stage 05.5, copy the generated Java files to your Android project:

```
05_5_abstract/output_java/com/stratum/adapters/*.java
    → app/src/main/java/com/stratum/adapters/
```

---

### Stage 06 — C++ Emit

Generates C++ source files and `CMakeLists.txt`.

**If you ran Stage 05.5:**
```
python 06_cpp_emit/main.py --input 05_5_abstract/output/patched/ --output 06_cpp_emit/output/
```

**If you skipped Stage 05.5:**
```
python 06_cpp_emit/main.py --input 05_resolve/output/ --output 06_cpp_emit/output/
```

---

### Stage 07 — Build

Compiles the generated C++ into a `.so` shared library using NDK + CMake + Ninja.

```
python 07_build/main.py ^
  --cpp "06_cpp_emit/output/" ^
  --setup "00_setup/output/setup_report.json" ^
  --nanobind "third_party/nanobind" ^
  --abi arm64-v8a ^
  --chaquopy 3.10.13-0 ^
  --output "07_build/output/"
```

Single line:
```
python 07_build/main.py --cpp "06_cpp_emit/output/" --setup "00_setup/output/setup_report.json" --nanobind "third_party/nanobind" --abi arm64-v8a --chaquopy 3.10.13-0 --output "07_build/output/"
```

> ⏱️ This is the slowest stage. Expect 1–5 minutes or more depending on number of classes.

| Flag | Purpose |
|------|---------|
| `--cpp` | Generated C++ source from Stage 06 |
| `--setup` | Path to `setup_report.json` from Stage 00 |
| `--nanobind` | Path to nanobind source |
| `--abi` | Target ABI (see table below) |
| `--chaquopy` | Chaquopy Python target version string |
| `--output` | Where to write the compiled `.so` |
| `--ultra-log` | Enable full trace logging (LOGV level) — development only |
| `--verbose-log` | Enable debug logging (LOGD level) — development only |

**ABI options:**

| ABI flag | Device type |
|----------|-------------|
| `arm64-v8a` | Modern Android phones (64-bit ARM) — default, use this |
| `armeabi-v7a` | Older 32-bit ARM devices |
| `x86_64` | Android emulator (x86 64-bit) |
| `x86` | Android emulator (x86 32-bit) |

**Logging flags:**

In development, add `--ultra-log` to get full method-level trace output in logcat. **Remove this in production** — it generates extremely verbose output and has a performance cost.

```
# Development — full trace
python 07_build/main.py ... --ultra-log

# Development — debug level
python 07_build/main.py ... --verbose-log

# Production — no logging flags (omit both)
python 07_build/main.py ...
```

**logcat filter for Stratum output:**
```
adb logcat -s "Stratum:V" "Stratum/TRACE:V" "Stratum/ARG:V" "Stratum/RET:V"
```

---

### Stage 08 — .pyi Stub Emit

Generates Python type stubs for IDE autocompletion. Can run in parallel with Stage 07 (both only depend on Stage 05 output).

```
python 08_pyi_emit/main.py --input "05_resolve/output/" --output "08_pyi_emit/output/"
```

---

### Stage 09 — Wheel

Packages the compiled `.so` and `.pyi` stubs into a Python wheel file.

```
python 09_wheel/main.py ^
  --so "07_build/output/_stratum.so" ^
  --pyi "08_pyi_emit/output/" ^
  --output "09_wheel/output/" ^
  --version 0.1.0 ^
  --abi arm64-v8a ^
  --chaquopy 3.10.13-0
```

Single line:
```
python 09_wheel/main.py --so "07_build/output/_stratum.so" --pyi "08_pyi_emit/output/" --output "09_wheel/output/" --version 0.1.0 --abi arm64-v8a --chaquopy 3.10.13-0
```

Match `--abi` and `--chaquopy` to exactly what you used in Stage 07.

---

## Android Studio Project Setup

### 1. Copy runtime files

From the Stratum repo, copy these two files to your Android project:

```
stratum/runtime/java/com/stratum/runtime/StratumActivity.java
stratum/runtime/java/com/stratum/runtime/StratumInvocationHandler.java
    → app/src/main/java/com/stratum/runtime/
```

### 2. Copy adapter files (only if you ran Stage 05.5)

```
05_5_abstract/output_java/com/stratum/adapters/*.java
    → app/src/main/java/com/stratum/adapters/
```

### 3. Update MainActivity

Your `MainActivity` must extend `StratumActivity` instead of `AppCompatActivity`:

```java
package com.example.yourapp;

import com.stratum.runtime.StratumActivity;

public class MainActivity extends StratumActivity {
}
```

That is all. `StratumActivity` handles loading the wheel, initializing the Python interpreter via Chaquopy, and calling your Python entry point.

### 4. Add the wheel to the Android project

Place the generated `.whl` from `09_wheel/output/` into:
```
app/src/main/python/
```

---

## Complete Example Runs

### Example 1 — Simple counter app (no callbacks, skip Stage 05.5)

**`02_inspect/targets.json`:**
```json
{
  "android_version": "35",
  "mode": "selective",
  "targets": [
    { "fqn": "android.app.Activity", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.view.View", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.view.ViewGroup", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.TextView", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.Button", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.LinearLayout", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.content.Context", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.os.Bundle", "enabled": true, "priority": 1, "notes": "" }
  ]
}
```

**`05_resolve/targets.json`:**
```json
{
  "enabled": true,
  "closure_mode": "parents_only",
  "targets": [
    { "fqn": "android.app.Activity" },
    { "fqn": "android.view.ContextThemeWrapper" },
    { "fqn": "android.content.ContextWrapper" },
    { "fqn": "android.content.Context" },
    { "fqn": "android.view.View" },
    { "fqn": "android.view.ViewGroup" },
    { "fqn": "android.widget.TextView" },
    { "fqn": "android.widget.Button" },
    { "fqn": "android.widget.LinearLayout" }
  ]
}
```

**Run sequence:**
```
python 00_setup/main.py --ndk-path "C:\projects\stratum\stratum\third_party\ndk25\android-ndk-r25c" --jar-path "C:\projects\stratum\stratum\third_party\android-35.jar" --api-version 35 --ndk-api 24 --chaquopy-version "3.10.13-0" --output "00_setup/output/"

python 01_extract/main.py --setup "00_setup/output/setup_report.json" --output "01_extract/output/"

python 02_inspect/main.py --input "01_extract/output/" --output "02_inspect/output/"

[edit 02_inspect/targets.json and 05_resolve/targets.json as above]

python 03_javap/main.py --input "01_extract/output/" --targets "02_inspect/targets.json" --setup "00_setup/output/setup_report.json" --output "03_javap/output/"

python 04_parse/main.py --input "03_javap/output/" --output "04_parse/output/"

python 05_resolve/main.py --input "04_parse/output/" --output "05_resolve/output/"

[skip Stage 05.5 — no abstract callbacks needed]

python 06_cpp_emit/main.py --input 05_resolve/output/ --output 06_cpp_emit/output/

python 07_build/main.py --cpp "06_cpp_emit/output/" --setup "00_setup/output/setup_report.json" --nanobind "third_party/nanobind" --abi arm64-v8a --chaquopy 3.10.13-0 --output "07_build/output/"

python 08_pyi_emit/main.py --input "05_resolve/output/" --output "08_pyi_emit/output/"

python 09_wheel/main.py --so "07_build/output/_stratum.so" --pyi "08_pyi_emit/output/" --output "09_wheel/output/" --version 0.1.0 --abi arm64-v8a --chaquopy 3.10.13-0
```

For this example's Python app code, see: https://github.com/a-s-l-a-h/stratum_v0_2_android_example

---

### Example 2 — OpenCV camera app (needs Stage 05.5 for callbacks)

**`02_inspect/targets.json`:**
```json
{
  "android_version": "35",
  "mode": "selective",
  "targets": [
    { "fqn": "android.app.Activity", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.view.View", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.view.ViewGroup", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.Button", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.TextView", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.ImageView", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.LinearLayout", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.widget.FrameLayout", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.content.Context", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.os.Handler", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.os.Looper", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.graphics.SurfaceTexture", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.graphics.Bitmap", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.view.Surface", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.view.TextureView", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.view.TextureView$SurfaceTextureListener", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CameraManager", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CameraDevice", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CameraDevice$StateCallback", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CaptureRequest", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CaptureRequest$Builder", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "android.hardware.camera2.CameraCharacteristics", "enabled": true, "priority": 1, "notes": "" },
    { "fqn": "java.nio.ByteBuffer", "enabled": true, "priority": 1, "notes": "" }
  ]
}
```

**`05_resolve/targets.json`:**
```json
{
  "enabled": true,
  "closure_mode": "parents_and_interfaces",
  "targets": [
    { "fqn": "android.app.Activity" },
    { "fqn": "android.content.Context" },
    { "fqn": "android.view.View" },
    { "fqn": "android.view.View$OnClickListener" },
    { "fqn": "android.view.ViewGroup" },
    { "fqn": "android.view.Gravity" },
    { "fqn": "android.widget.FrameLayout" },
    { "fqn": "android.widget.LinearLayout" },
    { "fqn": "android.widget.LinearLayout$LayoutParams" },
    { "fqn": "android.widget.Button" },
    { "fqn": "android.widget.ImageView" },
    { "fqn": "android.graphics.Color" },
    { "fqn": "android.os.Handler" },
    { "fqn": "android.os.Looper" },
    { "fqn": "java.util.Arrays" },
    { "fqn": "java.util.List" },
    { "fqn": "java.util.ArrayList" },
    { "fqn": "java.nio.ByteBuffer" },
    { "fqn": "java.nio.Buffer" },
    { "fqn": "android.graphics.SurfaceTexture" },
    { "fqn": "android.graphics.Bitmap" },
    { "fqn": "android.view.Surface" },
    { "fqn": "android.view.TextureView" },
    { "fqn": "android.view.TextureView$SurfaceTextureListener" },
    { "fqn": "android.hardware.camera2.CameraManager" },
    { "fqn": "android.hardware.camera2.CameraDevice" },
    { "fqn": "android.hardware.camera2.CameraDevice$StateCallback" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback" },
    { "fqn": "android.hardware.camera2.CaptureRequest" },
    { "fqn": "android.hardware.camera2.CaptureRequest$Builder" },
    { "fqn": "android.hardware.camera2.CameraCharacteristics" }
  ]
}
```

**`05_5_abstract/targets.json`:**
```json
{
  "enabled": true,
  "avoid": [
    "android.app.admin.NetworkEvent"
  ],
  "targets": [
    { "fqn": "android.hardware.camera2.CameraDevice$StateCallback" },
    { "fqn": "android.hardware.camera2.CameraCaptureSession$StateCallback" },
    { "fqn": "android.view.TextureView$SurfaceTextureListener" }
  ]
}
```

**Run sequence:**
```
python 00_setup/main.py --ndk-path "C:\projects\stratum\stratum\third_party\ndk25\android-ndk-r25c" --jar-path "C:\projects\stratum\stratum\third_party\android-35.jar" --api-version 35 --ndk-api 24 --chaquopy-version "3.10.13-0" --output "00_setup/output/"

python 01_extract/main.py --setup "00_setup/output/setup_report.json" --output "01_extract/output/"

python 02_inspect/main.py --input "01_extract/output/" --output "02_inspect/output/"

[edit targets.json files as above]

python 03_javap/main.py --input "01_extract/output/" --targets "02_inspect/targets.json" --setup "00_setup/output/setup_report.json" --output "03_javap/output/"

python 04_parse/main.py --input "03_javap/output/" --output "04_parse/output/"

python 05_resolve/main.py --input "04_parse/output/" --output "05_resolve/output/"

python 05_5_abstract/main.py --mode on --input 05_resolve/output/ --output 05_5_abstract/output/ --output-java 05_5_abstract/output_java/

[copy output_java files to Android Studio: app/src/main/java/com/stratum/adapters/]

python 06_cpp_emit/main.py --input 05_5_abstract/output/patched/ --output 06_cpp_emit/output/

python 07_build/main.py --cpp "06_cpp_emit/output/" --setup "00_setup/output/setup_report.json" --nanobind "third_party/nanobind" --abi arm64-v8a --chaquopy 3.10.13-0 --output "07_build/output/"

python 08_pyi_emit/main.py --input "05_resolve/output/" --output "08_pyi_emit/output/"

python 09_wheel/main.py --so "07_build/output/_stratum.so" --pyi "08_pyi_emit/output/" --output "09_wheel/output/" --version 0.1.0 --abi arm64-v8a --chaquopy 3.10.13-0
```

---

## Logging Reference

### Enable logging (development only)

```
--ultra-log   Full method trace — every JNI call, every arg, every return value
--verbose-log Debug level — method entry/exit without full arg dump
```

### Disable logging (production)

Omit both flags. The default build has no logging overhead.

### logcat filter

```
adb logcat -s "Stratum:V" "Stratum/TRACE:V" "Stratum/ARG:V" "Stratum/RET:V"
```

---

## Troubleshooting

**Stage 07 fails with Ninja not found:**
```
python -c "import ninja; import os; print(os.path.exists(os.path.join(ninja.BIN_DIR, 'ninja.exe')))"
```
Should print `True`. If not, run `pip install ninja`.

**Stage 07 fails with CMake not found:**
Verify CMake is on PATH: `cmake --version`. If not, add `C:\Program Files\CMake\bin` to your system PATH.

**Unknown class in C++ emit:**
Add the missing class to `02_inspect/targets.json` (enabled: true) and re-run from Stage 03.

**Abstract class error in Stage 06:**
The class likely needs a Java adapter. Add it to `05_5_abstract/targets.json` and run Stage 05.5.

**Wheel installs but methods missing:**
The class was not in `02_inspect/targets.json` or had `enabled: false`. Update and re-run from Stage 03.