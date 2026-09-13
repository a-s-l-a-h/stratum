
# Stratum Pipeline — Stage 07: Native Engine Compilation

## Overview

Stage 07 (`07_build/main.py`) compiles the 4 core C++ files emitted by Stage 06 into the native extension module: `_stratum.so`.

It manages downloading and caching the Chaquopy target Python package (headers and stub `libpython.so`), renders build configurations from Jinja-style templates, and executes `cmake` + `ninja` targeting the specified Android ABI via the Android NDK Clang toolchain.

---

## Key Features & Flags

### 1. Compile-Time Logging Stripping (`--log` vs `--no-log`)
* `--log` (default): Defines `-DSTRATUM_LOG_ENABLED=1`. Compiles in comprehensive JNI and dispatch logging. Logging can still be toggled dynamically at runtime from Python via `stratum.set_log_enabled(bool)`.
* `--no-log`: Defines `-DSTRATUM_LOG_ENABLED=0`. Preprocessor macros strip all `LOGD`, `LOGV`, and `LOGT` statements completely out of the binary, reducing binary footprint and achieving maximum runtime performance.

### 2. ABI Targets
Compiles specifically for Android architectures via `--abi`:
* `arm64-v8a` (Modern 64-bit physical devices)
* `armeabi-v7a` (Legacy 32-bit physical devices)
* `x86_64` (Modern 64-bit Android Studio emulators)
* `x86` (Legacy 32-bit Android Studio emulators)

### 3. Automatic Platform Clamping
NDK r25c supports up to API 33 compilation target headers. If the application configuration specifies an API higher than the NDK toolchain provides (e.g. API 35), Stage 07 automatically clamps the compilation target to API 33 (`-DANDROID_PLATFORM=android-33`) to prevent link-time errors while allowing the runtime to run on Android 15 (API 35).

---

## CLI Options

| Argument | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| `--cpp` | **Yes** | — | Path to Stage 06 output (`06_cpp_emit/output/`). |
| `--setup` | **Yes** | — | Path to `setup_report.json` from Stage 00. |
| `--nanobind` | **Yes** | — | Path to Nanobind repository (`third_party/nanobind`). |
| `--templates` | No | `07_build/templates` | Path to CMake template directory. |
| `--abi` | No | `arm64-v8a` | Target ABI: `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`. |
| `--chaquopy` | No | `3.12.0-0` | Chaquopy target version to fetch/link against. |
| `--output` | **Yes** | — | Output directory for compilation artifacts and `_stratum.so`. |
| `--log` | No | `True` | Compile with deep trace logging enabled. |
| `--no-log` | No | — | Compile production binary with logging macros stripped. |

---

## Command Line Example

### Debug / Development Build:
```bash
python 07_build/main.py \
    --cpp 06_cpp_emit/output \
    --setup 00_setup/output/setup_report.json \
    --nanobind third_party/nanobind \
    --abi arm64-v8a \
    --chaquopy 3.12.0-0 \
    --output 07_build/output \
    --log
```

### Production / Release Build:
```bash
python 07_build/main.py \
    --cpp 06_cpp_emit/output \
    --setup 00_setup/output/setup_report.json \
    --nanobind third_party/nanobind \
    --abi arm64-v8a \
    --output 07_build/output \
    --no-log
```
