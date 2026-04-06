# Stratum Pipeline — Stage 07: Build C++ (`_stratum.so`)

## Quick Start

**Production Build (Quiet / Fast)**
```bash
python 07_build/main.py --cpp "06_cpp_emit/output/" --setup "00_setup/output/setup_report.json" --nanobind "third_party/nanobind" --abi arm64-v8a --output "07_build/output/" --chaquopy 3.10.13-0
```

**Debug Build (Method boundaries & lookups logged)**
```bash
python 07_build/main.py --cpp "06_cpp_emit/output/" --setup "00_setup/output/setup_report.json" --nanobind "third_party/nanobind" --abi arm64-v8a --output "07_build/output/" --chaquopy 3.10.13-0 --verbose-log
```

**Ultra-Debug Build (Every JNI argument/return traced)**
```bash
python 07_build/main.py --cpp "06_cpp_emit/output/" --setup "00_setup/output/setup_report.json" --nanobind "third_party/nanobind" --abi arm64-v8a --output "07_build/output/" --chaquopy 3.10.13-0 --ultra-log
```

---

## What This Stage Does

Stage 07 compiles the C++ code generated in Stage 06 into a highly-optimized native Android shared library (`_stratum.so`). 

To do this, it:
1. **Downloads Chaquopy headers:** Automatically fetches the Chaquopy Python target ZIP from Maven for your requested ABI, extracting `Python.h` and `libpython.so`.
2. **Generates CMake Files:** Renders `CMakeLists.txt` and a special `StratumInit.cmake` file with absolute paths to bypass standard CMake Python discovery (which often fails in cross-compilation).
3. **Cross-Compiles:** Locates `ninja` and your Android NDK (located via Stage 00), then configures and builds the `.so` using the NDK's toolchain.

*Note: Chaquopy's `libpython.so` is linked dynamically. It is NOT bundled into the resulting `_stratum.so`. The actual Python interpreter will be provided by the Chaquopy plugin on the Android device at runtime.*

---

## Command-Line Arguments

| Argument | Required | Description |
|---|---|---|
| `--cpp` | ✅ Yes | Path to the generated C++ code (`06_cpp_emit/output/`). |
| `--setup` | ✅ Yes | Path to `setup_report.json` (from Stage 00) to locate the NDK and CMake. |
| `--nanobind` | ✅ Yes | Path to the nanobind source code cloned in Stage 00 (`third_party/nanobind/`). |
| `--output` | ✅ Yes | Directory where the final `_stratum.so` and build reports will be saved. |
| `--abi` | No | Target Android ABI. Defaults to `arm64-v8a`. Choices: `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`. |
| `--chaquopy` | No | Target Chaquopy Python version to link against (e.g., `3.10.13-0` or `3.12.0-0`). Must match your Android app's Chaquopy Python version. |

### Logging Arguments (Pick One)

| Argument | Description |
|---|---|
| *(None)* | **Production Mode.** Only fatal errors, Java exceptions, and warnings are logged. Maximum performance. |
| `--verbose-log` | **Diagnostic Mode.** Logs method entry/exit points, class initialization, and `GetMethodID` success/failures. Uses `LOGD` in logcat. |
| `--ultra-log` | **Trace Mode (Extremely Noisy).** Traces the direction of every single JNI call, logs the value of *every* C++ parameter passed, and the value of *every* pointer returned. Uses `LOGV` in logcat. Use only when hunting native crashes. |

---

## ABI Targets & Multi-Architecture Strategy

Android devices run on different processor architectures (ABIs). By default, this script builds for `arm64-v8a` (modern 64-bit Android devices, ~90% of the market).

**Important:** The ABI you choose here **must match** what you build in Stage 09 (Wheel Building). 

### How to build for multiple architectures (The Trick)
The script processes **one ABI per run**. If you want to bundle multiple architectures (e.g., ARM64 for real devices and x86_64 for the Android Studio Emulator), run Stage 07 multiple times, **changing the `--output` folder** each time so they don't overwrite each other:

```bash
# 1. Build ARM64
python 07_build/main.py --abi arm64-v8a --output "07_build/out_arm64/" ...

# 2. Build x86_64 (Emulator)
python 07_build/main.py --abi x86_64 --output "07_build/out_x86_64/" ...
```
Later, in Stage 09, you will point the wheel builder at both output folders to package them into a single Python `.whl`.

---

## Outputs

After a successful run, the `--output` directory will contain:

### `_stratum.so`
This is your final, compiled C++ Python extension. It is specifically built for the ABI you requested. 

### `build_report.json`
A diagnostic file summarizing the build times, file sizes, and architecture constraints.

```json
{
  "success": true,
  "so_path": "07_build/output/_stratum.so",
  "so_size_mb": 4.12,
  "abi": "arm64-v8a",
  "android_platform": "24",
  "build_time_seconds": 15.4,
  "chaquopy_version": "3.10.13-0"
}
```

### `build/` (Intermediate Directory)
Contains the raw CMakeCache, Ninja build scripts, and object files (`.o`). If you run into build errors, you can inspect `build/<abi>/CMakeFiles/CMakeOutput.log`.

---

## Exit Behaviour & Next Steps

| Result | Exit Code | Description |
|---|---|---|
| Build Success | `0` | `_stratum.so` created successfully. Proceed to Stage 08. |
| Ninja Missing | `1` | The `ninja` build system was not found in the NDK or system PATH. Run `pip install ninja`. |
| C++ Compile Error | `1` | Clang failed to compile the generated code. The terminal will print the C++ syntax error. Check Stage 06 output or manually inspect the `.cpp` files. |

**Next Step:**
Now that your C++ is compiled, you need Python `.pyi` type-stub files so your IDE (VSCode/PyCharm) knows how to autocomplete your Android classes. 
Proceed to Stage 08 (Pyi Emit).